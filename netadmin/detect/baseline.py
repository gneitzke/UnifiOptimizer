"""Per-series baselines: EWMA mean/variance + rolling P05/P50/P95.

This is the ``netadmin.detect.baseline`` seam every detector reads through the
:class:`DetectorContext`. It turns the raw time-series in the store into the two
things a σ-from-baseline classifier needs (ARCHITECTURE.md section 6, "Baselines"):

- an **EWMA mean and variance** per series, giving a slowly-adapting centre and
  spread a detector compares "now" against (``value`` outside ``mean ± kσ``);
- **rolling P05/P50/P95** per series, giving robust, distribution-shape-aware
  bands that do not assume normality (airtime and byte-rate distributions are
  heavily skewed, so a σ-band alone would cry wolf).

Both are persisted in the ``baselines`` table (``series_id, bucket, stat,
value, updated_ts``) so the daemon can restart without relearning, and
:meth:`Baselines.update_from_recent` advances them incrementally from a
per-series watermark on the 5-minute scheduler cadence.

Hour-of-day buckets
-------------------
Some metrics are diurnal -- client counts, airtime utilisation, and throughput
byte-rates all rise during the day and fall at night -- so comparing a 2 pm
reading against a 24 h aggregate baseline is wrong: 2 pm is *supposed* to be
busier than the daily mean. For those metrics we additionally keep 24 hour-of-day
buckets (``'h00'``..``'h23'``), and a detector asks for the bucket matching the
sample's hour. Everything else (RSSI above all -- 3 am RSSI should equal 3 pm
RSSI) uses the single ``'all'`` bucket. ``'all'`` is *always* maintained, for
every series, as the fallback while an hour bucket is still cold.

The diurnal classification lives here, not in ``netadmin.store.metrics`` (which
only records gauge-vs-counter). ``store.metrics`` is owned by the store layer and
carries no seasonality flag; seasonality is a *detection* concern, so the
authoritative diurnal set is :data:`DIURNAL_METRICS` below. If the store registry
ever grows a diurnal flag, this set becomes the fallback for it.

Quantile method: windowed empirical, not streaming P²
----------------------------------------------------
P05/P50/P95 are recomputed each cycle as *exact empirical percentiles over a
bounded recent window of stored raw samples*, rather than maintained by a
streaming estimator (Jain & Chlamtac P²). The reasons:

1. The store already retains raw samples (30 days) and hour/day rollups beyond
   that. A streaming estimator would duplicate that history as estimator state.
2. The ``baselines`` schema persists a single ``value`` per ``(series, bucket,
   stat)``. P² needs five marker heights *and* five marker positions per
   quantile; persisting that faithfully would abuse the ``stat`` column with a
   dozen synthetic rows per quantile. A single exact percentile fits the schema.
3. Exact percentiles are explainable -- an admin can verify P95 against the data
   -- which is the whole "runs anywhere, no black boxes" ethos of the rebuild.
4. Cost is bounded: quantiles are recomputed only for series/buckets that got
   new samples this cycle, over a capped lookback (:attr:`quantile_lookback_s`)
   and a hard ``LIMIT`` (:attr:`quantile_max_samples`), so a 5-minute job stays
   cheap even at ~2 000 series.

The EWMA (mean/variance) *is* incremental and streaming -- it folds each new
sample once, in order, from the watermark -- because that is O(1) state that fits
the schema exactly and is the primary band centre.

Backfill awareness
------------------
On startup the collector backfills gaps from controller history
(``poll_runs.source='backfill'``); those rows are coarser than live 60 s
collection and must not skew a baseline's *centre*. The ``samples`` table does
not tag rows by source, and there is no per-series -> job map exposed, so the
gating is done at the granularity poll accounting actually provides: a whole
*hour* that has no successful **live** ``poll_run`` (only backfill, or nothing)
was a collector outage reconstructed from controller history. Samples in such an
hour are skipped for the **EWMA fold** (the σ-band centre), while the watermark
still advances past them so they are never reprocessed and a gap never stalls the
baseline. When the window contains no live ``poll_run`` at all -- e.g. a unit test
that inserts samples without poll accounting -- the gating *fails open* and every
sample is folded, so baselines still build without a wired collector.

Rolling quantiles are computed over whatever raw the store retains (including any
backfilled rows): they are robust order statistics, a handful of coarse gap-hours
barely move a P95, and excluding them per-hour in SQL is not worth the cost. The
sensitive statistic -- the mean/variance centre -- is the one held strictly to
live samples.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid a hard import cycle; Repository is only a type here
    from netadmin.store.repository import Repository

__all__ = [
    "Band",
    "Baselines",
    "DIURNAL_METRICS",
    "hour_label",
    "DEFAULT_ALPHA",
    "DEFAULT_MIN_SAMPLES",
]

DAY_SECONDS = 86_400
HOUR_SECONDS = 3_600

# EWMA smoothing factor. Memory ~ 1/alpha samples; at the 60 s device/client
# cadence that is ~24 h (1 440 one-minute samples) for alpha = 2/(1440+1). Chosen
# so a day of behaviour informs the centre while a step change still fully
# propagates within a few days. Configurable per Baselines instance.
DEFAULT_ALPHA = 2.0 / (DAY_SECONDS / 60.0 + 1.0)  # ~= 0.001388

# Below this many folded samples a bucket's band is *not fabricated*: band()
# returns None so a detector treats it as "not enough history yet" (UNKNOWN),
# never as a real baseline. 30 is the conventional small-sample floor.
DEFAULT_MIN_SAMPLES = 30

# Metrics whose distribution swings with time of day, so they earn 24 hour-of-day
# buckets on top of 'all'. Client counts, airtime channel utilisation, and
# throughput byte-rates are diurnal; RSSI, noise, temperatures, error counters,
# and link attributes are not (their baseline should be time-of-day invariant).
DIURNAL_METRICS: frozenset[str] = frozenset(
    {
        # client counts (stat/device radio_table_stats, stat/report *.num_sta)
        "num_sta",
        "user_num_sta",
        "guest_num_sta",
        # airtime channel utilisation (radio_table_stats)
        "cu_total",
        "cu_self_rx",
        "cu_self_tx",
        # throughput byte-rates (port_table + stat/report ap/site/gw byte totals)
        "rx_bytes",
        "tx_bytes",
        "bytes",
        "wan-rx_bytes",
        "wan-tx_bytes",
        "wan_xput_up",
        "wan_xput_down",
    }
)

# baselines.stat values this module owns. The five band stats plus the folded
# sample count 'n'. The per-series watermark is stored out of band under a
# reserved bucket so it never collides with a real hour or 'all' band.
_STAT_MEAN = "ewma_mean"
_STAT_VAR = "ewma_var"
_STAT_P05 = "p05"
_STAT_P50 = "p50"
_STAT_P95 = "p95"
_STAT_N = "n"
_BAND_STATS = (_STAT_MEAN, _STAT_VAR, _STAT_P05, _STAT_P50, _STAT_P95, _STAT_N)

_META_BUCKET = "_meta"
_STAT_WATERMARK = "watermark"

_ALL = "all"


def hour_label(ts: int) -> str:
    """Hour-of-day bucket label (``'h00'``..``'h23'``) for a UTC epoch second."""
    hour = (ts % DAY_SECONDS) // HOUR_SECONDS
    return f"h{hour:02d}"


@dataclass
class Band:
    """A series' learned baseline for one bucket.

    ``mean``/``var`` are the EWMA centre and spread (``var`` is the exponentially
    weighted variance; ``sqrt(var)`` is the σ a detector bands on). ``p05``/
    ``p50``/``p95`` are rolling empirical percentiles over recent raw samples.
    ``n`` is the count of samples folded into the EWMA (the min-sample gate).
    ``updated_ts`` is when the band was last written.
    """

    mean: float
    var: float
    p05: float
    p50: float
    p95: float
    n: int
    updated_ts: int

    @property
    def std(self) -> float:
        """Standard deviation (``sqrt(var)``), clamped to non-negative."""
        return self.var**0.5 if self.var > 0 else 0.0


def _percentile(values_sorted: list[float], q: float) -> float:
    """Linear-interpolated percentile (numpy 'linear'/type-7) of sorted data.

    ``values_sorted`` must be ascending and non-empty; ``q`` in ``[0, 1]``.
    """
    n = len(values_sorted)
    if n == 1:
        return values_sorted[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


class Baselines:
    """EWMA + rolling-quantile baselines over the store, per the pinned seam.

    Construct via :meth:`for_repository`. :meth:`band` is the read path detectors
    use; :meth:`update_from_recent` is the incremental write path the 5-minute
    scheduler job calls.
    """

    def __init__(
        self,
        repo: "Repository",
        *,
        alpha: float = DEFAULT_ALPHA,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        quantile_lookback_s: int = 14 * DAY_SECONDS,
        quantile_max_samples: int = 2_000,
        backfill_aware: bool = True,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1), got {alpha}")
        self.repo = repo
        self.alpha = alpha
        self.min_samples = min_samples
        self.quantile_lookback_s = quantile_lookback_s
        self.quantile_max_samples = quantile_max_samples
        self.backfill_aware = backfill_aware
        # Read-only SQL for series enumeration and windowed reads. The repository
        # exposes its connection for "advanced callers"; writes still go through
        # the repository's upsert_baseline so transaction discipline is honoured.
        self._conn: sqlite3.Connection = repo.connection

    @classmethod
    def for_repository(cls, repo: "Repository") -> "Baselines":
        """Build a Baselines with default parameters bound to ``repo``."""
        return cls(repo)

    # ------------------------------------------------------------------ #
    # Read path
    # ------------------------------------------------------------------ #

    def band(self, series_id: int, *, bucket: Optional[str] = None) -> Optional[Band]:
        """Return the baseline :class:`Band` for a series/bucket, or None.

        ``bucket`` defaults to ``'all'`` (the always-maintained aggregate). Pass
        ``'h00'``..``'h23'`` for a diurnal series' hour-of-day band. Returns None
        -- never a fabricated value -- when the bucket has no baseline yet or has
        fewer than ``min_samples`` folded samples (cold start).
        """
        bucket = bucket or _ALL
        rows = self.repo.get_baselines(series_id, bucket)
        stats: dict[str, float] = {}
        updated_ts = 0
        for row in rows:
            stats[str(row["stat"])] = float(row["value"])
            updated_ts = max(updated_ts, int(row["updated_ts"]))

        # Need the full set of band stats; a partially-written bucket is not a band.
        if any(stat not in stats for stat in _BAND_STATS):
            return None
        n = int(stats[_STAT_N])
        if n < self.min_samples:
            return None
        return Band(
            mean=stats[_STAT_MEAN],
            var=stats[_STAT_VAR],
            p05=stats[_STAT_P05],
            p50=stats[_STAT_P50],
            p95=stats[_STAT_P95],
            n=n,
            updated_ts=updated_ts,
        )

    # ------------------------------------------------------------------ #
    # Write path (incremental)
    # ------------------------------------------------------------------ #

    def update_from_recent(self, now_ts: int) -> int:
        """Fold samples added since each series' watermark into its baselines.

        Cheap enough for a 5-minute scheduler job: it processes only series that
        have a sample newer than their persisted watermark, folds those samples
        into the EWMA mean/variance (skipping backfill-only hours), recomputes the
        rolling quantiles for the touched buckets, and advances the watermark.
        Gap-tolerant: a missed hour advances the watermark without corrupting
        state; backfill-aware per the module docstring. Returns the number of
        series whose baselines were updated.
        """
        live_hours = self._live_hours(now_ts) if self.backfill_aware else None
        candidates = self._series_needing_update(now_ts)

        updated = 0
        # One write transaction for the whole cycle: every upsert_baseline rides
        # this single BEGIN IMMEDIATE instead of committing on its own.
        with self.repo.transaction():
            for series_id, metric, watermark in candidates:
                if self._process_series(series_id, metric, watermark, now_ts, live_hours):
                    updated += 1
        return updated

    def _series_needing_update(self, now_ts: int) -> list[tuple[int, str, int]]:
        """(series_id, metric, watermark) for series with a fresh sample <= now.

        One aggregate query over ``samples`` gives the newest ts per series; a
        series is a candidate only when that exceeds its stored watermark. The
        watermark is read per series from the reserved ``_meta`` bucket.
        """
        rows = self._conn.execute(
            "SELECT se.series_id AS sid, se.metric AS metric, MAX(s.ts) AS mx "
            "FROM series se JOIN samples s ON s.series_id = se.series_id "
            "WHERE s.ts <= ? "
            "GROUP BY se.series_id, se.metric",
            (now_ts,),
        ).fetchall()
        out: list[tuple[int, str, int]] = []
        for row in rows:
            sid = int(row["sid"])
            mx = int(row["mx"])
            watermark = self._watermark(sid)
            if mx > watermark:
                out.append((sid, str(row["metric"]), watermark))
        return out

    def _watermark(self, series_id: int) -> int:
        value = self.repo.get_baseline(series_id, _META_BUCKET, _STAT_WATERMARK)
        return 0 if value is None else int(value)

    def _live_hours(self, now_ts: int) -> Optional[set[int]]:
        """Set of hour-bucket starts that had a successful live poll, or None.

        None signals "no live poll accounting in scope" -> gating fails open (fold
        every sample). Otherwise a sample whose hour start is absent from the set
        was a collector outage (backfill-only) and is skipped for the EWMA fold.
        The lookback matches the quantile window so a long backfill on a fresh
        install is still classified correctly.
        """
        start = now_ts - self.quantile_lookback_s
        rows = self._conn.execute(
            "SELECT DISTINCT ts FROM poll_runs "
            "WHERE ok = 1 AND source = 'live' AND ts >= ? AND ts <= ?",
            (start, now_ts),
        ).fetchall()
        if not rows:
            return None  # fail open: nothing to gate against
        return {int(r["ts"]) - (int(r["ts"]) % HOUR_SECONDS) for r in rows}

    def _process_series(
        self,
        series_id: int,
        metric: str,
        watermark: int,
        now_ts: int,
        live_hours: Optional[set[int]],
    ) -> bool:
        """Fold one series' new samples; recompute its touched buckets. -> updated?"""
        new_rows = self._conn.execute(
            "SELECT ts, value FROM samples "
            "WHERE series_id = ? AND ts > ? AND ts <= ? ORDER BY ts",
            (series_id, watermark, now_ts),
        ).fetchall()
        if not new_rows:
            return False

        diurnal = metric in DIURNAL_METRICS
        # Buckets that received at least one folded sample this cycle -> their
        # quantiles need recomputing. 'all' is folded for every series.
        touched: set[str] = set()
        max_ts = watermark

        for row in new_rows:
            ts = int(row["ts"])
            value = float(row["value"])
            max_ts = max(max_ts, ts)

            # Backfill gating on the EWMA fold only (see module docstring).
            if live_hours is not None:
                hour_start = ts - (ts % HOUR_SECONDS)
                if hour_start not in live_hours:
                    continue

            self._fold(series_id, _ALL, value, ts)
            touched.add(_ALL)
            if diurnal:
                hb = hour_label(ts)
                self._fold(series_id, hb, value, ts)
                touched.add(hb)

        for bucket in touched:
            self._recompute_quantiles(series_id, bucket, diurnal, now_ts)

        # Advance the watermark past everything examined this cycle, even samples
        # skipped as backfill-only, so a gap never stalls and nothing reprocesses.
        self.repo.upsert_baseline(
            series_id, _META_BUCKET, _STAT_WATERMARK, float(max_ts), ts=now_ts
        )
        return bool(touched)

    def _fold(self, series_id: int, bucket: str, value: float, ts: int) -> None:
        """Fold one sample into a bucket's EWMA mean/variance and count.

        Recurrence (Finch, incremental weighted mean/variance): the first sample
        seeds ``mean=value, var=0, n=1``; thereafter ``diff = x - mean``,
        ``incr = alpha*diff``, ``mean += incr``, ``var = (1-alpha)*(var +
        diff*incr)``, ``n += 1``.
        """
        n_prev = self.repo.get_baseline(series_id, bucket, _STAT_N)
        if n_prev is None:
            mean, var, n = value, 0.0, 1
        else:
            mean = self.repo.get_baseline(series_id, bucket, _STAT_MEAN) or 0.0
            var = self.repo.get_baseline(series_id, bucket, _STAT_VAR) or 0.0
            diff = value - mean
            incr = self.alpha * diff
            mean = mean + incr
            var = (1.0 - self.alpha) * (var + diff * incr)
            n = int(n_prev) + 1
        self.repo.upsert_baseline(series_id, bucket, _STAT_MEAN, mean, ts=ts)
        self.repo.upsert_baseline(series_id, bucket, _STAT_VAR, var, ts=ts)
        self.repo.upsert_baseline(series_id, bucket, _STAT_N, float(n), ts=ts)

    def _recompute_quantiles(self, series_id: int, bucket: str, diurnal: bool, now_ts: int) -> None:
        """Recompute P05/P50/P95 over a bounded recent window of raw samples.

        For ``'all'`` the window is the last :attr:`quantile_lookback_s` seconds
        (capped at :attr:`quantile_max_samples` newest rows). For an hour bucket
        the same window is additionally filtered to samples whose hour-of-day
        matches, so an ``h14`` band reflects 2 pm behaviour across recent days.
        """
        start = now_ts - self.quantile_lookback_s
        if bucket == _ALL:
            rows = self._conn.execute(
                "SELECT value FROM samples "
                "WHERE series_id = ? AND ts >= ? AND ts <= ? "
                "ORDER BY ts DESC LIMIT ?",
                (series_id, start, now_ts, self.quantile_max_samples),
            ).fetchall()
        else:
            hour = int(bucket[1:])
            rows = self._conn.execute(
                "SELECT value FROM samples "
                "WHERE series_id = ? AND ts >= ? AND ts <= ? "
                "AND ((ts % ?) / ?) = ? "
                "ORDER BY ts DESC LIMIT ?",
                (
                    series_id,
                    start,
                    now_ts,
                    DAY_SECONDS,
                    HOUR_SECONDS,
                    hour,
                    self.quantile_max_samples,
                ),
            ).fetchall()
        if not rows:
            return
        values = sorted(float(r["value"]) for r in rows)
        self.repo.upsert_baseline(
            series_id, bucket, _STAT_P05, _percentile(values, 0.05), ts=now_ts
        )
        self.repo.upsert_baseline(
            series_id, bucket, _STAT_P50, _percentile(values, 0.50), ts=now_ts
        )
        self.repo.upsert_baseline(
            series_id, bucket, _STAT_P95, _percentile(values, 0.95), ts=now_ts
        )
