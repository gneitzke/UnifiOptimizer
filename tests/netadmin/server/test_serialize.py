"""Entity references: the shared shape every payload points at an entity with.

The parent fields are what make a structural child linkable. A radio and a port
have no page of their own, so a UI that only knows their name can do nothing
with them; knowing the device they belong to turns "wifi1" into a link to the AP
that owns it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.server.serialize import entity_ref, entity_ref_map
from netadmin.store.repository import Repository

TS = 1_700_000_000


@pytest.fixture
def store(tmp_db_path: Path):
    store = Repository.open(tmp_db_path, site_id="default")
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01", name="ap-office"),
        ts=TS,
    )
    store.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id="aa:bb:cc:00:00:01:ng",
            name="wifi0",
            parent_id=ap,
        ),
        ts=TS,
    )
    store.ap_id = ap  # type: ignore[attr-defined]
    yield store
    store.close()


def test_child_ref_carries_its_parent(store: Repository) -> None:
    radio = store.find_entity(EntityType.RADIO, "aa:bb:cc:00:00:01:ng")
    refs = entity_ref_map(store, [radio["entity_id"]])
    ref = refs[int(radio["entity_id"])]
    assert ref["name"] == "wifi0"
    assert ref["parent_id"] == store.ap_id
    assert ref["parent_name"] == "ap-office"


def test_top_level_ref_has_no_parent(store: Repository) -> None:
    refs = entity_ref_map(store, [store.ap_id])
    ref = refs[store.ap_id]
    assert ref["parent_id"] is None
    assert ref["parent_name"] is None


def test_parent_name_needs_the_batch_lookup(store: Repository) -> None:
    """The single-row form still reports the parent id, just not its name."""
    radio = store.find_entity(EntityType.RADIO, "aa:bb:cc:00:00:01:ng")
    ref = entity_ref(radio)
    assert ref is not None
    assert ref["parent_id"] == store.ap_id
    assert ref["parent_name"] is None


def test_parent_already_in_the_batch_is_not_looked_up_twice(store: Repository) -> None:
    radio = store.find_entity(EntityType.RADIO, "aa:bb:cc:00:00:01:ng")
    refs = entity_ref_map(store, [radio["entity_id"], store.ap_id])
    assert refs[int(radio["entity_id"])]["parent_name"] == "ap-office"
    assert refs[store.ap_id]["parent_id"] is None
