"""An in-memory :class:`~netadmin.issues.models.IssueRepository` for tests.

Deliberately DB-like: it deep-copies issues on the way in and out so a test can
never accidentally observe an engine mutation that was not persisted through
:meth:`update_issue`. That surfaces "forgot to call update_issue" bugs the same
way a real SQLite store would.
"""

from __future__ import annotations

import copy
from typing import Optional

from netadmin.domain.entities import Entity, Timestamp
from netadmin.domain.types import IssueState
from netadmin.issues.models import Issue, IssueEvent


class InMemoryIssueRepository:
    """Satisfies the ``IssueRepository`` Protocol structurally."""

    def __init__(self) -> None:
        self._issues: dict[int, Issue] = {}
        self._events: list[IssueEvent] = []
        self._entities: dict[int, Entity] = {}
        self._next_issue_id = 1
        self._next_event_id = 1

    # --- test setup helpers (not part of the Protocol) ---
    def register_entity(self, entity: Entity) -> Entity:
        assert entity.entity_id is not None, "register_entity needs an entity_id"
        self._entities[entity.entity_id] = copy.deepcopy(entity)
        return entity

    def all_events(self, issue_id: Optional[int] = None) -> list[IssueEvent]:
        events = list(self._events)
        if issue_id is not None:
            events = [e for e in events if e.issue_id == issue_id]
        return copy.deepcopy(events)

    def event_kinds(self, issue_id: int) -> list[str]:
        return [e.kind for e in self._events if e.issue_id == issue_id]

    # --- IssueRepository Protocol ---
    def get_open_issue_by_fingerprint(self, fingerprint: str) -> Optional[Issue]:
        for issue in self._issues.values():
            if issue.fingerprint == fingerprint and issue.state is not IssueState.RESOLVED:
                return copy.deepcopy(issue)
        return None

    def get_recent_resolved_issue(
        self, fingerprint: str, resolved_since_ts: Timestamp
    ) -> Optional[Issue]:
        candidates = [
            issue
            for issue in self._issues.values()
            if issue.fingerprint == fingerprint
            and issue.state is IssueState.RESOLVED
            and issue.resolved_ts is not None
            and issue.resolved_ts >= resolved_since_ts
        ]
        if not candidates:
            return None
        newest = max(candidates, key=lambda i: i.resolved_ts or 0)
        return copy.deepcopy(newest)

    def insert_issue(self, issue: Issue) -> Issue:
        stored = copy.deepcopy(issue)
        stored.id = self._next_issue_id
        self._next_issue_id += 1
        self._issues[stored.id] = stored
        return copy.deepcopy(stored)

    def update_issue(self, issue: Issue) -> None:
        assert issue.id is not None, "cannot update an issue without an id"
        assert issue.id in self._issues, "update of unknown issue"
        self._issues[issue.id] = copy.deepcopy(issue)

    def delete_issue(self, issue_id: int) -> None:
        self._issues.pop(issue_id, None)
        self._events = [e for e in self._events if e.issue_id != issue_id]

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        issue = self._issues.get(issue_id)
        return copy.deepcopy(issue) if issue is not None else None

    def list_open_issues(self) -> list[Issue]:
        return [
            copy.deepcopy(issue)
            for issue in self._issues.values()
            if issue.state is not IssueState.RESOLVED
        ]

    def add_issue_event(self, event: IssueEvent) -> IssueEvent:
        stored = copy.deepcopy(event)
        stored.id = self._next_event_id
        self._next_event_id += 1
        self._events.append(stored)
        return copy.deepcopy(stored)

    def last_event_ts(self, issue_id: int, kind: str) -> Optional[Timestamp]:
        times = [e.ts for e in self._events if e.issue_id == issue_id and e.kind == kind]
        return max(times) if times else None

    def get_entity(self, entity_id: int) -> Optional[Entity]:
        entity = self._entities.get(entity_id)
        return copy.deepcopy(entity) if entity is not None else None
