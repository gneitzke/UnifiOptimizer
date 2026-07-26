"""Daemon component factory: wire the real ingest subsystems (ARCHITECTURE.md 5).

The FastAPI lifespan (``netadmin.server.main``) needs four running subsystems:
the collector scheduler, the WebSocket supervisor, the active probes, and a
one-shot startup backfill. Each builder produced a self-contained piece with its
own constructor; this module is the single seam that assembles them against one
shared, authenticated :class:`UnifiClient` and the process store, resolving the
naming drift between builders in favour of the architecture's names.

Nothing here mutates the controller. Every endpoint the wired jobs touch is in
the read set (section 5.1): ``stat/device`` / ``stat/sta`` / ``stat/health``
(collector), ``stat/event`` (catch-up), ``stat/report`` (backfill), the
read-only event WebSocket (supervisor). Probes are local DNS/ICMP only.

Construction is lazy and guarded by the caller: :func:`build_components` raises
when the controller is not configured, and the lifespan records that as an
unavailable subsystem rather than crashing (a health endpoint that honestly says
"no controller" beats a daemon that will not boot).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from netadmin.config import Settings
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.backfill import REPORT_METRICS, Backfiller
from netadmin.ingest.collector import Collector, build_scheduler
from netadmin.ingest.events import EventListener as EventsListener
from netadmin.ingest.events import WsSupervisor, catchup_events
from netadmin.ingest.probes import DEFAULT_ANCHOR, DnsProber, RttProber, persist_probe_samples
from netadmin.ingest.unifi.client import UnifiClient
from netadmin.ingest.unifi.endpoints import Endpoints
from netadmin.ingest.unifi.ws import EventListener as WsEventListener
from netadmin.logging import get_logger
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import Repository

logger = get_logger("ingest.factory")

# poll_runs.job / scheduler-job ids for the analysis jobs this module wires onto
# the collector's scheduler (alongside the detect_fast|window|daily passes the
# DetectorEngine records itself). Kept here — the daemon's composition seam —
# and mirrored in netadmin.server.runtime so /api/health reports on them.
JOB_BASELINE = "baseline"
JOB_SLE_MINUTES = "sle_minutes"
# The correlation pass (docs/ARCHITECTURE.md section 17): groups the confirmed
# open-issue set into incidents. One more firewalled interval job on the same
# scheduler, offset AFTER the detect passes so it reasons over the issues those
# passes just wrote. Mirrored in netadmin.server.runtime so /api/health reports it.
JOB_CORRELATE = "correlate"

# native_id prefix of the synthetic GATEWAY entity the prober attaches samples to
# when ``probe.gateway_ip`` is set but no real gateway entity exists (this site's
# gateway is a third-party router the controller never adopts). Mirrors the
# ``controller:{site_id}`` synthetic-entity convention in detect/detectors/infra.py.
# It carries only probe series (dns_latency_ms / gw_rtt_ms), never wan_latency, so
# ``client.dhcp._has_unifi_gateway`` and the WAN detectors correctly treat it as a
# probe-only gateway, not a UniFi WAN-health gateway.
_PROBE_TARGET_PREFIX = "probe_target:"

# Backfill report scopes worth pulling and the entity type each resolves to.
# ``site`` is omitted deliberately: it has no entity in the schema, so its rows
# never resolve and pulling it only loads the CloudKey with a Mongo aggregation
# whose every row is discarded (section 16 warns to keep report windows narrow).
BACKFILL_SCOPES: tuple[str, ...] = ("ap", "user", "gw")
_SCOPE_TYPE: dict[str, EntityType] = {
    "ap": EntityType.AP,
    "user": EntityType.CLIENT,
    "gw": EntityType.GATEWAY,
}

_RETENTION_PRUNE_JOB = "retention_prune"

# Per scope, the report metrics that have NO live collector source: the counter
# byte totals the reports carry (rx/tx bytes on ap/user, wan/lan bytes on gw).
# The report backfill anchors its gap on the newest ts of THESE series, not on
# ``max_sample_ts(entity_type)``. The live collector writes cpu/mem/uplink on the
# AP entity and rssi/satisfaction on the CLIENT entity every 60 s, so a
# whole-entity-type MAX(ts) is always ~60 s old and ``plan_report_windows`` would
# see the report gap as closed forever, never accumulating byte/num_sta/
# satisfaction history past the first window (ARCHITECTURE.md 5.3).
_REPORT_ANCHOR_METRICS: dict[str, tuple[str, ...]] = {
    scope: tuple(
        metric for _attr, metric, kind in REPORT_METRICS[scope] if kind is MetricKind.COUNTER
    )
    for scope in BACKFILL_SCOPES
}


@dataclass
class BuiltComponents:
    """The assembled subsystems the lifespan starts, plus the live collector.

    ``collector`` is exposed so the daemon can publish its ``CollectorStatus``
    snapshot at ``/api/health`` (section 5.2); the other four map onto the
    lifespan's start/stop contract.
    """

    scheduler: Any
    ws_supervisor: Any
    probes: Any
    backfill: Any  # zero-arg awaitable run once at startup
    collector: Collector


def build_endpoints(settings: Settings) -> tuple[Endpoints, UnifiClient]:
    """Construct a (not-yet-connected) client + endpoint facade from credentials.

    Raises :class:`RuntimeError` when the controller is not configured, so the
    caller can mark the whole ingest stack unavailable without a half-built
    subsystem. The client connects lazily on its first request (section 5.1),
    so no network I/O happens here.
    """
    creds = settings.unifi
    if not creds.is_configured:
        raise RuntimeError("controller not configured (set credentials in data/secrets.env)")
    client = UnifiClient(
        host=str(creds.host),
        site=creds.site,
        username=creds.username,
        password=creds.password,
        api_key=creds.api_key,
    )
    return Endpoints(client), client


class SupervisorTask:
    """Async start/stop wrapper that runs a :class:`WsSupervisor` as a task.

    The supervisor exposes ``run()`` / ``stop()`` but no ``start()``; the
    lifespan drives subsystems through ``start()``/``stop()``. This adapts the
    two: ``start`` schedules ``run`` on the loop, ``stop`` signals the supervisor
    and awaits the task. ``state`` gives ``/api/health`` a coarse running flag;
    the fine-grained transitions live in ``poll_runs(job='ws')``.
    """

    def __init__(self, supervisor: WsSupervisor) -> None:
        self._sup = supervisor
        self._task: Optional[asyncio.Task[Any]] = None

    @property
    def state(self) -> str:
        if self._task is None or self._task.done():
            return "stopped"
        return "running"

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._sup.run())

    async def stop(self) -> None:
        self._sup.stop()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best effort
                pass
            self._task = None


class ProbeRunner:
    """Runs the active DNS/RTT probes on a cadence, persisting to the gateway.

    ARCHITECTURE.md 5.4: the controller reports no DNS or path-latency timing, so
    this measures it locally. Each cycle it resolves the gateway entity from the
    store (probes attach to it), times a DNS lookup against the gateway resolver
    and a public anchor, and an ICMP/TCP RTT to the gateway, then writes the
    results through :func:`persist_probe_samples` (failures land as ``poll_runs``
    failures, never fabricated latencies).

    Target resolution (ARCHITECTURE.md 5.4): an explicit ``probe.gateway_ip`` is
    the target and probes run against it **without requiring any gateway entity** --
    the common case here, where the gateway is a third-party router the controller
    never adopts, so no UniFi gateway entity ever exists. Samples then land on a
    synthetic ``probe_target:{site_id}`` GATEWAY entity (upserted on demand). When
    ``gateway_ip`` is null, entity discovery is the fallback: a real gateway
    entity's ``ip`` state supplies the target. With neither a configured IP nor a
    real gateway entity a cycle is a quiet no-op -- honest, not a crash. Probers are
    cached and rebuilt only when the target changes, preserving the DNS-name
    rotation that keeps a single slow zone from skewing the signal.
    """

    def __init__(
        self,
        store: Repository,
        settings: Settings,
        *,
        interval_s: float,
    ) -> None:
        self._store = store
        self._site_id = settings.site_id
        self._cfg = settings.probe
        self._interval = max(1.0, float(interval_s))
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task[Any]] = None
        self._dns: Optional[DnsProber] = None
        self._rtt: Optional[RttProber] = None
        self._dns_key: Optional[str] = None
        self._rtt_key: Optional[str] = None
        self._warned_no_gateway = False

    @property
    def state(self) -> str:
        if self._task is None or self._task.done():
            return "stopped"
        return "running"

    async def start(self) -> None:
        if not self._cfg.enabled:
            logger.info("probes disabled by config (netadmin.probe.enabled=false)")
            return
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best effort
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a probe crash must not kill the runner
                logger.exception("probe cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def _gateway(self) -> Optional[tuple[int, Optional[str]]]:
        """Resolve (probe_entity_id, gateway_ip) from config + inventory.

        A real UniFi gateway entity, when present, owns the probe series (so all
        WAN data for that gateway co-locates). Otherwise an explicit
        ``probe.gateway_ip`` still runs, against a synthetic ``probe_target``
        entity. With neither a real gateway nor a configured IP, there is no target
        and the cycle no-ops.
        """
        configured_ip = self._cfg.gateway_ip
        rows = self._store.list_entities(EntityType.GATEWAY, site_id=self._site_id)
        real = [r for r in rows if not str(r["native_id"]).startswith(_PROBE_TARGET_PREFIX)]
        if real:
            entity_id = int(real[0]["entity_id"])
            ip = configured_ip or self._store.current_state(entity_id, "ip")
            return entity_id, ip
        if configured_ip:
            # No adopted gateway, but the operator named the target explicitly:
            # probe it against a synthetic probe-only gateway entity.
            return self._synthetic_probe_entity(), configured_ip
        if not self._warned_no_gateway:
            logger.debug(
                "no gateway entity and no probe.gateway_ip; probes idle until "
                "inventory syncs or a target is configured"
            )
            self._warned_no_gateway = True
        return None

    def _synthetic_probe_entity(self) -> int:
        """Upsert (idempotently) the synthetic probe-target gateway; return its id.

        Keyed on ``probe_target:{site_id}`` so exactly one persists per site and its
        id is stable across cycles and restarts. Typed GATEWAY as the nearest
        site-edge marker; it carries only probe series, never wan_latency, so it
        never masquerades as a UniFi WAN-health gateway (see ``_PROBE_TARGET_PREFIX``).
        """
        entity = Entity(
            entity_type=EntityType.GATEWAY,
            native_id=f"{_PROBE_TARGET_PREFIX}{self._site_id}",
            site_id=self._site_id,
            name="probe target",
            meta={"synthetic": True, "role": "probe_target"},
        )
        return self._store.upsert_entity(entity)

    async def _cycle(self) -> None:
        resolved = self._gateway()
        if resolved is None:
            return
        entity_id, ip = resolved
        resolver = self._cfg.gateway_resolver or ip
        anchor = self._cfg.anchor or DEFAULT_ANCHOR

        dns_key = f"{resolver}|{anchor}"
        if self._dns is None or dns_key != self._dns_key:
            self._dns = DnsProber(gateway_resolver=resolver, anchor=anchor)
            self._dns_key = dns_key

        samples = list(await self._dns.probe_once())

        if ip:
            if self._rtt is None or ip != self._rtt_key:
                self._rtt = RttProber(gateway_ip=ip)
                self._rtt_key = ip
            samples.append(await self._rtt.probe_once())

        persist_probe_samples(self._store, entity_id, samples)


def _utcnow_ts() -> int:
    """Epoch-second UTC now, for the analysis jobs' evaluation clock."""
    return int(datetime.now(timezone.utc).timestamp())


def _firewalled(
    store: Repository, job_name: str, work: Callable[[int], object]
) -> Callable[[], Awaitable[None]]:
    """Wrap a sync analysis body in the collector's exception-firewall pattern.

    Returns a zero-arg coroutine the scheduler runs on the loop thread (the store
    connection is loop-bound; a sync store write must not run in APScheduler's
    thread pool). Like :meth:`Collector._run` it ALWAYS records a ``poll_runs``
    row (ok/duration/error) and never propagates, so one crashing analysis cycle
    can never kill the scheduler and a gap is a recorded failure, not a stall. The
    detector passes record their own poll_runs; this covers ``baseline`` /
    ``sle_minutes`` whose bodies do not.
    """

    async def _run() -> None:
        ts = _utcnow_ts()
        start = time.monotonic()
        ok = False
        error: Optional[str] = None
        try:
            work(ts)
            ok = True
        except Exception as exc:  # noqa: BLE001 - the firewall must catch everything
            error = repr(exc)[:500]
            logger.exception("analysis job %s failed", job_name)
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            store.record_poll_run(job=job_name, ok=ok, ts=ts, duration_ms=duration_ms, error=error)
        except Exception:  # noqa: BLE001 - accounting must never kill the cycle
            logger.exception("failed to record poll_run for %s", job_name)

    return _run


def _add_analysis_jobs(
    scheduler: Any,
    settings: Settings,
    store: Repository,
    issue_engine: Any,
) -> None:
    """Wire detection + baselines + SLE onto the collector's one scheduler.

    All four analysis surfaces run in the single daemon process on the event-loop
    thread (ARCHITECTURE.md section 3), against the same loop-bound store the
    collector writes to:

    * ``detect_fast|window|detect_daily`` — the three detector tiers, registered by
      :func:`~netadmin.detect.engine.schedule_detection`; each pass records its own
      ``poll_runs`` row and drives the issue engine.
    * ``baseline`` (5 min) — :meth:`Baselines.update_from_recent`, the incremental
      EWMA / rolling-quantile fold.
    * ``sle_minutes`` (5 min) — the SLE user-minute accounting for the last
      *complete* 5-minute bucket (the current bucket is still filling).

    One :class:`Baselines` is built here and shared by the detector engine, the
    baseline-update job, and the SLE minutes job, so all three read and write the
    one ``baselines`` table through this repository. Nothing is started — the
    lifespan owns ``scheduler.start()``.
    """
    from netadmin.detect.baseline import Baselines
    from netadmin.detect.engine import EngineRunConfig, build_detector_engine, schedule_detection
    from netadmin.sle.minutes import SleMinutesJob, bucket_of

    baselines = Baselines.for_repository(store)
    det = settings.detect

    engine = build_detector_engine(
        store,
        issue_engine,
        settings=settings,
        baselines=baselines,
        config=EngineRunConfig(
            fast_interval_s=int(det.fast_s),
            window_interval_s=int(det.window_s),
            daily_hour=int(det.daily_hour),
        ),
    )
    # detect_fast / detect_window / detect_daily onto the same scheduler. Cadences
    # come from the engine's config (the DetectConfig block) -- NOT poll.device_s --
    # so ``settings.detect`` is the single source of truth for the detection tiers
    # and /api/health's staleness bound for detect_fast reads the same number.
    schedule_detection(engine, scheduler=scheduler, daily_hour=int(det.daily_hour))

    sle_job = SleMinutesJob(store, baselines, settings=settings)
    bucket_s = int(sle_job.cfg.bucket_seconds)

    def _baseline_work(ts: int) -> None:
        baselines.update_from_recent(ts)

    def _sle_work(ts: int) -> None:
        # The last complete bucket: the bucket containing ``ts`` is still filling.
        sle_job.run_bucket(bucket_of(ts, bucket_s) - bucket_s)

    now = datetime.now(timezone.utc)
    analysis_jobs = [
        (JOB_BASELINE, _firewalled(store, JOB_BASELINE, _baseline_work), int(det.baseline_s)),
        (
            JOB_SLE_MINUTES,
            _firewalled(store, JOB_SLE_MINUTES, _sle_work),
            int(settings.sle.minutes_s),
        ),
    ]
    for i, (job_id, func, seconds) in enumerate(analysis_jobs):
        scheduler.add_job(
            func,
            "interval",
            seconds=max(1, seconds),
            id=job_id,
            name=job_id,
            max_instances=1,
            coalesce=True,
            # Offset after the detect passes so a busy tick does not all fire at once.
            next_run_time=now + timedelta(seconds=15 + i * 3),
            replace_existing=True,
        )


def _add_correlation_job(scheduler: Any, settings: Settings, store: Repository) -> None:
    """Wire the correlation pass onto the collector's one scheduler (section 17).

    The pass groups the confirmed open-issue set into incidents; it is pure logic
    over the store (its only I/O is the :class:`CorrelationStore` Protocol the
    :class:`StoreCorrelationRepository` adapter satisfies) and idempotent, so an
    interval cadence — recompute from scratch each tick — is the whole contract.
    It runs behind the same ``poll_runs`` firewall as the other analysis jobs (a
    crashing pass records a failed run and never kills the scheduler), and its
    first run is offset a beat *after* the detect passes so it reasons over the
    issues those passes just wrote (steps 1-6 run against active/resolving issues;
    pending are excluded). Disabled by config -> no job is scheduled and
    ``/api/health`` reports ``correlate`` as never-run (honestly UNKNOWN).
    """
    cfg = settings.correlate
    if not cfg.enabled:
        logger.info("correlation disabled by config (netadmin.correlate.enabled=false)")
        return

    from netadmin.correlate.engine import CorrelationEngine
    from netadmin.correlate.models import CorrelationConfig
    from netadmin.correlate.store_repository import StoreCorrelationRepository

    engine = CorrelationEngine(
        StoreCorrelationRepository(store),
        config=CorrelationConfig(temporal_slack_s=int(cfg.temporal_slack_s)),
    )

    def _correlate_work(ts: int) -> None:
        engine.run(ts)

    now = datetime.now(timezone.utc)
    scheduler.add_job(
        _firewalled(store, JOB_CORRELATE, _correlate_work),
        "interval",
        seconds=max(1, int(cfg.interval_s)),
        id=JOB_CORRELATE,
        name=JOB_CORRELATE,
        max_instances=1,
        coalesce=True,
        # After the detect passes + baseline/sle jobs (staggered at 5-18 s) so a
        # busy tick does not fire everything at once and the first correlation
        # sees the freshly-written issues.
        next_run_time=now + timedelta(seconds=25),
        replace_existing=True,
    )


def _add_prune_job(scheduler: Any, store: Repository, *, prune_hour: int) -> None:
    """Add the nightly retention prune to the collector scheduler (section 4).

    The prune runs as a coroutine job so it executes on the event-loop thread
    the store's SQLite connection is bound to (section 3); a sync job would run
    in APScheduler's thread pool and raise a cross-thread SQLite error.
    """
    from apscheduler.triggers.cron import CronTrigger

    async def _prune() -> None:
        deleted = store.prune()
        logger.info("nightly retention prune deleted %s", deleted)

    scheduler.add_job(
        _prune,
        CronTrigger(hour=int(prune_hour), minute=0, timezone=timezone.utc),
        id=_RETENTION_PRUNE_JOB,
        name=_RETENTION_PRUNE_JOB,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )


def build_components(
    settings: Settings, store: Repository, *, issue_engine: Any = None
) -> BuiltComponents:
    """Assemble every ingest subsystem against one shared controller session.

    Raises when the controller is not configured (the caller marks the stack
    unavailable). The collector's ``events_catchup`` and ``reports_5min`` jobs
    are wired to the real catch-up and backfill machinery; a nightly prune job is
    added to the same scheduler; the detection tiers, baseline update, and SLE
    minutes jobs are wired onto that same scheduler (section 6 & 8); the WS
    supervisor and probe runner are wrapped for the lifespan's start/stop
    contract; and a one-shot startup backfill is returned as an awaitable.

    ``issue_engine`` is the shared :class:`~netadmin.issues.engine.IssueEngine` the
    detection passes drive; the daemon passes the same instance the API routers
    read through. When omitted (e.g. the factory smoke test) one is built from the
    store, since the engine is I/O-free and all state lives in the store.
    """
    endpoints, client = build_endpoints(settings)

    if issue_engine is None:
        from netadmin.issues.engine import IssueEngine
        from netadmin.issues.store_repository import StoreIssueRepository

        issue_engine = IssueEngine(StoreIssueRepository(store))

    backfiller = Backfiller(
        endpoints,
        store,
        scopes=BACKFILL_SCOPES,
        fivemin_retention_s=int(settings.backfill.fivemin_hours) * 3600,
        hourly_retention_s=int(settings.backfill.hourly_days) * 86400,
    )

    def _last_ts_by_scope() -> dict[str, Optional[int]]:
        return {
            s: store.max_sample_ts_for_metrics(_SCOPE_TYPE[s], _REPORT_ANCHOR_METRICS[s])
            for s in BACKFILL_SCOPES
        }

    async def _event_catchup(_ts: int) -> None:
        await catchup_events(store, endpoints)

    async def _reports_backfill(ts: int) -> None:
        await backfiller.run(_last_ts_by_scope(), now=ts)

    async def _startup_backfill() -> Any:
        return await backfiller.run(_last_ts_by_scope())

    collector = Collector(
        endpoints,
        store,
        site_id=settings.site_id,
        event_catchup=_event_catchup,
        reports_backfill=_reports_backfill,
    )
    scheduler = build_scheduler(collector, settings.poll)
    _add_prune_job(scheduler, store, prune_hour=settings.retention.prune_hour)
    _add_analysis_jobs(scheduler, settings, store, issue_engine)
    _add_correlation_job(scheduler, settings, store)

    def _ws_factory() -> EventsListener:
        return EventsListener(WsEventListener(client), store)

    ws_supervisor = SupervisorTask(WsSupervisor(_ws_factory, store))
    probes = ProbeRunner(store, settings, interval_s=settings.poll.probe_s)

    return BuiltComponents(
        scheduler=scheduler,
        ws_supervisor=ws_supervisor,
        probes=probes,
        backfill=_startup_backfill,
        collector=collector,
    )


__all__ = [
    "BACKFILL_SCOPES",
    "JOB_BASELINE",
    "JOB_SLE_MINUTES",
    "JOB_CORRELATE",
    "BuiltComponents",
    "ProbeRunner",
    "SupervisorTask",
    "build_components",
    "build_endpoints",
]
