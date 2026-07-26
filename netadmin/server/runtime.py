"""Daemon runtime state and the ``/api/health`` snapshot (ARCHITECTURE.md 12).

This module owns two things the FastAPI app leans on but that are not HTTP:

* :class:`DaemonState` -- the in-process handle the lifespan populates with
  references to the started subsystems (scheduler, WS supervisor, probes,
  backfill task) plus the accounting the health endpoint reports on (uptime,
  which components could not be built, backfill progress).
* :func:`build_health` -- turns the store plus that state into the honest health
  document section 12 specifies: last-successful-poll age *per job*, WS listener
  state, per-job consecutive failures, DB size, entity counts, and uptime. Every
  value the daemon cannot actually know right now is reported as ``UNKNOWN``
  rather than guessed -- a health endpoint that lies is worse than none.

It touches no SQL directly (section 4): every fact about the database comes back
through :class:`~netadmin.store.repository.Repository` methods, and the on-disk
size comes from ``stat`` on the configured path.
"""

from __future__ import annotations

import inspect
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from netadmin import __version__
from netadmin.ingest.collector import (
    JOB_EVENTS_CATCHUP,
    JOB_FAST_DEVICE,
    JOB_FAST_HEALTH,
    JOB_FAST_STA,
    JOB_REPORTS_5MIN,
)
from netadmin.ingest.probes import JOB_DNS, JOB_DNS_ANCHOR, JOB_GW_RTT
from netadmin.logging import get_logger

_log = get_logger("server.runtime")

UNKNOWN = "UNKNOWN"

# Detection-engine + analysis job ids (the ``poll_runs.job`` values the detector
# passes and the factory's baseline / SLE jobs write). Mirrored from
# netadmin.detect.engine (``detect_<cadence>``) and netadmin.ingest.factory
# (JOB_BASELINE / JOB_SLE_MINUTES) as plain strings so this module stays free of
# a heavy import; the values are the stable job-name contract, not code.
_JOB_DETECT_FAST = "detect_fast"
_JOB_DETECT_WINDOW = "detect_window"
_JOB_DETECT_DAILY = "detect_daily"
_JOB_BASELINE = "baseline"
_JOB_SLE_MINUTES = "sle_minutes"
_JOB_CORRELATE = "correlate"

# The REAL collector + probe + analysis job ids (the ``poll_runs.job`` /
# ``CollectorStatus`` keys the running daemon actually writes -- collector.py
# ``JOB_*``, probes.py ``JOB_*``, the detector passes, and the factory's analysis
# jobs), mapped to the settings *section* + attr carrying their configured cadence
# and the ARCHITECTURE.md default. Keying on these -- not on drifted "canonical"
# names the daemon never emits -- is what lets the staleness check fire: a wedged
# job whose last_ok ages past 2.5x its cadence reports ``stale`` instead of a
# permanent ``ok`` (the phantom-name bug). A cadence whose attr does not exist on
# its section (``detect_daily`` is a cron, not an interval) falls back to the
# default for the staleness bound.
_JOB_CADENCE: dict[str, tuple[str, str, int]] = {
    JOB_FAST_DEVICE: ("poll", "device_s", 60),
    JOB_FAST_STA: ("poll", "sta_s", 60),
    JOB_FAST_HEALTH: ("poll", "health_s", 60),
    JOB_EVENTS_CATCHUP: ("poll", "event_catchup_s", 300),
    JOB_REPORTS_5MIN: ("poll", "report_5min_s", 21_600),
    JOB_DNS: ("poll", "probe_s", 60),
    JOB_DNS_ANCHOR: ("poll", "probe_s", 60),
    JOB_GW_RTT: ("poll", "probe_s", 60),
    _JOB_DETECT_FAST: ("detect", "fast_s", 60),
    _JOB_DETECT_WINDOW: ("detect", "window_s", 900),
    _JOB_DETECT_DAILY: ("detect", "daily_interval_s", 86_400),  # cron; nominal for staleness
    _JOB_BASELINE: ("detect", "baseline_s", 300),
    _JOB_SLE_MINUTES: ("sle", "minutes_s", 300),
    _JOB_CORRELATE: ("correlate", "interval_s", 60),
}

# Jobs health reports on even before the first poll_runs row lands (as UNKNOWN),
# so the surface is honest about what is not yet running. These are the ids the
# collector and probes emit, so once they run their status resolves instead of
# sitting UNKNOWN forever alongside phantom canonical names.
DEFAULT_JOBS: tuple[str, ...] = tuple(_JOB_CADENCE)

# How far back health scans poll_runs to find each job's last success. Wide
# enough to cover the slowest cadence (report backfill) with headroom.
_POLL_LOOKBACK_S = 7 * 86400


def _job_interval(settings: Any, job: str) -> Optional[int]:
    """Configured cadence (seconds) for a job, or ``None`` if not a periodic job.

    Reads the job's cadence from its settings *section* (``poll`` / ``detect`` /
    ``sle``). Falls back to the architecture default when the section or attribute
    is absent (a partially-populated settings object, or a cron job like
    ``detect_daily`` that has no interval attr), so the staleness branch still
    fires for a wedged job rather than degrading to "no interval".
    """
    entry = _JOB_CADENCE.get(job)
    if entry is None:
        return None
    section_name, attr, default = entry
    section = getattr(settings, section_name, None)
    if section is None:
        return default
    value = getattr(section, attr, None)
    return int(value) if value is not None else default


@dataclass
class DaemonState:
    """Live handles and accounting for the running daemon.

    The lifespan constructs one, stores it on ``app.state.daemon``, fills in the
    subsystem references as it starts them, and the health router reads it. A
    subsystem that could not be built (a peer module not yet merged, or a
    construction error) is recorded in :attr:`unavailable`, keeping the daemon up
    and the failure visible instead of crashing the whole process.
    """

    started_ts: int
    scheduler: Any = None
    ws_supervisor: Any = None
    probes: Any = None
    # The collector's live ``CollectorStatus`` (per-job consecutive-failure
    # counters + last_ok timestamps), the source section 5.2 names for
    # ``/api/health``. The integrate pass populates this when it wires the real
    # collector; health prefers it over the poll_runs fallback when present.
    collector_status: Any = None
    # The outbound alert dispatcher (section 20), when one was built. Health reads
    # its per-channel counters through ``.health()``; ``None`` means no dispatcher
    # exists (a lifespan that never ran, or one that failed to build).
    alerts: Any = None
    # The auto-investigator (section 21), when one was built. Health reads its
    # counters through ``.health()``; ``None`` means no investigator exists (a
    # lifespan that never ran, or one that failed to build).
    auto_investigator: Any = None
    backfill_task: Any = None
    backfill_status: str = "pending"  # pending | running | done | failed | absent
    ready: bool = False
    unavailable: dict[str, str] = field(default_factory=dict)

    def mark_unavailable(self, name: str, exc: BaseException) -> None:
        reason = f"{type(exc).__name__}: {exc}"
        self.unavailable[name] = reason[:200]
        _log.warning("daemon subsystem %r unavailable: %s", name, reason)

    def uptime_s(self, *, now: Optional[int] = None) -> int:
        now = int(time.time()) if now is None else now
        return max(0, now - self.started_ts)


async def maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable; otherwise return it unchanged.

    Lets the lifespan drive components whose ``start``/``stop`` are sync (the
    APScheduler-style scheduler) or async (the WS supervisor, probes) through one
    code path.
    """
    if inspect.isawaitable(value):
        return await value
    return value


def _db_size_bytes(db_path: Any) -> Optional[int]:
    """Total on-disk size of the SQLite database, WAL and SHM sidecars included.

    Returns ``None`` (surfaced as UNKNOWN) when the main file does not exist yet.
    """
    if db_path is None:
        return None
    main = Path(db_path)
    if not main.exists():
        return None
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(main) + suffix)
        try:
            if p.exists():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _entity_counts(store: Any) -> dict[str, Any]:
    """Total and per-type entity counts, or UNKNOWN if the store is unavailable."""
    if store is None:
        return {"total": UNKNOWN, "by_type": {}}
    try:
        rows = store.list_entities()
    except Exception:  # pragma: no cover - defensive
        _log.exception("entity count query failed")
        return {"total": UNKNOWN, "by_type": {}}
    by_type = Counter(str(r["entity_type"]) for r in rows)
    return {"total": len(rows), "by_type": dict(sorted(by_type.items()))}


def _collector_snapshot(state: DaemonState) -> Optional[dict[str, Any]]:
    """The collector's per-job status snapshot, if a collector is wired in.

    Accepts either a ``CollectorStatus`` (has ``.snapshot()``) or an already
    JSON-friendly dict. Returns ``None`` when absent so health falls back to the
    ``poll_runs`` DB read.
    """
    status = state.collector_status
    if status is None:
        return None
    snap = getattr(status, "snapshot", None)
    try:
        if callable(snap):
            return snap()
        if isinstance(status, dict):
            return status
    except Exception:  # pragma: no cover - defensive
        _log.debug("collector status snapshot failed", exc_info=True)
    return None


def _job_names(store: Any, state: DaemonState, snapshot: Optional[dict[str, Any]]) -> list[str]:
    """The set of jobs to report on: the canonical list, any live scheduler job
    ids, and any job the collector has already recorded status for -- so real
    collector job ids surface even when they differ from the canonical names.
    """
    names = set(DEFAULT_JOBS)
    sched = state.scheduler
    getter = getattr(sched, "get_jobs", None)
    if callable(getter):
        try:
            for job in getter():
                job_id = getattr(job, "id", None)
                if job_id:
                    names.add(str(job_id))
        except Exception:  # pragma: no cover - defensive
            _log.debug("scheduler.get_jobs() failed", exc_info=True)
    if snapshot:
        for section in ("last_run_ts", "last_ok_ts", "consecutive_failures"):
            names.update(str(k) for k in (snapshot.get(section) or {}))
    return sorted(names)


def _job_health(
    store: Any, settings: Any, job: str, now: int, snapshot: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Last-success age and trailing failure count for one job (section 12).

    Prefers the collector's live ``CollectorStatus`` (the source section 5.2
    names) when a snapshot is present; otherwise reads successful *and* failed
    ``poll_runs`` in the lookback window through the repository. No data either
    way -> UNKNOWN (the job has never run, which for a not-yet-wired collector is
    the honest answer).
    """
    interval = _job_interval(settings, job)
    result: dict[str, Any] = {
        "job": job,
        "interval_s": interval,
        "last_success_ts": None,
        "last_success_age_s": UNKNOWN,
        "consecutive_failures": 0,
        "status": UNKNOWN,
    }

    # The collector's live snapshot only covers the jobs the COLLECTOR owns
    # (fast_device, fast_sta, ...). The detector passes, the analysis jobs
    # (baseline, sle_minutes, correlate) and the probes record their runs in
    # ``poll_runs`` instead and never appear in it. Treating "absent from the
    # snapshot" as "never ran" pinned all of those to UNKNOWN forever, even while
    # they were running every 60 s, so only trust the snapshot for jobs it
    # actually tracks and fall through to the poll_runs read for the rest.
    owned = snapshot is not None and any(
        job in (snapshot.get(section) or {})
        for section in ("last_ok_ts", "last_run_ts", "consecutive_failures")
    )
    if snapshot is not None and owned:
        last_ok = (snapshot.get("last_ok_ts") or {}).get(job)
        last_run = (snapshot.get("last_run_ts") or {}).get(job)
        cf = int((snapshot.get("consecutive_failures") or {}).get(job, 0))
        if last_ok is None and last_run is None and cf == 0:
            return result  # collector owns this job but it has never run
        result["consecutive_failures"] = cf
        if last_ok is None:
            result["status"] = "failing"
            return result
        age = max(0, now - int(last_ok))
        result["last_success_ts"] = int(last_ok)
        result["last_success_age_s"] = age
        result["status"] = _job_status(cf, interval, age)
        return result

    if store is None:
        return result
    try:
        rows = store.read_poll_runs(job, now - _POLL_LOOKBACK_S, now + 1)
    except Exception:  # pragma: no cover - defensive
        _log.exception("poll_runs read failed for job %r", job)
        return result
    if not rows:
        return result

    last_ok_ts: Optional[int] = None
    for r in rows:  # ascending by ts
        if int(r["ok"]) == 1:
            last_ok_ts = int(r["ts"])
    consecutive_failures = 0
    for r in reversed(rows):
        if int(r["ok"]) == 1:
            break
        consecutive_failures += 1

    result["consecutive_failures"] = consecutive_failures
    if last_ok_ts is None:
        # Ran but never succeeded in the window: failing, not unknown.
        result["status"] = "failing"
        return result

    age = max(0, now - last_ok_ts)
    result["last_success_ts"] = last_ok_ts
    result["last_success_age_s"] = age
    result["status"] = _job_status(consecutive_failures, interval, age)
    return result


def _job_status(consecutive_failures: int, interval: Optional[int], age: int) -> str:
    """Classify a job: ``failing`` overrides ``stale`` (overdue by >2.5 cadences)."""
    if consecutive_failures > 0:
        return "failing"
    if interval and age > interval * 2.5:
        return "stale"
    return "ok"


def _ws_state(state: DaemonState) -> dict[str, Any]:
    """Best-effort WS listener state, probed defensively off the supervisor.

    The supervisor is a peer subsystem; rather than couple to one shape, this
    reads the first recognised status attribute it exposes and reports UNKNOWN
    otherwise. When the supervisor could not be built, that reason is surfaced.
    """
    sup = state.ws_supervisor
    if sup is None:
        reason = state.unavailable.get("ws_supervisor")
        return {"state": UNKNOWN, "detail": reason} if reason else {"state": UNKNOWN}
    for attr in ("state", "status"):
        value = getattr(sup, attr, None)
        if value is not None:
            return {"state": str(value)}
    for attr in ("is_running", "connected", "running"):
        value = getattr(sup, attr, None)
        if isinstance(value, bool):
            return {"state": "connected" if value else "disconnected"}
    return {"state": UNKNOWN}


def _alerts_block(state: DaemonState) -> dict[str, Any]:
    """Per-channel outbound-alert counters (section 20), or an honest empty block.

    Carries no delivery URL: channels are identified by name, and ``last_error``
    holds an exception type or an HTTP status only. A dispatcher that could not be
    built surfaces its reason here as well as flipping overall health to degraded.
    """
    dispatcher = state.alerts
    if dispatcher is None:
        block: dict[str, Any] = {"enabled": False, "running": False, "channels": []}
        reason = state.unavailable.get("alerts")
        if reason:
            block["detail"] = reason
        return block
    try:
        return dict(dispatcher.health())
    except Exception:  # pragma: no cover - defensive
        _log.debug("alert dispatcher health failed", exc_info=True)
        return {"enabled": UNKNOWN, "running": UNKNOWN, "channels": []}


def _auto_investigate_block(state: DaemonState) -> dict[str, Any]:
    """Auto-investigation counters (section 21), or an honest disabled block.

    Carries no dossier text and no credentials: only whether it is armed, how deep
    its queue is, and the per-reason skip/failure tallies. A build failure surfaces
    its reason here as well as flipping overall health to degraded.
    """
    investigator = state.auto_investigator
    if investigator is None:
        block: dict[str, Any] = {"enabled": False, "running": False}
        reason = state.unavailable.get("auto_investigator")
        if reason:
            block["detail"] = reason
        return block
    try:
        return dict(investigator.health())
    except Exception:  # pragma: no cover - defensive
        _log.debug("auto-investigator health failed", exc_info=True)
        return {"enabled": UNKNOWN, "running": UNKNOWN}


def _components_status(state: DaemonState) -> dict[str, str]:
    """Per-subsystem status: ``ok`` for a built one, else its unavailable reason."""
    out: dict[str, str] = {}
    for name, ref in (
        ("scheduler", state.scheduler),
        ("ws_supervisor", state.ws_supervisor),
        ("probes", state.probes),
    ):
        if name in state.unavailable:
            out[name] = f"unavailable: {state.unavailable[name]}"
        elif ref is not None:
            out[name] = "ok"
        else:
            out[name] = UNKNOWN
    return out


def build_health(
    store: Any, state: DaemonState, settings: Any, *, now: Optional[int] = None
) -> dict[str, Any]:
    """Compose the section-12 health document.

    Honest by construction: missing data is UNKNOWN, never zero-filled. Overall
    ``status`` is ``starting`` until the lifespan finishes wiring, ``degraded``
    when a subsystem failed to build or a job is stale/failing, else ``ok``.
    """
    now = int(time.time()) if now is None else now

    snapshot = _collector_snapshot(state)
    job_names = _job_names(store, state, snapshot)
    job_reports = [_job_health(store, settings, job, now, snapshot) for job in job_names]

    alerts = _alerts_block(state)
    # A channel stuck at ``failing`` means notifications are not reaching anyone.
    # That is exactly the kind of silent failure health exists to make loud.
    alerts_failing = any(c.get("status") == "failing" for c in alerts.get("channels", []))

    degraded = (
        bool(state.unavailable)
        or alerts_failing
        or any(j["status"] in ("stale", "failing") for j in job_reports)
    )
    if not state.ready:
        overall = "starting"
    elif degraded:
        overall = "degraded"
    else:
        overall = "ok"

    db_path = getattr(settings, "db_path", None)
    return {
        "status": overall,
        "ready": state.ready,
        "version": __version__,
        "uptime_s": state.uptime_s(now=now),
        "now": now,
        "db": {
            "path": str(db_path) if db_path is not None else UNKNOWN,
            "size_bytes": _db_size_bytes(db_path),
        },
        "entities": _entity_counts(store),
        "jobs": job_reports,
        "websocket": _ws_state(state),
        "components": _components_status(state),
        "alerts": alerts,
        "auto_investigate": _auto_investigate_block(state),
        "backfill": state.backfill_status,
    }


__all__ = [
    "UNKNOWN",
    "DEFAULT_JOBS",
    "DaemonState",
    "maybe_await",
    "build_health",
]
