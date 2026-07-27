"""Data shapes and the repository seam for the issue-lifecycle engine.

Everything here is pure data plus one :class:`typing.Protocol`. The engine
(:mod:`netadmin.issues.engine`) is deliberately I/O-free: it mutates
:class:`Issue` objects and writes :class:`IssueEvent` rows *through* an
:class:`IssueRepository`, never with SQL of its own. Tests supply an in-memory
fake; the real :mod:`netadmin.store` implementation satisfies the same Protocol.

Field shapes mirror the ``issues`` and ``issue_events`` tables in
``docs/ARCHITECTURE.md`` section 4; the state machine they drive is section 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from netadmin.domain.entities import Entity, Timestamp
from netadmin.domain.types import FixState, IssueState, Severity


class EventKind:
    """Canonical ``issue_events.kind`` values (section 4).

    Kept as bare string constants (not an ``Enum``) so they serialize into the
    ``issue_events.kind`` TEXT column with zero ceremony and downstream
    consumers (the HA notification hook, the WebSocket) can match on plain
    strings.
    """

    DETECTED = "detected"
    ESCALATED = "escalated"
    RESOLVING = "resolving"
    ACKED = "acked"
    SNOOZED = "snoozed"
    FIX_PROPOSED = "fix_proposed"
    FIX_APPLIED = "fix_applied"
    FIX_VERIFIED = "fix_verified"
    FIX_FAILED = "fix_failed"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    INVESTIGATED = "investigated"


@dataclass
class Issue:
    """One tracked issue. Mirrors a row of the ``issues`` table.

    ``id`` is ``None`` until :meth:`IssueRepository.insert_issue` assigns one.
    ``first_seen_ts`` is preserved across a reopen so ``now - first_seen_ts`` is
    the true "still broken, day 5" age. ``evidence`` is the decoded JSON blob
    (the repository owns serialization).
    """

    fingerprint: str
    detector_key: str
    severity: Severity
    state: IssueState
    first_seen_ts: Timestamp
    last_seen_ts: Timestamp
    title: str
    id: Optional[int] = None
    entity_id: Optional[int] = None
    resolved_ts: Optional[Timestamp] = None
    clear_streak: int = 0
    occurrences: int = 1
    ack_ts: Optional[Timestamp] = None
    snooze_until_ts: Optional[Timestamp] = None
    evidence: dict[str, Any] = field(default_factory=dict)
    fix_state: Optional[FixState] = None
    reopened_from: Optional[int] = None


@dataclass
class IssueEvent:
    """One row of the ``issue_events`` trail. ``kind`` is an :class:`EventKind`."""

    issue_id: int
    ts: Timestamp
    kind: str
    id: Optional[int] = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Transition:
    """A lifecycle event, handed to ``on_transition`` callbacks and returned
    from :meth:`IssueEngine.process_cycle`.

    ``kind`` matches the persisted :class:`EventKind`. ``from_state`` /
    ``to_state`` are ``None`` for non-state events (creation's ``from_state``,
    ack/snooze/fix bookkeeping). ``severity`` and ``title`` are carried from the
    owning issue so the WebSocket/HA layer can present a transition faithfully
    (its real severity, a human title) without a follow-up DB read on the event
    loop. This is the payload the HA/WebSocket layer forwards; the engine builds
    it but never depends on what a callback does.
    """

    issue_id: int
    fingerprint: str
    detector_key: str
    severity: Severity
    title: str
    kind: str
    ts: Timestamp
    from_state: Optional[IssueState]
    to_state: Optional[IssueState]
    detail: dict[str, Any] = field(default_factory=dict)


# Inhibition sources must activate on their first fire: a downed controller or
# device suppresses everything below it, and an inhibitor that waited the default
# M=3 cycles would let three cycles of noise through before the freeze took hold
# (section 7). These get M=1 unless a caller explicitly overrides them.
INHIBITION_SOURCE_M: dict[str, int] = {
    "infra.controller_down": 1,
    "infra.device_down": 1,
}

# Security findings must not wait out the default M=3 daily confirmations. A
# foreign AP broadcasting our SSID is either on the air or it is not; on a DAILY
# cadence, M=3 means three days of an unreported evil twin. One false alarm is
# cheaper than that miss, so this key confirms on its first fire. These also get
# M=1 unless a caller explicitly overrides them.
FIRST_FIRE_CONFIRM_M: dict[str, int] = {
    "wifi.rogue_ap": 1,
}


@dataclass
class EngineConfig:
    """Tunables for the state machine (section 7 defaults).

    ``detector_m`` / ``detector_k`` override the per-detector confirm/clear
    thresholds by ``detector_key``. The inhibition sources
    (``infra.controller_down`` / ``infra.device_down``) default to ``M == 1`` --
    see :data:`INHIBITION_SOURCE_M` -- so they activate and start inhibiting on
    the first fire, and the security detectors in
    :data:`FIRST_FIRE_CONFIRM_M` do the same so a rogue AP is reported the day it
    appears. A caller can still override any of them explicitly.
    """

    default_m: int = 3  # consecutive fires: pending -> active
    default_k: int = 6  # clean evaluations: resolving -> resolved
    reopen_window_s: int = 24 * 3600  # resolved within this -> reopen the row
    fix_window_s: int = 48 * 3600  # fix_applied arms verification for this long
    detector_m: dict[str, int] = field(default_factory=dict)
    detector_k: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Seed the M=1 defaults without clobbering any explicit per-detector
        # override the caller passed in ``detector_m``.
        for source in (INHIBITION_SOURCE_M, FIRST_FIRE_CONFIRM_M):
            for key, m in source.items():
                self.detector_m.setdefault(key, m)

    def m_for(self, detector_key: str) -> int:
        return self.detector_m.get(detector_key, self.default_m)

    def k_for(self, detector_key: str) -> int:
        return self.detector_k.get(detector_key, self.default_k)


@runtime_checkable
class IssueRepository(Protocol):
    """The only seam the engine touches. The real store implements this with
    SQL; tests implement it in memory. No method here runs business logic — the
    engine owns the state machine, the repository owns persistence.
    """

    # --- issue rows ---
    def get_open_issue_by_fingerprint(self, fingerprint: str) -> Optional[Issue]:
        """The single non-resolved issue with this fingerprint, or ``None``.

        The partial unique index guarantees at most one exists.
        """

    def get_recent_resolved_issue(
        self, fingerprint: str, resolved_since_ts: Timestamp
    ) -> Optional[Issue]:
        """Most recently resolved issue with this fingerprint whose
        ``resolved_ts >= resolved_since_ts``; ``None`` outside the window."""

    def insert_issue(self, issue: Issue) -> Issue:
        """Persist a new issue, returning it with ``id`` assigned."""

    def update_issue(self, issue: Issue) -> None:
        """Persist mutations to an existing issue (matched by ``id``)."""

    def delete_issue(self, issue_id: int) -> None:
        """Remove an issue and its events (used to discard unconfirmed
        ``pending`` issues that clear before reaching M)."""

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        """Fetch by primary key (for ack / snooze / fix operations)."""

    def list_open_issues(self) -> list[Issue]:
        """All non-resolved issues (the engine filters for inhibition causes)."""

    # --- event trail ---
    def add_issue_event(self, event: IssueEvent) -> IssueEvent:
        """Append one row to the ``issue_events`` trail."""

    def last_event_ts(self, issue_id: int, kind: str) -> Optional[Timestamp]:
        """Timestamp of the newest event of ``kind`` for this issue, or ``None``
        (used to locate the ``fix_applied`` moment that arms verification)."""

    # --- inventory (for entity-parentage inhibition) ---
    def get_entity(self, entity_id: int) -> Optional[Entity]:
        """Resolve an entity so the engine can walk ``parent_id`` chains."""


__all__ = [
    "EventKind",
    "Issue",
    "IssueEvent",
    "Transition",
    "EngineConfig",
    "INHIBITION_SOURCE_M",
    "FIRST_FIRE_CONFIRM_M",
    "IssueRepository",
]
