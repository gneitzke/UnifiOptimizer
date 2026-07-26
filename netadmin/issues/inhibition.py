"""Data-driven inhibition (section 7).

An inhibition rule freezes an issue's whole state machine while a *cause* is in
effect: no creation, no escalation, and no clear-streak advancement (absence of
evidence is not evidence of absence). Rules are ``(cause_key, suppressed_scope)``
data, never branching code:

* ``infra.controller_down`` — scope ``global``: while the controller is
  unreachable nothing else can be trusted, so every other detector's issues are
  frozen in both directions.
* ``infra.device_down`` — scope ``children``: a downed switch/AP freezes only the
  issues on entities *below* it in the parentage tree (its ports, its radios,
  clients pinned to it).

A cause never inhibits itself: a ``global`` cause does not freeze issues that
share its ``detector_key`` (so ``controller_down`` can still activate and later
resolve, lifting the freeze), and a ``children`` cause is never its own
descendant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from netadmin.domain.entities import Entity


class InhibitionScope(str, Enum):
    """How far a cause reaches."""

    GLOBAL = "global"  # every other detector, whole site
    CHILDREN = "children"  # only entities parented (transitively) under the cause


@dataclass(frozen=True)
class InhibitionRule:
    """One ``(cause_key, suppressed_scope)`` pair."""

    cause_key: str
    suppressed_scope: InhibitionScope


DEFAULT_RULES: tuple[InhibitionRule, ...] = (
    InhibitionRule("infra.controller_down", InhibitionScope.GLOBAL),
    InhibitionRule("infra.device_down", InhibitionScope.CHILDREN),
)

# Ancestry resolver: (entity | None, entity_id | None) -> set of ancestor entity_ids.
AncestryResolver = Callable[[Optional[Entity], Optional[int]], set]


class InhibitionContext:
    """A frozen snapshot of which causes are in effect for one evaluation cycle.

    Built once per cycle by the engine from (a) the causes firing this cycle and
    (b) the causes already open-and-confirmed in the repository, then consulted
    for every candidate verdict.
    """

    def __init__(
        self,
        *,
        global_active: bool,
        global_cause_keys: set[str],
        down_device_ids: set[int],
        ancestry: AncestryResolver,
    ) -> None:
        self._global_active = global_active
        self._global_cause_keys = global_cause_keys
        self._down_device_ids = down_device_ids
        self._ancestry = ancestry

    @property
    def any_active(self) -> bool:
        return self._global_active or bool(self._down_device_ids)

    def is_inhibited(
        self,
        detector_key: str,
        entity: Optional[Entity],
        entity_id: Optional[int],
    ) -> bool:
        """True when a verdict for ``detector_key`` on this entity must be frozen."""
        # Global cause (controller_down) freezes everything except issues that
        # share the cause's own detector_key.
        if self._global_active and detector_key not in self._global_cause_keys:
            return True

        # Children cause (device_down) freezes any issue whose entity is a
        # transitive descendant of a downed device. A device is not its own
        # descendant, so the cause's own issue is never self-inhibited.
        if self._down_device_ids:
            ancestors = self._ancestry(entity, entity_id)
            if ancestors & self._down_device_ids:
                return True

        return False


__all__ = [
    "InhibitionScope",
    "InhibitionRule",
    "DEFAULT_RULES",
    "InhibitionContext",
    "AncestryResolver",
]
