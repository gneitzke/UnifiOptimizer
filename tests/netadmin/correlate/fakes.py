"""An in-memory :class:`~netadmin.correlate.models.CorrelationStore` for tests.

DB-like: it deep-copies incidents/members on the way in and out so a test can
never observe an engine mutation that was not persisted through the Protocol --
surfacing "forgot to update_incident" bugs the same way real SQLite would. It
also assigns incident ids from a counter and honours the open-fingerprint
uniqueness the partial index enforces in the store.
"""

from __future__ import annotations

import copy
from typing import Optional

from netadmin.correlate.models import Incident, IncidentMember, IncidentState
from netadmin.correlate.topology import TopologyIndex
from netadmin.domain.types import IssueState
from netadmin.issues.models import Issue

# Confirmed-and-open: what correlation reasons over (pending/resolved excluded).
_CORRELATABLE_STATES = (IssueState.ACTIVE, IssueState.RESOLVING)


class InMemoryCorrelationStore:
    """Satisfies the ``CorrelationStore`` Protocol structurally."""

    def __init__(self, issues: list[Issue], topology: TopologyIndex) -> None:
        self._issues = list(issues)
        self._topology = topology
        self._incidents: dict[int, Incident] = {}
        self._members: dict[int, list[IncidentMember]] = {}
        self._next_id = 1

    # --- test setup helpers (not part of the Protocol) ---
    def set_issues(self, issues: list[Issue]) -> None:
        self._issues = list(issues)

    def all_incidents(self) -> list[Incident]:
        return [copy.deepcopy(i) for i in self._incidents.values()]

    # --- CorrelationStore Protocol ---
    def correlatable_issues(self) -> list[Issue]:
        # Mirror the store: only confirmed, still-open issues are correlatable.
        return [copy.deepcopy(i) for i in self._issues if i.state in _CORRELATABLE_STATES]

    def topology(self) -> TopologyIndex:
        return self._topology

    def list_open_incidents(self) -> list[Incident]:
        return [
            copy.deepcopy(i) for i in self._incidents.values() if i.state != IncidentState.RESOLVED
        ]

    def get_open_incident_by_fingerprint(self, fingerprint: str) -> Optional[Incident]:
        for inc in self._incidents.values():
            if inc.fingerprint == fingerprint and inc.state != IncidentState.RESOLVED:
                return copy.deepcopy(inc)
        return None

    def insert_incident(self, incident: Incident) -> Incident:
        stored = copy.deepcopy(incident)
        stored.id = self._next_id
        self._next_id += 1
        self._incidents[stored.id] = stored
        return copy.deepcopy(stored)

    def update_incident(self, incident: Incident) -> None:
        assert incident.id is not None, "cannot update an incident without an id"
        assert incident.id in self._incidents, "update of unknown incident"
        self._incidents[incident.id] = copy.deepcopy(incident)

    def replace_incident_members(self, incident_id: int, members: list[IncidentMember]) -> None:
        assert incident_id in self._incidents, "members for unknown incident"
        self._members[incident_id] = [copy.deepcopy(m) for m in members]

    def get_incident_members(self, incident_id: int) -> list[IncidentMember]:
        members = self._members.get(incident_id, [])
        # root first, then by issue id -- mirrors the store's ORDER BY.
        ordered = sorted(members, key=lambda m: (0 if m.role == "root" else 1, m.issue_id))
        return [copy.deepcopy(m) for m in ordered]
