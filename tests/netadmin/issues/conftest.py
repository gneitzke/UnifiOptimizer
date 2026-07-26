"""Fixtures and tiny factories for the issue-engine test suite."""

from __future__ import annotations

from typing import Any, Callable, Optional

import pytest

from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import EntityType, Severity
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.models import EngineConfig
from tests.netadmin.issues.fakes import InMemoryIssueRepository

HOUR = 3600
DAY = 24 * HOUR


@pytest.fixture
def repo() -> InMemoryIssueRepository:
    return InMemoryIssueRepository()


@pytest.fixture
def config() -> EngineConfig:
    # Explicit defaults so the boundary tests read against known M / K / windows.
    return EngineConfig(default_m=3, default_k=6, reopen_window_s=DAY, fix_window_s=48 * HOUR)


@pytest.fixture
def engine(repo: InMemoryIssueRepository, config: EngineConfig) -> IssueEngine:
    return IssueEngine(repo, config=config)


EntityFactory = Callable[..., Entity]
FindingFactory = Callable[..., Finding]


@pytest.fixture
def make_entity() -> EntityFactory:
    def _make(
        native_id: str = "aa:bb:cc:00:00:01",
        entity_type: EntityType = EntityType.PORT,
        *,
        entity_id: Optional[int] = None,
        parent_id: Optional[int] = None,
        site_id: str = "default",
    ) -> Entity:
        return Entity(
            entity_type=entity_type,
            native_id=native_id,
            site_id=site_id,
            entity_id=entity_id,
            parent_id=parent_id,
        )

    return _make


@pytest.fixture
def make_finding(make_entity: EntityFactory) -> FindingFactory:
    def _make(
        detector_key: str = "wired.bad_cable",
        *,
        entity: Optional[Entity] = None,
        severity: Severity = Severity.P2,
        title: str = "rx_errors climbing",
        dims: Optional[dict[str, str]] = None,
        evidence: Optional[dict[str, Any]] = None,
        native_id: str = "aa:bb:cc:00:00:01",
        entity_id: Optional[int] = None,
        parent_id: Optional[int] = None,
    ) -> Finding:
        if entity is None:
            entity = make_entity(native_id=native_id, entity_id=entity_id, parent_id=parent_id)
        return Finding(
            detector_key=detector_key,
            entity=entity,
            severity=severity,
            title=title,
            dims=dims or {},
            evidence=evidence or {},
        )

    return _make


@pytest.fixture
def fp() -> Callable[[Finding], str]:
    return fingerprint
