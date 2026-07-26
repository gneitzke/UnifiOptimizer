"""Builders for the correlation test suite.

A tiny fluent topology + issue factory so each scenario reads as the fault story
it encodes ("mesh AP with a coverage hole and two dropping clients"), not as
dataclass boilerplate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from netadmin.correlate.topology import TopologyIndex, TopoNode
from netadmin.domain.types import IssueState, Severity
from netadmin.issues.models import Issue


@dataclass
class TopologyBuilder:
    """Accumulates entities (+ optional wired-uplink edges) into a TopologyIndex."""

    _nodes: dict[int, TopoNode] = field(default_factory=dict)
    _uplinks: dict[int, list[int]] = field(default_factory=dict)

    def add(
        self,
        entity_id: int,
        entity_type: str,
        *,
        parent_id: Optional[int] = None,
        name: Optional[str] = None,
        native_id: Optional[str] = None,
        site_id: str = "default",
    ) -> int:
        self._nodes[entity_id] = TopoNode(
            entity_id=entity_id,
            entity_type=entity_type,
            parent_id=parent_id,
            site_id=site_id,
            name=name,
            native_id=native_id,
        )
        return entity_id

    def feeds(self, feeder_id: int, target_id: int) -> "TopologyBuilder":
        """Record a wired-uplink edge: ``feeder_id`` feeds ``target_id``."""
        self._uplinks.setdefault(feeder_id, []).append(target_id)
        return self

    def build(self) -> TopologyIndex:
        return TopologyIndex(self._nodes.values(), uplinks=self._uplinks)


@pytest.fixture
def topo() -> TopologyBuilder:
    return TopologyBuilder()


def make_issue(
    issue_id: int,
    detector_key: str,
    entity_id: Optional[int],
    *,
    first_seen_ts: int = 1_000_000,
    last_seen_ts: Optional[int] = None,
    severity: Severity = Severity.P3,
    state: IssueState = IssueState.ACTIVE,
    fingerprint: Optional[str] = None,
    title: Optional[str] = None,
    evidence: Optional[dict[str, Any]] = None,
) -> Issue:
    """Build an :class:`Issue` in as few keystrokes as a scenario needs.

    Fingerprint defaults to a stable ``det|entity|id`` string so the incident
    fingerprint (sha1 of the root's) is reproducible across passes. ``evidence``
    lets a scenario carry a detector's recorded attribution (the correlation
    engine consults it -- e.g. a ``client.flaky`` finding self-attributed to
    ``device``).
    """
    return Issue(
        id=issue_id,
        fingerprint=fingerprint or f"{detector_key}|{entity_id}|{issue_id}",
        detector_key=detector_key,
        entity_id=entity_id,
        severity=severity,
        state=state,
        first_seen_ts=first_seen_ts,
        last_seen_ts=last_seen_ts if last_seen_ts is not None else first_seen_ts,
        title=title or f"{detector_key} on {entity_id}",
        evidence=evidence or {},
    )
