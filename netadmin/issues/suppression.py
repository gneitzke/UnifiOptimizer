"""Operator suppression, derived at read time (Gitea #49).

Suppression is the one attention mute the product ships: an operator parks an
issue's claim on attention (counts, badges, alert dispatch, HA sensors) without
touching a single measured number. It is **derived**, never a stored boolean:
three columns on the ``issues`` row (``suppressed_ts``, ``suppress_until_ts``,
``suppressed_severity``) plus an injected ``now`` decide whether an issue is
*suppressed right now*. Deriving it means expiry and severity-escalation void the
suppression with no sweeper, no engine write, and no lifecycle event at the
instant they take effect — the row already carries the two facts a reader needs.

The non-negotiable invariant this module protects by omission: nothing here
touches ``sle_minutes``, the health score, the offenders burden, or impact.
Suppressing an issue does not un-suffer the client-minutes it cost. This is the
*attention* side of the line only. Every count that shrinks because of
suppression must disclose the amount it shrank by ("9 open · 3 suppressed") —
that disclosure is the caller's job; this module only answers "is it suppressed
now".

Naming note: :mod:`netadmin.issues.inhibition` already uses "suppressed"
internally (``suppressed_scope``) for the engine's automatic cause-mutes-symptom
freeze. That is a *different* thing with a different owner — the engine mutes a
symptom while its cause is live. Operator suppression is a human parking an
attention claim. Keep the two straight: this module is operator-facing.

An issue is *suppressed now* when all three hold:

1. ``suppressed_ts`` is set (an operator suppressed it),
2. it has not expired (``suppress_until_ts`` is NULL, or ``now`` is before it),
3. its current severity is not *more severe* than ``suppressed_severity`` — a
   suppressed P3 that escalates to P1 is new information, and the suppression is
   void by derivation. The operator may re-suppress at the new severity.
"""

from __future__ import annotations

from typing import Any, Optional

from netadmin.domain.types import Severity

__all__ = [
    "is_suppressed",
    "issue_is_suppressed",
    "row_is_suppressed",
    "severity_rank",
]

# Lower rank == more severe, so "current is more severe than suppressed" is a
# strict ``<``. An unrecognised severity ranks *below* P3 (never more severe than
# anything), so a garbled value can never spuriously void a suppression.
_SEVERITY_RANK: dict[str, int] = {
    Severity.P1.value: 1,
    Severity.P2.value: 2,
    Severity.P3.value: 3,
}
_UNKNOWN_RANK = 99


def severity_rank(severity: Any) -> int:
    """Rank a severity (1 = P1, most severe). Unknown values rank last."""
    value = severity.value if isinstance(severity, Severity) else str(severity)
    return _SEVERITY_RANK.get(value.lower(), _UNKNOWN_RANK)


def is_suppressed(
    *,
    suppressed_ts: Optional[int],
    suppress_until_ts: Optional[int],
    suppressed_severity: Optional[str],
    severity: Any,
    now: int,
) -> bool:
    """Whether an issue with these fields is suppressed at ``now`` (the three rules).

    Scalar form so the same truth table is testable in isolation; the ``issue_``
    and ``row_`` adapters below feed it from the engine's dataclass and the
    store's rows respectively.
    """
    if suppressed_ts is None:
        return False
    # Rule 2 — expiry. ``now >= until`` is expired (the mute covers [ts, until)).
    if suppress_until_ts is not None and now >= suppress_until_ts:
        return False
    # Rule 3 — escalation void. Current severity strictly more severe than the
    # severity captured at suppression time lifts the mute by derivation.
    if suppressed_severity is not None and severity_rank(severity) < severity_rank(
        suppressed_severity
    ):
        return False
    return True


def issue_is_suppressed(issue: Any, now: int) -> bool:
    """Adapter: is this :class:`~netadmin.issues.models.Issue` suppressed at ``now``?"""
    return is_suppressed(
        suppressed_ts=getattr(issue, "suppressed_ts", None),
        suppress_until_ts=getattr(issue, "suppress_until_ts", None),
        suppressed_severity=getattr(issue, "suppressed_severity", None),
        severity=issue.severity,
        now=now,
    )


def _rget(row: Any, key: str) -> Any:
    """Read ``row[key]`` defensively — ``sqlite3.Row`` raises rather than returns
    None for an absent column, and a pre-migration row lacks these columns."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def row_is_suppressed(row: Any, now: int) -> bool:
    """Adapter: is this ``issues`` row (``sqlite3.Row`` or dict) suppressed at ``now``?"""
    return is_suppressed(
        suppressed_ts=_rget(row, "suppressed_ts"),
        suppress_until_ts=_rget(row, "suppress_until_ts"),
        suppressed_severity=_rget(row, "suppressed_severity"),
        severity=_rget(row, "severity"),
        now=now,
    )
