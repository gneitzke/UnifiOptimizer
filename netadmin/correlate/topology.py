"""Entity topology, the substrate correlation rules are evaluated over.

A :class:`TopologyIndex` is an immutable snapshot of the inventory's parent/child
tree plus an optional set of wired-uplink edges. The correlation engine
(``docs/ARCHITECTURE.md`` section 17) consults it to decide whether two issues'
entities stand in a *concrete topological relation* -- the only basis on which a
symptom may be attributed to a root. Everything here is pure lookups; no I/O.

The parent/child edges are the ones the store already models on
``entities.parent_id`` (section 4):

* switch -> ports
* AP -> radios
* AP (or switch) <-> its associated clients
* gateway is site-level (no parent)

Wired-uplink edges (which *device* a switch port or upstream device feeds) are
**not** persisted by the current ingest layer, so :attr:`TopologyIndex.uplinks`
is empty in production and the ``FEEDS`` relation stays dormant there. That is
deliberate and conservative: section 17 would rather emit no correlation than a
wrong one, and a rule that cannot resolve a concrete edge simply does not fire.
Tests (and a future ingest that records uplink topology) populate the edges
explicitly to exercise the wired-feeds-a-device rules.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

__all__ = ["TopoNode", "TopologyIndex"]


@dataclass(frozen=True)
class TopoNode:
    """One entity, reduced to the fields correlation cares about."""

    entity_id: int
    entity_type: str  # ap | switch | gateway | client | port | radio | wlan
    parent_id: Optional[int] = None
    site_id: str = "default"
    name: Optional[str] = None
    native_id: Optional[
        str
    ] = None  # MAC (devices/clients) — lets an evidence hint resolve to a node


class TopologyIndex:
    """An immutable snapshot of the entity tree, with ancestry/descendant lookups.

    Built once per correlation pass from the repository. ``uplinks`` maps a
    feeder entity (a switch port, or an upstream device) to the set of devices it
    directly feeds; it is optional and empty by default (see the module docstring).
    All walks are cycle-guarded so a malformed ``parent_id`` loop can never hang
    the engine.
    """

    def __init__(
        self,
        nodes: Iterable[TopoNode],
        *,
        uplinks: Optional[Mapping[int, Iterable[int]]] = None,
    ) -> None:
        self._by_id: dict[int, TopoNode] = {n.entity_id: n for n in nodes}
        self._children: dict[int, list[int]] = defaultdict(list)
        # Reverse index for resolving an evidence hint (an AP name or MAC recorded
        # by a detector) back to a concrete node. Lower-cased so a MAC or name
        # matches regardless of the casing the controller happened to report.
        self._by_hint: dict[str, int] = {}
        for node in self._by_id.values():
            if node.parent_id is not None:
                self._children[node.parent_id].append(node.entity_id)
            for hint in (node.name, node.native_id):
                if hint:
                    self._by_hint.setdefault(hint.strip().lower(), node.entity_id)
        # feeder -> directly-fed devices (wired uplink). Copied defensively.
        self._feeds: dict[int, set[int]] = {}
        for feeder, fed in (uplinks or {}).items():
            self._feeds[feeder] = {int(f) for f in fed}

    # ------------------------------------------------------------------ #
    # Node access
    # ------------------------------------------------------------------ #
    def get(self, entity_id: Optional[int]) -> Optional[TopoNode]:
        if entity_id is None:
            return None
        return self._by_id.get(entity_id)

    def __contains__(self, entity_id: int) -> bool:
        return entity_id in self._by_id

    def find_entity(self, hint: Optional[str]) -> Optional[TopoNode]:
        """Resolve an entity by the name-or-native-id a detector recorded.

        Detectors pin some symptoms on a *specific* device by human name or MAC
        (``client.flaky``'s ``attributed_ap``, ``wifi.sticky_client``'s
        ``current_ap``). Resolving that hint to a concrete node lets the engine
        check the recorded device against the candidate root's topology, instead
        of trusting the client's volatile current parent. Case-insensitive;
        returns ``None`` when the hint names nothing in this snapshot.
        """
        if not hint:
            return None
        eid = self._by_hint.get(str(hint).strip().lower())
        return self._by_id.get(eid) if eid is not None else None

    # ------------------------------------------------------------------ #
    # Parent/child ancestry
    # ------------------------------------------------------------------ #
    def ancestors(self, entity_id: int) -> list[int]:
        """Every transitive parent of ``entity_id`` (nearest first).

        The entity itself is excluded, so a node is never its own ancestor.
        """
        out: list[int] = []
        seen: set[int] = {entity_id}
        node = self._by_id.get(entity_id)
        parent = node.parent_id if node is not None else None
        while parent is not None and parent not in seen:
            seen.add(parent)
            out.append(parent)
            nxt = self._by_id.get(parent)
            parent = nxt.parent_id if nxt is not None else None
        return out

    def is_ancestor(self, ancestor_id: int, descendant_id: int) -> bool:
        """True when ``ancestor_id`` is a (transitive) parent of ``descendant_id``."""
        if ancestor_id == descendant_id:
            return False
        return ancestor_id in self.ancestors(descendant_id)

    def descendants(self, entity_id: int) -> set[int]:
        """Every transitive child of ``entity_id`` (excludes the entity itself)."""
        out: set[int] = set()
        stack = list(self._children.get(entity_id, ()))
        while stack:
            cur = stack.pop()
            if cur in out or cur == entity_id:
                continue
            out.add(cur)
            stack.extend(self._children.get(cur, ()))
        return out

    # ------------------------------------------------------------------ #
    # Wired-uplink ("feeds") relation
    # ------------------------------------------------------------------ #
    def feeds(self, feeder_id: int, target_id: int) -> bool:
        """True when ``feeder_id`` feeds ``target_id`` -- directly, transitively
        through further uplink edges, or by feeding one of ``target_id``'s
        ancestors (so a flapping switch port that feeds an AP also "feeds" that
        AP's clients and radios).
        """
        if feeder_id == target_id:
            return False
        fed_devices = self._reachable_fed(feeder_id)
        if not fed_devices:
            return False
        if target_id in fed_devices:
            return True
        # A device fed by the feeder covers everything parented under it.
        return any(anc in fed_devices for anc in self.ancestors(target_id))

    def _reachable_fed(self, feeder_id: int) -> set[int]:
        """Transitive closure of the uplink edges out of ``feeder_id``."""
        out: set[int] = set()
        stack = list(self._feeds.get(feeder_id, ()))
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(self._feeds.get(cur, ()))
        return out

    # ------------------------------------------------------------------ #
    # Site
    # ------------------------------------------------------------------ #
    def same_site(self, a_id: int, b_id: int) -> bool:
        a = self._by_id.get(a_id)
        b = self._by_id.get(b_id)
        if a is None or b is None:
            return False
        return a.site_id == b.site_id
