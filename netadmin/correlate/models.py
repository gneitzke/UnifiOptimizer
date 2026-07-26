"""Data shapes and the repository seam for the correlation engine (section 17).

Pure data plus one :class:`typing.Protocol`. The engine
(:mod:`netadmin.correlate.engine`) is I/O-free: it reads open issues + topology
and writes incidents *through* a :class:`CorrelationStore`, never with SQL of its
own. Tests supply an in-memory fake; the production
:class:`netadmin.correlate.store_repository.StoreCorrelationRepository` satisfies
the same Protocol against real SQLite.

Field shapes mirror the ``incidents`` and ``incident_members`` tables (migration
0004, verbatim from the architecture doc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from netadmin.correlate.topology import TopologyIndex
from netadmin.domain.entities import Timestamp
from netadmin.domain.types import Severity
from netadmin.issues.models import Issue

__all__ = [
    "IncidentRole",
    "IncidentState",
    "Incident",
    "IncidentMember",
    "CorrelationConfig",
    "CorrelationStore",
    "max_severity",
]


class IncidentRole:
    """Canonical ``incident_members.role`` values."""

    ROOT = "root"
    SYMPTOM = "symptom"


class IncidentState:
    """Canonical ``incidents.state`` values."""

    OPEN = "open"
    RESOLVED = "resolved"


# p1 is the most severe. Higher number == more severe, for a plain max().
_SEVERITY_ORDER: dict[Severity, int] = {Severity.P3: 1, Severity.P2: 2, Severity.P1: 3}


def max_severity(severities: list[Severity]) -> Severity:
    """The most severe of a set (p1 > p2 > p3); defaults to p3 when empty."""
    if not severities:
        return Severity.P3
    return max(severities, key=lambda s: _SEVERITY_ORDER[s])


@dataclass
class Incident:
    """One incident. Mirrors a row of the ``incidents`` table.

    ``id`` is ``None`` until :meth:`CorrelationStore.insert_incident` assigns one.
    ``fingerprint`` is ``sha1(root issue fingerprint)`` so the identity is stable
    across passes while the same root persists. ``first_seen_ts`` is preserved
    across updates so an incident's age keeps counting.
    """

    fingerprint: str
    root_issue_id: int
    severity: Severity
    state: str  # IncidentState.OPEN | IncidentState.RESOLVED
    first_seen_ts: Timestamp
    last_seen_ts: Timestamp
    title: str
    summary: str = ""
    id: Optional[int] = None
    resolved_ts: Optional[Timestamp] = None


@dataclass
class IncidentMember:
    """One row of ``incident_members``: an issue's role in an incident + why."""

    issue_id: int
    role: str  # IncidentRole.ROOT | IncidentRole.SYMPTOM
    rule: str  # audit token: the correlation rule that linked it (or "root")
    rationale: str  # one human line
    incident_id: Optional[int] = None


@dataclass
class CorrelationConfig:
    """Tunables for the correlation pass (section 17 defaults)."""

    # A symptom may predate its root by at most this many seconds and still be
    # attributed to it (the temporal guard). Detectors debounce over a few
    # cycles, so root and symptom first_seen legitimately differ by minutes; a
    # symptom that began *materially* before its cause cannot have been caused by
    # it, and the link is dropped.
    temporal_slack_s: int = 900

    # Optional *upper* bound: reject a symptom whose onset is more than this many
    # seconds *after* the root's, so a long-standing chronic root does not absorb
    # a freshly-arising, independently-caused symptom in its cell. ``None`` keeps
    # §17's one-sided guard (backward only), the default: a symptom genuinely
    # caused by a slowly-degrading root can cross threshold well after it, and the
    # root's preserved ``first_seen_ts`` (unchanged across reopens) is not the
    # current episode's onset, so a tight forward bound would wrongly split real
    # incidents. Deployments that value freshness over recall can set it.
    temporal_forward_window_s: Optional[int] = None


@runtime_checkable
class CorrelationStore(Protocol):
    """The only seam the engine touches. Production implements it with SQL; tests
    implement it in memory. No method here runs correlation logic -- the engine
    owns the grouping, the store owns persistence and the raw topology read.
    """

    # --- inputs ---
    def correlatable_issues(self) -> list[Issue]:
        """Open issues eligible for correlation: state ``active`` or ``resolving``.

        ``pending`` (unconfirmed) and ``resolved`` issues are excluded (section
        17, step 1) -- correlation reasons only over confirmed, still-open faults.
        """

    def topology(self) -> TopologyIndex:
        """A snapshot of the entity parent/child tree (+ any uplink edges)."""

    # --- incident rows ---
    def list_open_incidents(self) -> list[Incident]:
        """Every non-resolved incident (for idempotent identity + reconciliation)."""

    def get_open_incident_by_fingerprint(self, fingerprint: str) -> Optional[Incident]:
        """The single open incident with this fingerprint, or ``None``.

        The partial unique index guarantees at most one exists.
        """

    def insert_incident(self, incident: Incident) -> Incident:
        """Persist a new incident, returning it with ``id`` assigned."""

    def update_incident(self, incident: Incident) -> None:
        """Persist mutations to an existing incident (matched by ``id``)."""

    # --- members ---
    def replace_incident_members(self, incident_id: int, members: list[IncidentMember]) -> None:
        """Atomically replace an incident's member set (delete-then-insert).

        Recomputed every pass, so membership is authoritative each run.
        """

    def get_incident_members(self, incident_id: int) -> list[IncidentMember]:
        """The stored members of an incident (root first, then symptoms)."""
