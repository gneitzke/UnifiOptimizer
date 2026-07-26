"""wan.* detectors on synthetic Starlink traces.

The binding site context: a **Starlink dish behind a third-party router** — NO
UniFi gateway, so the only uplink signals are the ICMP ``gw_rtt_ms`` probe, the DNS
probes, and the ``probe.gw_rtt`` poll accounting (a failed probe = a lost packet).

These tests exercise the Starlink discipline the detectors must hold:

* healthy Starlink (calm base + brief ~15 s handoff spikes) produces **no**
  findings — the rolling-window p50 is robust to spikes;
* a *sustained* multi-window elevation of latency or loss fires, a single-window
  burst (a brief obstruction) does not;
* ``wan.latency_shift`` reports a sustained CUSUM regime change and ignores a
  transient dip that recovered;
* the gateway-less no-op / UNKNOWN paths stay honest.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Optional

from netadmin.detect.baseline import Band
from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.wan import (
    KEY_DNS_SLOW,
    KEY_FLAPPING,
    KEY_ISP_DEGRADED,
    KEY_LATENCY_SHIFT,
    BufferbloatDetector,
    DnsSlowDetector,
    IspDegradedDetector,
    LatencyShiftDetector,
    WanFlappingDetector,
)
from netadmin.detect.engine import UNKNOWN
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, Severity
from netadmin.store.repository import Repository, SampleReading

NOW = 4_000_000
WIN = 900  # one analysis window (15 min)


class _Baselines:
    """A baselines stub that returns a fixed band for a chosen series_id."""

    def __init__(self, bands: Optional[dict[int, Band]] = None) -> None:
        self._bands = bands or {}

    def band(self, series_id: int, *, bucket=None):
        return self._bands.get(series_id)


def _ctx(repo: Repository, *, baselines=None, settings=None, now: int = NOW) -> DetectorContext:
    return DetectorContext(
        repo=repo,
        baselines=baselines or _Baselines(),
        now_ts=now,
        site_id="default",
        settings=settings,
    )


def _gateway(repo: Repository, native_id: str = "gw-1") -> int:
    return repo.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id=native_id, site_id="default"),
        ts=NOW,
    )


def _gauge(repo: Repository, entity_id: int, metric: str, points) -> int:
    repo.record_samples(SampleReading(entity_id, metric, ts, v) for ts, v in points)
    return repo.get_series(entity_id, metric)


def _flat(metric_start: int, count: int, value: float, step: int = 60):
    return [(metric_start + i * step, value) for i in range(count)]


def _band(p05=0.0, p50=0.0, p95=0.0) -> Band:
    return Band(mean=p50, var=1.0, p05=p05, p50=p50, p95=p95, n=100, updated_ts=NOW)


def _seed_gw_rtt(
    repo: Repository,
    gw: int,
    *,
    now: int,
    span_s: int,
    val_fn: Callable[[int], Optional[float]],
    ok_fn: Callable[[int], bool] = lambda _t: True,
    step: int = 60,
) -> int:
    """Seed a synthetic gw_rtt probe trace over ``[now-span_s, now)``.

    ``val_fn(ts)`` yields the RTT ms (or None to drop a sample, e.g. a lost probe);
    ``ok_fn(ts)`` yields the poll outcome. Returns the gw_rtt series id.
    """
    pts = []
    t = now - span_s + step
    while t <= now:
        v = val_fn(t)
        if v is not None:
            pts.append((t, v))
        repo.record_poll_run(job="probe.gw_rtt", ok=ok_fn(t), ts=t)
        t += step
    if pts:
        repo.record_samples(SampleReading(gw, "gw_rtt_ms", ts, v) for ts, v in pts)
    sid = repo.get_series(gw, "gw_rtt_ms")
    assert sid is not None
    return sid


def _healthy_starlink(ts: int) -> float:
    """Calm base 30-45 ms with a brief handoff spike to ~100 ms every 4th sample."""
    minute = ts // 60
    if minute % 4 == 0:
        return 80.0 + (minute % 5) * 10.0  # 80-120 ms handoff spike
    return 30.0 + (minute % 4) * 5.0  # 30-45 ms calm base


# ====================================================================== #
# wan.isp_degraded  (rolling-window, sustained, robust to handoff spikes)
# ====================================================================== #
def test_isp_degraded_noop_without_source(repo: Repository) -> None:
    # No gateway / no latency series at all -> graceful no-op.
    assert IspDegradedDetector().evaluate(_ctx(repo)) == []


def test_isp_degraded_healthy_starlink_is_quiet(repo: Repository) -> None:
    # Base 30-45 ms with brief 80-120 ms handoff spikes: the window p50 stays ~37,
    # so nothing fires even though raw samples repeatedly cross 100 ms.
    gw = _gateway(repo)
    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=_healthy_starlink)
    baselines = _Baselines({sid: _band(p50=37.0)})
    assert IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_isp_degraded_fires_on_sustained_latency(repo: Repository) -> None:
    # A wholesale shift: every window's p50 ~90 ms vs a 37 ms baseline, sustained
    # across all four windows -> P2 latency degradation.
    gw = _gateway(repo)
    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=lambda _t: 90.0)
    baselines = _Baselines({sid: _band(p50=37.0)})
    findings = IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_ISP_DEGRADED
    assert f.severity is Severity.P2
    assert f.evidence["latency_fired"] is True
    assert f.evidence["loss_fired"] is False
    assert f.evidence["ratio"] >= 1.8


def test_isp_degraded_ignores_single_window_burst(repo: Repository) -> None:
    # Three calm windows and one window fully elevated to 90 ms (a brief obstruction
    # burst). Only one window deviates (< the 3-window sustained rule) -> quiet.
    gw = _gateway(repo)

    def val_fn(ts: int) -> float:
        return 90.0 if ts >= NOW - WIN else 37.0  # only the most-recent window hot

    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=val_fn)
    baselines = _Baselines({sid: _band(p50=37.0)})
    assert IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_isp_degraded_fires_p1_on_sustained_loss(repo: Repository) -> None:
    # Latency normal, but a sustained obstruction drops ~13% of probes every window
    # -> heavy sustained loss escalates to P1.
    gw = _gateway(repo)

    def ok_fn(ts: int) -> bool:
        return (ts // 60) % 8 != 0  # ~1 in 8 probes fails => ~12.5% loss

    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=lambda _t: 37.0, ok_fn=ok_fn)
    baselines = _Baselines({sid: _band(p50=37.0)})
    # Override the 7-day baseline loss fraction to 0 so the recent window is judged
    # against a clean baseline (in production the 7-day window dilutes it).
    settings = SimpleNamespace(
        thresholds={"wan.isp_degraded": {"baseline_loss_fraction": 0.0}}, poll=None
    )
    findings = IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines, settings=settings))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.P1
    assert f.evidence["loss_fired"] is True
    assert f.evidence["loss_fraction"] >= 0.05


def test_isp_degraded_ignores_single_window_loss(repo: Repository) -> None:
    # A brief obstruction: only the most-recent window loses probes -> not sustained.
    gw = _gateway(repo)

    def ok_fn(ts: int) -> bool:
        if ts >= NOW - WIN:  # only the last window
            return (ts // 60) % 4 != 0
        return True

    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=lambda _t: 37.0, ok_fn=ok_fn)
    baselines = _Baselines({sid: _band(p50=37.0)})
    settings = SimpleNamespace(
        thresholds={"wan.isp_degraded": {"baseline_loss_fraction": 0.0}}, poll=None
    )
    assert IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines, settings=settings)) == []


def test_isp_degraded_ignores_isolated_single_packet_drops(repo: Repository) -> None:
    # Three isolated single-packet drops, one in each of the three most-recent
    # windows — entirely normal LEO handoff/obstruction behaviour. Each window loses
    # exactly ONE of ~15 probes (6.7%), which naively clears the fraction floors, but
    # the per-window min-lost-probes gate (>= 2) means no window counts -> quiet.
    gw = _gateway(repo)
    drops = {NOW - 60, NOW - 960, NOW - 1860}  # one probe in windows 0, 1, 2

    def ok_fn(ts: int) -> bool:
        return ts not in drops

    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=lambda _t: 37.0, ok_fn=ok_fn)
    baselines = _Baselines({sid: _band(p50=37.0)})
    settings = SimpleNamespace(
        thresholds={"wan.isp_degraded": {"baseline_loss_fraction": 0.0}}, poll=None
    )
    assert IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines, settings=settings)) == []


def test_isp_degraded_holds_open_after_baseline_drift(repo: Repository) -> None:
    # A genuine sustained latency shift that has persisted long enough for the rolling
    # baseline to climb to the new level: window p50 == baseline p50 == 130 ms, so the
    # ratio test no longer trips. The absolute hold floor keeps the finding alive so a
    # still-degraded WAN does not silently auto-resolve into its own "normal".
    gw = _gateway(repo)
    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=lambda _t: 130.0)
    baselines = _Baselines({sid: _band(p50=130.0)})  # baseline drifted up to match
    findings = IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_ISP_DEGRADED
    assert f.evidence["latency_fired"] is True
    assert f.severity is Severity.P2


def test_isp_degraded_unknown_prober_down(repo: Repository) -> None:
    # A latency series exists but the prober barely ran (a real coverage gap) ->
    # UNKNOWN, distinct from loss (which is failed-but-recorded probes).
    gw = _gateway(repo)
    _gauge(repo, gw, "gw_rtt_ms", _flat(NOW - 600, 5, 90.0))
    for ts in (NOW - 300, NOW - 240, NOW - 180):  # only 3 poll rows over the lookback
        repo.record_poll_run(job="probe.gw_rtt", ok=True, ts=ts)
    baselines = _Baselines()
    assert IspDegradedDetector().evaluate(_ctx(repo, baselines=baselines)) is UNKNOWN


def test_isp_degraded_unknown_cold_baseline(repo: Repository) -> None:
    # Sustained high absolute latency but no baseline to confirm the trend ->
    # UNKNOWN, never a false OK.
    gw = _gateway(repo)
    _seed_gw_rtt(repo, gw, now=NOW, span_s=4 * WIN, val_fn=lambda _t: 90.0)
    assert IspDegradedDetector().evaluate(_ctx(repo)) is UNKNOWN  # no band supplied


# ====================================================================== #
# wan.latency_shift  (CUSUM regime-change; the Starlink bufferbloat substitute)
# ====================================================================== #
def test_latency_shift_noop_without_source(repo: Repository) -> None:
    assert LatencyShiftDetector().evaluate(_ctx(repo)) == []


def test_latency_shift_quiet_on_healthy_jitter(repo: Repository) -> None:
    gw = _gateway(repo)
    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=24 * WIN, val_fn=_healthy_starlink)
    baselines = _Baselines({sid: _band(p50=37.0)})
    assert LatencyShiftDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_latency_shift_fires_on_sustained_shift(repo: Repository) -> None:
    # Calm 37 ms for the first half of the horizon, then a sustained step to 90 ms
    # that persists to now -> a regime-change finding with before/after numbers.
    gw = _gateway(repo)
    shift_ts = NOW - 12 * WIN

    def val_fn(ts: int) -> float:
        return 90.0 if ts >= shift_ts else 37.0

    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=24 * WIN, val_fn=val_fn)
    baselines = _Baselines({sid: _band(p50=37.0)})
    findings = LatencyShiftDetector().evaluate(_ctx(repo, baselines=baselines))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_LATENCY_SHIFT
    assert f.severity is Severity.P3
    assert abs(f.evidence["before_p50_ms"] - 37.0) < 5.0
    assert abs(f.evidence["after_p50_ms"] - 90.0) < 5.0
    assert f.evidence["delta_ms"] >= 15.0
    assert NOW - 24 * WIN <= f.evidence["change_ts"] <= NOW


def test_latency_shift_ignores_transient_dip(repo: Repository) -> None:
    # A burst of elevated latency in the MIDDLE that recovered to normal by now:
    # the CUSUM decays back to zero, so no sustained shift is reported.
    gw = _gateway(repo)

    def val_fn(ts: int) -> float:
        # elevated only in windows ~10..6 windows ago, calm before and (crucially) after
        if NOW - 10 * WIN <= ts < NOW - 6 * WIN:
            return 90.0
        return 37.0

    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=24 * WIN, val_fn=val_fn)
    baselines = _Baselines({sid: _band(p50=37.0)})
    assert LatencyShiftDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_latency_shift_unknown_when_too_sparse(repo: Repository) -> None:
    # A series exists but only a couple of windows have enough samples -> UNKNOWN.
    gw = _gateway(repo)
    _gauge(repo, gw, "gw_rtt_ms", _flat(NOW - 120, 2, 90.0))
    baselines = _Baselines()
    assert LatencyShiftDetector().evaluate(_ctx(repo, baselines=baselines)) is UNKNOWN


# ====================================================================== #
# wan.dns_slow  (probe-only; works without a UniFi gateway)
# ====================================================================== #
def test_dns_slow_noop_without_probe(repo: Repository) -> None:
    assert DnsSlowDetector().evaluate(_ctx(repo)) == []


def test_dns_slow_fires_local(repo: Repository) -> None:
    from tests.netadmin.detect.support import seed_coverage

    gw = _gateway(repo)  # probe gateway (third-party router)
    seed_coverage(repo, job="probe.dns", now=NOW, window_s=900, interval_s=60)
    _gauge(repo, gw, "dns_latency_ms", _flat(NOW - 900, 15, 300.0))  # slow resolver
    _gauge(repo, gw, "dns_anchor_latency_ms", _flat(NOW - 900, 15, 20.0))  # anchor fine
    findings = DnsSlowDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_DNS_SLOW
    assert f.evidence["localised"] == "local"
    assert f.severity is Severity.P2


def test_dns_slow_localises_upstream(repo: Repository) -> None:
    from tests.netadmin.detect.support import seed_coverage

    gw = _gateway(repo)
    seed_coverage(repo, job="probe.dns", now=NOW, window_s=900, interval_s=60)
    _gauge(repo, gw, "dns_latency_ms", _flat(NOW - 900, 15, 300.0))
    _gauge(repo, gw, "dns_anchor_latency_ms", _flat(NOW - 900, 15, 400.0))  # anchor also slow
    findings = DnsSlowDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["localised"] == "upstream"


def test_dns_slow_confounder_fast_resolver_quiet(repo: Repository) -> None:
    from tests.netadmin.detect.support import seed_coverage

    gw = _gateway(repo)
    seed_coverage(repo, job="probe.dns", now=NOW, window_s=900, interval_s=60)
    _gauge(repo, gw, "dns_latency_ms", _flat(NOW - 900, 15, 30.0))  # snappy
    assert DnsSlowDetector().evaluate(_ctx(repo)) == []


def test_dns_slow_unknown_low_coverage(repo: Repository) -> None:
    gw = _gateway(repo)
    _gauge(repo, gw, "dns_latency_ms", _flat(NOW - 900, 15, 300.0))
    repo.record_poll_run(job="probe.dns", ok=True, ts=NOW - 60)
    assert DnsSlowDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wan.bufferbloat  (honestly UNKNOWN on the gateway-less Starlink site)
# ====================================================================== #
def test_bufferbloat_noop_without_probe(repo: Repository) -> None:
    assert BufferbloatDetector().evaluate(_ctx(repo)) == []


def test_bufferbloat_unknown_no_throughput(repo: Repository) -> None:
    # RTT spike present, but no throughput/plan signal -> cannot prove "under load".
    from tests.netadmin.detect.support import seed_coverage

    gw = _gateway(repo)
    seed_coverage(repo, job="probe.gw_rtt", now=NOW, window_s=900, interval_s=60)
    sid = _gauge(repo, gw, "gw_rtt_ms", _flat(NOW - 900, 15, 260.0))  # loaded p95 ~260
    baselines = _Baselines({sid: _band(p05=10.0)})  # idle 10 ms -> delta 250 > 200
    assert BufferbloatDetector().evaluate(_ctx(repo, baselines=baselines)) is UNKNOWN


def test_bufferbloat_fires_with_throughput(repo: Repository) -> None:
    from tests.netadmin.detect.support import seed_coverage

    gw = _gateway(repo)
    seed_coverage(repo, job="probe.gw_rtt", now=NOW, window_s=900, interval_s=60)
    sid = _gauge(repo, gw, "gw_rtt_ms", _flat(NOW - 900, 15, 260.0))
    _gauge(repo, gw, "wan_xput_down", _flat(NOW - 900, 15, 95.0))  # near 100 plan
    baselines = _Baselines({sid: _band(p05=10.0)})
    settings = SimpleNamespace(thresholds={}, poll=None, wan_plan_down_mbps=100.0)
    findings = BufferbloatDetector().evaluate(_ctx(repo, baselines=baselines, settings=settings))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["near_plan_rate"] is True


def test_bufferbloat_confounder_spike_without_load(repo: Repository) -> None:
    from tests.netadmin.detect.support import seed_coverage

    gw = _gateway(repo)
    seed_coverage(repo, job="probe.gw_rtt", now=NOW, window_s=900, interval_s=60)
    sid = _gauge(repo, gw, "gw_rtt_ms", _flat(NOW - 900, 15, 260.0))
    _gauge(repo, gw, "wan_xput_down", _flat(NOW - 900, 15, 5.0))  # idle link, 5 of 100
    baselines = _Baselines({sid: _band(p05=10.0)})
    settings = SimpleNamespace(thresholds={}, poll=None, wan_plan_down_mbps=100.0)
    assert BufferbloatDetector().evaluate(_ctx(repo, baselines=baselines, settings=settings)) == []


def test_bufferbloat_unknown_low_coverage(repo: Repository) -> None:
    gw = _gateway(repo)
    sid = _gauge(repo, gw, "gw_rtt_ms", _flat(NOW - 900, 15, 260.0))
    repo.record_poll_run(job="probe.gw_rtt", ok=True, ts=NOW - 60)
    baselines = _Baselines({sid: _band(p05=10.0)})
    assert BufferbloatDetector().evaluate(_ctx(repo, baselines=baselines)) is UNKNOWN


# ====================================================================== #
# wan.flapping
# ====================================================================== #
def test_flapping_noop_without_gateway_or_events(repo: Repository) -> None:
    assert WanFlappingDetector().evaluate(_ctx(repo)) == []


def test_flapping_fires_on_repeated_transitions(repo: Repository) -> None:
    gw = _gateway(repo)
    for i in range(3):
        repo.record_event(
            ts=NOW - 3600 * i - 10,
            key="EVT_GW_WANTransition",
            entity_id=gw,
            native_id=f"wt-{i}",
        )
    findings = WanFlappingDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_FLAPPING
    assert f.severity is Severity.P1
    assert f.evidence["transitions"] == 3


def test_flapping_confounder_single_transition_quiet(repo: Repository) -> None:
    gw = _gateway(repo)
    repo.record_event(ts=NOW - 100, key="EVT_GW_WANTransition", entity_id=gw, native_id="wt-solo")
    assert WanFlappingDetector().evaluate(_ctx(repo)) == []


# ====================================================================== #
# Combined: a full healthy Starlink trace fires nothing from either detector
# ====================================================================== #
def test_healthy_starlink_trace_fires_nothing(repo: Repository) -> None:
    gw = _gateway(repo)
    sid = _seed_gw_rtt(repo, gw, now=NOW, span_s=24 * WIN, val_fn=_healthy_starlink)
    baselines = _Baselines({sid: _band(p05=30.0, p50=37.0, p95=110.0)})
    ctx = _ctx(repo, baselines=baselines)
    assert IspDegradedDetector().evaluate(ctx) == []
    assert LatencyShiftDetector().evaluate(ctx) == []
