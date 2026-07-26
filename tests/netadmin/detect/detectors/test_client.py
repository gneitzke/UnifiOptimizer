"""client.flaky / client.dhcp / client.known_pathology on synthetic fixtures.

Each detector gets, per the phase contract: a firing case, a confounder-suppressed
case, and an UNKNOWN-coverage case.
"""

from __future__ import annotations

from types import SimpleNamespace

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
