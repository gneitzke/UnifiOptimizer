"""Startup / tech-visit backfill (ARCHITECTURE.md 5.3).

The controller keeps 5-minute stats for roughly a day and hourly stats for
roughly a week. Anything the live collector did not capture in that window is
gone forever, so on startup (and on demand in tech-visit mode) backfill reads
the max stored sample ts per scope, and for every gap that exceeds one interval
pulls ``stat/report`` and inserts the missing history with its **original
timestamps** and ``poll_runs.source='backfill'``.

Three properties keep this honest:

* **Disjoint tiers, no double counting.** 5-minute and hourly reports cover the
  same wall-clock time at different resolutions. Folding both into the same
  ``samples``/rollup series would count the same traffic twice. So the gap is
  split: the 5-minute tier fills only the recent portion the controller still
  retains at 5-min resolution; the hourly tier fills only the *older* part of
  the gap that 5-min can no longer reach. See :func:`plan_report_windows`.

* **Report values are already per-bucket aggregates, stored verbatim.** A
  ``stat/report`` row's ``bytes``/``rx_bytes`` is the total *for that bucket*,
  not a cumulative counter-since-boot. Feeding it through the repository's
  counter-diffing path (which subtracts consecutive readings) would produce
  garbage. Backfill therefore tags every :class:`SampleReading` with
  ``kind=GAUGE`` so the repository stores it verbatim, while the metric *name*
  still governs rollup aggregation through the metric registry (a counter's
  buckets aggregate as ``sum``, exactly right for a pre-aggregated delta). The
  raw value written is identical to what a live counter delta would have been,
  so backfilled and live rows coexist in one series consistently.

* **Gap math, capped at controller retention.** Older-than-retention gaps are
  unrecoverable and reported as such, never fabricated. ``INSERT OR IGNORE`` in
  the repository means a live row already present at a timestamp always wins.

Report-attr -> metric mapping (:data:`REPORT_METRICS`) is defined here for the
Integrate agent to reconcile against the live collector's metric names. Where a
report exposes a metric the collector does not yet emit (``wan_rx_bytes`` and
friends on the gateway/site scope), this module registers it as a counter in the
shared store registry so its rollups aggregate correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from netadmin.domain.types import EntityType
from netadmin.ingest.unifi.endpoints import Endpoints
from netadmin.logging import get_logger
from netadmin.store.metrics import MetricKind, register_metric
from netadmin.store.repository import Repository, SampleReading

logger = get_logger("ingest.backfill")

# Report intervals backfill drives, and their bucket width in seconds.
FIVEMIN = "5minutes"
HOURLY = "hourly"
INTERVAL_SECONDS: dict[str, int] = {FIVEMIN: 300, HOURLY: 3600}

# Report scopes and the entity type each row's ``oid`` resolves to. ``site`` has
# no per-device entity type in the schema, so it is resolved (or skipped) only
# through a caller-supplied resolver; the default resolver returns None for it.
SCOPE_ENTITY: dict[str, Optional[EntityType]] = {
    "ap": EntityType.AP,
    "user": EntityType.CLIENT,
    "gw": EntityType.GATEWAY,
    "site": None,
}

_C = MetricKind.COUNTER
_G = MetricKind.GAUGE

# Per-scope: (report attr requested, stored metric name, metric kind). The kind
# here is the *series* kind (drives rollup aggregation via the registry); the
# reading itself is always stored verbatim (see module docstring). Hyphenated
# report attrs (``wan-rx_bytes``) map to underscore metric names.
REPORT_METRICS: dict[str, list[tuple[str, str, MetricKind]]] = {
    "ap": [
        ("rx_bytes", "rx_bytes", _C),
        ("tx_bytes", "tx_bytes", _C),
        ("num_sta", "num_sta", _G),
        ("satisfaction", "satisfaction", _G),
    ],
    "user": [
        ("rx_bytes", "rx_bytes", _C),
        ("tx_bytes", "tx_bytes", _C),
        # Report attr "signal" (dBm) -> the collector's canonical "rssi" metric
        # (mapping.py sources client RSSI in dBm from Client.signal, stored as
        # "rssi"), so backfilled and live client RSSI share one series.
        ("signal", "rssi", _G),
        ("satisfaction", "satisfaction", _G),
    ],
    "gw": [
        ("wan-rx_bytes", "wan_rx_bytes", _C),
        ("wan-tx_bytes", "wan_tx_bytes", _C),
        ("lan-rx_bytes", "lan_rx_bytes", _C),
        ("lan-tx_bytes", "lan_tx_bytes", _C),
    ],
    "site": [
        ("num_sta", "num_sta", _G),
        ("wan-rx_bytes", "wan_rx_bytes", _C),
        ("wan-tx_bytes", "wan_tx_bytes", _C),
    ],
}

# Register counter metrics the report exposes that the live collector may not
# have registered yet, so read_rollup aggregates them as sum. Idempotent.
for _scope_metrics in REPORT_METRICS.values():
    for _attr, _metric, _kind in _scope_metrics:
        if _kind is MetricKind.COUNTER:
            register_metric(_metric, MetricKind.COUNTER)

# Conservative per-tier controller-retention defaults (section 5.3). Overridable
# via the constructor; the collector verifies them per install at runtime.
DEFAULT_FIVEMIN_RETENTION_S = 24 * 3600  # ~1 day of 5-minute stats
DEFAULT_HOURLY_RETENTION_S = 7 * 24 * 3600  # ~1 week of hourly stats

# Chunk widths: keep report windows narrow -- each stat/report is a Mongo
# aggregation on the CloudKey and wide windows are slow (section 16).
DEFAULT_CHUNK_SECONDS: dict[str, int] = {
    FIVEMIN: 6 * 3600,  # 6 h per 5-minute request
    HOURLY: 2 * 24 * 3600,  # 2 days per hourly request
}

# (scope, oid) -> entity_id or None (unknown -> skip; backfill never invents
# inventory, that is the sync job's role).
EntityResolver = Callable[[str, str], Optional[int]]


@dataclass
class BackfillWindow:
    """One chunked report request: ``[start_ts, end_ts)`` in epoch seconds."""

    interval: str
    scope: str
    start_ts: int
    end_ts: int


@dataclass
class ScopeResult:
    """Outcome of backfilling one scope across both tiers."""

    scope: str
    windows: int = 0
    rows_inserted: int = 0
    buckets: int = 0
    skipped_unresolved: int = 0
    errors: int = 0


@dataclass
class BackfillResult:
    """Aggregate outcome of a backfill run."""

    scopes: dict[str, ScopeResult] = field(default_factory=dict)

    @property
    def rows_inserted(self) -> int:
        return sum(s.rows_inserted for s in self.scopes.values())

    @property
    def windows(self) -> int:
        return sum(s.windows for s in self.scopes.values())

    @property
    def buckets(self) -> int:
        return sum(s.buckets for s in self.scopes.values())

    @property
    def errors(self) -> int:
        return sum(s.errors for s in self.scopes.values())


# --------------------------------------------------------------------------- #
# Gap math (pure)
# --------------------------------------------------------------------------- #
def plan_report_windows(
    last_ts: Optional[int],
    now: int,
    *,
    fivemin_retention_s: int = DEFAULT_FIVEMIN_RETENTION_S,
    hourly_retention_s: int = DEFAULT_HOURLY_RETENTION_S,
) -> dict[str, Optional[tuple[int, int]]]:
    """Disjoint per-tier backfill windows for one scope, in epoch seconds.

    ``last_ts`` is the max stored sample ts for the scope (``None`` on a fresh
    install -> pull the full retained history). Returns ``{"5minutes": (lo, hi)
    or None, "hourly": (lo, hi) or None}`` where, with the tier boundary
    ``B = hour_floor(now - fivemin_retention)``:

    * the 5-minute window covers ``[max(last_ts, B), now]`` -- the recent gap the
      controller still retains at 5-min resolution;
    * the hourly window covers only the *older* part of the gap the 5-minute tier
      cannot reach: ``[max(last_ts, now - hourly_retention), B]``.

    The boundary is snapped **down to an hour** so the hourly window ends exactly
    where the 5-minute window begins. Hourly report buckets are hour-aligned;
    with an unaligned boundary the last hourly bucket (hour start ``H`` with
    ``H < boundary < H+3600``) carries a full hour of traffic covering
    ``[H, H+3600)`` while the 5-minute tier separately fills ``[boundary, now]``,
    double-counting ``[boundary, H+3600)`` into the byte/throughput rollups.
    Snapping to the hour makes that straddling bucket belong to exactly one tier.

    The two never overlap, so folding both into one series never double-counts.
    A tier whose gap is <= one interval (no meaningful gap) is ``None``. A gap
    older than a tier's retention is clamped to the retention floor: history the
    controller has already dropped is unrecoverable, never fabricated.
    """
    fivemin_floor = now - fivemin_retention_s
    hourly_floor = now - hourly_retention_s
    # Shared tier boundary, floored to the hour (see docstring): hourly ends here
    # and 5-minute begins here, so no hour-aligned bucket straddles the seam.
    boundary = fivemin_floor - (fivemin_floor % INTERVAL_SECONDS[HOURLY])

    five_lo = boundary if last_ts is None else max(boundary, last_ts)
    five: Optional[tuple[int, int]] = (
        (five_lo, now) if (now - five_lo) > INTERVAL_SECONDS[FIVEMIN] else None
    )

    hourly_hi = boundary  # 5-min tier owns everything at or after this
    hourly_lo = hourly_floor if last_ts is None else max(hourly_floor, last_ts)
    hourly: Optional[tuple[int, int]] = (
        (hourly_lo, hourly_hi) if (hourly_hi - hourly_lo) > INTERVAL_SECONDS[HOURLY] else None
    )
    return {FIVEMIN: five, HOURLY: hourly}


def chunk_window(start_ts: int, end_ts: int, chunk_s: int) -> list[tuple[int, int]]:
    """Split ``[start_ts, end_ts)`` into <= ``chunk_s`` pieces, oldest first."""
    if chunk_s <= 0:
        raise ValueError("chunk_s must be positive")
    chunks: list[tuple[int, int]] = []
    lo = start_ts
    while lo < end_ts:
        hi = min(lo + chunk_s, end_ts)
        chunks.append((lo, hi))
        lo = hi
    return chunks


def default_entity_resolver(repo: Repository) -> EntityResolver:
    """Resolver that maps a report ``(scope, oid)`` to an existing entity_id.

    Returns None for unknown entities and for the ``site`` scope (no site entity
    type in the schema): backfill inserts history for inventory the sync job has
    already discovered, and never invents devices of its own.
    """

    def resolve(scope: str, oid: str) -> Optional[int]:
        etype = SCOPE_ENTITY.get(scope)
        if etype is None or not oid:
            return None
        row = repo.find_entity(etype, oid)
        return None if row is None else int(row["entity_id"])

    return resolve


def job_name(interval: str, scope: str) -> str:
    """poll_runs job identity for a report tier + scope."""
    return f"report.{interval}.{scope}"


class Backfiller:
    """Pulls ``stat/report`` gap windows into the store (section 5.3)."""

    def __init__(
        self,
        endpoints: Endpoints,
        repo: Repository,
        *,
        resolver: Optional[EntityResolver] = None,
        scopes: tuple[str, ...] = ("ap", "user", "gw", "site"),
        fivemin_retention_s: int = DEFAULT_FIVEMIN_RETENTION_S,
        hourly_retention_s: int = DEFAULT_HOURLY_RETENTION_S,
        chunk_seconds: Optional[Mapping[str, int]] = None,
        now_fn: Callable[[], int] = None,  # type: ignore[assignment]
    ) -> None:
        self._ep = endpoints
        self._repo = repo
        self._resolve = resolver or default_entity_resolver(repo)
        self._scopes = scopes
        self._fivemin_retention_s = fivemin_retention_s
        self._hourly_retention_s = hourly_retention_s
        self._chunk_s = dict(DEFAULT_CHUNK_SECONDS)
        if chunk_seconds:
            self._chunk_s.update(chunk_seconds)
        if now_fn is None:
            import time

            now_fn = lambda: int(time.time())  # noqa: E731
        self._now_fn = now_fn

    async def run(
        self,
        last_ts_by_scope: Mapping[str, Optional[int]],
        *,
        now: Optional[int] = None,
    ) -> BackfillResult:
        """Backfill every configured scope from its last stored sample ts.

        ``last_ts_by_scope`` maps a report scope (``ap``/``user``/``gw``/
        ``site``) to the max stored sample ts for that scope in epoch seconds,
        or ``None`` for "no data yet" (pull the full retained history). A scope
        absent from the map is treated as ``None``.
        """
        now = self._now_fn() if now is None else now
        result = BackfillResult()
        for scope in self._scopes:
            last_ts = last_ts_by_scope.get(scope)
            result.scopes[scope] = await self._backfill_scope(scope, last_ts, now)
        return result

    async def _backfill_scope(self, scope: str, last_ts: Optional[int], now: int) -> ScopeResult:
        res = ScopeResult(scope=scope)
        plan = plan_report_windows(
            last_ts,
            now,
            fivemin_retention_s=self._fivemin_retention_s,
            hourly_retention_s=self._hourly_retention_s,
        )
        attrs = [attr for attr, _metric, _kind in REPORT_METRICS[scope]]
        for interval, window in plan.items():
            if window is None:
                continue
            lo, hi = window
            for c_lo, c_hi in chunk_window(lo, hi, self._chunk_s[interval]):
                res.windows += 1
                try:
                    await self._fetch_chunk(interval, scope, c_lo, c_hi, attrs, res)
                except Exception as exc:  # noqa: BLE001 - firewall per chunk
                    res.errors += 1
                    self._repo.record_poll_run(
                        job=job_name(interval, scope),
                        ok=False,
                        ts=c_hi,
                        error=f"{type(exc).__name__}: {exc}"[:200],
                        source="backfill",
                    )
                    logger.warning(
                        "backfill %s.%s [%d,%d) failed: %s",
                        interval,
                        scope,
                        c_lo,
                        c_hi,
                        exc,
                    )
        return res

    async def _fetch_chunk(
        self,
        interval: str,
        scope: str,
        start_ts: int,
        end_ts: int,
        attrs: list[str],
        res: ScopeResult,
    ) -> None:
        rows = await self._ep.stat_report(
            interval,
            scope,
            start_ms=start_ts * 1000,
            end_ms=end_ts * 1000,
            attrs=attrs,
        )
        readings: list[SampleReading] = []
        bucket_ts: set[int] = set()
        for row in rows:
            data = row.model_dump()
            time_ms = data.get("time")
            if time_ms is None:
                continue
            ts = int(time_ms) // 1000
            if ts < start_ts or ts >= end_ts:
                continue  # defensive: controller sometimes pads the range
            oid = data.get("oid") or data.get("o")
            entity_id = self._resolve(scope, oid if oid is None else str(oid))
            if entity_id is None:
                res.skipped_unresolved += 1
                continue
            for attr, metric, _kind in REPORT_METRICS[scope]:
                value = data.get(attr)
                if value is None:
                    continue
                readings.append(
                    SampleReading(
                        entity_id=entity_id,
                        metric=metric,
                        ts=ts,
                        value=float(value),
                        # Store verbatim: report values are pre-aggregated
                        # bucket totals, not cumulative counters (module
                        # docstring). The metric name still drives counter-aware
                        # rollup aggregation through the registry.
                        kind=MetricKind.GAUGE,
                    )
                )
            bucket_ts.add(ts)

        if readings:
            res.rows_inserted += self._repo.record_samples(readings)
        # One backfill poll_run per distinct bucket ts, so coverage_breakdown()
        # can report exactly which intervals were reconstructed from history
        # rather than collected live (section 4: gaps must be queryable).
        for ts in sorted(bucket_ts):
            self._repo.record_poll_run(
                job=job_name(interval, scope), ok=True, ts=ts, source="backfill"
            )
        res.buckets += len(bucket_ts)


__all__ = [
    "FIVEMIN",
    "HOURLY",
    "INTERVAL_SECONDS",
    "SCOPE_ENTITY",
    "REPORT_METRICS",
    "DEFAULT_FIVEMIN_RETENTION_S",
    "DEFAULT_HOURLY_RETENTION_S",
    "DEFAULT_CHUNK_SECONDS",
    "EntityResolver",
    "BackfillWindow",
    "ScopeResult",
    "BackfillResult",
    "plan_report_windows",
    "chunk_window",
    "default_entity_resolver",
    "job_name",
    "Backfiller",
]
