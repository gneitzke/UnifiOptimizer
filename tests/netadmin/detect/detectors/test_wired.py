"""wired.* detectors, on synthetic port/switch fixtures.

Each detector gets at least: one firing case, one confounder-suppressed case, and
one UNKNOWN-coverage case, plus severity/clear checks where they carry weight. The
real temp-DB :class:`Repository` is used; baselines are a small stub so the
outlier-relative detectors (uplink saturation, broadcast storm, SFP drift) can be
driven deterministically without spinning the whole EWMA engine.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

from netadmin.detect.baseline import Band
from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.wired import (
    KEY_BAD_CABLE,
    KEY_BROADCAST_STORM,
    KEY_DUPLEX_MISMATCH,
    KEY_POE_BUDGET,
    KEY_PORT_FLAPPING,
    KEY_SFP_DEGRADED,
    KEY_STP_LOOP,
    KEY_UPLINK_SATURATION,
    BadCableDetector,
    BroadcastStormDetector,
    DuplexMismatchDetector,
    PoeBudgetDetector,
    PortFlappingDetector,
    SfpDegradedDetector,
    StpLoopDetector,
    UplinkSaturationDetector,
)
from netadmin.detect.engine import UNKNOWN
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, Severity
from netadmin.store.repository import Repository, SampleReading
from tests.netadmin.detect.support import seed_coverage

NOW = 1_700_000_000
INTERVAL = 60


# ---------------------------------------------------------------------- #
# Stub baselines + fixture builders
# ---------------------------------------------------------------------- #
class StubBaselines:
    """A baselines double: returns a registered :class:`Band` per series_id."""

    def __init__(self) -> None:
        self._bands: dict[int, Band] = {}

    def register(self, series_id: int, band: Band) -> None:
        self._bands[series_id] = band

    def band(self, series_id: int, *, bucket: Optional[str] = None):
        return self._bands.get(series_id)

    def update_from_recent(self, now_ts: int) -> int:  # pragma: no cover - unused
        return 0


def band(*, mean: float = 0.0, p50: float = 0.0, p95: float = 0.0) -> Band:
    return Band(mean=mean, var=0.0, p05=0.0, p50=p50, p95=p95, n=100, updated_ts=NOW)


def _ctx(repo, *, baselines=None, settings=None, now: int = NOW) -> DetectorContext:
    return DetectorContext(
        repo=repo,
        baselines=baselines or StubBaselines(),
        now_ts=now,
        site_id="default",
        settings=settings,
    )


def full_coverage(repo: Repository) -> None:
    seed_coverage(repo, job="fast_device", now=NOW, window_s=600, interval_s=60)


def low_coverage(repo: Repository) -> None:
    repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 120)
    repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 60)


def make_switch(repo: Repository, native_id: str = "sw:1", meta: Optional[dict] = None) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.SWITCH,
            native_id=native_id,
            site_id="default",
            name=native_id,
            meta=meta or {},
        ),
        ts=NOW,
    )


def make_port(
    repo: Repository,
    *,
    sw_id: int,
    idx: int,
    is_uplink: bool = False,
    meta: Optional[dict] = None,
    speed: Optional[int] = None,
    full_duplex: Optional[bool] = None,
    up: Optional[bool] = True,
    stp_state: Optional[str] = None,
    sfp_rxfault: Optional[bool] = None,
    sfp_txfault: Optional[bool] = None,
) -> int:
    m = {"media": "GE", "is_uplink": is_uplink}
    m.update(meta or {})
    pid = repo.upsert_entity(
        Entity(
            entity_type=EntityType.PORT,
            native_id=f"sw:1:{idx}",
            site_id="default",
            name=f"port{idx}",
            parent_id=sw_id,
            meta=m,
        ),
        ts=NOW,
    )
    for attr, val in (
        ("speed", speed),
        ("full_duplex", full_duplex),
        ("up", up),
        ("stp_state", stp_state),
        ("sfp_rxfault", sfp_rxfault),
        ("sfp_txfault", sfp_txfault),
    ):
        if val is not None:
            repo.record_state_change(pid, attr, val, ts=NOW)
    return pid


def make_client(repo: Repository, *, sw_id: int, name: str, oui: str = "") -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=f"cli:{name}",
            site_id="default",
            name=name,
            parent_id=sw_id,
            meta={"oui": oui, "is_wired": True},
        ),
        ts=NOW,
    )


def seed_counter(
    repo: Repository, eid: int, metric: str, *, step: float, count: int = 6, end_ts: int = NOW
) -> int:
    """Cumulative counter readings so windowed ``rate()`` yields ~``step``/interval."""
    start = end_ts - (count - 1) * INTERVAL
    cumulative = 0.0
    readings = []
    ts = start
    for _ in range(count):
        readings.append(SampleReading(eid, metric, ts, cumulative))
        cumulative += step
        ts += INTERVAL
    repo.record_samples(readings)
    return repo.get_series(eid, metric)


def seed_gauge(
    repo: Repository, eid: int, metric: str, value: float, *, count: int = 3, end_ts: int = NOW
) -> int:
    start = end_ts - (count - 1) * INTERVAL
    readings = [SampleReading(eid, metric, start + i * INTERVAL, value) for i in range(count)]
    repo.record_samples(readings)
    return repo.get_series(eid, metric)


# ====================================================================== #
# wired.bad_cable
# ====================================================================== #
def test_bad_cable_fires_on_error_rate(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1)
    seed_counter(repo, pid, "rx_errors", step=20)  # 20 errors/min > 10 default

    findings = BadCableDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_BAD_CABLE
    assert f.severity is Severity.P2
    assert "error_rate" in f.evidence["signals"]
    assert "counter_reset_handled" in f.confounders_checked


def test_bad_cable_uplink_escalates_to_p1(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, is_uplink=True)
    seed_counter(repo, pid, "rx_errors", step=20)
    findings = BadCableDetector().evaluate(_ctx(repo))
    assert findings[0].severity is Severity.P1


def test_bad_cable_clean_port_is_a_clear(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1)
    seed_counter(repo, pid, "rx_errors", step=0)  # no errors accruing
    assert BadCableDetector().evaluate(_ctx(repo)) == []


def test_bad_cable_records_packet_normalization_confounder(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1)
    seed_counter(repo, pid, "rx_errors", step=20)
    seed_counter(repo, pid, "rx_packets", step=100_000)
    f = BadCableDetector().evaluate(_ctx(repo))[0]
    assert "packet_volume_normalized" in f.confounders_checked
    assert "error_packet_fraction" in f.evidence


def test_bad_cable_downshift_fires_on_gigabit_port_at_100(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"max_speed": 1000}, speed=100)
    seed_counter(repo, pid, "rx_errors", step=0)  # errors clean; downshift is the signal
    findings = BadCableDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert "speed_downshift" in f.evidence["signals"]
    assert f.evidence["negotiated_speed"] == 100
    assert "port_gigabit_capable" in f.confounders_checked


def test_bad_cable_downshift_suppressed_by_known_100mbps_peer(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"max_speed": 1000}, speed=100)
    make_client(repo, sw_id=sw, name="Garage-ESP32-sensor")  # a by-design 10/100 peer
    seed_counter(repo, pid, "rx_errors", step=0)
    findings = BadCableDetector().evaluate(_ctx(repo))
    assert findings == []


def test_bad_cable_downshift_suppressed_for_petcare_hub(repo: Repository) -> None:
    # Sure Petcare Hub: a fixed-100 smart-home hub with a generic OUI. A gigabit
    # port at 100 Mbps to it is the device, not a broken pair — matched by name.
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"speed_caps": 1048623}, speed=100)
    make_client(repo, sw_id=sw, name="Sure Petcare Hub")
    seed_counter(repo, pid, "rx_errors", step=0)
    assert BadCableDetector().evaluate(_ctx(repo)) == []


def test_bad_cable_downshift_fires_from_ingested_speed_caps(repo: Repository) -> None:
    # The ingest layer now writes the raw UniFi speed_caps bitmask (not max_speed)
    # into port meta. A gigabit port (0x10002F) linked at 100 Mbps must reach the
    # downshift confounder logic and report a 1000 Mbps ceiling (not 10000).
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"speed_caps": 1048623}, speed=100)
    seed_counter(repo, pid, "rx_errors", step=0)  # clean errors; downshift is the signal
    findings = BadCableDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert "speed_downshift" in f.evidence["signals"]
    assert f.evidence["negotiated_speed"] == 100
    assert f.evidence["port_capable_speed"] == 1000
    assert "port_gigabit_capable" in f.confounders_checked


def test_speed_caps_max_decodes_real_unifi_values() -> None:
    from netadmin.detect.detectors.wired import _speed_caps_max

    assert _speed_caps_max(1048623) == 1000  # copper GE: autoneg+10/100 + 0x20 1000
    assert _speed_caps_max(1048608) == 1000  # 1G SFP: 0x20 only (+ high flag bit)
    assert _speed_caps_max(0x20) == 1000
    assert _speed_caps_max(0x40) == 2500
    assert _speed_caps_max(0x100) == 10000
    assert _speed_caps_max(0x10) == 100  # 100-only port is not gigabit-capable
    assert _speed_caps_max(0) is None
    assert _speed_caps_max(None) is None
    assert _speed_caps_max(1) is None  # autoneg flag alone advertises no speed


def test_bad_cable_downshift_ignored_when_not_gigabit_capable(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    # No max_speed/speed_caps -> cannot assert gigabit capability -> no downshift.
    pid = make_port(repo, sw_id=sw, idx=1, speed=100)
    seed_counter(repo, pid, "rx_errors", step=0)
    assert BadCableDetector().evaluate(_ctx(repo)) == []


def test_bad_cable_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1)
    assert BadCableDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.duplex_mismatch
# ====================================================================== #
def test_duplex_fires_on_half_duplex_modern_link(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, speed=1000, full_duplex=False, up=True)
    findings = DuplexMismatchDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].detector_key == KEY_DUPLEX_MISMATCH
    assert findings[0].severity is Severity.P2


def test_duplex_suppressed_on_legacy_speed(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    # Half-duplex at 10 Mbps is a legacy hub link, not a mismatch.
    make_port(repo, sw_id=sw, idx=1, speed=10, full_duplex=False, up=True)
    assert DuplexMismatchDetector().evaluate(_ctx(repo)) == []


def test_duplex_suppressed_on_down_port(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, speed=1000, full_duplex=False, up=False)
    assert DuplexMismatchDetector().evaluate(_ctx(repo)) == []


def test_duplex_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, speed=1000, full_duplex=False)
    assert DuplexMismatchDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.port_flapping
# ====================================================================== #
def _seed_flaps(repo: Repository, pid: int, n: int, *, end_ts: int = NOW, span: int = 500) -> None:
    step = span // n
    for i in range(n):
        repo.record_state_change(pid, "up", bool(i % 2), ts=end_ts - span + i * step)


def test_port_flapping_fires_on_transitions(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, up=None)
    _seed_flaps(repo, pid, 6)  # 6 transitions in 10 min > 5
    findings = PortFlappingDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].detector_key == KEY_PORT_FLAPPING
    assert findings[0].evidence["transitions_short"] >= 5


def test_port_flapping_infra_port_is_p1_and_flags_reboot_loop(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, is_uplink=True, up=None)
    _seed_flaps(repo, pid, 6)
    # PoE draw dips to 0 between flaps -> powered device reboot loop.
    repo.record_samples(
        [
            SampleReading(pid, "poe_power", NOW - 240, 6.5),
            SampleReading(pid, "poe_power", NOW - 180, 0.0),
            SampleReading(pid, "poe_power", NOW - 120, 6.5),
        ]
    )
    f = PortFlappingDetector().evaluate(_ctx(repo))[0]
    assert f.severity is Severity.P1
    assert f.evidence["poe_reboot_loop"] is True
    assert "poe_reboot_correlated" in f.confounders_checked


def test_port_flapping_quiet_below_threshold(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, up=None)
    _seed_flaps(repo, pid, 2)  # stable link, 2 transitions
    assert PortFlappingDetector().evaluate(_ctx(repo)) == []


def test_port_flapping_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, up=None)
    _seed_flaps(repo, pid, 6)
    assert PortFlappingDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.uplink_saturation
# ====================================================================== #
def test_uplink_saturation_fires_with_drops(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, is_uplink=True, speed=100)
    seed_counter(repo, pid, "tx_bytes", step=6.5e8)  # ~86% of 100 Mbps
    seed_counter(repo, pid, "tx_dropped", step=50)  # rising drops corroborate
    findings = UplinkSaturationDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].detector_key == KEY_UPLINK_SATURATION
    assert findings[0].evidence["utilization_pct"] >= 80


def test_uplink_saturation_suppressed_within_diurnal_norm(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, is_uplink=True, speed=100)
    sid = seed_counter(repo, pid, "tx_bytes", step=6.5e8)
    seed_counter(repo, pid, "tx_dropped", step=50)
    # This hour's p95 sits above the current per-interval delta -> normal for now.
    baselines = StubBaselines()
    baselines.register(sid, band(p95=1e12))
    findings = UplinkSaturationDetector().evaluate(_ctx(repo, baselines=baselines))
    assert findings == []


def test_uplink_saturation_suppressed_without_drops(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, is_uplink=True, speed=100)
    seed_counter(repo, pid, "tx_bytes", step=6.5e8)  # high util, but no drops seeded
    assert UplinkSaturationDetector().evaluate(_ctx(repo)) == []


def test_uplink_saturation_ignores_access_ports(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, is_uplink=False, speed=100)
    seed_counter(repo, pid, "tx_bytes", step=6.5e8)
    seed_counter(repo, pid, "tx_dropped", step=50)
    assert UplinkSaturationDetector().evaluate(_ctx(repo)) == []


def test_uplink_saturation_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, is_uplink=True, speed=100)
    assert UplinkSaturationDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.poe_budget
# ====================================================================== #
def test_poe_budget_fires_critical_over_budget(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo, meta={"total_max_power": 60.0})
    p1 = make_port(repo, sw_id=sw, idx=1)
    p2 = make_port(repo, sw_id=sw, idx=2)
    seed_gauge(repo, p1, "poe_power", 30.0)
    seed_gauge(repo, p2, "poe_power", 25.0)  # 55/60 = 91% > 90 crit
    findings = PoeBudgetDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_POE_BUDGET
    assert f.severity is Severity.P1
    assert f.evidence["budget_pct"] >= 90
    assert "budget_known" in f.confounders_checked


def test_poe_budget_warn_tier_is_p2(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo, meta={"total_max_power": 60.0})
    p1 = make_port(repo, sw_id=sw, idx=1)
    seed_gauge(repo, p1, "poe_power", 50.0)  # 83% -> warn
    f = PoeBudgetDetector().evaluate(_ctx(repo))[0]
    assert f.severity is Severity.P2


def test_poe_budget_fires_on_overload_event_without_budget(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)  # no budget meta
    make_port(repo, sw_id=sw, idx=1)
    repo.record_event(ts=NOW - 100, key="EVT_SW_PoeOverload", entity_id=sw)
    f = PoeBudgetDetector().evaluate(_ctx(repo))[0]
    assert f.severity is Severity.P1
    assert f.evidence["overload_events"] == 1


def test_poe_budget_quiet_under_budget(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo, meta={"total_max_power": 60.0})
    p1 = make_port(repo, sw_id=sw, idx=1)
    seed_gauge(repo, p1, "poe_power", 20.0)  # 33%
    assert PoeBudgetDetector().evaluate(_ctx(repo)) == []


def test_poe_budget_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    make_switch(repo, meta={"total_max_power": 60.0})
    assert PoeBudgetDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.stp_loop
# ====================================================================== #
def test_stp_loop_fires_on_blocking_event(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1)
    repo.record_event(ts=NOW - 100, key="EVT_SW_StpPortBlocking", entity_id=pid)
    findings = StpLoopDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].detector_key == KEY_STP_LOOP
    assert findings[0].severity is Severity.P1
    assert findings[0].evidence["blocking_event"] is True


def test_stp_loop_fires_on_blocking_state(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, stp_state="blocking")
    f = StpLoopDetector().evaluate(_ctx(repo))[0]
    assert f.evidence["stp_state"] == "blocking"


def test_stp_loop_quiet_on_forwarding_state(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, stp_state="forwarding")
    assert StpLoopDetector().evaluate(_ctx(repo)) == []


def test_stp_loop_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1)
    repo.record_event(ts=NOW - 100, key="EVT_SW_StpPortBlocking", entity_id=pid)
    assert StpLoopDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.broadcast_storm
# ====================================================================== #
def test_broadcast_storm_fires_on_multiple_ports(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    baselines = StubBaselines()
    for idx in (1, 2):
        pid = make_port(repo, sw_id=sw, idx=idx)
        sid = seed_counter(repo, pid, "rx_broadcast", step=1000)  # ~1000/interval
        baselines.register(sid, band(p50=10.0))  # baseline ~10 -> 100x
    findings = BroadcastStormDetector().evaluate(_ctx(repo, baselines=baselines))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_BROADCAST_STORM
    assert f.severity is Severity.P1
    assert f.evidence["ports_storming"] == 2
    assert "multi_port_simultaneous" in f.confounders_checked


def test_broadcast_storm_suppressed_on_single_port(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    baselines = StubBaselines()
    # One chatty port storms, the other is quiet -> not a storm.
    p1 = make_port(repo, sw_id=sw, idx=1)
    s1 = seed_counter(repo, p1, "rx_broadcast", step=1000)
    baselines.register(s1, band(p50=10.0))
    p2 = make_port(repo, sw_id=sw, idx=2)
    s2 = seed_counter(repo, p2, "rx_broadcast", step=5)
    baselines.register(s2, band(p50=10.0))
    assert BroadcastStormDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_broadcast_storm_quiet_without_baseline(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    for idx in (1, 2):
        pid = make_port(repo, sw_id=sw, idx=idx)
        seed_counter(repo, pid, "rx_broadcast", step=1000)
    # No bands registered (cold baselines) -> cannot judge outlier -> no fire.
    assert BroadcastStormDetector().evaluate(_ctx(repo)) == []


def test_broadcast_storm_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    make_switch(repo)
    assert BroadcastStormDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wired.sfp_degraded
# ====================================================================== #
def test_sfp_degraded_fires_at_power_floor(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_rxpower", -16.0)  # below -14 floor
    findings = SfpDegradedDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].detector_key == KEY_SFP_DEGRADED
    assert "rx_power_floor" in findings[0].evidence["signals"]


def test_sfp_degraded_fires_on_drift(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    sid = seed_gauge(repo, pid, "sfp_rxpower", -10.0)
    baselines = StubBaselines()
    baselines.register(sid, band(mean=-6.0))  # drop of 4 dB >= 3
    f = SfpDegradedDetector().evaluate(_ctx(repo, baselines=baselines))[0]
    assert "rx_power_drift" in f.evidence["signals"]
    assert "baseline_drift_checked" in f.confounders_checked


def test_sfp_degraded_fires_on_fault_flag(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"}, sfp_rxfault=True)
    f = SfpDegradedDetector().evaluate(_ctx(repo))[0]
    assert "rx_fault" in f.evidence["signals"]


def test_sfp_degraded_quiet_on_healthy_optic(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    sid = seed_gauge(repo, pid, "sfp_rxpower", -6.0)  # healthy
    baselines = StubBaselines()
    baselines.register(sid, band(mean=-6.0))  # no drift
    assert SfpDegradedDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_sfp_degraded_unknown_on_low_coverage(repo: Repository) -> None:
    low_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_rxpower", -16.0)
    assert SfpDegradedDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_sfp_degraded_rx_floor_is_p2(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_rxpower", -16.0)
    assert SfpDegradedDetector().evaluate(_ctx(repo))[0].severity is Severity.P2


def test_sfp_degraded_drift_only_is_p3(repo: Repository) -> None:
    # A trend worth watching, not a link already out of band.
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    sid = seed_gauge(repo, pid, "sfp_rxpower", -10.0)
    baselines = StubBaselines()
    baselines.register(sid, band(mean=-6.0))
    f = SfpDegradedDetector().evaluate(_ctx(repo, baselines=baselines))[0]
    assert f.evidence["signals"] == ["rx_power_drift"]
    assert f.severity is Severity.P3


def test_sfp_degraded_fires_on_tx_power_floor(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_txpower", -9.5)  # below the -8 dBm default floor
    f = SfpDegradedDetector().evaluate(_ctx(repo))[0]
    assert "tx_power_floor" in f.evidence["signals"]
    assert f.evidence["sfp_txpower_dbm"] == -9.5
    assert f.severity is Severity.P2


def test_sfp_degraded_fires_on_module_temperature(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_temperature", 75.0)  # past the 70 C default limit
    f = SfpDegradedDetector().evaluate(_ctx(repo))[0]
    assert f.evidence["signals"] == ["module_temp"]
    assert f.evidence["sfp_temperature_c"] == 75.0


def test_sfp_degraded_hot_chassis_suppresses_a_lone_module_temp(repo: Repository) -> None:
    # A warm optic inside a warm switch is the switch's problem;
    # infra.device_overheating owns that finding, so this one stays quiet.
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_temperature", 75.0)
    seed_gauge(repo, sw, "temp", 68.0)  # host chassis past the 60 C default
    assert SfpDegradedDetector().evaluate(_ctx(repo)) == []


def test_sfp_degraded_hot_chassis_keeps_module_temp_as_secondary_evidence(
    repo: Repository,
) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_rxpower", -16.0)  # a real, independent signal
    seed_gauge(repo, pid, "sfp_temperature", 75.0)
    seed_gauge(repo, sw, "temp", 68.0)

    f = SfpDegradedDetector().evaluate(_ctx(repo))[0]
    assert f.evidence["signals"] == ["rx_power_floor"]  # module temp did not trigger
    assert f.evidence["module_temp_secondary_to_chassis"] is True
    assert f.evidence["chassis_temp_c"] == 68.0
    assert "chassis_temp_checked" in f.confounders_checked


def test_sfp_degraded_module_temp_fires_when_the_chassis_is_cool(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_temperature", 75.0)
    seed_gauge(repo, sw, "temp", 40.0)
    f = SfpDegradedDetector().evaluate(_ctx(repo))[0]
    assert "module_temp" in f.evidence["signals"]
    # The chassis was read and cleared, so the audit trail records the check.
    assert "chassis_temp_checked" in f.confounders_checked
    assert f.evidence["chassis_temp_c"] == 40.0


def test_sfp_degraded_module_temp_fires_when_the_host_has_no_sensor(repo: Repository) -> None:
    # No chassis series at all: unknown is not "hot", so the arm still fires and
    # the confounder is not claimed.
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    seed_gauge(repo, pid, "sfp_temperature", 75.0)
    f = SfpDegradedDetector().evaluate(_ctx(repo))[0]
    assert "module_temp" in f.evidence["signals"]
    assert "chassis_temp_checked" not in f.confounders_checked


def test_sfp_degraded_fires_on_bias_current_drift(repo: Repository) -> None:
    # Aging laser: bias current climbs to hold output. Absolute limits are
    # vendor-specific and unexposed, so it is judged against its own baseline.
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    sid = seed_gauge(repo, pid, "sfp_current", 8.0)
    baselines = StubBaselines()
    baselines.register(sid, band(mean=6.0))  # +33% over baseline, past the 25% default

    f = SfpDegradedDetector().evaluate(_ctx(repo, baselines=baselines))[0]
    assert f.evidence["signals"] == ["bias_current_drift"]
    assert f.evidence["bias_current_rise_pct"] == 33.3
    assert f.severity is Severity.P3  # drift-only
    assert "bias_baseline_drift_checked" in f.confounders_checked


def test_sfp_degraded_quiet_on_stable_bias_current(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1, meta={"media": "SFP+"})
    sid = seed_gauge(repo, pid, "sfp_current", 6.1)
    baselines = StubBaselines()
    baselines.register(sid, band(mean=6.0))
    assert SfpDegradedDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_sfp_degraded_quiet_on_a_copper_port(repo: Repository) -> None:
    # No optic, no DOM, no fault flags recorded: nothing to say.
    full_coverage(repo)
    sw = make_switch(repo)
    make_port(repo, sw_id=sw, idx=1)
    assert SfpDegradedDetector().evaluate(_ctx(repo)) == []


# ====================================================================== #
# catalog registration
# ====================================================================== #
def test_all_wired_detectors_registered() -> None:
    from netadmin.detect.catalog import DEFAULT_CATALOG

    for key in (
        KEY_BAD_CABLE,
        KEY_DUPLEX_MISMATCH,
        KEY_PORT_FLAPPING,
        KEY_UPLINK_SATURATION,
        KEY_POE_BUDGET,
        KEY_STP_LOOP,
        KEY_BROADCAST_STORM,
        KEY_SFP_DEGRADED,
    ):
        assert key in DEFAULT_CATALOG.keys


def test_thresholds_are_overridable(repo: Repository) -> None:
    full_coverage(repo)
    sw = make_switch(repo)
    pid = make_port(repo, sw_id=sw, idx=1)
    seed_counter(repo, pid, "rx_errors", step=5)  # 5/min, under default 10
    assert BadCableDetector().evaluate(_ctx(repo)) == []
    settings = SimpleNamespace(thresholds={KEY_BAD_CABLE: {"errors_per_min": 3.0}}, poll=None)
    findings = BadCableDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1
