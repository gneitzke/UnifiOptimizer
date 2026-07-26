"""Tech-visit runner (ARCHITECTURE.md section 3, "two modes, one engine").

The daemon's startup path *without the scheduler*. A just-in-time, one-shot run
for when an admin arrives at a network they do not continuously monitor: connect
**read-only**, backfill everything the controller still retains, run baselines +
a full detector pass + SLE over that window, and hand back a :class:`VisitReport`
(issues with evidence, SLE scores, a topology summary, and honest data-coverage
caveats).

This is *thin orchestration* (section 3): every step reuses an existing module —
the collector's inventory jobs, ``catchup_events``, the ``Backfiller``, the
``Baselines`` fold, the ``SleMinutesJob``, and the real ``DetectorEngine`` driving
the real ``IssueEngine``. No new detection logic lives here; the runner only
sequences the daemon's building blocks against a fresh working store and captures
the result.

Read-only, always. The only controller calls made are GETs from the read set
(``stat/device`` / ``stat/sta`` / ``stat/health`` / ``stat/rogueap`` /
``stat/event`` / ``stat/report``); nothing here can mutate the controller. The
fix engine is the only mutating component, and a visit never invokes it.

Point-in-time confirmation. A visit is a single assessment, not a running watch,
so its :class:`IssueEngine` is built with ``default_m = 1``: a detector that fires
in the one pass confirms its issue immediately (``pending -> active``) rather than
waiting the daemon's three consecutive cycles that a one-shot run can never
supply. The clear/resolve hysteresis is irrelevant to a run that never gets a
second cycle.

Every collection step is firewalled: a controller endpoint that 404s, times out,
or returns nothing degrades that step to a recorded caveat and the run continues,
so a partial controller still yields an honest, partial report.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from netadmin.config import Settings
from netadmin.domain.types import Cadence, EntityType
from netadmin.logging import get_logger

logger = get_logger("visit.runner")

# Step ids/order the runner walks. Exposed so the API/UI can render the pipeline
# before the first step reports, and so the progress log is stable across runs.
STEP_ORDER: tuple[tuple[str, str], ...] = (
    ("inventory", "Collect current inventory"),
    ("rogueap", "Scan neighbouring / rogue APs"),
    ("events", "Catch up the event log"),
    ("backfill", "Backfill retained history"),
    ("baselines", "Fold baselines"),
    ("sle", "Compute SLE minutes"),
    ("detect", "Run detectors"),
    ("report", "Assemble the report"),
)

# The SLE minute sweep over a multi-day window is the runner's one genuinely heavy
# loop (one bucket every 5 minutes). Cap how far back it sweeps so a long lookback
# does not turn a "quick visit" into an hour of bucket math; the scoring window is
# capped to match so the headline reflects exactly what was computed.
_MAX_SLE_SWEEP_S = 3 * 86_400


@dataclass
class VisitStep:
    """One pipeline step's outcome, streamed to progress listeners and stored."""

    id: str
    label: str
    status: str = "pending"  # pending | running | ok | failed | skipped
    detail: Optional[str] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ProgressFn = Callable[[VisitStep], None]


@dataclass
class VisitReport:
    """The self-contained result of one tech visit.

    Everything the CLI console summary, the HTML/JSON report, and the ``/visit``
    UI need — with no back-reference to the working store, so the store can be
    thrown away the moment the run ends.
    """

    started_ts: int
    finished_ts: int
    window_start_ts: int
    window_end_ts: int
    site_id: str
    lookback_days: int
    controller_host: Optional[str]
    headline_score: Optional[float]
    sles: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    topology: dict[str, Any] = field(default_factory=dict)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    db_path: Optional[str] = None

    @property
    def duration_s(self) -> int:
        return max(0, self.finished_ts - self.started_ts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _StepTracker:
    """Drives step lifecycle, records durations, and fans out to a progress fn."""

    def __init__(self, progress: Optional[ProgressFn]) -> None:
        self._progress = progress
        self.steps: dict[str, VisitStep] = {
            sid: VisitStep(id=sid, label=label) for sid, label in STEP_ORDER
        }
        self.caveats: list[str] = []

    def _emit(self, step: VisitStep) -> None:
        if self._progress is not None:
            try:
                self._progress(step)
            except Exception:  # noqa: BLE001 - a listener must never break the run
                logger.debug("visit progress listener raised", exc_info=True)

    def caveat(self, text: str) -> None:
        if text and text not in self.caveats:
            self.caveats.append(text)

    @contextmanager
    def step(self, step_id: str) -> Iterator[VisitStep]:
        """Run a step: mark running, time it, mark ok/failed, and firewall it.

        An exception inside the ``with`` body is caught, recorded as a failed step
        plus a caveat, and *swallowed* — one broken step never aborts the visit.
        """
        step = self.steps[step_id]
        step.status = "running"
        self._emit(step)
        start = time.monotonic()
        try:
            yield step
            if step.status == "running":
                step.status = "ok"
        except Exception as exc:  # noqa: BLE001 - firewall per step
            step.status = "failed"
            step.detail = f"{type(exc).__name__}: {exc}"[:200]
            self.caveat(f"{step.label} failed: {step.detail}")
            logger.warning("visit step %s failed", step_id, exc_info=True)
        finally:
            step.duration_ms = int((time.monotonic() - start) * 1000)
            self._emit(step)

    def as_list(self) -> list[dict[str, Any]]:
        return [self.steps[sid].to_dict() for sid, _ in STEP_ORDER]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def run_visit_async(
    settings: Settings,
    *,
    endpoints: Any = None,
    store: Any = None,
    db_path: Optional[str] = None,
    lookback_days: Optional[int] = None,
    now: Optional[int] = None,
    progress: Optional[ProgressFn] = None,
) -> VisitReport:
    """Run one tech visit and return its :class:`VisitReport`.

    ``endpoints`` — an :class:`~netadmin.ingest.unifi.endpoints.Endpoints` facade
    (or a read-only fake in tests). When omitted it is built from ``settings``
    credentials (lazy connect; the client is closed on the way out). ``store`` —
    a working :class:`~netadmin.store.repository.Repository`; when omitted a fresh
    one is opened (at ``db_path`` if given, else a temp file) and closed on exit.
    ``lookback_days`` caps the analysis window (default: the hourly-backfill
    retention). ``now`` pins the clock for deterministic tests. ``progress`` is
    called with each :class:`VisitStep` as it transitions.
    """
    from netadmin.ingest.collector import Collector
    from netadmin.store.repository import Repository

    now = int(time.time()) if now is None else int(now)
    lookback_days = int(lookback_days) if lookback_days else int(settings.backfill.hourly_days)
    lookback_days = max(1, lookback_days)
    window_s = lookback_days * 86_400
    window_start = now - window_s
    site_id = settings.site_id

    creds = settings.unifi
    controller_host = creds.host if creds else None

    owns_store = store is None
    owns_client = False
    client = None
    tracker = _StepTracker(progress)
    started_ts = now

    if owns_store:
        path = db_path or _temp_visit_db()
        store = Repository.open(
            path,
            site_id=site_id,
            retention_hourly_days=max(int(settings.retention.hourly_months) * 30, lookback_days),
        )
    resolved_db_path = _store_path(store, db_path)

    if endpoints is None:
        from netadmin.ingest.factory import build_endpoints

        endpoints, client = build_endpoints(settings)
        owns_client = True

    try:
        collector = Collector(endpoints, store, site_id=site_id)

        # 1) Current inventory + one live sample point (device/sta/health). Each
        # job firewalls itself and records its own poll_runs; a False return means
        # the controller call failed, which we surface as a caveat.
        with tracker.step("inventory") as step:
            ok_dev = await collector.fast_device()
            ok_sta = await collector.fast_sta()
            await collector.fast_health()
            # Our own SSIDs (rest/wlanconf, a GET). Part of inventory, not a
            # separate step: without it wifi.rogue_ap cannot tell one of our
            # SSIDs from a neighbour's, and reports the spoof subtype UNKNOWN.
            await collector.wlanconf()
            n = len(store.list_entities(site_id=site_id))
            step.detail = f"{n} entities"
            if not (ok_dev and ok_sta):
                tracker.caveat(
                    "Inventory poll was incomplete; some devices or clients may be missing."
                )

        # 2) Rogue / neighbour BSS inventory (coverage + CCI context).
        with tracker.step("rogueap"):
            await collector.rogueap()

        # 3) Event-log catch-up (stat/event; the WS snapshot is a daemon-only
        # long-lived stream, so a visit relies on the paged catch-up alone).
        with tracker.step("events") as step:
            from netadmin.ingest.events import catchup_events

            inserted = await catchup_events(store, endpoints, now=now)
            step.detail = f"{inserted} new events"

        # 4) Backfill the retained history onto the entities the inventory sync
        # just created (backfill never invents inventory).
        with tracker.step("backfill") as step:
            result = await _run_backfill(settings, endpoints, store, lookback_days, now)
            if result is not None:
                step.detail = f"{result.rows_inserted} rows, {result.buckets} buckets"
                if result.errors:
                    tracker.caveat(
                        f"{result.errors} backfill window(s) failed; history may have gaps."
                    )

        # 5) Baselines fold (EWMA + rolling quantiles) over everything collected.
        from netadmin.detect.baseline import Baselines

        baselines = Baselines.for_repository(store)
        with tracker.step("baselines") as step:
            updated = baselines.update_from_recent(now)
            step.detail = f"{updated} series"

        # 6) SLE minutes over the (capped) window, then score it.
        sle_start = max(window_start, now - _MAX_SLE_SWEEP_S)
        with tracker.step("sle") as step:
            buckets = _run_sle(store, baselines, settings, sle_start, now)
            step.detail = f"{buckets} buckets"
            if sle_start > window_start:
                tracker.caveat(
                    "SLE minutes were computed over the most recent "
                    f"{_MAX_SLE_SWEEP_S // 86_400} day(s) of the window (sweep cap)."
                )

        # 7) A full detector pass (all three cadence tiers) at ``now``.
        with tracker.step("detect") as step:
            fired = _run_detectors(store, baselines, settings, now)
            step.detail = f"{fired} findings"

        # 8) Assemble the report.
        with tracker.step("report"):
            report = _build_report(
                store,
                settings,
                started_ts=started_ts,
                finished_ts=int(time.time()) if now is None else now,
                window_start_ts=sle_start,
                window_end_ts=now,
                lookback_days=lookback_days,
                site_id=site_id,
                controller_host=controller_host,
                tracker=tracker,
                db_path=resolved_db_path,
            )
        # Refresh the step log now that the report step itself has closed, so the
        # embedded steps reflect its ``ok`` (it was still ``running`` mid-assembly).
        report.steps = tracker.as_list()
        return report
    finally:
        if owns_client and client is not None:
            await _close_client(client)
        if owns_store:
            store.close()


def run_visit(
    settings: Settings,
    *,
    endpoints: Any = None,
    store: Any = None,
    db_path: Optional[str] = None,
    lookback_days: Optional[int] = None,
    now: Optional[int] = None,
    progress: Optional[ProgressFn] = None,
) -> VisitReport:
    """Synchronous entry point (CLI + API worker thread).

    Drives :func:`run_visit_async` on a private event loop. Call this from a
    worker thread (never the daemon's event loop): it opens its own store on the
    calling thread and does the heavy sync analysis there, fully isolated from the
    daemon's loop-bound store.
    """
    return asyncio.run(
        run_visit_async(
            settings,
            endpoints=endpoints,
            store=store,
            db_path=db_path,
            lookback_days=lookback_days,
            now=now,
            progress=progress,
        )
    )


# --------------------------------------------------------------------------- #
# Step helpers (thin wrappers over existing modules)
# --------------------------------------------------------------------------- #
async def _run_backfill(
    settings: Settings, endpoints: Any, store: Any, lookback_days: int, now: int
) -> Any:
    """Pull ``stat/report`` for the whole window onto the synced inventory."""
    from netadmin.ingest.backfill import Backfiller
    from netadmin.ingest.factory import _REPORT_ANCHOR_METRICS, _SCOPE_TYPE, BACKFILL_SCOPES

    hourly_retention_s = min(lookback_days, int(settings.backfill.hourly_days)) * 86_400
    hourly_retention_s = max(hourly_retention_s, lookback_days * 86_400)
    backfiller = Backfiller(
        endpoints,
        store,
        scopes=BACKFILL_SCOPES,
        fivemin_retention_s=int(settings.backfill.fivemin_hours) * 3600,
        hourly_retention_s=hourly_retention_s,
    )
    last_ts_by_scope = {
        s: store.max_sample_ts_for_metrics(_SCOPE_TYPE[s], _REPORT_ANCHOR_METRICS[s])
        for s in BACKFILL_SCOPES
    }
    return await backfiller.run(last_ts_by_scope, now=now)


def _run_sle(store: Any, baselines: Any, settings: Settings, start_ts: int, end_ts: int) -> int:
    """Sweep the SLE minute accounting over ``[start_ts, end_ts)``; count buckets."""
    from netadmin.sle.minutes import SleMinutesJob

    job = SleMinutesJob(store, baselines, settings=settings)
    results = job.run_range(start_ts, end_ts)
    return len(results)


# Jobs a visit collects **in full** in its single pass: one complete, current poll
# of every device and client. For a point-in-time audit that *is* complete coverage
# of the present configuration, so the visit context reports these jobs as covered.
# Without this, every detector coverage-gates to UNKNOWN on the lone fresh sample
# and a visit could never fire the very config-audit findings it exists to surface
# (the coverage gate answers the daemon-era question "did the continuous poller keep
# up?" — inapplicable to a one-shot run). Time-series detectors are unaffected: they
# still compute on the samples they can actually read (genuinely sparse here), so
# lifting the gate never manufactures a false positive — it only stops the gate from
# hiding a real, snapshot-visible misconfiguration.
_VISIT_SNAPSHOT_JOBS = frozenset({"fast_device", "fast_sta", "fast_health"})


def _run_detectors(store: Any, baselines: Any, settings: Settings, now: int) -> int:
    """One pass of every cadence tier at ``now``. Returns total findings fired.

    The engine drives a point-in-time :class:`IssueEngine` (``default_m = 1``) so a
    detector that fires in this single pass confirms its issue immediately, which
    is the only sensible semantics for a one-shot visit (section 7's M consecutive
    cycles can never accrue in a run that has exactly one cycle). Coverage is read
    through a visit-scoped context (see :data:`_VISIT_SNAPSHOT_JOBS`).
    """
    from netadmin.detect.context import DetectorContext
    from netadmin.detect.engine import DetectorEngine
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.models import EngineConfig
    from netadmin.issues.store_repository import StoreIssueRepository

    class _VisitContext(DetectorContext):
        def coverage(self, window_seconds: int, job: str) -> float:
            if window_seconds <= 0:
                return 0.0
            if job in _VISIT_SNAPSHOT_JOBS:
                return 1.0  # a visit fully polled current state; the audit is complete
            interval_s = self._job_intervals.get(job, 60)
            start = self.now_ts - int(window_seconds)
            # A visit's history is what it backfilled, so count all retained polls,
            # not only live ones (source=None) — the honest coverage a visit has.
            return self.repo.expected_coverage(job, start, self.now_ts, interval_s, source=None)

    class _VisitDetectorEngine(DetectorEngine):
        def build_context(self, now_ts: int) -> DetectorContext:
            return _VisitContext(
                repo=self._repo,
                baselines=self._baselines,
                now_ts=now_ts,
                site_id=self._site_id,
                settings=self._settings,
            )

    issue_engine = IssueEngine(StoreIssueRepository(store), config=EngineConfig(default_m=1))
    engine = _VisitDetectorEngine(
        repo=store, issue_engine=issue_engine, baselines=baselines, settings=settings
    )
    fired = 0
    for cadence in (Cadence.FAST, Cadence.WINDOW, Cadence.DAILY):
        result = engine.run(cadence, now)
        fired += len(result.findings)
    return fired


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #
def _build_report(
    store: Any,
    settings: Settings,
    *,
    started_ts: int,
    finished_ts: int,
    window_start_ts: int,
    window_end_ts: int,
    lookback_days: int,
    site_id: str,
    controller_host: Optional[str],
    tracker: _StepTracker,
    db_path: Optional[str],
) -> VisitReport:
    from netadmin.server.serialize import entity_ref, entity_ref_map
    from netadmin.sle.scores import sle_scores

    # --- issues (open first, then any resolved), with entity refs resolved ---
    rows = store.list_issues()
    refs = entity_ref_map(store, [r["entity_id"] for r in rows])
    issues: list[dict[str, Any]] = []
    counts = {"total": 0, "p1": 0, "p2": 0, "p3": 0, "open": 0}
    for r in rows:
        item = _issue_dict(r)
        eid = r["entity_id"]
        item["entity"] = refs.get(int(eid)) if eid is not None else None
        issues.append(item)
        counts["total"] += 1
        sev = str(r["severity"])
        if sev in counts:
            counts[sev] += 1
        if str(r["state"]) != "resolved":
            counts["open"] += 1
    issues.sort(key=lambda i: (_severity_rank(i["severity"]), -int(i["first_seen_ts"])))

    # --- SLE scores (score and explanation are one GROUP BY; section 8) ---
    score_report = sle_scores(store, window_start_ts, window_end_ts, settings=settings)
    offender_ids = [
        off["attributed_entity_id"]
        for s in score_report.sles.values()
        for off in s.top_offenders
        if off.get("attributed_entity_id") is not None
    ]
    off_names = entity_ref_map(store, offender_ids)
    sles = _serialize_scores(score_report, off_names)

    # --- topology summary ---
    topology = _build_topology(store, site_id, entity_ref)

    # --- data-coverage caveats ---
    coverage = _coverage_breakdown(store, settings, window_start_ts, window_end_ts)
    _coverage_caveats(coverage, tracker)
    if not issues:
        tracker.caveat(
            "No issues detected in this window. Time-series detectors need "
            "continuous live polling and are limited in a one-shot visit; run the "
            "daemon for full coverage."
        )

    return VisitReport(
        started_ts=started_ts,
        finished_ts=finished_ts,
        window_start_ts=window_start_ts,
        window_end_ts=window_end_ts,
        site_id=site_id,
        lookback_days=lookback_days,
        controller_host=controller_host,
        headline_score=score_report.headline,
        sles=sles,
        issues=issues,
        issue_counts=counts,
        topology=topology,
        coverage=coverage,
        caveats=list(tracker.caveats),
        steps=tracker.as_list(),
        db_path=db_path,
    )


def _issue_dict(row: Any) -> dict[str, Any]:
    from netadmin.server.serialize import decode_json

    data = dict(row)
    data["evidence"] = decode_json(data.get("evidence"), {})
    ev = data["evidence"]
    data["confounders"] = list(ev.get("confounders_checked", [])) if isinstance(ev, dict) else []
    return data


def _serialize_scores(report: Any, off_names: dict[int, Any]) -> dict[str, Any]:
    return {
        "start_ts": report.start_ts,
        "end_ts": report.end_ts,
        "headline": report.headline,
        "weights": report.weights,
        "sles": {
            key: {
                "sle": s.sle,
                "score": s.score,
                "total_minutes": s.total_minutes,
                "ok_minutes": s.ok_minutes,
                "fail_minutes": s.fail_minutes,
                "classifiers": s.classifiers,
                "top_offenders": [
                    {
                        **off,
                        "entity": (
                            off_names.get(int(off["attributed_entity_id"]))
                            if off.get("attributed_entity_id") is not None
                            else None
                        ),
                    }
                    for off in s.top_offenders
                ],
            }
            for key, s in report.sles.items()
        },
    }


def _build_topology(store: Any, site_id: str, entity_ref: Any) -> dict[str, Any]:
    """Entity counts by type plus a compact device inventory for the report."""
    rows = store.list_entities(site_id=site_id)
    by_type: dict[str, int] = {}
    devices: list[dict[str, Any]] = []
    device_types = {EntityType.AP.value, EntityType.SWITCH.value, EntityType.GATEWAY.value}
    for r in rows:
        etype = str(r["entity_type"])
        by_type[etype] = by_type.get(etype, 0) + 1
        if etype in device_types:
            ref = entity_ref(r)
            if ref is not None:
                devices.append(ref)
    devices.sort(key=lambda d: (str(d.get("type")), str(d.get("name") or "")))
    return {
        "entity_count": len(rows),
        "by_type": dict(sorted(by_type.items())),
        "devices": devices,
    }


# poll_runs jobs whose live coverage tells the operator how trustworthy the
# window is. Each maps to the settings cadence used to compute expected coverage.
_COVERAGE_JOBS: tuple[tuple[str, str, str], ...] = (
    ("fast_device", "poll", "device_s"),
    ("fast_sta", "poll", "sta_s"),
    ("fast_health", "poll", "health_s"),
)


def _coverage_breakdown(
    store: Any, settings: Settings, start_ts: int, end_ts: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for job, section_name, attr in _COVERAGE_JOBS:
        section = getattr(settings, section_name, None)
        interval = int(getattr(section, attr, 60)) if section is not None else 60
        bd = store.coverage_breakdown(job, start_ts, end_ts, interval)
        out.append(
            {
                "job": job,
                "interval_s": interval,
                "live": bd["live"],
                "backfill": bd["backfill"],
                "total": bd["total"],
            }
        )
    return out


def _coverage_caveats(coverage: list[dict[str, Any]], tracker: _StepTracker) -> None:
    live_fracs = [c["live"] for c in coverage if c.get("live") is not None]
    if live_fracs and max(live_fracs) < 0.5:
        tracker.caveat(
            "Live poll coverage over the window is thin (a visit collects one live "
            "sample), so time-series detectors that require sustained live data "
            "return UNKNOWN rather than firing. Findings here come from the current "
            "configuration snapshot, the event log, and backfilled history."
        )


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _severity_rank(sev: str) -> int:
    return {"p1": 0, "p2": 1, "p3": 2}.get(str(sev), 3)


def _temp_visit_db() -> str:
    """A throwaway on-disk SQLite path for an ad-hoc visit (WAL needs a real file)."""
    d = tempfile.mkdtemp(prefix="netadmin-visit-")
    return str(Path(d) / "visit.db")


def _store_path(store: Any, db_path: Optional[str]) -> Optional[str]:
    if db_path:
        return str(db_path)
    try:
        row = store.connection.execute("PRAGMA database_list").fetchone()
        file = row["file"] if row is not None else None
        return str(file) if file else None
    except Exception:  # noqa: BLE001 - path is cosmetic
        return None


async def _close_client(client: Any) -> None:
    for name in ("aclose", "close"):
        closer = getattr(client, name, None)
        if closer is None:
            continue
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - best-effort teardown
            logger.debug("visit client close failed", exc_info=True)
        return


__all__ = [
    "STEP_ORDER",
    "VisitStep",
    "VisitReport",
    "run_visit",
    "run_visit_async",
]
