"""Fixtures for the detection test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository, SampleReading


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    """A migrated, empty repository on pytest's tmp db."""
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


@pytest.fixture
def ap_entity_id(repo: Repository) -> int:
    """A stored AP entity id, for series/samples tests."""
    entity = Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:dd:ee:01", name="ap-1")
    return repo.upsert_entity(entity, ts=0)


def record_gauge(
    repo: Repository, entity_id: int, metric: str, points: list[tuple[int, float]]
) -> int:
    """Insert gauge samples ``(ts, value)`` verbatim; return the series_id."""
    repo.record_samples(SampleReading(entity_id, metric, ts, value) for ts, value in points)
    return repo.get_series(entity_id, metric)
