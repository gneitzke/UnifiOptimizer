"""The production binding: real SQLite store -> issue engine's Protocol.

Phase 0 built :mod:`netadmin.store` and :mod:`netadmin.issues` in parallel, and
their issue-facing surfaces drifted. The store speaks a uniform, row-based SQL
API (``get_open_issue`` returning ``sqlite3.Row``, ``insert_issue(**columns)``
returning an ``int`` id, ``update_issue(issue_id, **fields)``,
``record_issue_event(...)``). The engine is deliberately I/O-free (``docs/
ARCHITECTURE.md`` section 7): it mutates :class:`~netadmin.issues.models.Issue`
dataclasses and persists them *through* the
:class:`~netadmin.issues.models.IssueRepository` Protocol.

Rather than warp either side -- forcing the store to return dataclasses would
break its uniform row API, and forcing the pure-logic engine to consume rows
would violate section 7 and its unit-test contract -- the reconciliation is one
deliberate adapter here, in production, at the seam between the two layers. This
is the object a composition root (daemon / tech-visit / server) constructs to run
the engine against the real database:

    store = Repository.open(db_path)
    engine = IssueEngine(StoreIssueRepository(store))

It touches no SQL of its own (section 4 keeps SQL inside ``Repository``); it only
translates between the store's rows/columns and the engine's dataclasses, and
routes deletes through the store's :meth:`Repository.delete_issue` seam.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from netadmin.domain.entities import Entity, Timestamp
from netadmin.domain.types import EntityType, FixState, IssueState, Severity
from netadmin.issues.models import Issue, IssueEvent
from netadmin.store.repository import Repository

__all__ = ["StoreIssueRepository"]


def _issue_from_row(row: sqlite3.Row) -> Issue:
    """Rehydrate an :class:`Issue` dataclass from an ``issues`` row."""
    return Issue(
        id=row["id"],
        fingerprint=row["fingerprint"],
        detector_key=row["detector_key"],
        entity_id=row["entity_id"],
        severity=Severity(row["severity"]),
        state=IssueState(row["state"]),
        first_seen_ts=row["first_seen_ts"],
        last_seen_ts=row["last_seen_ts"],
        resolved_ts=row["resolved_ts"],
        clear_streak=row["clear_streak"],
        occurrences=row["occurrences"],
        ack_ts=row["ack_ts"],
        snooze_until_ts=row["snooze_until_ts"],
        title=row["title"],
        evidence=json.loads(row["evidence"] or "{}"),
        fix_state=FixState(row["fix_state"]) if row["fix_state"] else None,
        reopened_from=row["reopened_from"],
    )


def _entity_from_row(row: sqlite3.Row) -> Entity:
    """Rehydrate an :class:`Entity` from an ``entities`` row (parentage walks)."""
    return Entity(
        entity_type=EntityType(row["entity_type"]),
        native_id=row["native_id"],
        site_id=row["site_id"],
        entity_id=row["entity_id"],
        parent_id=row["parent_id"],
        name=row["name"],
        model=row["model"],
        first_seen_ts=row["first_seen_ts"],
        last_seen_ts=row["last_seen_ts"],
    )


def _issue_columns(issue: Issue) -> dict[str, object]:
    """Project an :class:`Issue` onto the store's ``issues`` column kwargs.

    Enums are unwrapped to their string values; ``evidence`` stays a ``dict`` and
    is JSON-encoded by the store, which owns serialization.
    """
    return {
        "fingerprint": issue.fingerprint,
        "detector_key": issue.detector_key,
        "entity_id": issue.entity_id,
        "severity": issue.severity.value,
        "state": issue.state.value,
        "first_seen_ts": issue.first_seen_ts,
        "last_seen_ts": issue.last_seen_ts,
        "resolved_ts": issue.resolved_ts,
        "clear_streak": issue.clear_streak,
        "occurrences": issue.occurrences,
        "ack_ts": issue.ack_ts,
        "snooze_until_ts": issue.snooze_until_ts,
        "title": issue.title,
        "evidence": issue.evidence,
        "fix_state": issue.fix_state.value if issue.fix_state else None,
        "reopened_from": issue.reopened_from,
    }


class StoreIssueRepository:
    """Adapt a concrete :class:`~netadmin.store.repository.Repository` to the
    engine's :class:`~netadmin.issues.models.IssueRepository` Protocol.

    Structural (``runtime_checkable``) conformance is asserted in the integration
    test; nothing here runs business logic -- the engine owns the state machine,
    the store owns persistence, this only translates between their vocabularies.
    """

    def __init__(self, store: Repository) -> None:
        self._store = store

    # --- issue rows ---
    def get_open_issue_by_fingerprint(self, fingerprint: str) -> Optional[Issue]:
        row = self._store.get_open_issue(fingerprint)
        return _issue_from_row(row) if row is not None else None

    def get_recent_resolved_issue(
        self, fingerprint: str, resolved_since_ts: Timestamp
    ) -> Optional[Issue]:
        # Indexed single-row lookup in SQL (idx_issues_fp_resolved), not a
        # full-table load + Python scan: this runs every new-fingerprint fire and
        # the resolved history is never pruned.
        row = self._store.get_recent_resolved_issue(fingerprint, resolved_since_ts)
        return _issue_from_row(row) if row is not None else None

    def insert_issue(self, issue: Issue) -> Issue:
        issue.id = int(self._store.insert_issue(**_issue_columns(issue)))
        return issue

    def update_issue(self, issue: Issue) -> None:
        assert issue.id is not None, "cannot update an issue without an id"
        self._store.update_issue(issue.id, **_issue_columns(issue))

    def delete_issue(self, issue_id: int) -> None:
        self._store.delete_issue(issue_id)

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        row = self._store.get_issue(issue_id)
        return _issue_from_row(row) if row is not None else None

    def list_open_issues(self) -> list[Issue]:
        return [_issue_from_row(r) for r in self._store.list_issues(open_only=True)]

    # --- event trail ---
    def add_issue_event(self, event: IssueEvent) -> IssueEvent:
        event.id = int(
            self._store.record_issue_event(
                event.issue_id, event.kind, ts=event.ts, detail=event.detail
            )
        )
        return event

    def last_event_ts(self, issue_id: int, kind: str) -> Optional[Timestamp]:
        times = [r["ts"] for r in self._store.list_issue_events(issue_id) if r["kind"] == kind]
        return max(times) if times else None

    # --- inventory (entity-parentage inhibition) ---
    def get_entity(self, entity_id: int) -> Optional[Entity]:
        row = self._store.get_entity(entity_id)
        return _entity_from_row(row) if row is not None else None
