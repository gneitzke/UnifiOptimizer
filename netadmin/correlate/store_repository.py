"""The production binding: real SQLite store -> correlation engine's Protocol.

The store speaks a uniform row/column SQL API; the engine
(:mod:`netadmin.correlate.engine`) is pure logic over
:class:`~netadmin.correlate.models.Incident` /
:class:`~netadmin.issues.models.Issue` dataclasses and a
:class:`~netadmin.correlate.topology.TopologyIndex`. This adapter is the one
deliberate seam that translates between them, mirroring
:class:`netadmin.issues.store_repository.StoreIssueRepository`:

    store = Repository.open(db_path)
    engine = CorrelationEngine(StoreCorrelationRepository(store))

It touches no SQL of its own (section 4 keeps SQL inside ``Repository``); it only
reshapes the store's rows into the engine's dataclasses and builds the topology
snapshot. Wired-uplink ("feeds") edges are not persisted by the current ingest
layer, so the topology is parent/child only here -- the ``FEEDS`` rules stay
dormant in production until an uplink edge exists (deliberately conservative; see
``topology.py``).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from netadmin.correlate.models import Incident, IncidentMember
from netadmin.correlate.topology import TopologyIndex, TopoNode
from netadmin.domain.types import Severity
from netadmin.issues.models import Issue
from netadmin.issues.store_repository import _issue_from_row
from netadmin.store.repository import Repository

__all__ = ["StoreCorrelationRepository"]


def _incident_from_row(row: sqlite3.Row) -> Incident:
    return Incident(
        id=row["id"],
        fingerprint=row["fingerprint"],
        root_issue_id=row["root_issue_id"],
        severity=Severity(row["severity"]),
        state=row["state"],
        first_seen_ts=row["first_seen_ts"],
        last_seen_ts=row["last_seen_ts"],
        resolved_ts=row["resolved_ts"],
        title=row["title"],
        summary=row["summary"],
    )


def _member_from_row(row: sqlite3.Row) -> IncidentMember:
    return IncidentMember(
        incident_id=row["incident_id"],
        issue_id=row["issue_id"],
        role=row["role"],
        rule=row["rule"],
        rationale=row["rationale"],
    )


def _topo_node_from_row(row: sqlite3.Row) -> TopoNode:
    return TopoNode(
        entity_id=row["entity_id"],
        entity_type=row["entity_type"],
        parent_id=row["parent_id"],
        site_id=row["site_id"],
        name=row["name"],
        native_id=row["native_id"],
    )


class StoreCorrelationRepository:
    """Adapt a concrete :class:`Repository` to the engine's
    :class:`~netadmin.correlate.models.CorrelationStore` Protocol.
    """

    def __init__(self, store: Repository) -> None:
        self._store = store

    # --- inputs ---
    def correlatable_issues(self) -> list[Issue]:
        return [_issue_from_row(r) for r in self._store.list_correlatable_issues()]

    def topology(self) -> TopologyIndex:
        nodes = [_topo_node_from_row(r) for r in self._store.entity_topology()]
        return TopologyIndex(nodes)

    # --- incident rows ---
    def list_open_incidents(self) -> list[Incident]:
        return [_incident_from_row(r) for r in self._store.list_incidents(open_only=True)]

    def get_open_incident_by_fingerprint(self, fingerprint: str) -> Optional[Incident]:
        row = self._store.get_open_incident(fingerprint)
        return _incident_from_row(row) if row is not None else None

    def insert_incident(self, incident: Incident) -> Incident:
        incident.id = int(
            self._store.insert_incident(
                fingerprint=incident.fingerprint,
                root_issue_id=incident.root_issue_id,
                severity=incident.severity.value,
                state=incident.state,
                first_seen_ts=incident.first_seen_ts,
                last_seen_ts=incident.last_seen_ts,
                title=incident.title,
                summary=incident.summary,
                resolved_ts=incident.resolved_ts,
            )
        )
        return incident

    def update_incident(self, incident: Incident) -> None:
        assert incident.id is not None, "cannot update an incident without an id"
        self._store.update_incident(
            incident.id,
            fingerprint=incident.fingerprint,
            root_issue_id=incident.root_issue_id,
            severity=incident.severity.value,
            state=incident.state,
            first_seen_ts=incident.first_seen_ts,
            last_seen_ts=incident.last_seen_ts,
            resolved_ts=incident.resolved_ts,
            title=incident.title,
            summary=incident.summary,
        )

    # --- members ---
    def replace_incident_members(self, incident_id: int, members: list[IncidentMember]) -> None:
        self._store.replace_incident_members(
            incident_id,
            [
                {
                    "issue_id": m.issue_id,
                    "role": m.role,
                    "rule": m.rule,
                    "rationale": m.rationale,
                }
                for m in members
            ],
        )

    def get_incident_members(self, incident_id: int) -> list[IncidentMember]:
        return [_member_from_row(r) for r in self._store.list_incident_members(incident_id)]
