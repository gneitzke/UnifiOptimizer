"""client.flaky / client.dhcp / client.known_pathology on synthetic fixtures.

Each detector gets, per the phase contract: a firing case, a confounder-suppressed
case, and an UNKNOWN-coverage case.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from netadmin import config
from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.client import (
    KEY_DHCP,
    KEY_FLAKY,
    KEY_KNOWN_PATHOLOGY,
    DhcpClientDetector,
    FlakyClientDetector,
    KnownPathologyDetector,
)
from netadmin.detect.engine import UNKNOWN
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, Severity
from netadmin.store.repository import Repository, SampleReading
from tests.netadmin.detect.support import FakeBaselines, seed_coverage

NOW = 4_000_000


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep KB resolution off the developer's real ``./data``.

    ``config.DATA_DIR`` is ``Path.cwd() / "data"``, and docs/DEVICE_DATABASE.md now
    tells operators to put their own ``wifi_device_capabilities.json`` there. Without
    this, anyone who follows that advice on their dev box gets unrelated failures in
    the known_pathology tests. Pinning it at an empty tmp dir also makes "no override"
    mean the *packaged baseline*, specifically.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")


def _ctx(repo: Repository, *, settings=None, now: int = NOW) -> DetectorContext:
    return DetectorContext(
        repo=repo,
        baselines=FakeBaselines(),
        now_ts=now,
        site_id="default",
        settings=settings,
    )


def _client(
    repo: Repository,
    *,
    mac: str,
    name: str | None = None,
    ap_id: int | None = None,
    ip: str | None = None,
    oui: str | None = None,
    first_seen: int = NOW - 100_000,
) -> int:
    eid = repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=mac,
            site_id="default",
            name=name,
            parent_id=ap_id,
            meta={"oui": oui} if oui else {},
            first_seen_ts=first_seen,
        ),
        ts=NOW,
    )
    if ip is not None:
        repo.record_state_change(eid, "ip", ip, ts=NOW - 200)
    if ap_id is not None:
        repo.record_state_change(eid, "ap_mac", f"ap-of-{ap_id}", ts=NOW - 200)
    return eid


def _ap(repo: Repository, native_id: str, name: str | None = None) -> int:
    return repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=native_id, site_id="default", name=name),
        ts=NOW,
    )


def _disconnect(repo: Repository, client_id: int, ap_id: int, ts: int, *, reason=None) -> None:
    data = {"reason": reason} if reason is not None else {}
    repo.record_event(
        ts=ts,
        key="EVT_WU_Disconnected",
        entity_id=client_id,
        related_entity_id=ap_id,
        native_id=f"disc-{client_id}-{ts}",
        data=data,
    )


# ====================================================================== #
# client.flaky
# ====================================================================== #
def test_flaky_fires_and_attributes_ap_fault(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1", "ap-lobby")
    # 3 clients, each with many pathological disconnects on the same AP -> ap_fault.
    for i in range(3):
        cid = _client(repo, mac=f"cc:{i}", ap_id=ap)
        for k in range(6):
            _disconnect(repo, cid, ap, NOW - 600 - k * 10, reason=1)

    findings = FlakyClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 3
    f = findings[0]
    assert f.detector_key == KEY_FLAKY
    assert f.evidence["attribution"] == "ap_fault"
    assert f.severity is Severity.P2
    assert "attributed_ap" in f.evidence


def test_flaky_device_attribution_many_aps(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    ap1, ap2 = _ap(repo, "ap-1"), _ap(repo, "ap-2")
    cid = _client(repo, mac="dd:1", ap_id=ap1)
    for k in range(4):
        _disconnect(repo, cid, ap1, NOW - 600 - k * 10, reason=1)
        _disconnect(repo, cid, ap2, NOW - 700 - k * 10, reason=1)
    findings = FlakyClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["attribution"] == "device"
    assert findings[0].severity is Severity.P3


def test_flaky_confounder_benign_roams_suppressed(repo: Repository) -> None:
    # Reason code 8 (leaving BSS) is benign roam churn: weighted down so a mobile
    # client that roams a lot never reads as flaky.
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1")
    cid = _client(repo, mac="ee:1", ap_id=ap)
    for k in range(20):
        _disconnect(repo, cid, ap, NOW - 100 - k * 10, reason=8)
    assert FlakyClientDetector().evaluate(_ctx(repo)) == []


def test_flaky_unknown_on_low_coverage(repo: Repository) -> None:
    ap = _ap(repo, "ap-1")
    cid = _client(repo, mac="ff:1", ap_id=ap)
    for k in range(6):
        _disconnect(repo, cid, ap, NOW - 100 - k * 10, reason=1)
    # Only 2 sta polls in a 3600 s window -> coverage far below 0.5.
    repo.record_poll_run(job="fast_sta", ok=True, ts=NOW - 120)
    repo.record_poll_run(job="fast_sta", ok=True, ts=NOW - 60)
    assert FlakyClientDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# client.dhcp
# ====================================================================== #
def test_dhcp_apipa_single_is_p3(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW)
    _client(repo, mac="a1", ip="169.254.10.5")
    findings = DhcpClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_DHCP
    assert f.severity is Severity.P3
    assert f.evidence["fault"] == "apipa_self_assigned"
    assert f.evidence["pool_utilization"] == "unavailable_no_unifi_gateway"


def test_dhcp_network_wide_is_p1(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW)
    for i in range(3):
        _client(repo, mac=f"b{i}", ip="169.254.0.9")
    findings = DhcpClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 3
    assert all(f.severity is Severity.P1 for f in findings)
    assert findings[0].evidence["network_wide"] is True


def test_dhcp_confounder_healthy_ip_quiet(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW)
    _client(repo, mac="c1", ip="192.168.1.50", ap_id=_ap(repo, "ap-1"))
    assert DhcpClientDetector().evaluate(_ctx(repo)) == []


def test_dhcp_unknown_on_low_coverage(repo: Repository) -> None:
    _client(repo, mac="d1", ip="169.254.1.1")
    repo.record_poll_run(job="fast_sta", ok=True, ts=NOW - 60)
    assert DhcpClientDetector().evaluate(_ctx(repo)) is UNKNOWN


def _unifi_gateway_with_health(repo: Repository, native_id: str = "gw-1") -> int:
    """A UniFi gateway that reports WAN health (a ``wan_latency`` series), so the
    detector's ``_has_unifi_gateway`` gate sees an authoritative DHCP/L3 authority.
    """
    gid = repo.upsert_entity(
        Entity(entity_type=EntityType.GATEWAY, native_id=native_id, site_id="default"),
        ts=NOW,
    )
    repo.record_samples([SampleReading(gid, "wan_latency", NOW - 60, 10.0)])
    return gid


def test_dhcp_association_without_ip_noops_without_gateway(repo: Repository) -> None:
    # Regression: on a gateway-less site (this deployment) an associated client with
    # no controller-reported IP is absent L3 telemetry, not a DHCP failure. The
    # association_without_ip arm must no-op — without this gate three static/wired
    # devices with no learned IP falsely escalated to a network-wide P1.
    seed_coverage(repo, job="fast_sta", now=NOW)
    _client(repo, mac="noip-1", ap_id=_ap(repo, "ap-1"))  # associated, no IP recorded
    assert DhcpClientDetector().evaluate(_ctx(repo)) == []


def test_dhcp_association_without_ip_fires_with_gateway(repo: Repository) -> None:
    # With a UniFi gateway (authoritative lease table) the same no-IP association is
    # a genuine DHCP failure and fires P3 for the single client.
    seed_coverage(repo, job="fast_sta", now=NOW)
    _unifi_gateway_with_health(repo)
    _client(repo, mac="noip-2", ap_id=_ap(repo, "ap-1"))  # associated, no IP recorded
    findings = DhcpClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["fault"] == "association_without_ip"
    assert findings[0].severity is Severity.P3


# ====================================================================== #
# client.known_pathology
# ====================================================================== #
def test_known_pathology_iot_pmf_fires(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1")
    cid = _client(repo, mac="io:1", name="ESP32-sensor", ap_id=ap)
    for k in range(4):
        _disconnect(repo, cid, ap, NOW - 100 - k * 10, reason=15)
    findings = KnownPathologyDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_KNOWN_PATHOLOGY
    assert f.evidence["pathology"] == "iot_pmf_11r"
    assert f.evidence["wlan_config"] == "not_verified"


def test_known_pathology_ios_roam_fires(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    cid = _client(repo, mac="ap:1", name="Johns-iPhone-15")
    # roam_count is a counter; feed cumulative readings so deltas sum to >= 5.
    pts = [(NOW - 3000 + i * 60, float(i)) for i in range(0, 8)]
    repo.record_samples(SampleReading(cid, "roam_count", ts, v) for ts, v in pts)
    findings = KnownPathologyDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["pathology"] == "ios_aggressive_roam"


def test_known_pathology_confounder_iot_without_symptom_quiet(repo: Repository) -> None:
    # A 2.4-only IoT device that is NOT disconnecting is not a pathology: symptom
    # required, never inventory-only.
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    _client(repo, mac="io:2", name="esp8266-plug", ap_id=_ap(repo, "ap-1"))
    assert KnownPathologyDetector().evaluate(_ctx(repo)) == []


def test_known_pathology_unknown_on_low_coverage(repo: Repository) -> None:
    ap = _ap(repo, "ap-1")
    cid = _client(repo, mac="io:3", name="ESP32", ap_id=ap)
    for k in range(4):
        _disconnect(repo, cid, ap, NOW - 100 - k * 10, reason=15)
    repo.record_poll_run(job="fast_sta", ok=True, ts=NOW - 60)
    assert KnownPathologyDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_known_pathology_threshold_override(repo: Repository) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1")
    cid = _client(repo, mac="io:4", name="ESP32", ap_id=ap)
    _disconnect(repo, cid, ap, NOW - 100, reason=15)  # only 1 disconnect
    settings = SimpleNamespace(
        thresholds={KEY_KNOWN_PATHOLOGY: {"iot_disconnect_min": 1}}, poll=None
    )
    findings = KnownPathologyDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1


def _iot_client_with_disconnects(repo: Repository, *, mac: str, name: str) -> None:
    seed_coverage(repo, job="fast_sta", now=NOW, window_s=3600, interval_s=60)
    ap = _ap(repo, "ap-1")
    cid = _client(repo, mac=mac, name=name, ap_id=ap)
    for k in range(4):
        _disconnect(repo, cid, ap, NOW - 100 - k * 10, reason=15)


def test_known_pathology_kb_path_override_is_honoured(repo: Repository, tmp_path: Path) -> None:
    """An explicit kb_path outranks the default location.

    The device name matches nothing in the shipped KB, so a finding here can only
    come from the override actually being read.
    """
    custom_kb = tmp_path / "custom_kb.json"
    custom_kb.write_text(json.dumps({"known_2.4ghz_only": {"patterns": ["widgetron"]}}))
    _iot_client_with_disconnects(repo, mac="io:5", name="Widgetron-9000")

    settings = SimpleNamespace(
        thresholds={KEY_KNOWN_PATHOLOGY: {"kb_path": str(custom_kb)}}, poll=None
    )
    findings = KnownPathologyDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1
    assert findings[0].evidence["pathology"] == "iot_pmf_11r"


def test_known_pathology_degrades_quietly_when_the_kb_is_missing(
    repo: Repository, tmp_path: Path
) -> None:
    """KB-empty must return no findings rather than raise -- but must SAY so.

    The iot_pmf_11r branch is driven entirely by the KB's known_2.4ghz_only list,
    so with no KB it goes quiet. That silence is the whole bug this commit fixes,
    so the warning is the early-warning system and is asserted, not assumed.

    Captured with a handler attached to the module logger rather than ``caplog``:
    ``netadmin/logging.py`` sets ``propagate = False`` on the ``netadmin`` root so
    the package does not double-log, and ``caplog`` attaches to the *root* logger,
    so it sees nothing once logging has been configured. Configuration happens on
    first ``get_logger`` call, which makes a caplog-based assertion here pass or
    fail on test ordering. Same workaround as
    ``tests/netadmin/server/test_auth.py::test_unauthenticated_startup_logs_warning``.
    """
    _iot_client_with_disconnects(repo, mac="io:6", name="ESP32-sensor")
    missing = tmp_path / "absent.json"

    messages: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("netadmin.detect.device_kb")
    handler = _Cap()
    logger.addHandler(handler)
    try:
        settings = SimpleNamespace(
            thresholds={KEY_KNOWN_PATHOLOGY: {"kb_path": str(missing)}}, poll=None
        )
        assert KnownPathologyDetector().evaluate(_ctx(repo, settings=settings)) == []
    finally:
        logger.removeHandler(handler)

    text = "\n".join(messages)
    assert str(missing) in text
    # The remediation matters: a typo'd kb_path otherwise leaves no way to know
    # a working baseline exists.
    assert "packaged baseline" in text


def test_known_pathology_retries_a_failed_kb_instead_of_caching_it(
    repo: Repository, tmp_path: Path
) -> None:
    """A KB that fails once must not disable the branch until the daemon restarts.

    The daemon runs for months; a half-written save or a mount blip would
    otherwise poison the detector permanently, reporting "no IoT pathologies"
    with total confidence.
    """
    kb_path = tmp_path / "later.json"
    _iot_client_with_disconnects(repo, mac="io:a", name="ESP32-sensor")
    settings = SimpleNamespace(
        thresholds={KEY_KNOWN_PATHOLOGY: {"kb_path": str(kb_path)}}, poll=None
    )
    detector = KnownPathologyDetector()
    assert detector.evaluate(_ctx(repo, settings=settings)) == []  # nothing there yet

    kb_path.write_text(json.dumps({"known_2.4ghz_only": {"patterns": ["esp32"]}}))
    findings = detector.evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1, "the same detector instance must pick up the repaired KB"


def test_known_pathology_survives_a_malformed_kb_section(repo: Repository, tmp_path: Path) -> None:
    """A hand-edited section of the wrong shape must not take the detector offline.

    ``{"known_2.4ghz_only": [...]}`` (a bare list rather than an object with a
    ``patterns`` key) used to raise AttributeError on ``list.get``; the engine
    isolates a raising detector, so one typo cost the whole pass.
    """
    broken_kb = tmp_path / "broken_kb.json"
    broken_kb.write_text(json.dumps({"known_2.4ghz_only": ["esp32", "tuya"]}))
    _iot_client_with_disconnects(repo, mac="io:8", name="ESP32-sensor")

    settings = SimpleNamespace(
        thresholds={KEY_KNOWN_PATHOLOGY: {"kb_path": str(broken_kb)}}, poll=None
    )
    assert KnownPathologyDetector().evaluate(_ctx(repo, settings=settings)) == []


def test_known_pathology_ignores_string_valued_patterns(repo: Repository, tmp_path: Path) -> None:
    """A string is iterable: 'e' would otherwise match nearly every device name."""
    broken_kb = tmp_path / "string_patterns.json"
    broken_kb.write_text(json.dumps({"known_2.4ghz_only": {"patterns": "esp32"}}))
    _iot_client_with_disconnects(repo, mac="io:9", name="Johns-MacBook-Pro")

    settings = SimpleNamespace(
        thresholds={KEY_KNOWN_PATHOLOGY: {"kb_path": str(broken_kb)}}, poll=None
    )
    assert KnownPathologyDetector().evaluate(_ctx(repo, settings=settings)) == []


def test_known_pathology_finds_the_default_kb_with_no_override(repo: Repository) -> None:
    """The counterpart: with no kb_path configured, the shipped KB must be found.

    This is the regression guard -- an ESP32 fires only if the default resolution
    landed on a real KB file.
    """
    _iot_client_with_disconnects(repo, mac="io:7", name="ESP32-sensor")

    findings = KnownPathologyDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["device_class"] == "known_2.4ghz_only"
