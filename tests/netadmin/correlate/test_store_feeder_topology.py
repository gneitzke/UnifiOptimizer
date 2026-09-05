"""Regression for Gitea #B6: the four wired ``FEEDS`` rules were unreachable in
production because the store binding never supplied any feeder edges.

``StoreCorrelationRepository.topology`` used to build a parent/child-only index,
so ``TopologyIndex.uplinks`` was always empty and ``feeds`` always returned
``False`` -- no downstream symptom could ever be grouped under an upstream wired
fault. These tests drive the *real* :class:`Repository`: they persist a
switch -> port -> AP inventory (with the AP's uplink identity in meta, exactly as
``map_device`` now records it) and assert the derived topology resolves the
physical feeding relation while keeping containment distinct, and that a real
``FEEDS`` rule then correlates the AP's symptom to the upstream port fault.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.correlate.engine import CorrelationEngine
from netadmin.correlate.store_repository import StoreCorrelationRepository
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository
from tests.netadmin.correlate.conftest import make_issue
from tests.netadmin.correlate.fakes import InMemoryCorrelationStore

SWITCH_MAC = "aa:bb:cc:dd:ee:01"
AP_MAC = "aa:bb:cc:dd:ee:02"
UPLINK_PORT = 5


@pytest.fixture
def wired_inventory(tmp_db_path: Path):
    """A switch, its uplink port, and an AP hanging off that port.

    The AP records the physical feeder identity in meta the way ``map_device``
    does from the GET ``stat/device`` ``uplink`` block. Yields the repo and the
    three entity ids.
    """
    repo = Repository.open(tmp_db_path)
    switch_id = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id=SWITCH_MAC, name="sw-core"),
        ts=1_000_000,
    )
    port_id = repo.upsert_entity(
        Entity(
            entity_type=EntityType.PORT,
            native_id=f"{SWITCH_MAC}:{UPLINK_PORT}",
            name="Port 5",
            parent_id=switch_id,
        ),
        ts=1_000_000,
    )
    ap_id = repo.upsert_entity(
        Entity(
            entity_type=EntityType.AP,
            native_id=AP_MAC,
            name="ap-loft",
            meta={"uplink_mac": SWITCH_MAC, "uplink_remote_port": UPLINK_PORT},
        ),
        ts=1_000_000,
    )
    yield repo, switch_id, port_id, ap_id
    repo.close()


def test_feeder_edges_reconstructed_from_uplink_meta(wired_inventory) -> None:
    repo, switch_id, port_id, ap_id = wired_inventory
    edges = set(repo.feeder_edges())
    # Both the upstream switch and its uplink port feed the AP.
    assert (switch_id, ap_id) in edges
    assert (port_id, ap_id) in edges
    # ...and nothing feeds the switch or the port themselves.
    assert not any(fed in (switch_id, port_id) for _, fed in edges)


def test_store_topology_resolves_feeds_but_not_containment(wired_inventory) -> None:
    repo, switch_id, port_id, ap_id = wired_inventory
    topo = StoreCorrelationRepository(repo).topology()

    # Physical feeding resolves (this was the dead relation).
    assert topo.feeds(port_id, ap_id) is True
    assert topo.feeds(switch_id, ap_id) is True

    # Containment stays a strictly separate relation: the port is contained in
    # the switch, but the AP is NOT a child of the switch it merely hangs off.
    assert topo.is_ancestor(switch_id, port_id) is True
    assert topo.is_ancestor(switch_id, ap_id) is False
    assert topo.is_ancestor(port_id, ap_id) is False


def test_feeds_dormant_without_uplink_meta(tmp_db_path: Path) -> None:
    """The pre-fix state: a switch/port/AP with a containment chain but no uplink
    identity yields no feeder edge, so ``feeds`` stays False (the exact bug)."""
    repo = Repository.open(tmp_db_path)
    switch_id = repo.upsert_entity(
        Entity(entity_type=EntityType.SWITCH, native_id=SWITCH_MAC), ts=1_000_000
    )
    ap_id = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=AP_MAC), ts=1_000_000
    )
    topo = StoreCorrelationRepository(repo).topology()
    assert repo.feeder_edges() == []
    assert topo.feeds(switch_id, ap_id) is False
    repo.close()


def test_port_flap_rule_correlates_downstream_ap_symptom(wired_inventory) -> None:
    """End to end over the store-derived topology: a flapping uplink port becomes
    the root and the AP's coverage hole is grouped under it via a FEEDS rule."""
    repo, switch_id, port_id, ap_id = wired_inventory
    topo = StoreCorrelationRepository(repo).topology()

    T = 2_000_000
    root = make_issue(10, "wired.port_flapping", port_id, first_seen_ts=T)
    symptom = make_issue(11, "net.coverage_hole", ap_id, first_seen_ts=T + 30)

    store = InMemoryCorrelationStore([root, symptom], topo)
    CorrelationEngine(store).run(T + 60)

    incidents = {i.root_issue_id: i for i in store.all_incidents()}
    inc = incidents[10]
    members = {m.issue_id: m for m in store.get_incident_members(inc.id)}
    assert set(members) == {10, 11}
    link = members[11]
    assert link.rule.startswith("port_flapping->")
    assert link.rule.endswith(":feeds")
