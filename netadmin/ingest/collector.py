"""Collector jobs + APScheduler wiring (ARCHITECTURE.md 5.2).

The collector is the daemon's heartbeat: async jobs that poll the controller on
fixed cadences, map each response through :mod:`netadmin.ingest.mapping`, and
write inventory + metrics through the :class:`~netadmin.store.repository.Repository`.

Two invariants the architecture makes non-negotiable:

* **Per-cycle exception firewall.** Every job runs inside :meth:`Collector._run`,
  which ALWAYS records a ``poll_runs`` row (ok/duration/error) and updates the
  consecutive-failure counter, and never propagates. One crashing cycle must not
  kill the scheduler or the next cycle -- a gap is a recorded failure, never a
  silent stall.
* **One instance per job.** The scheduler runs ``max_instances=1`` with
  ``coalesce=True`` and staggered start offsets so cadences do not align and a
  slow poll never stacks on itself.

The collector holds one long-lived :class:`Repository` so counter-delta state
persists across cycles. ``events_catchup`` and ``reports_5min`` delegate to
injected callables (WS catch-up dedupe and ``stat/report`` backfill), wired by
the daemon factory; the ``rogueap`` / ``alarms`` / ``anomalies`` read-set jobs
(ARCHITECTURE.md 5.1) run their bodies directly against the endpoint facade.

**Accepted tradeoff -- synchronous store writes on the event loop.** Every job
body writes to SQLite *synchronously* (``upsert_entity`` / ``record_samples`` /
``record_events``) while running on the daemon's single event-loop thread, so a
poll cycle briefly blocks the loop for the duration of its write transaction.
This is deliberate (ARCHITECTURE.md sections 3-4): the store is a local WAL
SQLite file whose writes are sub-millisecond at this scale, the connection is
bound to the loop thread on purpose (a cross-thread write raises), and "one poll
cycle = one transaction" is far simpler to reason about than an async write
queue. The escape hatch, if a write ever turns hot (large backfills, a slow
disk): move that body's store work onto ``loop.run_in_executor`` with a
*dedicated* SQLite connection for the executor thread -- never share this one.
Heavy analysis (detectors, baselines) already belongs in a thread executor per
section 3; poll writes stay inline until measured to need otherwise.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.mapping import (
    EntityRecord,
    EntityRef,
    SampleBatch,
    map_clients,
    map_devices,
    map_health,
)
from netadmin.logging import get_logger
from netadmin.store.repository import Repository, SampleReading

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from netadmin.ingest.unifi.endpoints import Endpoints

logger = get_logger("ingest.collector")

# Device entity types a client can be attached to, tried in order when resolving
# a client's parent whose exact type guess (AP vs switch) may be wrong.
_DEVICE_PARENT_TYPES = (EntityType.AP, EntityType.SWITCH, EntityType.GATEWAY)

# Job names (also the poll_runs.job values and scheduler job ids).
JOB_FAST_DEVICE = "fast_device"
JOB_FAST_STA = "fast_sta"
JOB_FAST_HEALTH = "fast_health"
JOB_EVENTS_CATCHUP = "events_catchup"
JOB_REPORTS_5MIN = "reports_5min"
JOB_ROGUEAP = "rogueap"
JOB_WLANCONF = "wlanconf"
JOB_ALARMS = "alarms"
JOB_ANOMALIES = "anomalies"

# entities.entity_type for neighbor / rogue BSS rows (ARCHITECTURE.md 5.1). These
# are stored as inventory entities: the entities table has no type CHECK and
# ``upsert_entity`` str()-coerces a non-enum type, so this needs no schema or
# EntityType change. The daily poll upserts each BSSID, refreshing last_seen_ts +
# meta so the rogue-AP inventory the detectors read stays current.
ROGUE_BSS_TYPE = "rogue_bss"
# Cap the per-BSS sighting log so a long-lived neighbor cannot grow meta without
# bound; well above any recency window's worth of daily scans.
_ROGUE_SCAN_LOG_MAX = 60
# Distinct channels remembered per BSS. A neighbor that hops channels no longer
# churns a new issue per channel (the channel left the fingerprint), so the
# channels it has actually been seen on are evidence and need a short history.
_ROGUE_CHANNEL_LOG_MAX = 8


def _prior_meta(prior_row: Optional[Any]) -> dict[str, Any]:
    """Decoded ``meta`` of an existing ``rogue_bss`` row ({} when absent/unparsable)."""
    if prior_row is None:
        return {}
    try:
        meta = json.loads(prior_row["meta"] or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _prior_scan_ts(prior_meta: dict[str, Any]) -> list[int]:
    """Existing scan-timestamp log for a ``rogue_bss`` row (empty if none)."""
    raw = prior_meta.get("scan_ts")
    if not isinstance(raw, list):
        return []
    return [int(t) for t in raw if isinstance(t, (int, float))]


def _prior_channels(prior_meta: dict[str, Any]) -> list[int]:
    """Existing distinct-channel log for a ``rogue_bss`` row (empty if none)."""
    raw = prior_meta.get("channels")
    if not isinstance(raw, list):
        return []
    return [int(c) for c in raw if isinstance(c, (int, float))]


# Timestamps at/above this magnitude are epoch milliseconds, below are epoch
# seconds (mirrors netadmin.ingest.events: epoch-s stays < 1e11 until year 5138,
# epoch-ms is already ~1.7e12).
_MS_THRESHOLD = 100_000_000_000


def _fold_epoch_s(value: object) -> Optional[int]:
    """Fold a controller ms/s timestamp to epoch **seconds**, or None.

    Alarm/anomaly rows carry ``time`` / ``start_time`` in ms on every observed
    controller; a value already in seconds passes through unscaled. Non-numeric
    inputs (a missing field) yield None so the caller can fall back to the cycle
    timestamp.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    v = int(value)
    return v // 1000 if v >= _MS_THRESHOLD else v


def _hash_native(prefix: str, *parts: object) -> str:
    """A stable ``<prefix><sha1>`` dedupe id from the salient fields of a row."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return f"{prefix}{hashlib.sha1(raw.encode()).hexdigest()}"


@dataclass
class CollectorStatus:
    """Live collector health, surfaced at ``/api/health`` (section 5.2).

    Tracks, per job, the consecutive-failure streak (reset to 0 on any success)
    and the last run/ok timestamps and error. ``consecutive_failures(None)``
    returns the worst streak across all jobs -- the number ``/api/health`` alarms
    on when the controller has gone unreachable.
    """

    consecutive: dict[str, int] = field(default_factory=dict)
    last_run_ts: dict[str, int] = field(default_factory=dict)
    last_ok_ts: dict[str, int] = field(default_factory=dict)
    last_error: dict[str, Optional[str]] = field(default_factory=dict)
    total_runs: int = 0
    total_failures: int = 0

    def record(self, job: str, ok: bool, *, ts: int, error: Optional[str] = None) -> None:
        self.total_runs += 1
        self.last_run_ts[job] = ts
        if ok:
            self.consecutive[job] = 0
            self.last_ok_ts[job] = ts
            self.last_error[job] = None
        else:
            self.consecutive[job] = self.consecutive.get(job, 0) + 1
            self.last_error[job] = error
            self.total_failures += 1

    def consecutive_failures(self, job: Optional[str] = None) -> int:
        if job is not None:
            return self.consecutive.get(job, 0)
        return max(self.consecutive.values(), default=0)

    def snapshot(self) -> dict[str, object]:
        """A JSON-friendly view for the health endpoint."""
        return {
            "consecutive_failures": dict(self.consecutive),
            "worst_consecutive_failures": self.consecutive_failures(),
            "last_run_ts": dict(self.last_run_ts),
            "last_ok_ts": dict(self.last_ok_ts),
            "last_error": dict(self.last_error),
            "total_runs": self.total_runs,
            "total_failures": self.total_failures,
        }


class Collector:
    """Owns the poll jobs, the repository, and the shared status object."""

    def __init__(
        self,
        endpoints: "Endpoints",
        repo: Repository,
        *,
        status: Optional[CollectorStatus] = None,
        site_id: str = "default",
        clock: Callable[[], float] = time.time,
        event_catchup: Optional[Callable[[int], Awaitable[object]]] = None,
        reports_backfill: Optional[Callable[[int], Awaitable[object]]] = None,
    ) -> None:
        self._ep = endpoints
        self._repo = repo
        self._status = status or CollectorStatus()
        self._site_id = site_id
        self._clock = clock
        # Integration seam (ARCHITECTURE.md 5.2/5.3): the ``events_catchup`` and
        # ``reports_5min`` job bodies. When injected (by the daemon factory) they
        # drive the real ``stat/event`` catch-up and ``stat/report`` backfill;
        # when absent the jobs stay inert placeholders that still record a clean
        # poll_run, so a collector built without them (unit tests) is harmless.
        self._event_catchup = event_catchup
        self._reports_backfill = reports_backfill

    @property
    def status(self) -> CollectorStatus:
        return self._status

    @property
    def repo(self) -> Repository:
        return self._repo

    # ------------------------------------------------------------------ #
    # firewall
    # ------------------------------------------------------------------ #
    async def _run(self, job: str, work: Callable[[int], object]) -> bool:
        """Run one cycle of ``work`` behind the exception firewall.

        ``work`` is an async callable taking the cycle timestamp. This method
        records a ``poll_runs`` row and updates the status counter no matter what,
        and never raises: a crash here is a recorded failure, not a dead
        scheduler.
        """
        ts = int(self._clock())
        start = time.monotonic()
        ok = False
        error: Optional[str] = None
        try:
            await work(ts)
            ok = True
        except Exception as exc:  # noqa: BLE001 - the firewall must catch everything
            error = repr(exc)[:500]
            logger.exception("collector job %s failed", job)
        duration_ms = int((time.monotonic() - start) * 1000)

        try:
            self._repo.record_poll_run(job=job, ok=ok, ts=ts, duration_ms=duration_ms, error=error)
        except Exception:  # noqa: BLE001 - accounting must never kill the cycle
            logger.exception("collector failed to record poll_run for %s", job)
        self._status.record(job, ok, ts=ts, error=error)
        return ok

    # ------------------------------------------------------------------ #
    # public jobs (scheduler targets)
    # ------------------------------------------------------------------ #
    async def fast_device(self) -> bool:
        return await self._run(JOB_FAST_DEVICE, self._collect_device)

    async def fast_sta(self) -> bool:
        return await self._run(JOB_FAST_STA, self._collect_sta)

    async def fast_health(self) -> bool:
        return await self._run(JOB_FAST_HEALTH, self._collect_health)

    async def events_catchup(self) -> bool:
        return await self._run(JOB_EVENTS_CATCHUP, self._collect_events)

    async def reports_5min(self) -> bool:
        return await self._run(JOB_REPORTS_5MIN, self._collect_reports)

    async def rogueap(self) -> bool:
        return await self._run(JOB_ROGUEAP, self._collect_rogueap)

    async def wlanconf(self) -> bool:
        return await self._run(JOB_WLANCONF, self._collect_wlanconf)

    async def alarms(self) -> bool:
        return await self._run(JOB_ALARMS, self._collect_alarms)

    async def anomalies(self) -> bool:
        return await self._run(JOB_ANOMALIES, self._collect_anomalies)

    # ------------------------------------------------------------------ #
    # job bodies
    # ------------------------------------------------------------------ #
    async def _collect_device(self, ts: int) -> None:
        # Controller I/O first (outside the write lock), then the whole cycle's
        # inventory + state_changes + samples + rollups commit as ONE transaction
        # (ARCHITECTURE.md section 4: "one poll cycle = one transaction").
        devices = await self._ep.stat_device()
        mapping = map_devices(devices, ts, site_id=self._site_id)
        with self._repo.transaction():
            id_by_ref = self._apply_inventory(mapping.inventory, ts)
            self._write_batch(mapping.batch, id_by_ref)

    async def _collect_sta(self, ts: int) -> None:
        clients = await self._ep.stat_sta()
        mapping = map_clients(clients, ts, site_id=self._site_id)
        with self._repo.transaction():
            id_by_ref = self._apply_inventory(mapping.inventory, ts)
            self._write_batch(mapping.batch, id_by_ref)

    async def _collect_health(self, ts: int) -> None:
        subsystems = await self._ep.stat_health()
        health = map_health(subsystems, ts, site_id=self._site_id)
        if health.gateway_native_id is None or not health.batch.samples:
            return
        ref: EntityRef = (EntityType.GATEWAY, health.gateway_native_id)
        with self._repo.transaction():
            eid = self._find_device_entity(health.gateway_native_id)
            if eid is None:
                eid = self._repo.upsert_entity(
                    Entity(
                        entity_type=EntityType.GATEWAY,
                        native_id=health.gateway_native_id,
                        site_id=self._site_id,
                        first_seen_ts=ts,
                        last_seen_ts=ts,
                    ),
                    ts=ts,
                )
            self._write_batch(health.batch, {ref: eid})

    async def _collect_events(self, ts: int) -> None:
        """WS-missed ``stat/event`` catch-up dedupe (section 5.1-5.2).

        Delegates to the injected catch-up callable (``events.catchup_events``
        via the daemon factory). Without one this is an inert placeholder.
        """
        if self._event_catchup is None:
            logger.debug("events_catchup placeholder (ts=%d)", ts)
            return
        await self._event_catchup(ts)

    async def _collect_reports(self, ts: int) -> None:
        """``stat/report/5minutes.*`` incremental backfill (section 5.3).

        Delegates to the injected backfill callable (a ``Backfiller`` run over
        the recent gap, wired by the daemon factory on the 6 h cadence). Without
        one this is an inert placeholder.
        """
        if self._reports_backfill is None:
            logger.debug("reports_5min placeholder (ts=%d)", ts)
            return
        await self._reports_backfill(ts)

    async def _collect_rogueap(self, ts: int) -> None:
        """``stat/rogueap`` daily -> neighbor/rogue BSS inventory (section 5.1).

        Each neighbor BSS is upserted as a ``rogue_bss`` inventory entity keyed
        by BSSID, so a re-seen BSS refreshes its ``last_seen_ts`` and signal meta
        rather than duplicating -- the rogue-AP table the CCI/coverage detectors
        read (ARCHITECTURE.md 6). Whole poll commits as one transaction.
        """
        rogues = await self._ep.stat_rogueap()
        rows = [r for r in rogues if getattr(r, "bssid", None)]
        if not rows:
            return
        with self._repo.transaction():
            for r in rows:
                # Continuous-presence tracking: persistence is "seen in N distinct
                # recent scans", not a first-to-last span (a BSS seen once months
                # ago and again today spans months yet was absent the interim).
                # Each poll appends this scan's ts to a bounded sighting log carried
                # in meta; the detector counts distinct recent scans off it.
                prior = self._repo.find_entity(ROGUE_BSS_TYPE, str(r.bssid), site_id=self._site_id)
                prior_meta = _prior_meta(prior)
                scans = _prior_scan_ts(prior_meta)
                if ts not in scans:
                    scans.append(ts)
                scans = scans[-_ROGUE_SCAN_LOG_MAX:]
                # Distinct channels, most recent last: the evidence that replaces
                # channel-in-the-fingerprint for a hopping neighbor.
                channels = [c for c in _prior_channels(prior_meta) if c != r.channel]
                if r.channel is not None:
                    channels.append(int(r.channel))
                channels = channels[-_ROGUE_CHANNEL_LOG_MAX:]
                meta = {
                    k: v
                    for k, v in {
                        "channel": r.channel,
                        "channels": channels or None,
                        "rssi": r.rssi,
                        "signal": r.signal,
                        "band": r.band,
                        "security": r.security,
                        "is_rogue": r.is_rogue,
                        "is_ubnt": r.is_ubnt,
                        "seen_by_ap": r.ap_mac,
                        "scan_ts": scans,
                    }.items()
                    if v is not None
                }
                self._repo.upsert_entity(
                    Entity(
                        entity_type=ROGUE_BSS_TYPE,  # type: ignore[arg-type]
                        native_id=str(r.bssid),
                        site_id=self._site_id,
                        name=r.essid,
                        meta=meta,
                        first_seen_ts=ts,
                        last_seen_ts=ts,
                    ),
                    ts=ts,
                )

    async def _collect_wlanconf(self, ts: int) -> None:
        """``rest/wlanconf`` daily -> our own SSIDs as WLAN entities (section 5.1).

        A GET, not a write: this reads the WLAN config so the detection layer can
        tell one of *our* SSIDs from a neighbour's, which is what turns a
        neighbour BSS into an evil-twin finding instead of a guess. Each SSID is
        upserted keyed by the controller's wlanconf id (its ``name`` falls back to
        keying by SSID when a console omits the id), refreshing ``last_seen_ts``
        so a WLAN deleted on the controller ages out of the detector's set instead
        of lingering forever. An empty/absent route leaves the inventory untouched
        and the detector falls back to client-reported ESSIDs. Whole poll commits
        as one transaction.
        """
        wlans = await self._ep.rest_wlanconf()
        rows = [w for w in wlans if getattr(w, "name", None)]
        if not rows:
            return
        with self._repo.transaction():
            for w in rows:
                meta = {
                    k: v
                    for k, v in {
                        "enabled": w.enabled,
                        "security": w.security,
                        "wpa_mode": w.wpa_mode,
                        "is_guest": w.is_guest,
                    }.items()
                    if v is not None
                }
                self._repo.upsert_entity(
                    Entity(
                        entity_type=EntityType.WLAN,
                        native_id=str(w.id or w.name),
                        site_id=self._site_id,
                        name=w.name,
                        meta=meta,
                        first_seen_ts=ts,
                        last_seen_ts=ts,
                    ),
                    ts=ts,
                )

    async def _collect_alarms(self, ts: int) -> None:
        """``list/alarm`` every 15 min -> controller alarms into ``events``.

        Alarms are normalized like events (ms->s timestamp, controller ``_id`` as
        the dedupe native id, else a hash) and deduped by the store, so repeated
        polls of the same open alarm insert once (section 5.1).
        """
        alarms = await self._ep.list_alarm()
        records = [rec for rec in (self._alarm_record(a, ts) for a in alarms) if rec is not None]
        if records:
            self._repo.record_events(records)

    async def _collect_anomalies(self, ts: int) -> None:
        """``stat/anomalies`` every 15 min -> anomaly signals into ``events``.

        Each anomaly is stored as an ``ANOMALY_<type>`` event, resolved to the
        subject client when its MAC is known, deduped on (ts, mac, type) so a
        window re-poll does not duplicate the signal (section 5.1).
        """
        anomalies = await self._ep.stat_anomalies()
        records = [
            rec for rec in (self._anomaly_record(a, ts) for a in anomalies) if rec is not None
        ]
        if records:
            self._repo.record_events(records)

    def _alarm_record(self, alarm: object, ts: int) -> Optional[dict[str, object]]:
        key = getattr(alarm, "key", None)
        if not key:
            return None  # events.key is NOT NULL
        ets = _fold_epoch_s(getattr(alarm, "time", None)) or ts
        msg = getattr(alarm, "msg", None)
        alarm_id = getattr(alarm, "id", None)
        native = f"a:{alarm_id}" if alarm_id else _hash_native("ah:", ets, key, msg)
        return {
            "ts": ets,
            "key": key,
            "entity_id": None,
            "related_entity_id": None,
            "native_id": native,
            "msg": msg,
            "data": alarm.model_dump(exclude_none=True),  # type: ignore[attr-defined]
        }

    def _anomaly_record(self, anomaly: object, ts: int) -> Optional[dict[str, object]]:
        label = getattr(anomaly, "anomaly", None)
        mac = getattr(anomaly, "mac", None)
        start = getattr(anomaly, "start_time", None)
        ets = _fold_epoch_s(start) or ts
        key = f"ANOMALY_{label}".upper() if label else "ANOMALY"
        entity_id: Optional[int] = None
        if mac:
            row = self._repo.find_entity(EntityType.CLIENT, mac, site_id=self._site_id)
            if row is not None:
                entity_id = int(row["entity_id"])
        native = _hash_native("an:", ets, mac, label, start)
        return {
            "ts": ets,
            "key": key,
            "entity_id": entity_id,
            "related_entity_id": None,
            "native_id": native,
            "msg": label,
            "data": anomaly.model_dump(exclude_none=True),  # type: ignore[attr-defined]
        }

    # ------------------------------------------------------------------ #
    # store helpers
    # ------------------------------------------------------------------ #
    def _apply_inventory(self, records: list[EntityRecord], ts: int) -> dict[EntityRef, int]:
        """Upsert inventory parents-first; return the ref -> entity_id map.

        Parent refs are resolved against entities upserted earlier in this same
        call, falling back to the store for entities created by an earlier job or
        cycle. Changed tracked attributes are diffed into ``state_changes``.
        """
        id_by_ref: dict[EntityRef, int] = {}
        for rec in records:
            parent_id: Optional[int] = None
            if rec.parent_ref is not None:
                parent_id = id_by_ref.get(rec.parent_ref)
                if parent_id is None:
                    parent_id = self._find_device_entity(rec.parent_ref[1])
            rec.entity.parent_id = parent_id
            eid = self._repo.upsert_entity(rec.entity, ts=ts)
            id_by_ref[rec.ref] = eid
            attrs = {k: v for k, v in rec.tracked_attrs.items() if v is not None}
            if attrs:
                self._repo.sync_entity_state(eid, attrs, ts=ts)
        return id_by_ref

    def _write_batch(self, batch: SampleBatch, id_by_ref: dict[EntityRef, int]) -> int:
        """Resolve a metric batch to entity ids and record it. Returns rows written."""
        readings: list[SampleReading] = []
        for sample in batch.samples:
            eid = id_by_ref.get(sample.ref)
            if eid is None:
                eid = self._resolve_ref(sample.ref)
            if eid is None:
                logger.debug("dropping sample for unresolved entity %s", sample.ref)
                continue
            readings.append(
                SampleReading(
                    entity_id=eid,
                    metric=sample.metric,
                    ts=batch.ts,
                    value=sample.value,
                    unit=sample.unit,
                )
            )
        if not readings:
            return 0
        return self._repo.record_samples(readings)

    def _resolve_ref(self, ref: EntityRef) -> Optional[int]:
        """Look up an entity id for a ref already in the store, or None."""
        etype, native_id = ref
        row = self._repo.find_entity(etype, native_id, site_id=self._site_id)
        if row is not None:
            return int(row["entity_id"])
        # A client's parent guess may name the wrong device type; try the others.
        return self._find_device_entity(native_id)

    def _find_device_entity(self, native_id: str) -> Optional[int]:
        """Find a device entity (ap/switch/gateway) by MAC, across types."""
        for etype in _DEVICE_PARENT_TYPES:
            row = self._repo.find_entity(etype, native_id, site_id=self._site_id)
            if row is not None:
                return int(row["entity_id"])
        return None


# --------------------------------------------------------------------------- #
# scheduler factory
# --------------------------------------------------------------------------- #
def build_scheduler(
    collector: Collector,
    poll: object,
    *,
    scheduler: Optional["AsyncIOScheduler"] = None,
    stagger_s: float = 2.0,
) -> "AsyncIOScheduler":
    """Build (or configure) an AsyncIOScheduler wiring the collector's jobs.

    Every job is ``max_instances=1`` + ``coalesce=True`` with a staggered first
    run (``i * stagger_s`` seconds out) so the fast cadences do not all fire at
    the same instant. ``poll`` is a settings ``poll`` object (or any object with
    the ``*_s`` interval attributes); missing attributes fall back to the
    architecture defaults. Pass ``scheduler`` to configure an existing instance
    (e.g. one created in the FastAPI lifespan); pass a small ``stagger_s`` in
    tests to make the first runs fire promptly.
    """
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    sched = scheduler or AsyncIOScheduler(timezone=timezone.utc)
    now = datetime.now(timezone.utc)

    jobs = [
        (JOB_FAST_DEVICE, collector.fast_device, getattr(poll, "device_s", 60)),
        (JOB_FAST_STA, collector.fast_sta, getattr(poll, "sta_s", 60)),
        (JOB_FAST_HEALTH, collector.fast_health, getattr(poll, "health_s", 60)),
        (JOB_EVENTS_CATCHUP, collector.events_catchup, getattr(poll, "event_catchup_s", 300)),
        (JOB_REPORTS_5MIN, collector.reports_5min, getattr(poll, "report_5min_s", 21_600)),
        (JOB_ROGUEAP, collector.rogueap, getattr(poll, "rogueap_s", 86_400)),
        (JOB_WLANCONF, collector.wlanconf, getattr(poll, "wlanconf_s", 86_400)),
        (JOB_ALARMS, collector.alarms, getattr(poll, "alarm_s", 900)),
        (JOB_ANOMALIES, collector.anomalies, getattr(poll, "anomaly_s", 900)),
    ]
    for i, (job_id, func, seconds) in enumerate(jobs):
        sched.add_job(
            func,
            "interval",
            seconds=seconds,
            id=job_id,
            name=job_id,
            max_instances=1,
            coalesce=True,
            next_run_time=now + timedelta(seconds=i * stagger_s),
            replace_existing=True,
        )
    return sched


__all__ = [
    "Collector",
    "CollectorStatus",
    "build_scheduler",
    "JOB_FAST_DEVICE",
    "JOB_FAST_STA",
    "JOB_FAST_HEALTH",
    "JOB_EVENTS_CATCHUP",
    "JOB_REPORTS_5MIN",
    "JOB_ROGUEAP",
    "JOB_WLANCONF",
    "JOB_ALARMS",
    "JOB_ANOMALIES",
    "ROGUE_BSS_TYPE",
]
