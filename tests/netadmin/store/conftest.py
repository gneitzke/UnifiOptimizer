"""Fixtures for the store test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    """A migrated, empty repository on pytest's tmp db."""
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


@pytest.fixture
def switch_entity_id(repo: Repository) -> int:
    """A stored switch entity, returning its id (for series/samples tests)."""
    entity = Entity(entity_type=EntityType.SWITCH, native_id="aa:bb:cc:dd:ee:01", name="sw-core")
    return repo.upsert_entity(entity, ts=1_000_000)
