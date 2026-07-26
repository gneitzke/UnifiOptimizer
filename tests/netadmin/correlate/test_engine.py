"""Behavioural tests for the correlation engine (docs/ARCHITECTURE.md section 17).

False correlation is the enemy, so these lean hard on the conservative cases: two
unrelated faults must never fuse, a symptom that predates its cause must not
attach, and the more-upstream of two candidate roots must win. The canonical
"weak backhaul" story, idempotency, and resolution round it out.
"""

from __future__ import annotations

from typing import Optional

import pytest

from netadmin.correlate.engine import CorrelationEngine, incident_fingerprint
from netadmin.correlate.models import CorrelationConfig, IncidentRole, IncidentState
from netadmin.correlate.topology import TopologyIndex
from netadmin.domain.types import IssueState, Severity
from netadmin.issues.models import Issue
from tests.netadmin.correlate.conftest import TopologyBuilder, make_issue
from tests.netadmin.correlate.fakes import InMemoryCorrelationStore

NOW = 2_000_000
T = 1_000_000  # a baseline "root started here" timestamp


def _run(
    issues: list[Issue],
    topology: TopologyIndex,
    *,
    now: int = NOW,
    config: Optional[CorrelationConfig] = None,
    store: Optional[InMemoryCorrelationStore] = None,
) -> InMemoryCorrelationStore:
    store = store or InMemoryCorrelationStore(issues, topology)
    CorrelationEngine(store, config=config).run(now)
    return store


def _members(store: InMemoryCorrelationStore, incident_id):
    return store.get_incident_members(incident_id)


def _role_of(store: InMemoryCorrelationStore, incident_id, issue_id) -> Optional[str]:
    for m in _members(store, incident_id):
        if m.issue_id == issue_id:
            return m.role
    return None


# --------------------------------------------------------------------------- #
# Canonical case: one incident, correct root, rationales present.
# --------------------------------------------------------------------------- #
def test_canonical_mesh_backhaul_incident(topo: TopologyBuilder) -> None:
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    c1 = topo.add(2, "client", parent_id=ap, name="Thermostat")
    c2 = topo.add(3, "client", parent_id=ap, name="Doorbell")

    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + 10, severity=Severity.P2),
        make_issue(12, "client.flaky", c1, first_seen_ts=T + 20, severity=Severity.P3),
        make_issue(13, "client.flaky", c2, first_seen_ts=T + 30, severity=Severity.P3),
    ]
    store = _run(issues, topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 1, "the four issues form exactly one incident"
    inc = incidents[0]

    # Correct root: the weak backhaul, not a symptom.
    assert inc.root_issue_id == 10
    assert inc.severity is Severity.P2  # max across members
    assert inc.state == IncidentState.OPEN

    members = _members(store, inc.id)
    roles = {m.issue_id: m.role for m in members}
    assert roles == {
        10: IncidentRole.ROOT,
        11: IncidentRole.SYMPTOM,
        12: IncidentRole.SYMPTOM,
        13: IncidentRole.SYMPTOM,
    }
    # Every symptom carries a concrete rule id and a non-empty rationale.
    for m in members:
        if m.role == IncidentRole.SYMPTOM:
            assert m.rule and "->" in m.rule
            assert m.rationale.strip()
    # The coverage hole is linked same-AP; the clients via the parent AP.
    cov = next(m for m in members if m.issue_id == 11)
    assert cov.rule == "mesh_uplink->coverage_hole:same_entity"
    flaky = next(m for m in members if m.issue_id == 12)
    assert flaky.rule == "mesh_uplink->flaky:parent_child"

    # Plain-language surface.
    assert inc.title == "Weak mesh backhaul on AP-Garage-Mesh"
    assert "1 coverage hole" in inc.summary
    assert "2 client dropouts" in inc.summary


# --------------------------------------------------------------------------- #
# Temporal guard: a symptom cannot predate its cause beyond the slack window.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "symptom_offset, expect_linked",
    [
        (60, True),  # symptom appears just after the root -> attaches
        (-60, True),  # small ordering jitter within the slack window -> attaches
        (-7200, False),  # symptom began two hours before the root -> spurious, dropped
    ],
    ids=["after", "within-slack", "predates"],
)
def test_temporal_guard(topo: TopologyBuilder, symptom_offset: int, expect_linked: bool) -> None:
    ap = topo.add(1, "ap", name="AP-Attic")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + symptom_offset),
    ]
    store = _run(issues, topo.build(), config=CorrelationConfig(temporal_slack_s=900))

    incidents = store.all_incidents()
    if expect_linked:
        assert len(incidents) == 1
        assert _role_of(store, incidents[0].id, 11) == IncidentRole.SYMPTOM
    else:
        # Two standalone incidents-of-one; the coverage hole is never a symptom.
        assert len(incidents) == 2
        for inc in incidents:
            assert _role_of(store, inc.id, 11) != IncidentRole.SYMPTOM
            assert len(_members(store, inc.id)) == 1


# --------------------------------------------------------------------------- #
# Root priority: the more upstream of two candidate roots wins.
# --------------------------------------------------------------------------- #
def test_root_priority_firmware_beats_ap_wifi(topo: TopologyBuilder) -> None:
    """A firmware regression outranks the AP's own mesh issue for a client dropout."""
    ap = topo.add(1, "ap", name="AP-Office")
    client = topo.add(2, "client", parent_id=ap, name="Laptop")
    issues = [
        make_issue(10, "net.firmware_regression", ap, first_seen_ts=T),
        make_issue(11, "wifi.mesh_uplink", ap, first_seen_ts=T),
        make_issue(12, "client.flaky", client, first_seen_ts=T + 10),
    ]
    store = _run(issues, topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.root_issue_id == 10  # firmware regression, not the mesh issue

    flaky = next(m for m in _members(store, inc.id) if m.issue_id == 12)
    assert flaky.role == IncidentRole.SYMPTOM
    # A firmware regression's blanket subtree sweep must not directly claim a
    # volatile client (it roams; its flakiness may be its own NIC). The client
    # attaches via the concrete mesh-backhaul rule instead; that mesh issue rolls
    # up under the firmware regression, so the client still lands in firmware's
    # incident -- but through a real causal chain, not a wildcard sweep.
    assert flaky.rule == "mesh_uplink->flaky:parent_child"
    # The AP's mesh issue is itself absorbed under the firmware regression.
    assert _role_of(store, inc.id, 11) == IncidentRole.SYMPTOM


def test_root_priority_wired_feeder_beats_ap_wifi(topo: TopologyBuilder) -> None:
    """The doc's headline: a flapping uplink port beats the AP's own wifi issue."""
    sw = topo.add(1, "switch", name="SW-Core")
    port = topo.add(2, "port", parent_id=sw, name="SW-Core:5")
    ap = topo.add(3, "ap", name="AP-Den")
    client = topo.add(4, "client", parent_id=ap, name="TV")
    topo.feeds(port, ap)  # the flapping port feeds the AP

    issues = [
        make_issue(10, "wired.port_flapping", port, first_seen_ts=T),
        make_issue(11, "wifi.mesh_uplink", ap, first_seen_ts=T),
        make_issue(12, "client.flaky", client, first_seen_ts=T + 10),
    ]
    store = _run(issues, topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.root_issue_id == 10  # the flapping port is the root

    flaky = next(m for m in _members(store, inc.id) if m.issue_id == 12)
    assert flaky.rule.startswith("port_flapping->")
    assert flaky.rule.endswith(":feeds")


# --------------------------------------------------------------------------- #
# Conservatism: unrelated faults on different segments never fuse.
# --------------------------------------------------------------------------- #
def test_unrelated_issues_stay_separate(topo: TopologyBuilder) -> None:
    ap_a = topo.add(1, "ap", name="AP-A")
    client_a = topo.add(2, "client", parent_id=ap_a, name="Phone-A")
    ap_b = topo.add(3, "ap", name="AP-B")
    client_b = topo.add(4, "client", parent_id=ap_b, name="Phone-B")

    issues = [
        make_issue(10, "client.flaky", client_a, first_seen_ts=T),
        make_issue(11, "client.flaky", client_b, first_seen_ts=T),
    ]
    store = _run(issues, topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 2, "two unrelated dropouts are never fused"
    for inc in incidents:
        members = _members(store, inc.id)
        assert len(members) == 1
        assert members[0].role == IncidentRole.ROOT
    # Distinct identities, keyed by each issue's own fingerprint.
    assert {i.fingerprint for i in incidents} == {
        incident_fingerprint("client.flaky|2|10"),
        incident_fingerprint("client.flaky|4|11"),
    }


def test_device_attributed_flaky_client_does_not_attach_to_ap_root(
    topo: TopologyBuilder,
) -> None:
    """A client flaky across many APs is its own radio, not the AP's backhaul.

    The ``client.flaky`` detector already ruled out a single-AP cause
    (``attribution == "device"``, ``attributed_ap`` empty, confounder
    ``many_aps_rules_out_single_ap_fault``). Even though it is currently
    associated with a mesh-uplink AP, pinning it on that AP's backhaul would be a
    wrong attribution -- it must stay a standalone incident-of-one.
    """
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    roamer = topo.add(2, "client", parent_id=ap, name="RoamingPhone")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "client.flaky",
            roamer,
            first_seen_ts=T + 50,
            evidence={"attribution": "device", "ap_count": 4},
        ),
    ]
    store = _run(issues, topo.build())

    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 11}, "the device-fault client is never fused in"
    # The mesh incident holds only its own root; the flaky client stands alone.
    assert {m.issue_id for m in _members(store, incidents[10].id)} == {10}
    assert _role_of(store, incidents[11].id, 11) == IncidentRole.ROOT
    assert len(_members(store, incidents[11].id)) == 1


def test_flaky_client_attributed_to_other_ap_stays_standalone(
    topo: TopologyBuilder,
) -> None:
    """A client dropping on AP-Y but now roamed onto AP-X is not pinned to AP-X.

    The ``client.flaky`` detector recorded ``attributed_ap = "AP-Y"`` (that is
    where the drops happened). The client has since roamed and its topology
    parent is AP-X, which has an open mesh-uplink issue. Attaching the AP-Y-caused
    dropout to AP-X's backhaul would be a wrong root AND a fusion of two unrelated
    faults -- refused; the client stands alone.
    """
    ap_x = topo.add(1, "ap", name="AP-X")
    ap_y = topo.add(2, "ap", name="AP-Y")
    doorbell = topo.add(3, "client", parent_id=ap_x, name="Doorbell")  # currently on AP-X
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap_x, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "client.flaky",
            doorbell,
            first_seen_ts=T + 50,
            evidence={"attribution": "device_or_deadspot", "attributed_ap": "AP-Y"},
        ),
    ]
    store = _run(issues, topo.build())

    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 11}, "the AP-Y dropout is never fused into AP-X's mesh"
    assert {m.issue_id for m in _members(store, incidents[10].id)} == {10}
    assert _role_of(store, incidents[11].id, 11) == IncidentRole.ROOT
    _ = ap_y  # AP-Y has no issue of its own; the client simply stands alone


def test_flaky_client_attributed_to_current_ap_still_attaches(topo: TopologyBuilder) -> None:
    """The guard is exact: when the recorded AP IS the mesh AP, the link holds."""
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    client = topo.add(2, "client", parent_id=ap, name="Doorbell")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "client.flaky",
            client,
            first_seen_ts=T + 50,
            evidence={"attribution": "ap_fault", "attributed_ap": "AP-Garage-Mesh"},
        ),
    ]
    store = _run(issues, topo.build())
    incidents = store.all_incidents()
    assert len(incidents) == 1
    assert _role_of(store, incidents[0].id, 11) == IncidentRole.SYMPTOM


def test_sticky_client_glued_to_other_ap_not_attributed(topo: TopologyBuilder) -> None:
    """A sticky client's tx_power root must be the AP it is glued to, not its
    momentary parent. ``current_ap`` (a MAC) names the real AP."""
    ap_loud = topo.add(1, "ap", name="AP-Loud", native_id="aa:bb:cc:00:00:01")
    ap_other = topo.add(2, "ap", name="AP-Other", native_id="aa:bb:cc:00:00:02")
    client = topo.add(3, "client", parent_id=ap_loud, name="Speaker")  # parent = the loud AP
    issues = [
        # The loud AP is AP-Other, but the sticky client is glued to AP-Loud.
        make_issue(10, "wifi.tx_power_loud", ap_other, first_seen_ts=T),
        make_issue(
            11,
            "wifi.sticky_client",
            client,
            first_seen_ts=T + 50,
            evidence={"current_ap": "aa:bb:cc:00:00:01"},
        ),
    ]
    store = _run(issues, topo.build())
    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 11}, "sticky client is not pinned to an unrelated loud AP"


def test_firmware_regression_does_not_sweep_volatile_client(topo: TopologyBuilder) -> None:
    """A blanket firmware subtree sweep must not claim a client child.

    A client currently on the upgraded AP, with its own attributed dropout on that
    same AP (so the attributed-AP guard is satisfied), still does not attach to the
    firmware regression: clients roam and carry their own faults, so the wildcard
    subtree rule is restricted to the device + its radios/ports.
    """
    ap = topo.add(1, "ap", name="AP-Office")
    laptop = topo.add(2, "client", parent_id=ap, name="Laptop")
    issues = [
        make_issue(10, "net.firmware_regression", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "client.flaky",
            laptop,
            first_seen_ts=T + 100,
            evidence={"attribution": "device_or_deadspot", "attributed_ap": "AP-Office"},
        ),
    ]
    store = _run(issues, topo.build())
    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 11}, "the client is not swept under the firmware regression"
    assert {m.issue_id for m in _members(store, incidents[10].id)} == {10}


def test_local_dns_slow_not_attributed_to_isp(topo: TopologyBuilder) -> None:
    """A local-resolver DNS fault must not be fused under the ISP degradation."""
    gw = topo.add(1, "gateway", name="UDM")
    issues = [
        make_issue(10, "wan.isp_degraded", gw, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "wan.dns_slow",
            gw,
            first_seen_ts=T + 10,
            severity=Severity.P2,
            evidence={"localised": "local"},  # resolver slow, public anchor fine
        ),
    ]
    store = _run(issues, topo.build())
    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 11}, "local DNS fault stays its own incident, not an ISP symptom"


def test_external_airtime_saturation_not_rooted_on_mesh(topo: TopologyBuilder) -> None:
    """Airtime saturated by an external co-channel neighbor is not the mesh's fault."""
    ap = topo.add(1, "ap", name="AP-Den")
    radio = topo.add(2, "radio", parent_id=ap, name="AP-Den 5G")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "wifi.airtime_saturation",
            radio,
            first_seen_ts=T + 10,
            severity=Severity.P2,
            evidence={"dominant_source": "non_self"},  # external interferer, not our load
        ),
    ]
    store = _run(issues, topo.build())
    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 11}, "external airtime saturation is not a mesh-backhaul symptom"


def test_self_airtime_saturation_still_attaches_to_mesh(topo: TopologyBuilder) -> None:
    """Self-generated airtime saturation DOES track the failing backhaul."""
    ap = topo.add(1, "ap", name="AP-Den")
    radio = topo.add(2, "radio", parent_id=ap, name="AP-Den 5G")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "wifi.airtime_saturation",
            radio,
            first_seen_ts=T + 10,
            severity=Severity.P2,
            evidence={"dominant_source": "self"},
        ),
    ]
    store = _run(issues, topo.build())
    incidents = store.all_incidents()
    assert len(incidents) == 1
    assert _role_of(store, incidents[0].id, 11) == IncidentRole.SYMPTOM


def test_single_ap_flaky_client_still_attaches(topo: TopologyBuilder) -> None:
    """The guard is narrow: a client flaky on *one* AP (deadspot/ap_fault) still
    attributes to that AP's root -- only ``device`` (many-AP) is held back."""
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    client = topo.add(2, "client", parent_id=ap, name="Doorbell")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(
            11,
            "client.flaky",
            client,
            first_seen_ts=T + 50,
            evidence={"attribution": "device_or_deadspot", "attributed_ap": "AP-Garage-Mesh"},
        ),
    ]
    store = _run(issues, topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 1
    assert _role_of(store, incidents[0].id, 11) == IncidentRole.SYMPTOM


def test_two_roots_do_not_cross_attribute_symptoms(topo: TopologyBuilder) -> None:
    """Two identical faults on two APs: each coverage hole stays under its own AP."""
    ap_a = topo.add(1, "ap", name="AP-A")
    ap_b = topo.add(2, "ap", name="AP-B")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap_a, first_seen_ts=T),
        make_issue(11, "net.coverage_hole", ap_a, first_seen_ts=T + 5),
        make_issue(20, "wifi.mesh_uplink", ap_b, first_seen_ts=T),
        make_issue(21, "net.coverage_hole", ap_b, first_seen_ts=T + 5),
    ]
    store = _run(issues, topo.build())

    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    assert set(incidents) == {10, 20}
    # AP-A's coverage hole is in AP-A's incident only.
    assert _role_of(store, incidents[10].id, 11) == IncidentRole.SYMPTOM
    assert _role_of(store, incidents[10].id, 21) is None
    assert _role_of(store, incidents[20].id, 21) == IncidentRole.SYMPTOM
    assert _role_of(store, incidents[20].id, 11) is None


# --------------------------------------------------------------------------- #
# Unattributed issues become incidents-of-one.
# --------------------------------------------------------------------------- #
def test_unattributed_issue_is_incident_of_one(topo: TopologyBuilder) -> None:
    ap = topo.add(1, "ap", name="AP-Shed")
    client = topo.add(2, "client", parent_id=ap, name="Sensor")
    # A lone client dropout with no root cause present.
    store = _run([make_issue(10, "client.flaky", client, first_seen_ts=T)], topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.root_issue_id == 10
    assert inc.summary == ""  # no causal story for an incident-of-one
    members = _members(store, inc.id)
    assert len(members) == 1 and members[0].role == IncidentRole.ROOT


def test_wan_internal_symptoms_group_on_gateway(topo: TopologyBuilder) -> None:
    gw = topo.add(1, "gateway", name="UDM")
    issues = [
        make_issue(10, "wan.isp_degraded", gw, first_seen_ts=T, severity=Severity.P1),
        # DNS slowness localised UPSTREAM (public anchor also slow) -> genuinely a
        # symptom of the ISP; a local-resolver fault would not attach (see below).
        make_issue(
            11,
            "wan.dns_slow",
            gw,
            first_seen_ts=T + 10,
            severity=Severity.P2,
            evidence={"localised": "upstream"},
        ),
        make_issue(12, "wan.bufferbloat", gw, first_seen_ts=T + 10, severity=Severity.P2),
    ]
    store = _run(issues, topo.build())

    incidents = store.all_incidents()
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.root_issue_id == 10
    assert inc.severity is Severity.P1
    assert "in that cell" not in inc.summary  # WAN reach is not an AP cell
    assert _role_of(store, inc.id, 11) == IncidentRole.SYMPTOM
    assert _role_of(store, inc.id, 12) == IncidentRole.SYMPTOM


# --------------------------------------------------------------------------- #
# Idempotency: a re-run preserves incident identity (id) by root fingerprint.
# --------------------------------------------------------------------------- #
def test_idempotent_rerun_same_incident_id(topo: TopologyBuilder) -> None:
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    client = topo.add(2, "client", parent_id=ap, name="Thermostat")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + 10),
        make_issue(12, "client.flaky", client, first_seen_ts=T + 20),
    ]
    topo_index = topo.build()
    store = InMemoryCorrelationStore(issues, topo_index)
    engine = CorrelationEngine(store)

    engine.run(NOW)
    first = store.all_incidents()
    assert len(first) == 1
    first_id = first[0].id
    first_first_seen = first[0].first_seen_ts

    # Re-run a later pass with the same open set.
    engine.run(NOW + 300)
    again = store.all_incidents()
    assert len(again) == 1, "re-running does not duplicate the incident"
    assert again[0].id == first_id, "identity preserved by root fingerprint"
    assert again[0].first_seen_ts == first_first_seen, "age keeps counting"
    assert again[0].last_seen_ts == NOW + 300, "last_seen advances"


# --------------------------------------------------------------------------- #
# Lifecycle: an incident resolves when its members resolve.
# --------------------------------------------------------------------------- #
def test_incident_resolves_when_members_resolve(topo: TopologyBuilder) -> None:
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    client = topo.add(2, "client", parent_id=ap, name="Thermostat")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + 10),
        make_issue(12, "client.flaky", client, first_seen_ts=T + 20),
    ]
    store = InMemoryCorrelationStore(issues, topo.build())
    engine = CorrelationEngine(store)

    engine.run(NOW)
    inc_id = store.all_incidents()[0].id

    # Everything clears (the backhaul was fixed): the issues resolve and leave the
    # correlatable set.
    for issue in issues:
        issue.state = IssueState.RESOLVED
    store.set_issues(issues)

    engine.run(NOW + 600)
    incidents = store.all_incidents()
    assert len(incidents) == 1
    resolved = incidents[0]
    assert resolved.id == inc_id  # same row, now closed
    assert resolved.state == IncidentState.RESOLVED
    assert resolved.resolved_ts == NOW + 600


def test_incident_of_one_resolves_when_issue_resolves(topo: TopologyBuilder) -> None:
    ap = topo.add(1, "ap", name="AP-Shed")
    client = topo.add(2, "client", parent_id=ap, name="Sensor")
    issue = make_issue(10, "client.flaky", client, first_seen_ts=T)
    store = InMemoryCorrelationStore([issue], topo.build())
    engine = CorrelationEngine(store)

    engine.run(NOW)
    assert store.all_incidents()[0].state == IncidentState.OPEN

    issue.state = IssueState.RESOLVED
    store.set_issues([issue])
    engine.run(NOW + 60)

    inc = store.all_incidents()[0]
    assert inc.state == IncidentState.RESOLVED
    assert inc.resolved_ts == NOW + 60


def test_symptom_resolving_shrinks_but_keeps_incident_open(topo: TopologyBuilder) -> None:
    """One symptom clears while the root persists: incident stays, membership shrinks."""
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    client = topo.add(2, "client", parent_id=ap, name="Thermostat")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + 10),
        make_issue(12, "client.flaky", client, first_seen_ts=T + 20),
    ]
    store = InMemoryCorrelationStore(issues, topo.build())
    engine = CorrelationEngine(store)
    engine.run(NOW)
    inc_id = store.all_incidents()[0].id

    # The client recovers; the backhaul + coverage hole persist.
    issues[2].state = IssueState.RESOLVED
    store.set_issues(issues)
    engine.run(NOW + 300)

    incidents = store.all_incidents()
    assert len(incidents) == 1
    assert incidents[0].id == inc_id
    assert incidents[0].state == IncidentState.OPEN
    member_ids = {m.issue_id for m in _members(store, inc_id)}
    assert member_ids == {10, 11}  # the resolved client dropped out


def test_root_resolves_while_symptom_persists_keeps_incident_open(
    topo: TopologyBuilder,
) -> None:
    """§17: an incident resolves only when ALL members resolve.

    The operator fixes the backhaul (root), but a coverage hole in that cell is
    still open. The incident must stay open under its own identity -- same id,
    same first_seen (the multi-day clock never resets) -- with the resolved root
    still shown, and the lingering symptom must NOT re-appear as a brand-new
    incident-of-one.
    """
    ap = topo.add(1, "ap", name="AP-Garage-Mesh")
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + 10, severity=Severity.P2),
    ]
    store = InMemoryCorrelationStore(issues, topo.build())
    engine = CorrelationEngine(store)
    engine.run(NOW)
    first = store.all_incidents()
    assert len(first) == 1
    inc_id = first[0].id
    first_seen = first[0].first_seen_ts

    # Root fixed; the coverage hole persists.
    issues[0].state = IssueState.RESOLVED
    store.set_issues(issues)
    engine.run(NOW + 5000)

    incidents = store.all_incidents()
    assert len(incidents) == 1, "the symptom does not spawn a second, fresh incident"
    inc = incidents[0]
    assert inc.id == inc_id, "identity preserved -- not a new incident-of-one"
    assert inc.state == IncidentState.OPEN, "still open: a member is still active"
    assert inc.first_seen_ts == first_seen, "age keeps counting; the clock does not reset"
    assert inc.last_seen_ts == NOW + 5000
    member_ids = {m.issue_id for m in _members(store, inc_id)}
    assert member_ids == {10, 11}, "root shown resolved (10) + surviving symptom (11)"

    # Finally the symptom resolves too -> the incident resolves.
    issues[1].state = IssueState.RESOLVED
    store.set_issues(issues)
    engine.run(NOW + 9000)
    done = store.all_incidents()
    assert len(done) == 1 and done[0].id == inc_id
    assert done[0].state == IncidentState.RESOLVED
    assert done[0].resolved_ts == NOW + 9000


# --------------------------------------------------------------------------- #
# Temporal forward window (opt-in): a chronic root cannot absorb a much-later,
# independently-caused symptom.
# --------------------------------------------------------------------------- #
def test_forward_window_rejects_much_later_symptom(topo: TopologyBuilder) -> None:
    ap = topo.add(1, "ap", name="AP-Garage")
    # Root open for ~2 months; a brand-new coverage hole appears on the same AP.
    two_months = 60 * 86_400
    issues = [
        make_issue(10, "wifi.mesh_uplink", ap, first_seen_ts=T, severity=Severity.P2),
        make_issue(11, "net.coverage_hole", ap, first_seen_ts=T + two_months, severity=Severity.P2),
    ]

    # Default (one-sided §17 guard): the same-AP link still forms.
    linked = _run(issues, topo.build())
    assert len(linked.all_incidents()) == 1

    # With a forward window, the much-later onset is rejected -> two incidents.
    windowed = _run(
        issues,
        topo.build(),
        config=CorrelationConfig(temporal_slack_s=900, temporal_forward_window_s=7 * 86_400),
    )
    incidents = {i.root_issue_id: i for i in windowed.all_incidents()}
    assert set(incidents) == {10, 11}
    assert len(_members(windowed, incidents[10].id)) == 1
