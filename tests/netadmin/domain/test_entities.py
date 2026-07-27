"""The one display-label rule every entity-naming surface shares.

``entity_display_label`` is small, but it is the seam that decides whether four
saturated radios read as four faults or as one row repeated (Gitea #44), so the
rule is pinned here rather than re-derived in each consumer's tests.
"""

from __future__ import annotations

import pytest

from netadmin.domain.entities import entity_display_label
from netadmin.domain.types import EntityType


@pytest.mark.parametrize("child", [EntityType.RADIO.value, EntityType.PORT.value])
def test_a_child_is_named_by_its_parent(child: str) -> None:
    assert entity_display_label("wifi0", child, "Loft") == "Loft / wifi0"


@pytest.mark.parametrize(
    "top_level",
    [
        EntityType.AP.value,
        EntityType.SWITCH.value,
        EntityType.GATEWAY.value,
        EntityType.CLIENT.value,
        EntityType.WLAN.value,
    ],
)
def test_a_top_level_entity_keeps_its_own_name(top_level: str) -> None:
    # Even with a parent in hand: an AP under a switch is still just the AP.
    assert entity_display_label("Loft", top_level, "Core Switch") == "Loft"


def test_an_unresolved_parent_degrades_to_the_bare_name() -> None:
    # A stale parent_id must never render as "None / wifi0".
    assert entity_display_label("wifi0", EntityType.RADIO.value, None) == "wifi0"
    assert entity_display_label("wifi0", EntityType.RADIO.value, "") == "wifi0"


def test_a_child_already_named_after_its_device_is_not_restated() -> None:
    # The prefix exists to identify; "Living Room 2.4G" already does.
    label = entity_display_label("Living Room 2.4G", EntityType.RADIO.value, "Living Room")
    assert label == "Living Room 2.4G"
    # Case-insensitively, because admins are not consistent about it.
    assert entity_display_label("loft uplink", EntityType.PORT.value, "Loft") == "loft uplink"


def test_an_unknown_entity_type_is_left_alone() -> None:
    # rogue_bss rows and anything else outside the managed taxonomy.
    assert entity_display_label("NEIGHBOR-2G4", "rogue_bss", "Loft") == "NEIGHBOR-2G4"
