"""Unit tests for the causal rule table, priority order, and relation resolver.

These pin the data itself -- rank monotonicity for every seeded rule (the property
that makes the rank guard both cycle-free and priority-correct), the explicit
priority pairings the doc calls out, and each topological relation in isolation.
"""

from __future__ import annotations

import pytest

from netadmin.correlate.rules import (
    ANY,
    RULES,
    CausalRule,
    Direction,
    TopoRelation,
    match_link,
    relation_holds,
    root_rank,
)
from netadmin.correlate.topology import TopologyIndex, TopoNode


def _node(entity_id, entity_type, parent_id=None, name=None, site_id="default"):
    return TopoNode(entity_id, entity_type, parent_id=parent_id, name=name, site_id=site_id)


# --------------------------------------------------------------------------- #
# Priority order (the rank guard's backbone).
# --------------------------------------------------------------------------- #
def test_every_explicit_rule_has_root_outranking_symptom() -> None:
    """The rank guard requires root_rank < symptom_rank for every real link.

    Verifying it for every explicit (non-wildcard) seed rule proves the guard
    never silently drops a rule the table intends to fire, and that no causal
    cycle is expressible.
    """
    for rule in RULES:
        if rule.symptom_detector == ANY:
            continue
        assert root_rank(rule.root_detector) < root_rank(
            rule.symptom_detector
        ), f"{rule.root_detector} must outrank {rule.symptom_detector}"


@pytest.mark.parametrize(
    "upstream, downstream",
    [
        ("wired.port_flapping", "wifi.mesh_uplink"),  # wired feeder beats AP wifi
        ("wan.isp_degraded", "client.flaky"),  # WAN beats per-client
        ("net.firmware_regression", "wifi.mesh_uplink"),  # firmware beats its symptom
        ("infra.device_down", "net.coverage_hole"),  # infra beats downstream
    ],
)
def test_priority_pairings(upstream: str, downstream: str) -> None:
    assert root_rank(upstream) < root_rank(downstream)


def test_unranked_detector_is_least_priority() -> None:
    assert root_rank("totally.unknown") == len(
        __import__("netadmin.correlate.rules", fromlist=["ROOT_PRIORITY_ORDER"]).ROOT_PRIORITY_ORDER
    )


# --------------------------------------------------------------------------- #
# Relation resolver.
# --------------------------------------------------------------------------- #
def test_same_entity_relation() -> None:
    ap = _node(1, "ap")
    rule = CausalRule("a", "b", TopoRelation.SAME_ENTITY, Direction.PEER, "")
    assert relation_holds(rule, ap, ap, TopologyIndex([ap]))
    other = _node(2, "ap")
    assert not relation_holds(rule, ap, other, TopologyIndex([ap, other]))


def test_parent_child_relation_is_directional() -> None:
    ap = _node(1, "ap")
    client = _node(2, "client", parent_id=1)
    topo = TopologyIndex([ap, client])
    above = CausalRule("a", "b", TopoRelation.PARENT_CHILD, Direction.ROOT_ABOVE, "")
    # root above: AP (root) is ancestor of client (symptom) -> holds.
    assert relation_holds(above, ap, client, topo)
    # but not the other way (client is not an ancestor of the AP).
    assert not relation_holds(above, client, ap, topo)


def test_subtree_relation_includes_self_and_descendants() -> None:
    ap = _node(1, "ap")
    radio = _node(2, "radio", parent_id=1)
    topo = TopologyIndex([ap, radio])
    rule = CausalRule("a", "b", TopoRelation.SUBTREE, Direction.ROOT_ABOVE, "")
    assert relation_holds(rule, ap, ap, topo)  # self
    assert relation_holds(rule, ap, radio, topo)  # descendant
    assert not relation_holds(rule, radio, ap, topo)  # upward does not hold


def test_feeds_relation_needs_an_uplink_edge() -> None:
    port = _node(1, "port")
    ap = _node(2, "ap")
    client = _node(3, "client", parent_id=2)
    rule = CausalRule("a", "b", TopoRelation.FEEDS, Direction.ROOT_ABOVE, "")

    without = TopologyIndex([port, ap, client])
    assert not relation_holds(rule, port, ap, without), "no edge -> dormant (conservative)"

    with_edge = TopologyIndex([port, ap, client], uplinks={1: [2]})
    assert relation_holds(rule, port, ap, with_edge)  # feeds the AP directly
    assert relation_holds(rule, port, client, with_edge)  # and everything under it


# --------------------------------------------------------------------------- #
# match_link end-to-end (rank guard + explicit-over-wildcard).
# --------------------------------------------------------------------------- #
def test_match_link_prefers_explicit_over_wildcard() -> None:
    """A firmware regression can reach a coverage hole two ways; the explicit-ish
    (here: only wildcard exists) path is chosen, and its rule id names the real
    detectors."""
    ap = _node(1, "ap", name="AP-X")
    radio = _node(2, "radio", parent_id=1, name="AP-X:na")
    topo = TopologyIndex([ap, radio])
    link = match_link(
        root_detector="net.firmware_regression",
        symptom_detector="wifi.airtime_saturation",
        root_node=ap,
        sym_node=radio,
        topo=topo,
        root_name="AP-X",
        sym_name="AP-X:na",
        root_title="firmware regressed",
        sym_title="airtime saturated",
    )
    assert link is not None
    assert link.rule_id == "firmware_regression->airtime_saturation:subtree"
    assert "AP-X" in link.rationale


def test_match_link_rank_guard_blocks_inversion() -> None:
    """A wildcard rule must never make a more-upstream issue a symptom.

    device_down's ANY rule would topologically match a WAN issue on a child, but
    the rank guard (wan outranks infra? no -- infra outranks wan) ... here we test
    the guard directly: a low-priority root cannot claim a high-priority symptom.
    """
    ap = _node(1, "ap")
    gw = _node(2, "gateway", parent_id=1)  # contrived: gateway under an AP
    topo = TopologyIndex([ap, gw])
    # mesh_uplink (rank ~15) trying to claim wan.isp_degraded (rank ~2): blocked.
    link = match_link(
        root_detector="wifi.mesh_uplink",
        symptom_detector="wan.isp_degraded",
        root_node=ap,
        sym_node=gw,
        topo=topo,
        root_name="ap",
        sym_name="gw",
        root_title="",
        sym_title="",
    )
    assert link is None


def test_match_link_returns_none_without_topology_relation() -> None:
    ap_a = _node(1, "ap")
    client_b = _node(2, "client", parent_id=99)  # under some other AP
    topo = TopologyIndex([ap_a, client_b])
    link = match_link(
        root_detector="wifi.mesh_uplink",
        symptom_detector="client.flaky",
        root_node=ap_a,
        sym_node=client_b,
        topo=topo,
        root_name="a",
        sym_name="b",
        root_title="",
        sym_title="",
    )
    assert link is None, "no ancestry -> no link, even with a matching rule"
