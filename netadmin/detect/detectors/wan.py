"""WAN-edge detectors (section 6, ``wan.*``), tuned for a Starlink uplink.

The binding site is a **Starlink dish behind a third-party router**: there is no
UniFi gateway, so the controller reports no WAN health, no WAN throughput, and no
``EVT_GW_WANTransition`` events. Our *only* uplink signals are the active probes —
ICMP ``gw_rtt_ms`` and DNS ``dns_latency_ms`` / ``dns_anchor_latency_ms`` — plus
the probe ``poll_runs`` accounting (a failed probe is a lost packet).

What "normal Starlink" looks like (and why fixed thresholds lie)
---------------------------------------------------------------
Starlink is a LEO constellation with per-satellite handoffs roughly every 15 s.
Healthy behaviour therefore includes, *by design*:

* **Latency jitter with brief spikes.** A calm base of ~30-45 ms with short
  excursions to 80-120 ms at each ~15 s handoff. A single-sample threshold ("RTT
  > 100 ms") fires on every handoff — useless. The honest statistic is a
  **rolling-window p50**: brief handoff spikes are a small minority of samples and
  barely move a 15-minute median, while a genuine regime change moves it wholesale.
* **Periodic obstruction dips.** Trees/mounts clip a few satellites' passes,
  producing short bursts of loss and latency that recur but are not a fault. Only
  a *sustained* dip (many consecutive windows) is actionable.
* **Wide throughput variance.** Plan rate is nominal, not provisioned; instantaneous
  throughput swings enormously with sky conditions and load. A fixed
  ``wan_plan_*_mbps`` "near plan" gate is therefore meaningless here — those config
  keys default to ``None`` ("auto" = disabled) and exist only as optional manual
  overrides for a non-Starlink site.

The discipline every detector below follows
-------------------------------------------
1. **Rolling-window aggregates, never single samples.** p50/p95 over 15-minute
   windows are the unit of judgement.
2. **Trend vs a long rolling baseline, not an absolute line.** The current
   window's aggregate is compared to the series' own 7-day rolling baseline
   (:class:`~netadmin.detect.baseline.Band`), so a jittery-but-normal link is
   judged against itself.
3. **Sustained multi-window deviation.** A finding requires the deviation to hold
   across several consecutive windows; one bad window (a handoff burst, a prober
   hiccup) never fires.

Detectors
---------
* :class:`IspDegradedDetector` (``wan.isp_degraded``) — sustained elevation of the
  windowed probe-latency p50 vs its 7-day baseline, and/or a sustained rise in the
  per-window failed-probe fraction vs the baseline fraction. P2 on latency, P1 on
  sustained heavy loss.
* :class:`LatencyShiftDetector` (``wan.latency_shift``) — CUSUM change-point
  detection on the windowed latency p50: reports "latency regime changed at <time>"
  with before/after numbers. This is the Starlink-actionable equivalent of the
  bufferbloat detector that a gateway-less site cannot run.
* :class:`DnsSlowDetector` (``wan.dns_slow``) — gateway-resolver DNS timing vs a
  public anchor, localising slow-local vs slow-upstream. Probe-only, so it works on
  the Starlink site.
* :class:`BufferbloatDetector` (``wan.bufferbloat``) — honestly ``UNKNOWN`` on a
  gateway-less site: bufferbloat needs a *load* signal (throughput near plan) to
  separate "latency under load" from ordinary jitter, and Starlink exposes none.
  :class:`LatencyShiftDetector` is the actionable substitute.
* :class:`WanFlappingDetector` (``wan.flapping``) — ``EVT_GW_WANTransition`` churn.
  No UniFi gateway emits these events, so it no-ops here.

Tuning
------
All thresholds live on :class:`StarlinkWanProfile` (the shipped defaults) and are
overridable per detector through ``settings.thresholds[<detector_key>][<name>]``
(the standard :meth:`DetectorContext.threshold` seam). The defaults are the
Starlink profile; a fibre/DSL site can override them wholesale.

"No-op gracefully" means: emit no findings, log the reason once (per detector
instance), and never spuriously clear — a detector with nothing to measure returns
``[]`` only when it has genuinely looked and found the world quiet, and
:data:`UNKNOWN` when it could not look.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from netadmin.detect.engine import COVERAGE_MIN, UNKNOWN, EvalResult
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.logging import get_logger

_log = get_logger("detect.wan")

KEY_ISP_DEGRADED = "wan.isp_degraded"
KEY_LATENCY_SHIFT = "wan.latency_shift"
KEY_DNS_SLOW = "wan.dns_slow"
KEY_BUFFERBLOAT = "wan.bufferbloat"
KEY_FLAPPING = "wan.flapping"

# Probe metric names (must match netadmin.ingest.probes).
METRIC_DNS_LATENCY = "dns_latency_ms"
METRIC_DNS_ANCHOR = "dns_anchor_latency_ms"
METRIC_GW_RTT = "gw_rtt_ms"
# UniFi-gateway health metrics (netadmin.ingest.mapping); absent on the Starlink
# site, kept so a non-Starlink deployment with a UniFi gateway still works.
METRIC_WAN_LATENCY = "wan_latency"
METRIC_WWW_LATENCY = "www_latency"
METRIC_WAN_DROPS = "wan_drops"
METRIC_WAN_XPUT_DOWN = "wan_xput_down"
METRIC_WAN_XPUT_UP = "wan_xput_up"

# Latency sources in preference order, and the poll_runs job that measures each
# one's coverage/loss. gw_rtt (the probe) is first — it is the only source the
# Starlink site has; the UniFi-gateway metrics are fallbacks for other sites.
_LATENCY_SOURCES: tuple[tuple[str, str], ...] = (
    (METRIC_GW_RTT, "probe.gw_rtt"),
    (METRIC_WAN_LATENCY, "fast_health"),
    (METRIC_WWW_LATENCY, "fast_health"),
)

EVT_WAN_TRANSITION = "EVT_GW_WANTransition"


# ====================================================================== #
# Starlink tuning profile (the shipped defaults; every field overridable)
# ====================================================================== #
@dataclass(frozen=True)
class StarlinkWanProfile:
    """Default WAN thresholds tuned for a Starlink uplink (module docstring).

    Every field is read by a detector through ``ctx.threshold(key, name, default)``
    where ``default`` is the field below, so a deployment overrides any of them via
    ``settings.thresholds[<detector_key>][<name>]`` without code changes. The
    defaults encode the Starlink profile: a jittery LEO link with ~15 s handoff
    spikes, periodic obstruction dips, and wide throughput variance.
    """

    # --- rolling-window shape (shared) ---
    window_s: int = 900  # one 15-minute analysis window
    probe_interval_s: int = 60  # nominal probe cadence, for coverage expectation

    # --- wan.isp_degraded ---
    isp_n_windows: int = 4  # windows of history inspected (~1 h)
    isp_sustained_windows: int = 3  # >= this many deviating windows => sustained
    isp_min_window_samples: int = 5  # samples needed to trust a window's p50
    # latency: current window p50 must clear BOTH the ratio-vs-baseline AND the
    # absolute floor (Starlink is fine below the floor regardless of ratio).
    isp_latency_ratio: float = 1.8  # window p50 vs 7-day baseline p50
    isp_min_latency_ms: float = 60.0  # absolute floor; below this is never degraded
    # Absolute "still-degraded" hold floor. The ratio test judges the window against
    # the series' own rolling baseline, so once a genuine elevated regime persists
    # past the baseline lookback the baseline climbs to the new level, the ratio
    # stops tripping, and a still-bad WAN would silently auto-resolve. A window whose
    # p50 is at/above this absolute floor counts as degraded regardless of baseline,
    # so a persistent shift does not become its own "normal". Set to a level that is
    # unambiguously bad for the link (Starlink healthy p50 ~30-45 ms, handoff spikes
    # to ~120 ms are a sample minority that barely move a 15-min median). This is the
    # bounded in-detector guard; a frozen pre-incident baseline is the fuller fix.
    isp_latency_hold_ms: float = 100.0
    # loss: per-window failed-probe fraction vs the 7-day baseline fraction.
    isp_loss_ratio: float = 3.0  # window fraction vs baseline fraction
    isp_min_loss_fraction: float = 0.03  # absolute floor to consider loss elevated
    isp_loss_p1_fraction: float = 0.05  # sustained window loss above this => P1
    # Per-window sample gates that keep sparse windows from quantizing. With a 60 s
    # probe cadence a 900 s window holds ~15 probes, so ONE dropped probe is already
    # 6.7% loss — enough to clear the fraction floors on its own. Require a minimum
    # number of probe rows before a window's loss fraction is trusted, and a minimum
    # absolute count of *lost* probes (> 1, above the single-packet quantum) before
    # the window counts as loss-deviating. Three isolated single-packet drops across
    # three windows (normal LEO handoff/obstruction behaviour) then never fire.
    isp_min_window_polls: int = 8  # probe rows needed to judge a window's loss
    isp_min_lost_probes: int = 2  # lost probes needed for a window to count as loss
    isp_baseline_loss_window_s: int = 7 * 86_400
    isp_min_baseline_polls: int = 20  # fewer baseline polls => assume 0 baseline loss

    # --- wan.latency_shift (CUSUM) ---
    shift_horizon_windows: int = 24  # windowed-p50 points analysed (~6 h)
    shift_min_window_samples: int = 5  # samples needed for a window p50
    shift_ref_windows: int = 4  # earliest windows used as the "before" reference
    shift_cusum_slack_ms: float = 12.0  # k: ~half the minimum detectable shift
    shift_cusum_threshold_ms: float = 36.0  # h: accumulate before declaring a shift
    shift_min_delta_ms: float = 15.0  # after-minus-before must exceed this to report

    # --- wan.dns_slow ---
    dns_warn_ms: float = 150.0
    dns_critical_ms: float = 1000.0

    # --- wan.bufferbloat ---
    bufferbloat_ms: float = 200.0  # loaded p95 minus idle floor
    near_plan_fraction: float = 0.8  # load gate; needs wan_plan_* (auto/None => off)

    # --- wan.flapping ---
    flapping_window_s: int = 86_400
    flapping_transitions_min: int = 3


_PROFILE = StarlinkWanProfile()


# --------------------------------------------------------------------------- #
# small numeric + read helpers
# --------------------------------------------------------------------------- #
def _percentile(values_sorted: list[float], q: float) -> float:
    n = len(values_sorted)
    if n == 1:
        return values_sorted[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return values_sorted[lo] + (values_sorted[hi] - values_sorted[lo]) * frac


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return _percentile(sorted(values), 0.5)


def _series_values(ctx: Any, entity_id: int, metric: str, start: int, end: int) -> list[float]:
    """Raw sample values for ``(entity_id, metric)`` over ``[start, end)``."""
    sid = ctx.repo.get_series(entity_id, metric)
    if sid is None:
        return []
    out: list[float] = []
    for row in ctx.repo.read_raw(sid, start, end):
        val = row.get("value")
        if val is not None:
            out.append(float(val))
    return out


def _window_values(ctx: Any, entity_id: int, metric: str, seconds: int) -> list[float]:
    return _series_values(ctx, entity_id, metric, ctx.now_ts - int(seconds), ctx.now_ts)


def _gateways(ctx: Any) -> list[Entity]:
    return [g for g in ctx.entities(EntityType.GATEWAY) if g.entity_id is not None]


def _poll_counts(polls: list[Any], start: int, end: int) -> tuple[int, int]:
    """(total, failed) poll_runs rows whose ts falls in ``[start, end)``."""
    total = 0
    failed = 0
    for row in polls:
        ts = int(_row_val(row, "ts"))
        if start <= ts < end:
            total += 1
            if int(_row_val(row, "ok")) == 0:
                failed += 1
    return total, failed


class _LogOnce:
    """A per-instance "log this no-op once" latch keyed by a reason string."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def __call__(self, log: Any, key: str, msg: str) -> None:
        if key not in self._seen:
            self._seen.add(key)
            log.info(msg)


def _select_latency_source(ctx: Any) -> tuple[Optional[Entity], Optional[str], Optional[str]]:
    """Pick the gateway + latency metric + coverage job to judge WAN latency on.

    Walks :data:`_LATENCY_SOURCES` in preference order (probe ``gw_rtt`` first, the
    only source the Starlink site has) and returns the first gateway that has a
    series for that metric. Returns ``(None, None, None)`` when no gateway exposes
    any latency source — the graceful gateway-less no-op.
    """
    for gw in _gateways(ctx):
        for metric, job in _LATENCY_SOURCES:
            if ctx.repo.get_series(gw.entity_id, metric) is not None:
                return gw, metric, job
    return None, None, None


# ====================================================================== #
# wan.isp_degraded
# ====================================================================== #
class IspDegradedDetector:
    """``wan.isp_degraded`` — sustained WAN latency / loss degradation.

    On the Starlink site the only latency signal is the ICMP ``gw_rtt_ms`` probe.
    Rather than threshold a raw sample (which every ~15 s handoff spike would trip),
    the detector inspects the last :attr:`StarlinkWanProfile.isp_n_windows` 15-minute
    windows and, per window, computes the p50 of the probe latency and the fraction
    of failed probes.

    * **Latency**: a window "deviates" when its p50 clears both the absolute floor
      and ``ratio × baseline_p50`` (the series' own 7-day rolling p50). A finding
      requires deviation across ``isp_sustained_windows`` of the inspected windows,
      so brief handoff spikes — a minority of samples that barely move a 15-minute
      median — never fire.
    * **Loss**: a window's failed-probe fraction (from ``poll_runs``) vs the 7-day
      baseline fraction; sustained across the same window count. Heavy sustained
      loss escalates to P1.

    Verdicts: a genuine coverage gap (the prober itself was not running) is
    ``UNKNOWN``; suspiciously high absolute latency with no baseline to confirm the
    trend is ``UNKNOWN`` (never a false OK); otherwise a :class:`Finding` or ``[]``.
    Failed probes are the loss signal, so the coverage gate counts *all* recorded
    probe rows (ok or failed), not just successes — a lossy link is "we looked",
    not "we could not look".
    """

    key = KEY_ISP_DEGRADED
    scope = EntityType.GATEWAY
    cadence = Cadence.WINDOW

    def __init__(self) -> None:
        self._log_once = _LogOnce()

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", _PROFILE.window_s))
        n_windows = int(ctx.threshold(self.key, "n_windows", _PROFILE.isp_n_windows))
        sustained = int(
            ctx.threshold(self.key, "sustained_windows", _PROFILE.isp_sustained_windows)
        )
        min_samples = int(
            ctx.threshold(self.key, "min_window_samples", _PROFILE.isp_min_window_samples)
        )
        ratio = float(ctx.threshold(self.key, "latency_ratio", _PROFILE.isp_latency_ratio))
        min_latency = float(ctx.threshold(self.key, "min_latency_ms", _PROFILE.isp_min_latency_ms))
        hold_latency = float(
            ctx.threshold(self.key, "latency_hold_ms", _PROFILE.isp_latency_hold_ms)
        )
        loss_ratio = float(ctx.threshold(self.key, "loss_ratio", _PROFILE.isp_loss_ratio))
        min_loss = float(
            ctx.threshold(self.key, "min_loss_fraction", _PROFILE.isp_min_loss_fraction)
        )
        loss_p1 = float(ctx.threshold(self.key, "loss_p1_fraction", _PROFILE.isp_loss_p1_fraction))
        min_window_polls = int(
            ctx.threshold(self.key, "min_window_polls", _PROFILE.isp_min_window_polls)
        )
        min_lost_probes = int(
            ctx.threshold(self.key, "min_lost_probes", _PROFILE.isp_min_lost_probes)
        )
        probe_interval = int(ctx.threshold(self.key, "probe_interval_s", _PROFILE.probe_interval_s))

        gw, metric, job = _select_latency_source(ctx)
        if gw is None:
            self._log_once(_log, "no_source", "wan.isp_degraded: no WAN latency source; no-op")
            return []

        now = ctx.now_ts
        lookback = window_s * n_windows
        polls = ctx.repo.read_poll_runs(job, now - lookback, now)

        # Coverage gate: did the prober actually run? Count ALL recorded rows (ok or
        # failed) — a failed probe is the loss signal, not a gap. UNKNOWN only when
        # the collector itself was down (too few rows vs expected).
        expected = lookback / probe_interval if probe_interval > 0 else 0
        if expected > 0 and len(polls) < COVERAGE_MIN * expected:
            return UNKNOWN

        sid = ctx.repo.get_series(gw.entity_id, metric)
        band = ctx.baselines.band(sid) if sid is not None else None
        baseline_p50 = band.p50 if band is not None else None
        baseline_loss = self._baseline_loss_fraction(ctx, job, now)

        latency_dev = 0  # windows past the ratio-vs-baseline deviation gate
        latency_hold = 0  # windows at/above the absolute still-degraded hold floor
        latency_abs_hi = 0  # windows past the absolute floor (baseline-independent)
        loss_dev = 0  # windows past the loss gate
        worst_window_p50 = 0.0
        worst_loss_fraction = 0.0
        for i in range(n_windows):
            w_end = now - i * window_s
            w_start = w_end - window_s
            vals = _series_values(ctx, gw.entity_id, metric, w_start, w_end)
            if len(vals) >= min_samples:
                p50 = _percentile(sorted(vals), 0.5)
                worst_window_p50 = max(worst_window_p50, p50)
                if p50 >= min_latency:
                    latency_abs_hi += 1
                    if (
                        baseline_p50 is not None
                        and baseline_p50 > 0
                        and p50 >= ratio * baseline_p50
                    ):
                        latency_dev += 1
                # Baseline-independent hold: a window still sitting at/above the
                # absolute "clearly bad" floor keeps the finding alive even after the
                # rolling baseline has drifted up to the degraded level (finding: a
                # persistent shift must not become its own normal and auto-resolve).
                if hold_latency > 0 and p50 >= hold_latency:
                    latency_hold += 1
            total, failed = _poll_counts(polls, w_start, w_end)
            # Only judge loss on a window with enough probe rows, and only count a
            # window whose *absolute* lost-probe count clears the single-packet
            # quantum (> 1). A lone dropped probe in a ~15-probe window is 6.7% loss
            # — normal LEO handoff/obstruction behaviour, not a fault.
            if total >= min_window_polls:
                frac = failed / total
                loss_floor = max(min_loss, loss_ratio * baseline_loss)
                if failed >= min_lost_probes and frac >= loss_floor:
                    loss_dev += 1
                    worst_loss_fraction = max(worst_loss_fraction, frac)

        # A baseline is required to fire on latency at all (a cold start with no
        # reference stays UNKNOWN below). Given one, EITHER a ratio deviation OR a
        # sustained hold above the absolute floor fires — the hold path is what keeps
        # a genuine shift open after its own baseline has drifted up to match it.
        latency_fire = band is not None and (latency_dev >= sustained or latency_hold >= sustained)
        loss_fire = loss_dev >= sustained

        if latency_fire or loss_fire:
            return [
                self._finding(
                    gw,
                    metric,
                    latency_fire=latency_fire,
                    loss_fire=loss_fire,
                    window_p50=worst_window_p50,
                    baseline_p50=baseline_p50,
                    loss_fraction=worst_loss_fraction,
                    baseline_loss=baseline_loss,
                    loss_p1=loss_p1,
                    sustained=sustained,
                )
            ]

        # No baseline to confirm a latency trend, yet latency is high in absolute
        # terms across sustained windows -> we cannot judge -> UNKNOWN, never OK.
        if band is None and latency_abs_hi >= sustained:
            return UNKNOWN
        return []

    def _baseline_loss_fraction(self, ctx: Any, job: str, now: int) -> float:
        override = ctx.threshold(self.key, "baseline_loss_fraction", None)
        if override is not None:
            return float(override)
        window_s = int(
            ctx.threshold(self.key, "baseline_loss_window_s", _PROFILE.isp_baseline_loss_window_s)
        )
        min_polls = int(
            ctx.threshold(self.key, "min_baseline_polls", _PROFILE.isp_min_baseline_polls)
        )
        polls = ctx.repo.read_poll_runs(job, now - window_s, now)
        total = len(polls)
        if total < min_polls:
            return 0.0  # too little history: absolute floor governs, not a ratio
        failed = sum(1 for r in polls if int(_row_val(r, "ok")) == 0)
        return failed / total

    def _finding(
        self,
        gw: Entity,
        metric: str,
        *,
        latency_fire: bool,
        loss_fire: bool,
        window_p50: float,
        baseline_p50: Optional[float],
        loss_fraction: float,
        baseline_loss: float,
        loss_p1: float,
        sustained: int,
    ) -> Finding:
        severity = Severity.P1 if (loss_fire and loss_fraction >= loss_p1) else Severity.P2
        parts = []
        if latency_fire and baseline_p50:
            parts.append(f"latency p50 {window_p50:.0f} ms vs {baseline_p50:.0f} ms baseline")
        if loss_fire:
            parts.append(f"loss {loss_fraction * 100:.1f}%")
        title = "WAN degraded (" + ", ".join(parts) + f", sustained ≥{sustained} windows)"
        return Finding(
            detector_key=self.key,
            entity=gw,
            severity=severity,
            title=title,
            dims={},
            evidence={
                "latency_metric": metric,
                "latency_fired": latency_fire,
                "window_p50_ms": round(window_p50, 1),
                "baseline_p50_ms": None if baseline_p50 is None else round(baseline_p50, 1),
                "ratio": (
                    round(window_p50 / baseline_p50, 2)
                    if (baseline_p50 and baseline_p50 > 0)
                    else None
                ),
                "loss_fired": loss_fire,
                "loss_fraction": round(loss_fraction, 4),
                "baseline_loss_fraction": round(baseline_loss, 4),
                "sustained_windows_required": sustained,
            },
            confounders_checked=[
                "rolling_window_p50_robust_to_handoff_spikes",
                "trend_vs_own_7d_baseline_not_absolute",
                "absolute_hold_floor_prevents_baseline_drift_autoresolve",
                "sustained_multi_window_required",
                "per_window_min_probe_count_and_lost_probe_floor",
                "loss_from_probe_run_accounting_not_gap",
                "starlink_jitter_profile",
            ],
        )


# ====================================================================== #
# wan.latency_shift  (CUSUM regime-change detection)
# ====================================================================== #
class LatencyShiftDetector:
    """``wan.latency_shift`` — a sustained upward shift in the WAN latency regime.

    Bufferbloat cannot be judged on a gateway-less Starlink site (no load signal),
    so this is the actionable substitute: a CUSUM change-point test on the sequence
    of 15-minute windowed latency p50 values. When the cumulative upward deviation
    from the "before" reference crosses the threshold *and* is still elevated at the
    latest window (i.e. the shift persists to now), it reports the change point and
    the before/after numbers — "latency regime changed at <time>".

    Why CUSUM: it is the classic sequential change-detector. It accumulates
    ``max(0, S + (x - mu0 - k))`` per window; the slack ``k`` absorbs Starlink's
    ordinary jitter so only a persistent level change accumulates. A transient
    obstruction dip rises the statistic briefly then decays back to zero once the
    windows return to normal, so requiring the statistic to still exceed the
    threshold at the final window filters those out — the "sustained" rule.

    The "before" level ``mu0`` is the series' 7-day baseline p50 when available,
    else the median of the earliest reference windows. With neither (too little
    history) the detector is ``UNKNOWN`` rather than guessing. P3: a regime change
    is informational/actionable, not an outage.
    """

    key = KEY_LATENCY_SHIFT
    scope = EntityType.GATEWAY
    cadence = Cadence.WINDOW

    def __init__(self) -> None:
        self._log_once = _LogOnce()

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", _PROFILE.window_s))
        horizon = int(ctx.threshold(self.key, "horizon_windows", _PROFILE.shift_horizon_windows))
        min_samples = int(
            ctx.threshold(self.key, "min_window_samples", _PROFILE.shift_min_window_samples)
        )
        ref_windows = int(ctx.threshold(self.key, "ref_windows", _PROFILE.shift_ref_windows))
        k = float(ctx.threshold(self.key, "cusum_slack_ms", _PROFILE.shift_cusum_slack_ms))
        h = float(ctx.threshold(self.key, "cusum_threshold_ms", _PROFILE.shift_cusum_threshold_ms))
        min_delta = float(ctx.threshold(self.key, "min_delta_ms", _PROFILE.shift_min_delta_ms))

        gw, metric, _job = _select_latency_source(ctx)
        if gw is None or ctx.repo.get_series(gw.entity_id, metric) is None:
            self._log_once(_log, "no_source", "wan.latency_shift: no WAN latency source; no-op")
            return []

        now = ctx.now_ts
        # Build the windowed-p50 sequence oldest -> newest over the horizon, keeping
        # only windows with enough samples to trust their median.
        points: list[tuple[int, float]] = []  # (window_start_ts, p50)
        for i in range(horizon):
            w_start = now - (horizon - i) * window_s
            w_end = w_start + window_s
            vals = _series_values(ctx, gw.entity_id, metric, w_start, w_end)
            if len(vals) >= min_samples:
                points.append((w_start, _percentile(sorted(vals), 0.5)))

        if len(points) < ref_windows + 2:
            return UNKNOWN  # a series exists but too sparse to conclude a regime

        sid = ctx.repo.get_series(gw.entity_id, metric)
        band = ctx.baselines.band(sid) if sid is not None else None
        if band is not None and band.p50 > 0:
            mu0 = band.p50
        else:
            mu0 = _median([p for _ts, p in points[:ref_windows]]) or 0.0
        if mu0 <= 0:
            return UNKNOWN

        change_ts, after_p50, active = self._cusum_upward(points, mu0, k, h)
        if not active or after_p50 is None or (after_p50 - mu0) < min_delta:
            return []

        when = datetime.fromtimestamp(change_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return [
            Finding(
                detector_key=self.key,
                entity=gw,
                severity=Severity.P3,
                title=f"WAN latency regime changed at {when} ({mu0:.0f} → {after_p50:.0f} ms p50)",
                dims={},
                evidence={
                    "latency_metric": metric,
                    "change_ts": change_ts,
                    "before_p50_ms": round(mu0, 1),
                    "after_p50_ms": round(after_p50, 1),
                    "delta_ms": round(after_p50 - mu0, 1),
                    "cusum_slack_ms": k,
                    "cusum_threshold_ms": h,
                },
                confounders_checked=[
                    "cusum_slack_absorbs_starlink_jitter",
                    "shift_must_persist_to_latest_window",
                    "windowed_p50_not_single_sample",
                    "reference_from_7d_baseline_or_early_windows",
                ],
            )
        ]

    def _cusum_upward(
        self, points: list[tuple[int, float]], mu0: float, k: float, h: float
    ) -> tuple[int, Optional[float], bool]:
        """One-sided upward CUSUM over the windowed p50 sequence.

        Returns ``(change_ts, after_p50, active)``. ``active`` is True only when the
        statistic still exceeds ``h`` at the final (latest) window — the shift
        persists to now. ``change_ts`` is the start of the window where the current
        run of accumulation began (the last point the statistic was zero), i.e. the
        estimated change time. ``after_p50`` is the median window-p50 from the
        change point to now.
        """
        s = 0.0
        run_start_idx = 0  # index where the current non-zero run began
        for idx, (_ts, x) in enumerate(points):
            increment = x - mu0 - k
            if s <= 0 and increment > 0:
                run_start_idx = idx  # a fresh run starts here
            s = max(0.0, s + increment)
        if s <= h:
            return 0, None, False
        change_ts = points[run_start_idx][0]
        after_vals = [p for _ts, p in points[run_start_idx:]]
        return change_ts, _median(after_vals), True


# ====================================================================== #
# wan.dns_slow
# ====================================================================== #
class DnsSlowDetector:
    """``wan.dns_slow`` — gateway-resolver DNS timing vs a public anchor.

    Reads the active DNS prober's series. Sustained resolver latency over the warn
    (default 150 ms) / critical (default 1 s) lines fires; the public-anchor timing
    then localises blame — a slow gateway resolver while the anchor is fine points
    at the local resolver, while both slow points upstream. Probe-only, so it works
    on the gateway-less Starlink site.

    Uses the window's p50 (not a single sample) so a lone slow lookup — common on
    Starlink at a handoff — does not fire; the coverage gate keeps it honest when
    the prober was down.
    """

    key = KEY_DNS_SLOW
    scope = EntityType.GATEWAY
    cadence = Cadence.WINDOW

    def __init__(self) -> None:
        self._log_once = _LogOnce()

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", _PROFILE.window_s))
        warn_ms = float(ctx.threshold(self.key, "warn_ms", _PROFILE.dns_warn_ms))
        crit_ms = float(ctx.threshold(self.key, "critical_ms", _PROFILE.dns_critical_ms))

        gw = self._gateway_with_dns(ctx)
        if gw is None:
            self._log_once(_log, "no_probe", "wan.dns_slow: no DNS-probe data; no-op")
            return []

        if ctx.coverage(window_s, "probe.dns") < COVERAGE_MIN:
            return UNKNOWN  # too few probe samples to trust a sustained reading

        resolver = _window_values(ctx, gw.entity_id, METRIC_DNS_LATENCY, window_s)
        if not resolver:
            return []
        resolver_p50 = _percentile(sorted(resolver), 0.5)
        if resolver_p50 < warn_ms:
            return []

        anchor = _window_values(ctx, gw.entity_id, METRIC_DNS_ANCHOR, window_s)
        anchor_p50 = _percentile(sorted(anchor), 0.5) if anchor else None
        # If the public anchor is also slow, the fault is upstream, not the local
        # resolver. The anchor comparison is the confounder that splits the two.
        localised = "upstream" if (anchor_p50 is not None and anchor_p50 >= warn_ms) else "local"
        severity = Severity.P2  # ceiling; escalate wording at critical
        confounders = ["anchor_comparison_localises_fault", "probe_coverage_gated"]
        return [
            Finding(
                detector_key=self.key,
                entity=gw,
                severity=severity,
                title=(
                    f"DNS resolution slow ({resolver_p50:.0f} ms, {localised})"
                    + (" — critical" if resolver_p50 >= crit_ms else "")
                ),
                dims={},
                evidence={
                    "resolver_p50_ms": round(resolver_p50, 1),
                    "anchor_p50_ms": None if anchor_p50 is None else round(anchor_p50, 1),
                    "localised": localised,
                    "severity_tier": "critical" if resolver_p50 >= crit_ms else "warn",
                },
                confounders_checked=confounders,
            )
        ]

    def _gateway_with_dns(self, ctx: Any) -> Optional[Entity]:
        for gw in _gateways(ctx):
            if ctx.repo.get_series(gw.entity_id, METRIC_DNS_LATENCY) is not None:
                return gw
        return None


# ====================================================================== #
# wan.bufferbloat
# ====================================================================== #
class BufferbloatDetector:
    """``wan.bufferbloat`` — RTT-under-load spike, gated on near-plan throughput.

    Bufferbloat is latency that only appears when the link is saturated, so a bare
    RTT spike is not enough — it must coincide with WAN throughput near the
    provisioned plan rate. Two reasons this is ``UNKNOWN`` on the Starlink site:

    1. **No throughput series.** Without a UniFi gateway there is no ``wan_xput_*``
       to establish "under load".
    2. **Plan rate is meaningless.** Starlink throughput varies so widely that a
       fixed ``wan_plan_*_mbps`` "near plan" line does not represent saturation.
       Those keys default to ``None`` ("auto" = disabled) and are optional manual
       overrides for a non-Starlink link.

    So on Starlink this stays honestly ``UNKNOWN`` and
    :class:`LatencyShiftDetector` is the actionable latency-regime substitute. On a
    site that *does* expose throughput and a plan rate, it fires on a genuine
    loaded-vs-idle RTT gap. The topology limit is always recorded in evidence.
    """

    key = KEY_BUFFERBLOAT
    scope = EntityType.GATEWAY
    cadence = Cadence.WINDOW

    def __init__(self) -> None:
        self._log_once = _LogOnce()

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", _PROFILE.window_s))
        spike_ms = float(ctx.threshold(self.key, "loaded_minus_idle_ms", _PROFILE.bufferbloat_ms))
        plan_fraction = float(
            ctx.threshold(self.key, "near_plan_fraction", _PROFILE.near_plan_fraction)
        )

        gw = self._gateway_with_rtt(ctx)
        if gw is None:
            self._log_once(_log, "no_probe", "wan.bufferbloat: no RTT-probe data; no-op")
            return []

        if ctx.coverage(window_s, "probe.gw_rtt") < COVERAGE_MIN:
            return UNKNOWN

        # Idle baseline: the RTT series' learned p05 (its quiet floor). Loaded: the
        # window's p95. Their gap is the bloat under load.
        series_id = ctx.repo.get_series(gw.entity_id, METRIC_GW_RTT)
        band = ctx.baselines.band(series_id) if series_id is not None else None
        rtts = _window_values(ctx, gw.entity_id, METRIC_GW_RTT, window_s)
        if band is None or not rtts:
            return UNKNOWN  # no idle reference or no samples -> cannot judge bloat

        loaded = _percentile(sorted(rtts), 0.95)
        idle = band.p05
        delta = loaded - idle
        if delta < spike_ms:
            return []

        # The load gate: is WAN throughput actually near plan? Needs a UniFi
        # gateway's xput series + a configured plan rate. Absent either, the
        # "under load" premise is unproven -> UNKNOWN, with the limit named.
        near_plan = self._near_plan(ctx, gw, window_s, plan_fraction)
        if near_plan is None:
            self._log_once(
                _log,
                "no_throughput",
                "wan.bufferbloat: RTT spike seen but no throughput/plan signal; UNKNOWN",
            )
            return UNKNOWN
        if not near_plan:
            return []  # RTT spike without load is not bufferbloat

        return [
            Finding(
                detector_key=self.key,
                entity=gw,
                severity=Severity.P2,
                title=f"Bufferbloat: RTT +{delta:.0f} ms under load",
                dims={},
                evidence={
                    "loaded_rtt_ms": round(loaded, 1),
                    "idle_rtt_ms": round(idle, 1),
                    "delta_ms": round(delta, 1),
                    "near_plan_rate": True,
                    "topology_limit": "throughput_from_unifi_gateway_only",
                },
                confounders_checked=[
                    "throughput_load_gate_required",
                    "idle_baseline_vs_loaded_p95",
                    "probe_coverage_gated",
                ],
            )
        ]

    def _gateway_with_rtt(self, ctx: Any) -> Optional[Entity]:
        for gw in _gateways(ctx):
            if ctx.repo.get_series(gw.entity_id, METRIC_GW_RTT) is not None:
                return gw
        return None

    def _near_plan(
        self, ctx: Any, gw: Entity, window_s: int, plan_fraction: float
    ) -> Optional[bool]:
        """True/False if throughput near plan can be judged, else None (unknown).

        Needs both a WAN throughput series (UniFi gateway only) and a configured
        plan rate. Missing either returns None so the caller emits UNKNOWN. On the
        Starlink site both are absent by design ("auto" plan) -> always None.
        """
        settings = getattr(ctx, "_settings", None)
        plan_down = getattr(settings, "wan_plan_down_mbps", None) if settings else None
        plan_up = getattr(settings, "wan_plan_up_mbps", None) if settings else None
        down = _window_values(ctx, gw.entity_id, METRIC_WAN_XPUT_DOWN, window_s)
        up = _window_values(ctx, gw.entity_id, METRIC_WAN_XPUT_UP, window_s)
        judged = False
        if plan_down and down:
            judged = True
            if max(down) >= plan_fraction * float(plan_down):
                return True
        if plan_up and up:
            judged = True
            if max(up) >= plan_fraction * float(plan_up):
                return True
        if not judged:
            return None
        return False


# ====================================================================== #
# wan.flapping
# ====================================================================== #
class WanFlappingDetector:
    """``wan.flapping`` — repeated ``EVT_GW_WANTransition`` (link up/down churn).

    Counts WAN-transition events in a 24 h window; at or above the threshold
    (default 3) the WAN link is flapping and a P1 fires. Purely event-driven, so it
    needs no gateway *entity* — but no UniFi gateway emits these events on the
    Starlink site, so with neither a gateway nor any transition event it no-ops.
    """

    key = KEY_FLAPPING
    scope = EntityType.GATEWAY
    cadence = Cadence.WINDOW

    def __init__(self) -> None:
        self._log_once = _LogOnce()

    def evaluate(self, ctx: Any) -> EvalResult:
        window_s = int(ctx.threshold(self.key, "window_s", _PROFILE.flapping_window_s))
        threshold = int(
            ctx.threshold(self.key, "transitions_min", _PROFILE.flapping_transitions_min)
        )
        since = ctx.now_ts - window_s

        events = ctx.events(keys={EVT_WAN_TRANSITION}, since_ts=since)
        gateways = _gateways(ctx)
        if not events and not gateways:
            self._log_once(
                _log, "no_gateway", "wan.flapping: no gateway and no WAN-transition events; no-op"
            )
            return []
        if len(events) < threshold:
            return []

        gw = gateways[0] if gateways else _synthetic_gateway(ctx.site_id)
        last_ts = max(int(_row_val(e, "ts")) for e in events)
        return [
            Finding(
                detector_key=self.key,
                entity=gw,
                severity=Severity.P1,
                title=f"WAN flapping: {len(events)} transitions in {window_s // 3600}h",
                dims={},
                evidence={
                    "transitions": len(events),
                    "window_s": window_s,
                    "last_transition_ts": last_ts,
                },
                confounders_checked=[
                    "sustained_count_not_single_transition",
                    "window_bounded_24h",
                ],
            )
        ]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _row_val(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _synthetic_gateway(site_id: str) -> Entity:
    """An unpersisted stand-in gateway (for event-only findings on gateway-less sites)."""
    return Entity(
        entity_type=EntityType.GATEWAY,
        native_id=f"wan:{site_id}",
        site_id=site_id,
        entity_id=None,
        name="wan",
    )


__all__ = [
    "KEY_ISP_DEGRADED",
    "KEY_LATENCY_SHIFT",
    "KEY_DNS_SLOW",
    "KEY_BUFFERBLOAT",
    "KEY_FLAPPING",
    "StarlinkWanProfile",
    "IspDegradedDetector",
    "LatencyShiftDetector",
    "DnsSlowDetector",
    "BufferbloatDetector",
    "WanFlappingDetector",
]
