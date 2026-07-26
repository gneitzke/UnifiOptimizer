"""Value objects shared by the outbound alert channels (``docs/ARCHITECTURE.md`` 20).

Pure data. Nothing here does I/O, reads a clock, or imports a provider library, so
the policy and format layers can be tested as plain functions.

The central shape is :class:`AlertEvent`: a lifecycle :class:`~netadmin.issues.models.Transition`
paired with the *normalised* event class an operator actually cares about
(``opened`` / ``reopened`` / ``resolved``). The engine's eleven ``EventKind`` values
are an audit trail; these three are the notification vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from netadmin.domain.types import Severity
from netadmin.issues.models import Transition

__all__ = [
    "OPENED",
    "REOPENED",
    "RESOLVED",
    "ALERT_EVENTS",
    "OPEN_EVENTS",
    "severity_rank",
    "meets_min_severity",
    "AlertEvent",
    "DigestSummary",
    "Payload",
    "ChannelStatus",
    "STATUS_OK",
    "STATUS_FAILING",
    "STATUS_INERT",
]

# --- the notification vocabulary ------------------------------------------- #

OPENED = "opened"
REOPENED = "reopened"
RESOLVED = "resolved"

ALERT_EVENTS: tuple[str, ...] = (OPENED, REOPENED, RESOLVED)

# The two classes that announce a *live* problem. Grouped because the dedupe rule
# treats them identically: either one marks a fingerprint "currently announced".
OPEN_EVENTS = frozenset({OPENED, REOPENED})

# Lower rank == more severe, so a ``min_severity`` floor is a simple ``<=``.
_SEVERITY_RANK: dict[str, int] = {
    Severity.P1.value: 1,
    Severity.P2.value: 2,
    Severity.P3.value: 3,
}
# An unrecognised severity ranks below P3 rather than above P1: an unknown value
# must never escalate itself past a channel's floor.
_UNKNOWN_RANK = 99


def severity_rank(severity: Any) -> int:
    """Rank a severity (1 = P1, most severe). Unknown values rank last."""
    value = severity.value if isinstance(severity, Severity) else str(severity)
    return _SEVERITY_RANK.get(value.lower(), _UNKNOWN_RANK)


def meets_min_severity(severity: Any, minimum: Any) -> bool:
    """True when ``severity`` is at least as severe as a channel's ``min_severity``."""
    return severity_rank(severity) <= severity_rank(minimum)


# --- the event a channel decides on ---------------------------------------- #


@dataclass(frozen=True)
class AlertEvent:
    """One notifiable lifecycle change: a normalised class plus its transition.

    Everything a payload builder needs is already on the transition (severity,
    title, detector, fingerprint), which is why the whole alert subsystem needs no
    store handle and never touches SQL.
    """

    event: str
    transition: Transition

    @property
    def fingerprint(self) -> str:
        return self.transition.fingerprint

    @property
    def severity(self) -> str:
        sev = self.transition.severity
        return sev.value if isinstance(sev, Severity) else str(sev)

    @property
    def title(self) -> str:
        return self.transition.title

    @property
    def detector_key(self) -> str:
        return self.transition.detector_key

    @property
    def issue_id(self) -> int:
        return self.transition.issue_id

    @property
    def ts(self) -> int:
        return int(self.transition.ts)

    @property
    def is_open(self) -> bool:
        """True for ``opened``/``reopened`` (a live problem), False for ``resolved``."""
        return self.event in OPEN_EVENTS


@dataclass(frozen=True)
class DigestSummary:
    """A coalesced batch of events, rendered as one message when a channel floods.

    Counts are exact (nothing is silently lost); ``top_titles`` is a short,
    severity-ordered sample so the message stays readable.
    """

    channel: str
    count: int
    by_event: dict[str, int]
    by_severity: dict[str, int]
    top_titles: list[str]
    first_ts: int
    last_ts: int

    @property
    def worst_severity(self) -> str:
        """The most severe severity present, for colour/priority selection."""
        for sev in (Severity.P1.value, Severity.P2.value, Severity.P3.value):
            if self.by_severity.get(sev):
                return sev
        return Severity.P3.value


@dataclass(frozen=True)
class Payload:
    """One ready-to-POST body: a JSON doc *or* raw text, plus provider headers.

    Field names mirror ``httpx``'s so the transport forwards them verbatim.
    """

    json: Optional[dict[str, Any]] = None
    content: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)


# --- per-channel accounting ------------------------------------------------ #

STATUS_OK = "ok"
STATUS_FAILING = "failing"
STATUS_INERT = "inert"


@dataclass
class ChannelStatus:
    """Live counters for one channel, projected straight into ``/api/health``.

    ``last_error`` carries an exception *type name* or an HTTP status only. The
    delivery URL is a credential and never appears here, in a log line, or in an
    error message -- channels are identified by name.
    """

    name: str
    type: str
    configured: bool = False
    status: str = STATUS_INERT
    delivered: int = 0
    failed: int = 0
    dropped: int = 0
    digested: int = 0
    consecutive_failures: int = 0
    last_success_ts: Optional[int] = None
    last_error: Optional[str] = None

    def as_health(self) -> dict[str, Any]:
        """The ``/api/health`` projection (section 12)."""
        return {
            "name": self.name,
            "type": self.type,
            "configured": self.configured,
            "status": self.status,
            "delivered": self.delivered,
            "failed": self.failed,
            "dropped": self.dropped,
            "digested": self.digested,
            "last_success_ts": self.last_success_ts,
            "last_error": self.last_error,
        }
