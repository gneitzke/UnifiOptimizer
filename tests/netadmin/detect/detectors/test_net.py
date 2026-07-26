"""net.coverage_hole / net.firmware_regression on synthetic fixtures.

Firing, confounder-suppressed, and UNKNOWN-coverage cases per detector.
"""

from __future__ import annotations

from types import SimpleNamespace

from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.net import (
    KEY_COVERAGE_HOLE,
    KEY_FIRMWARE_REGRESSION,
    CoverageHoleDetector,
    FirmwareRegressionDetector,
)
from netadmin.detect.engine import UNKNOWN, DetectorResult
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, Severity
from netadmin.store.repository import Repository, SampleReading
from tests.netadmin.detect.support import FakeBaselines, seed_coverage

NOW = 4_000_000


def _ctx(repo: Repository, *, settings=None, now: int = NOW) -> DetectorContext:
    return DetectorContext(
        repo=repo,
        baselines=FakeBaselines(),
        now_ts=now,
        site_id="default",
        settings=settings,
    )


def _ap(repo: Repository, native_id: str, name: str | None = None, model: str | None = None) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.AP,
            native_id=native_id,
            site_id="default",
            name=name,
            model=model,
        ),
        ts=NOW,
    )


def _client(repo: Repository, mac: str, ap_id: int) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=mac,
            site_id="default",
            parent_id=ap_id,
            first_seen_ts=NOW - 700_000,
        ),
        ts=NOW,
    )


def _rssi(repo: Repository, client_id: int, points) -> None:
    repo.record_samples(SampleReading(client_id, "rssi", ts, v) for ts, v in points)


# ====================================================================== #
# net.coverage_hole
# ====================================================================== #
def test_coverage_hole_fires(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=21600, interval_s=60)
    ap = _ap(repo, "ap-1", "ap-basement")
    for i in range(3):
        cid = _client(repo, f"cc:{i}", ap)
        # 10 weak samples inside the 6 h window; nothing better anywhere in history.
        _rssi(repo, cid, [(NOW - 20000 + k * 300, -82.0) for k in range(10)])
    findings = CoverageHoleDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_COVERAGE_HOLE
    assert f.severity is Severity.P2
    assert f.evidence["stuck_clients"] == 3
    assert f.evidence["client_rssi_p25"] <= -75


def test_coverage_hole_confounder_sticky_client_excluded(repo: Repository) -> None:
    # Weak recent histogram, but each client has a STRONG RSSI earlier in history
    # (a better AP exists for them) -> sticky, not a coverage hole.
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=21600, interval_s=60)
    ap = _ap(repo, "ap-1")
    for i in range(3):
        cid = _client(repo, f"dd:{i}", ap)
        recent = [(NOW - 20000 + k * 300, -82.0) for k in range(10)]
        strong_history = [(NOW - 25200, -55.0)]  # older than the 6 h window, within 7 d
        _rssi(repo, cid, strong_history + recent)
    assert CoverageHoleDetector().evaluate(_ctx(repo)) == []


def test_coverage_hole_unknown_low_coverage(repo: Repository) -> None:
    ap = _ap(repo, "ap-1")
    cid = _client(repo, "ee:1", ap)
    _rssi(repo, cid, [(NOW - 20000 + k * 300, -82.0) for k in range(10)])
    repo.record_poll_run(job="fast_sta", ok=True, ts=NOW - 600)
    assert CoverageHoleDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_coverage_hole_confounder_too_few_samples(repo: Repository) -> None:
    # A weak but tiny sample set is not enough to *declare* a hole -- but it is
    # also not enough to declare the AP *clear*: too few samples is a per-AP
    # coverage gap, so the detector returns the AP as UNKNOWN (freeze), never an
    # empty-list clear that would resolve an open hole issue by absence.
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=21600, interval_s=60)
    ap = _ap(repo, "ap-1")
    cid = _client(repo, "ff:1", ap)
    _rssi(repo, cid, [(NOW - 20000 + k * 300, -82.0) for k in range(3)])  # < min_samples
    result = CoverageHoleDetector().evaluate(_ctx(repo))
    assert isinstance(result, DetectorResult)
    assert result.findings == []
    assert result.unknown_entities == {ap}


# ====================================================================== #
# net.firmware_regression
# ====================================================================== #
_FW_THRESHOLDS = {
    KEY_FIRMWARE_REGRESSION: {
        "lookback_s": 7200,
        "compare_window_s": 3600,
        "settle_s": 600,
    }
}


def _fw_settings(extra=None):
    thresholds = {KEY_FIRMWARE_REGRESSION: dict(_FW_THRESHOLDS[KEY_FIRMWARE_REGRESSION])}
    if extra:
        thresholds[KEY_FIRMWARE_REGRESSION].update(extra)
    return SimpleNamespace(thresholds=thresholds, poll=None)


def _upgrade(repo: Repository, device_id: int, *, old: str, new: str, ts: int) -> None:
    repo.record_state_change(device_id, "firmware", old, ts=NOW - 100_000)
    repo.record_state_change(device_id, "firmware", new, ts=ts)


def _disc(repo: Repository, device_id: int, ts: int, tag: str) -> None:
    repo.record_event(
        ts=ts,
        key="EVT_WU_Disconnected",
        entity_id=None,
        related_entity_id=device_id,
        native_id=f"d-{device_id}-{tag}",
    )


def test_firmware_regression_fires_single_device(repo: Repository) -> None:
    seed_coverage(repo, job="fast_device", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1", "ap-x", model="U6-Pro")
    up_ts = NOW - 1800
    _upgrade(repo, ap, old="6.0.0", new="6.1.0", ts=up_ts)
    # Post window [up+600, now]: several disconnects; pre window: none.
    for k in range(5):
        _disc(repo, ap, NOW - 1000 + k * 100, f"post{k}")
    findings = FirmwareRegressionDetector().evaluate(_ctx(repo, settings=_fw_settings()))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_FIRMWARE_REGRESSION
    assert f.severity is Severity.P2
    assert f.evidence["version"] == "6.1.0"
    assert f.evidence["post_disconnects_per_hour"] > f.evidence["pre_disconnects_per_hour"]


def test_firmware_regression_fleet_escalates_p1(repo: Repository) -> None:
    seed_coverage(repo, job="fast_device", now=NOW, window_s=3600, interval_s=60)
    up_ts = NOW - 1800
    for n in range(2):
        ap = _ap(repo, f"ap-{n}", f"ap-{n}", model="U6-Pro")
        _upgrade(repo, ap, old="6.0.0", new="6.1.0", ts=up_ts)
        for k in range(5):
            _disc(repo, ap, NOW - 1000 + k * 100, f"post{n}-{k}")
    findings = FirmwareRegressionDetector().evaluate(_ctx(repo, settings=_fw_settings()))
    assert len(findings) == 2
    assert all(f.severity is Severity.P1 for f in findings)
    assert all(f.evidence["fleet_wide"] for f in findings)


def test_firmware_regression_confounder_no_upgrade_quiet(repo: Repository) -> None:
    seed_coverage(repo, job="fast_device", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1", model="U6-Pro")
    # Disconnects but no firmware change in lookback -> nothing to attribute.
    for k in range(5):
        _disc(repo, ap, NOW - 1000 + k * 100, f"x{k}")
    assert FirmwareRegressionDetector().evaluate(_ctx(repo, settings=_fw_settings())) == []


def test_firmware_regression_confounder_stable_post_quiet(repo: Repository) -> None:
    # Upgrade happened, but the post-upgrade disconnect rate is not elevated.
    seed_coverage(repo, job="fast_device", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1", model="U6-Pro")
    up_ts = NOW - 1800
    _upgrade(repo, ap, old="6.0.0", new="6.1.0", ts=up_ts)
    # Pre window (3600 s) and post window (1200 s) differ in length, so match the
    # per-hour rate, not the raw count: 3 pre + 1 post => ~3/h each, no regression.
    for k in range(3):
        _disc(repo, ap, up_ts - 300 - k * 100, f"pre{k}")
    _disc(repo, ap, NOW - 300, "post")
    assert FirmwareRegressionDetector().evaluate(_ctx(repo, settings=_fw_settings())) == []


def test_firmware_regression_unknown_low_coverage(repo: Repository) -> None:
    ap = _ap(repo, "ap-1", model="U6-Pro")
    _upgrade(repo, ap, old="6.0.0", new="6.1.0", ts=NOW - 1800)
    for k in range(5):
        _disc(repo, ap, NOW - 1000 + k * 100, f"post{k}")
    repo.record_poll_run(job="fast_device", ok=True, ts=NOW - 60)
    assert FirmwareRegressionDetector().evaluate(_ctx(repo, settings=_fw_settings())) is UNKNOWN


def test_firmware_regression_excludes_settle_window(repo: Repository) -> None:
    # An upgrade still inside its settle window is too early to judge -> no finding.
    seed_coverage(repo, job="fast_device", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1", model="U6-Pro")
    up_ts = NOW - 300  # 300 s ago, settle_s = 600 -> still settling
    _upgrade(repo, ap, old="6.0.0", new="6.1.0", ts=up_ts)
    for k in range(5):
        _disc(repo, ap, NOW - 100 - k * 10, f"post{k}")
    assert FirmwareRegressionDetector().evaluate(_ctx(repo, settings=_fw_settings())) == []
