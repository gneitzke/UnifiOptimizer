"""The causal rule table -- encoded expert knowledge, as data (section 17).

A seasoned admin's synthesis ("your weak backhaul is *the* problem; the coverage
hole and the client dropouts are its symptoms") is captured here as a table of
:class:`CausalRule` rows, never as branching code. Each row states: *this* root
detector, firing on an entity that stands in *this* topological relation to a
symptom entity, explains *that* symptom detector -- with a one-line rationale.

Two knobs make the engine conservative and reproducible:

* **The rank guard.** :data:`ROOT_PRIORITY_ORDER` is an explicit, documented
  ordering from most-upstream/infrastructural to most-downstream/leaf. A link is
  only ever admitted when the root outranks the symptom (``root_rank <
  symptom_rank``). This is what makes "wired-feeding-an-AP beats the AP's own
  wifi issue", "WAN beats per-client", and "a firmware regression beats the
  symptom it caused" fall out of a single reproducible rule, and it makes causal
  cycles impossible.
* **Explicit beats wildcard.** A rule whose ``symptom_detector`` is a concrete
  key is a hand-vetted causal statement and is preferred, for the recorded
  rule/rationale, over a broad ``ANY`` rule (``infra.device_down`` /
  ``net.firmware_regression`` / a flapping feeder sweeping up "any surviving
  issue on the affected subtree").

Matching a pair is pure: :func:`match_link` returns the best rule's audit line
for an ordered ``(root, symptom)`` pair or ``None``. The temporal guard and
root-among-candidates selection live in the engine; this module only answers
"could this root, topologically and by rule, explain this symptom?".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from netadmin.correlate.topology import TopologyIndex, TopoNode

__all__ = [
    "ANY",
    "TopoRelation",
    "Direction",
    "CausalRule",
    "MatchedLink",
    "RULES",
    "ROOT_PRIORITY_ORDER",
    "root_rank",
    "relation_holds",
    "match_link",
]

# Wildcard symptom detector: "any open issue that stands in the relation".
ANY = "*"


class TopoRelation(str, Enum):
    """The concrete topological edge a rule requires between root and symptom.

    Every relation is defined *root-relative* (the accompanying :class:`Direction`
    disambiguates the one case -- ``PARENT_CHILD`` -- where the edge is not
    inherently oriented).
    """

    SAME_ENTITY = "same_entity"  # root and symptom are the same entity (e.g. same AP, same gateway)
    SUBTREE = "subtree"  # symptom is the root entity itself or a descendant of it
    PARENT_CHILD = (
        "parent_child"  # a strict ancestor/descendant edge (direction picks the root end)
    )
    FEEDS = "feeds"  # root device/port feeds the symptom's device via a wired uplink
    SAME_SITE = "same_site"  # root and symptom are distinct entities in the same site


class Direction(str, Enum):
    """Which end of the edge is the root."""

    ROOT_ABOVE = "root_above"  # root is the ancestor / feeder / upstream cause
    ROOT_BELOW = (
        "root_below"  # root is the descendant (unused in the seed set; modeled for completeness)
    )
    PEER = "peer"  # symmetric relation (same entity / same site)


@dataclass(frozen=True)
class CausalRule:
    """One row of the causal table: ``(root, symptom, relation, direction)`` + a
    rationale template.

    ``rationale_template`` is a ``str.format`` string over the fields
    ``{root_name}``, ``{sym_name}``, ``{root_title}`` and ``{sym_title}``.
    """

    root_detector: str
    symptom_detector: str  # a concrete detector key, or ANY
    relation: TopoRelation
    direction: Direction
    rationale_template: str
    # Optional guards that make a *causal* statement conditional on what the
    # symptom detector itself measured — never on a guess. A link is admitted
    # only when every guard below holds; otherwise the pair does not match and
    # the symptom is left to stand alone (§17: a wrong grouping is worse than
    # none).
    #
    # ``symptom_evidence_in``: ``(evidence_key, allowed_values)`` — the symptom's
    #   recorded ``evidence[key]`` must be one of ``allowed_values``. Used to gate
    #   ``isp_degraded -> dns_slow`` on the DNS slowness being *upstream*-localised
    #   (not a local-resolver fault) and ``mesh_uplink -> airtime_saturation`` on
    #   the airtime being *self*-generated (not an external co-channel neighbor).
    # ``symptom_entity_types``: an allowlist of the symptom entity's type. Used to
    #   keep a broad ``SUBTREE`` sweep (firmware regression) off *volatile client*
    #   children, which roam between APs and must not be attributed by mere
    #   current-parent topology.
    symptom_evidence_in: Optional[tuple[str, frozenset[str]]] = None
    symptom_entity_types: Optional[frozenset[str]] = None

    def rule_id(self, symptom_detector_key: str) -> str:
        """The audit token stored on ``incident_members.rule``.

        For a wildcard rule the *concrete* symptom key is substituted, so the
        recorded link always names the real detectors it connected, e.g.
        ``mesh_uplink->coverage_hole:same_entity``.
        """
        root = _short(self.root_detector)
        sym = _short(symptom_detector_key)
        return f"{root}->{sym}:{self.relation.value}"


@dataclass(frozen=True)
class MatchedLink:
    """A rule that fired for a ``(root, symptom)`` pair: its audit id + rationale."""

    rule_id: str
    rationale: str


def _short(detector_key: str) -> str:
    """``"wifi.mesh_uplink" -> "mesh_uplink"`` for compact audit ids."""
    return detector_key.split(".", 1)[-1]


# --------------------------------------------------------------------------- #
# The seed table (docs/ARCHITECTURE.md section 17, step 2).
#
# Explicit rows first (concrete symptom detectors), wildcard rows last. Ordering
# is cosmetic -- match_link prefers explicit over wildcard regardless -- but it
# keeps the vetted causal statements legible at the top.
# --------------------------------------------------------------------------- #
RULES: tuple[CausalRule, ...] = (
    # wifi.mesh_uplink (on an AP) is the classic hidden root: a weak wireless
    # backhaul manifests as a coverage hole in the same cell, clients dropping on
    # that AP, and its radio saturating.
    CausalRule(
        "wifi.mesh_uplink",
        "net.coverage_hole",
        TopoRelation.SAME_ENTITY,
        Direction.PEER,
        "The coverage hole on {root_name} follows the weak mesh backhaul on the same AP.",
    ),
    CausalRule(
        "wifi.mesh_uplink",
        "client.flaky",
        TopoRelation.PARENT_CHILD,
        Direction.ROOT_ABOVE,
        "{sym_name} keeps dropping on {root_name}, whose mesh backhaul is failing.",
    ),
    CausalRule(
        "wifi.mesh_uplink",
        "wifi.airtime_saturation",
        TopoRelation.PARENT_CHILD,
        Direction.ROOT_ABOVE,
        "Airtime saturation on {root_name}'s radio tracks its failing mesh backhaul.",
        # Only when the airtime is *self*-generated. A radio saturated by an
        # external co-channel neighbor (``dominant_source == "non_self"``) is not
        # explained by this AP's backhaul; rooting it on the mesh would steer the
        # operator to the wrong fix. The detector already splits self vs non-self.
        symptom_evidence_in=("dominant_source", frozenset({"self"})),
    ),
    # wifi.tx_power_loud (on an AP) drags in the sticky clients concentrated on it.
    CausalRule(
        "wifi.tx_power_loud",
        "wifi.sticky_client",
        TopoRelation.PARENT_CHILD,
        Direction.ROOT_ABOVE,
        "{sym_name} clings to {root_name}, which is transmitting too loud.",
    ),
    # wan.isp_degraded (on the gateway) explains the other WAN-quality symptoms on
    # the same gateway. Client-latency fan-out is deliberately NOT seeded: there
    # is no per-client latency detector to attach, and sweeping unrelated client
    # flaps under WAN would be exactly the "fusing two unrelated problems" the
    # section warns against.
    CausalRule(
        "wan.isp_degraded",
        "wan.dns_slow",
        TopoRelation.SAME_ENTITY,
        Direction.PEER,
        "Slow DNS coincides with the degraded ISP uplink on {root_name}.",
        # Only when the DNS slowness is *upstream*-localised. The dns_slow detector
        # compares the gateway resolver against a public anchor: a slow resolver
        # while the anchor is fine is a *local* fault (misconfigured/overloaded
        # resolver), independent of the ISP — fusing it under isp_degraded would
        # hide the real local root and offer the wrong (ISP) fix.
        symptom_evidence_in=("localised", frozenset({"upstream"})),
    ),
    CausalRule(
        "wan.isp_degraded",
        "wan.bufferbloat",
        TopoRelation.SAME_ENTITY,
        Direction.PEER,
        "Bufferbloat coincides with the degraded ISP uplink on {root_name}.",
    ),
    # Wired feeder faults own the downstream device's issues. FEEDS needs a
    # concrete uplink edge, so these stay dormant until the topology records one
    # (module docstring in topology.py) -- conservative by construction.
    CausalRule(
        "wired.port_flapping",
        ANY,
        TopoRelation.FEEDS,
        Direction.ROOT_ABOVE,
        "{sym_title} on {sym_name} sits downstream of the flapping port {root_name}.",
    ),
    CausalRule(
        "wired.bad_cable",
        ANY,
        TopoRelation.FEEDS,
        Direction.ROOT_ABOVE,
        "{sym_title} on {sym_name} sits behind the bad cable on {root_name}.",
    ),
    # L2 faults sweep the segment behind them (same FEEDS caveat).
    CausalRule(
        "wired.stp_loop",
        ANY,
        TopoRelation.FEEDS,
        Direction.ROOT_ABOVE,
        "{sym_title} on {sym_name} is on the L2 segment behind the STP loop at {root_name}.",
    ),
    CausalRule(
        "wired.broadcast_storm",
        ANY,
        TopoRelation.FEEDS,
        Direction.ROOT_ABOVE,
        "{sym_title} on {sym_name} is on the segment flooded by the broadcast storm at {root_name}.",
    ),
    # A firmware regression on a device owns that device's (and its children's)
    # post-upgrade degradations.
    CausalRule(
        "net.firmware_regression",
        ANY,
        TopoRelation.SUBTREE,
        Direction.ROOT_ABOVE,
        "{sym_title} on {sym_name} degraded after the firmware change on {root_name}.",
        # The regression's evidence is a change-point on the *device* (disconnect
        # rate, port errors, radio resets), so it owns the device's own and its
        # radios'/ports' post-upgrade issues — but NOT arbitrary client children,
        # which roam onto the AP and carry their own faults (a NIC-driver bug is
        # not a firmware regression). Volatile clients attach only via a specific
        # per-symptom causal rule, never this blanket subtree sweep.
        symptom_entity_types=frozenset({"ap", "radio", "switch", "port", "gateway", "wlan"}),
    ),
    # A downed device is the explicit root for any issue still open on it or its
    # children (usually already inhibited; this makes it the root when not).
    CausalRule(
        "infra.device_down",
        ANY,
        TopoRelation.SUBTREE,
        Direction.ROOT_ABOVE,
        "{sym_title} on {sym_name} is under the down device {root_name}.",
    ),
)


# --------------------------------------------------------------------------- #
# Root priority (docs/ARCHITECTURE.md section 17, step 4).
#
# Most-upstream / most-infrastructural first. Lower index == higher priority ==
# "more likely to be the true root". Used both to pick a symptom's root among
# several candidates and (via the rank guard) to forbid a downstream issue from
# ever being made the root of an upstream one.
# --------------------------------------------------------------------------- #
ROOT_PRIORITY_ORDER: tuple[str, ...] = (
    # site-wide infrastructure
    "infra.controller_down",
    "infra.device_down",
    # WAN beats anything per-client
    "wan.isp_degraded",
    "wan.flapping",
    "wan.dns_slow",
    "wan.bufferbloat",
    # L2 / wired infrastructure, feeder-first
    "wired.stp_loop",
    "wired.broadcast_storm",
    "wired.port_flapping",
    "wired.bad_cable",
    "wired.uplink_saturation",
    "wired.poe_budget",
    "wired.sfp_degraded",
    "wired.duplex_mismatch",
    # a firmware regression beats the symptoms it caused
    "net.firmware_regression",
    # wifi infrastructure on an AP, backhaul-first
    "wifi.mesh_uplink",
    "wifi.airtime_saturation",
    "wifi.tx_power_loud",
    "wifi.min_rssi_misconfig",
    "wifi.channel_plan",
    "wifi.dfs_recurring",
    "wifi.neighbor_density",
    "wifi.rogue_ap",
    # downstream / leaf symptoms
    "net.coverage_hole",
    "wifi.sticky_client",
    "wifi.pingpong_roamer",
    "wifi.roam_quality",
    "wifi.band_steering",
    "wifi.legacy_rates",
    "client.dhcp",
    "client.flaky",
    "client.known_pathology",
)

_RANK: dict[str, int] = {key: i for i, key in enumerate(ROOT_PRIORITY_ORDER)}
# Anything unlisted ranks below every named detector (least root-like).
_UNRANKED = len(ROOT_PRIORITY_ORDER)


def root_rank(detector_key: str) -> int:
    """Priority rank of a detector (lower == more upstream/root-like)."""
    return _RANK.get(detector_key, _UNRANKED)


# --------------------------------------------------------------------------- #
# Relation evaluation
# --------------------------------------------------------------------------- #
def relation_holds(
    rule: CausalRule,
    root_node: Optional[TopoNode],
    sym_node: Optional[TopoNode],
    topo: TopologyIndex,
) -> bool:
    """True when ``root_node`` and ``sym_node`` stand in ``rule``'s relation."""
    if root_node is None or sym_node is None:
        return False
    root_id = root_node.entity_id
    sym_id = sym_node.entity_id
    rel = rule.relation

    if rel is TopoRelation.SAME_ENTITY:
        return root_id == sym_id
    if rel is TopoRelation.SUBTREE:
        return root_id == sym_id or topo.is_ancestor(root_id, sym_id)
    if rel is TopoRelation.PARENT_CHILD:
        if rule.direction is Direction.ROOT_BELOW:
            return topo.is_ancestor(sym_id, root_id)
        return topo.is_ancestor(root_id, sym_id)
    if rel is TopoRelation.FEEDS:
        return topo.feeds(root_id, sym_id)
    if rel is TopoRelation.SAME_SITE:
        return root_id != sym_id and topo.same_site(root_id, sym_id)
    return False


def _render_rationale(
    rule: CausalRule,
    root_name: str,
    sym_name: str,
    root_title: str,
    sym_title: str,
) -> str:
    return rule.rationale_template.format(
        root_name=root_name,
        sym_name=sym_name,
        root_title=root_title,
        sym_title=sym_title,
    )


def _guards_hold(
    rule: CausalRule,
    sym_node: Optional[TopoNode],
    sym_evidence: Optional[Mapping[str, Any]],
) -> bool:
    """Whether ``rule``'s optional evidence / entity-type guards are satisfied."""
    if rule.symptom_entity_types is not None:
        if sym_node is None or sym_node.entity_type not in rule.symptom_entity_types:
            return False
    if rule.symptom_evidence_in is not None:
        key, allowed = rule.symptom_evidence_in
        value = None if sym_evidence is None else sym_evidence.get(key)
        if value is None or str(value) not in allowed:
            return False
    return True


def match_link(
    *,
    root_detector: str,
    symptom_detector: str,
    root_node: Optional[TopoNode],
    sym_node: Optional[TopoNode],
    topo: TopologyIndex,
    root_name: str,
    sym_name: str,
    root_title: str,
    sym_title: str,
    sym_evidence: Optional[Mapping[str, Any]] = None,
) -> Optional[MatchedLink]:
    """Best rule linking this ordered ``(root, symptom)`` pair, or ``None``.

    Applies the rank guard (root must strictly outrank the symptom), the
    topological relation, and any per-rule evidence / entity-type guards for every
    candidate rule; an explicit-symptom rule is preferred over a wildcard one for
    the recorded audit line.
    """
    # The rank guard forbids inverting priority (and makes cycles impossible):
    # a downstream detector can never be made the root of an upstream one.
    if root_rank(root_detector) >= root_rank(symptom_detector):
        return None

    wildcard: Optional[MatchedLink] = None
    for rule in RULES:
        if rule.root_detector != root_detector:
            continue
        is_wildcard = rule.symptom_detector == ANY
        if not is_wildcard and rule.symptom_detector != symptom_detector:
            continue
        if not relation_holds(rule, root_node, sym_node, topo):
            continue
        if not _guards_hold(rule, sym_node, sym_evidence):
            continue
        link = MatchedLink(
            rule_id=rule.rule_id(symptom_detector),
            rationale=_render_rationale(rule, root_name, sym_name, root_title, sym_title),
        )
        if not is_wildcard:
            return link  # explicit, hand-vetted rule wins immediately
        wildcard = wildcard or link
    return wildcard
