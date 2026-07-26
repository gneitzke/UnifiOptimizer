"""infra.* detectors (controller_down, device_down, device_overheating).

Driven on synthetic poll_runs / events / gauge series against the real temp-DB
repository; baselines are stubbed so the thermal drift arm is deterministic.
"""

from __future__ import annotations

from types import SimpleNamespace

from netadmin.detect.baseline import Band
from netadmin.detect.catalog import build_catalog
from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.infra import (
    KEY_CONTROLLER_DOWN,
    KEY_DEVICE_DOWN,
    KEY_DEVICE_OVERHEATING,
    ControllerDownDetector,
    DeviceDownDetector,
    DeviceOverheatingDetector,
)
from netadmin.detect.engine import UNKNOWN
from netadmin.domain.entities import Entity
from netadmin.domain.types import Cadence, EntityType, IssueState, Severity
from netadmin.store.repository import Repository, SampleReading
from tests.netadmin.detect.support import (
    FakeBaselines,
    build_stack,
    entry,
    make_finding,
    seed_coverage,
    seed_device,
)

NOW = 4_000_000


def _ctx(repo: Repository, *, settings=None, now: int = NOW, baselines=None) -> DetectorContext:
    return DetectorContext(
        repo=repo,
        baselines=baselines or FakeBaselines(),
        now_ts=now,
        site_id="default",
        settings=settings,
    )


def _fail(repo: Repository, job: str, ts: int) -> None:
    repo.record_poll_run(job=job, ok=False, ts=ts, error="timeout")


def _ok(repo: Repository, job: str, ts: int) -> None:
    repo.record_poll_run(job=job, ok=True, ts=ts)


# ====================================================================== #
# infra.controller_down
# ====================================================================== #
def test_controller_down_fires_after_consecutive_failures(repo: Repository) -> None:
    _ok(repo, "fast_device", NOW - 240)
    for ts in (NOW - 180, NOW - 120, NOW - 60):
        _fail(repo, "fast_device", ts)

    findings = ControllerDownDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_CONTROLLER_DOWN
    assert f.severity is Severity.P1
    assert f.evidence["consecutive_failures"] == 3
    assert f.entity.native_id == "controller:default"
    assert f.entity.entity_id is None


def test_controller_down_quiet_below_threshold(repo: Repository) -> None:
    _ok(repo, "fast_device", NOW - 180)
    _fail(repo, "fast_device", NOW - 120)
    _fail(repo, "fast_device", NOW - 60)
    assert ControllerDownDetector().evaluate(_ctx(repo)) == []


def test_controller_down_clears_on_most_recent_success(repo: Repository) -> None:
    for ts in (NOW - 180, NOW - 120, NOW - 60):
        _fail(repo, "fast_device", ts)
    _ok(repo, "fast_device", NOW - 30)  # newest poll succeeded -> streak resets
    assert ControllerDownDetector().evaluate(_ctx(repo)) == []


def test_controller_down_quiet_with_no_polls(repo: Repository) -> None:
    assert ControllerDownDetector().evaluate(_ctx(repo)) == []


def test_controller_down_threshold_override(repo: Repository) -> None:
    settings = SimpleNamespace(
        thresholds={KEY_CONTROLLER_DOWN: {"consecutive_failures": 2}}, poll=None
    )
    _fail(repo, "fast_device", NOW - 120)
    _fail(repo, "fast_device", NOW - 60)
    findings = ControllerDownDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1
    assert findings[0].evidence["threshold"] == 2


def test_controller_down_is_not_gated_on_coverage(repo: Repository) -> None:
    # No successful polls at all (coverage 0) is precisely the fire condition, not
    # a reason to abstain: controller_down never returns UNKNOWN.
    for ts in (NOW - 180, NOW - 120, NOW - 60):
        _fail(repo, "fast_device", ts)
    result = ControllerDownDetector().evaluate(_ctx(repo))
    assert result is not UNKNOWN
    assert len(result) == 1


def test_controller_down_activates_and_inhibits_siblings(repo: Repository) -> None:
    from tests.netadmin.detect.support import StubDetector

    other = StubDetector("t.other", Cadence.FAST, lambda ctx: [make_finding("t.other")])
    catalog = build_catalog([entry(ControllerDownDetector(), ceiling=Severity.P1), entry(other)])
    stack = build_stack(repo, catalog=catalog)
    for ts in (NOW - 180, NOW - 120, NOW - 60):
        _fail(repo, "fast_device", ts)

    stack.detector_engine.run_fast(NOW)

    open_keys = {r["detector_key"]: r for r in repo.list_issues(open_only=True)}
    assert KEY_CONTROLLER_DOWN in open_keys
    # M=1 inhibition source -> active immediately, freezing everything else.
    assert open_keys[KEY_CONTROLLER_DOWN]["state"] == IssueState.ACTIVE.value
    assert "t.other" not in open_keys  # sibling finding was inhibited (global freeze)


# ====================================================================== #
# infra.device_down
# ====================================================================== #
def test_device_down_unknown_on_low_coverage(repo: Repository) -> None:
    seed_device(repo, native_id="sw-1", state="0", last_seen_ts=NOW)
    # Only two successful polls in a 10-slot window -> coverage 0.2 < 0.5.
    _ok(repo, "fast_device", NOW - 120)
    _ok(repo, "fast_device", NOW - 60)
    assert DeviceDownDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_device_down_fires_on_offline_state(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    seed_device(repo, native_id="sw-1", name="sw-core", state="0", last_seen_ts=NOW)

    findings = DeviceDownDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_DEVICE_DOWN
    assert f.severity is Severity.P1
    assert f.entity.native_id == "sw-1"
    assert f.evidence["triggers"] == ["state_offline"]


def test_device_down_quiet_for_online_fresh_device(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    seed_device(repo, native_id="sw-1", state="1", last_seen_ts=NOW)
    assert DeviceDownDetector().evaluate(_ctx(repo)) == []


def test_device_down_fires_on_unresolved_lost_contact(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = seed_device(repo, native_id="sw-1", state="1", last_seen_ts=NOW)
    repo.record_event(ts=NOW - 100, key="EVT_SW_Lost_Contact", entity_id=eid)

    findings = DeviceDownDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["triggers"] == ["lost_contact"]
    assert findings[0].evidence["last_lost_contact_ts"] == NOW - 100


def test_device_down_reconnect_after_lost_clears(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = seed_device(repo, native_id="sw-1", state="1", last_seen_ts=NOW)
    repo.record_event(ts=NOW - 200, key="EVT_SW_Lost_Contact", entity_id=eid)
    repo.record_event(ts=NOW - 100, key="EVT_SW_Connected", entity_id=eid)
    assert DeviceDownDetector().evaluate(_ctx(repo)) == []


def test_device_down_stale_only_online_state_suppressed(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    # Online state but silent for 1000 s. This is the controller dropping the
    # device from stat/device for a few cycles (poll_runs stay ok=1), NOT a downed
    # device: staleness alone against a recorded-online state must not fire.
    seed_device(
        repo, native_id="ap-1", entity_type=EntityType.AP, state="1", last_seen_ts=NOW - 1000
    )
    assert DeviceDownDetector().evaluate(_ctx(repo)) == []


def test_device_down_stale_only_no_state_fires(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    # No state ever recorded (None) + stale: nothing contradicts the silence, so
    # the stale signal still fires (the legitimate stale-only path survives).
    seed_device(repo, native_id="ap-1", entity_type=EntityType.AP, last_seen_ts=NOW - 1000)
    findings = DeviceDownDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["triggers"] == ["stale_last_seen"]


def test_device_down_stale_online_cascade_does_not_fire(repo: Repository) -> None:
    # The mass false-positive cascade of Finding 3: the controller returns an
    # empty/partial stat/device list for several cycles, every device's last_seen
    # goes stale while state stays online, coverage stays high (poll_runs ok=1) so
    # controller_down never inhibits. None of these may fire device_down.
    seed_coverage(repo, now=NOW)
    for i in range(9):
        seed_device(
            repo,
            native_id=f"ap-{i}",
            entity_type=EntityType.AP,
            state="1",
            last_seen_ts=NOW - 1000,
        )
    for i in range(3):
        seed_device(repo, native_id=f"sw-{i}", state="1", last_seen_ts=NOW - 1000)
    assert DeviceDownDetector().evaluate(_ctx(repo)) == []


def test_device_down_transitional_state_suppresses_stale_only(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    # Provisioning (state 5) + silence is expected, not a failure.
    seed_device(
        repo, native_id="ap-1", entity_type=EntityType.AP, state="5", last_seen_ts=NOW - 1000
    )
    assert DeviceDownDetector().evaluate(_ctx(repo)) == []


def test_device_down_offline_transitional_still_fires(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    # An explicit offline read is not suppressed even from a transitional-ish state:
    # here state is offline AND stale, so state_offline carries it.
    seed_device(
        repo, native_id="ap-1", entity_type=EntityType.AP, state="0", last_seen_ts=NOW - 1000
    )
    findings = DeviceDownDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert "state_offline" in findings[0].evidence["triggers"]


def test_device_down_ignores_clients(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    seed_device(repo, native_id="sw-1", state="1", last_seen_ts=NOW)  # healthy switch
    # A client that looks "offline" must be ignored: clients are not devices.
    seed_device(
        repo, native_id="client-mac", entity_type=EntityType.CLIENT, state="0", last_seen_ts=NOW
    )
    assert DeviceDownDetector().evaluate(_ctx(repo)) == []


def test_device_down_fires_per_device(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    seed_device(repo, native_id="sw-1", state="0", last_seen_ts=NOW)
    seed_device(repo, native_id="ap-1", entity_type=EntityType.AP, state="0", last_seen_ts=NOW)
    findings = DeviceDownDetector().evaluate(_ctx(repo))
    assert {f.entity.native_id for f in findings} == {"sw-1", "ap-1"}


def test_device_down_activates_immediately_through_the_stack(repo: Repository) -> None:
    catalog = build_catalog([entry(DeviceDownDetector(), ceiling=Severity.P1)])
    stack = build_stack(repo, catalog=catalog)
    seed_coverage(repo, now=NOW)
    seed_device(repo, native_id="sw-1", state="0", last_seen_ts=NOW)

    stack.detector_engine.run_fast(NOW)
    issue = next(
        r for r in repo.list_issues(open_only=True) if r["detector_key"] == KEY_DEVICE_DOWN
    )
    assert issue["state"] == IssueState.ACTIVE.value  # M=1 inhibition source
    assert issue["severity"] == "p1"


# ====================================================================== #
# infra.device_overheating
# ====================================================================== #
class _StubBaselines:
    """Baselines double: a registered :class:`Band` per series_id, else cold."""

    def __init__(self) -> None:
        self._bands: dict[int, Band] = {}

    def register(self, series_id: int, mean: float) -> None:
        self._bands[series_id] = Band(
            mean=mean, var=0.0, p05=mean, p50=mean, p95=mean, n=100, updated_ts=NOW
        )

    def band(self, series_id: int, *, bucket=None):
        return self._bands.get(series_id)

    def update_from_recent(self, now_ts: int) -> int:  # pragma: no cover - unused
        return 0


def _seed_gauge(
    repo: Repository, eid: int, metric: str, values: list[float], *, interval: int = 60
) -> int:
    """Record ``values`` as a gauge series, newest one interval before ``NOW``.

    A detector's window is ``[now - seconds, now)``, so the newest sample is
    placed strictly before ``NOW`` to keep every seeded value inside it.
    """
    start = NOW - len(values) * interval
    repo.record_samples(
        [SampleReading(eid, metric, start + i * interval, float(v)) for i, v in enumerate(values)]
    )
    return repo.get_series(eid, metric)


def _thermal_switch(
    repo: Repository,
    *,
    native_id: str = "sw-1",
    name: str = "sw-core",
    has_temperature: bool = True,
    has_fan: bool = False,
) -> int:
    return seed_device(
        repo,
        native_id=native_id,
        name=name,
        state="1",
        last_seen_ts=NOW,
        meta={"has_temperature": has_temperature, "has_fan": has_fan},
    )


def test_overheating_unknown_on_low_coverage(repo: Repository) -> None:
    eid = _thermal_switch(repo)
    _seed_gauge(repo, eid, "temp", [95.0, 95.0, 95.0])
    _ok(repo, "fast_device", NOW - 120)
    _ok(repo, "fast_device", NOW - 60)  # 0.2 coverage < 0.5
    assert DeviceOverheatingDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_overheating_fires_p1_on_controller_flag(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    repo.record_state_change(eid, "overheating", True, ts=NOW)
    _seed_gauge(repo, eid, "temp", [72.0, 73.0, 74.0])  # below every absolute tier

    findings = DeviceOverheatingDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_DEVICE_OVERHEATING
    assert f.severity is Severity.P1
    assert f.evidence["signals"] == ["controller_overheating_flag"]
    assert "temperature_capability_checked" in f.confounders_checked


def test_overheating_fires_p2_on_sustained_critical_temp(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    _seed_gauge(repo, eid, "temp", [92.0, 93.0, 94.0])

    findings = DeviceOverheatingDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.P2
    assert f.evidence["signals"] == ["sustained_critical_temp"]
    assert f.evidence["min_temp_c"] == 92.0
    assert f.evidence["max_temp_c"] == 94.0
    assert "94" in f.title


def test_overheating_quiet_on_a_single_hot_spike(repo: Repository) -> None:
    # One hot sample in an otherwise cool window is a spike, not a fault: the
    # critical arm requires the whole window at or above the tier.
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    _seed_gauge(repo, eid, "temp", [70.0, 70.0, 95.0])
    assert DeviceOverheatingDetector().evaluate(_ctx(repo)) == []


def test_overheating_quiet_on_a_cool_chassis(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    _seed_gauge(repo, eid, "temp", [45.0, 46.0, 45.0])
    assert DeviceOverheatingDetector().evaluate(_ctx(repo)) == []


def test_overheating_fires_p3_on_baseline_drift(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    sid = _seed_gauge(repo, eid, "temp", [78.0, 78.0, 78.0])
    baselines = _StubBaselines()
    baselines.register(sid, 65.0)  # +13 C over baseline, past the 8 C default

    findings = DeviceOverheatingDetector().evaluate(_ctx(repo, baselines=baselines))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.P3
    assert f.evidence["signals"] == ["baseline_drift"]
    assert f.evidence["baseline_rise_c"] == 13.0
    # One sensor cannot separate a warm room from a warm device; say so.
    assert "ambient_check_skipped_single_sensor" in f.confounders_checked


def test_overheating_skips_a_device_with_no_temperature_sensor(repo: Repository) -> None:
    # Every UniFi AP reports has_temperature=false. Even with a stray temp series
    # recorded against it, it is not judged on hardware it does not have.
    seed_coverage(repo, now=NOW)
    eid = seed_device(
        repo,
        native_id="ap-1",
        entity_type=EntityType.AP,
        state="1",
        last_seen_ts=NOW,
        meta={"has_temperature": False},
    )
    _seed_gauge(repo, eid, "temp", [95.0, 96.0, 97.0])
    assert DeviceOverheatingDetector().evaluate(_ctx(repo)) == []


def test_overheating_ambient_suppresses_the_drift_arm(repo: Repository) -> None:
    # Two sensors warm at the same time is one warm room, not two failing switches.
    seed_coverage(repo, now=NOW)
    baselines = _StubBaselines()
    for native_id in ("sw-1", "sw-2"):
        eid = _thermal_switch(repo, native_id=native_id, name=native_id)
        sid = _seed_gauge(repo, eid, "temp", [85.0, 85.0, 85.0])
        baselines.register(sid, 65.0)  # +20 C drift on both

    assert DeviceOverheatingDetector().evaluate(_ctx(repo, baselines=baselines)) == []


def test_overheating_ambient_does_not_suppress_the_critical_arm(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    baselines = _StubBaselines()
    hot = _thermal_switch(repo, native_id="sw-1", name="sw-hot")
    warm = _thermal_switch(repo, native_id="sw-2", name="sw-warm")
    baselines.register(_seed_gauge(repo, hot, "temp", [95.0, 95.0, 95.0]), 60.0)
    baselines.register(_seed_gauge(repo, warm, "temp", [85.0, 85.0, 85.0]), 60.0)

    findings = DeviceOverheatingDetector().evaluate(_ctx(repo, baselines=baselines))
    assert len(findings) == 1
    f = findings[0]
    assert f.entity.native_id == "sw-1"
    assert f.severity is Severity.P2
    assert f.evidence["signals"] == ["sustained_critical_temp"]  # drift arm suppressed
    assert f.evidence["ambient_warm"] is True
    assert "ambient_checked" in f.confounders_checked


def test_overheating_records_a_stopped_fan_as_evidence(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo, has_fan=True)
    _seed_gauge(repo, eid, "temp", [92.0, 93.0, 94.0])
    _seed_gauge(repo, eid, "fan_level", [0.0, 0.0, 0.0])

    f = DeviceOverheatingDetector().evaluate(_ctx(repo))[0]
    assert "fan_checked" in f.confounders_checked
    assert f.evidence["fan_level"] == 0.0
    assert f.evidence["fan_stopped_while_hot"] is True


def test_overheating_recent_reboot_suppresses_the_measured_arms(repo: Repository) -> None:
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    _seed_gauge(repo, eid, "temp", [92.0, 93.0, 94.0])
    _seed_gauge(repo, eid, "uptime", [60.0, 120.0, 180.0])  # booted 3 minutes ago
    assert DeviceOverheatingDetector().evaluate(_ctx(repo)) == []


def test_overheating_recent_reboot_never_suppresses_the_controller_flag(repo: Repository) -> None:
    # The controller's own verdict is authoritative and precedes a thermal
    # shutdown; a fresh boot is no reason to sit on it.
    seed_coverage(repo, now=NOW)
    eid = _thermal_switch(repo)
    repo.record_state_change(eid, "overheating", True, ts=NOW)
    _seed_gauge(repo, eid, "temp", [92.0, 93.0, 94.0])
    _seed_gauge(repo, eid, "uptime", [60.0, 120.0, 180.0])

    f = DeviceOverheatingDetector().evaluate(_ctx(repo))[0]
    assert f.severity is Severity.P1
    assert f.evidence["signals"] == ["controller_overheating_flag"]
    assert f.evidence["recent_reboot_uptime_s"] == 180
    assert "recent_reboot_checked" in f.confounders_checked


def test_overheating_is_registered_in_the_catalog() -> None:
    from netadmin.detect.catalog import DEFAULT_CATALOG

    entry_ = DEFAULT_CATALOG.get(KEY_DEVICE_OVERHEATING)
    assert entry_.cadence is Cadence.WINDOW
    assert entry_.severity_ceiling is Severity.P1
    assert entry_.playbook is not None
