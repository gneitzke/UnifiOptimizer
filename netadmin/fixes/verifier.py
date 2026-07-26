"""The fix verifier (``docs/ARCHITECTURE.md`` sections 7 & 9).

The propose -> apply -> verify loop is closed by the issue engine, which already
arms a verification window when a ``fix_applied`` event lands and flips the issue
to ``fix_verified`` if it resolves inside that window (or ``fix_failed`` if it
refires). The verifier is the thin, read-mostly surface the fix engine hands the
UI/CLI: it *arms* the window for an applied change and *reports* where that fix
sits in its window, without re-implementing any of the state machine.

It touches only the engine's public seam -- :meth:`IssueEngine.apply_fix` to arm,
and the engine's repository (``get_issue`` / ``last_event_ts``) plus its config
(``fix_window_s``) to read -- so the single source of truth for verification stays
the issue engine, not a copy of its logic here.
"""

from __future__ import annotations

from typing import Any, Optional

from netadmin.domain.entities import Timestamp
from netadmin.domain.types import FixState, IssueState
from netadmin.fixes.models import VerificationResult, VerificationStatus
from netadmin.issues.engine import IssueEngine
from netadmin.issues.models import EventKind, Transition

__all__ = ["Verifier"]


class Verifier:
    """Arm and read an applied fix's verification window via the issue engine."""

    def __init__(self, engine: IssueEngine) -> None:
        self._engine = engine

    def arm(
        self, issue_id: int, now: Timestamp, detail: Optional[dict[str, Any]] = None
    ) -> Optional[Transition]:
        """Arm verification for ``issue_id`` by recording a ``fix_applied`` event.

        Delegates to :meth:`IssueEngine.apply_fix`, whose timestamp is exactly what
        the window is measured from. Call this once a real apply succeeds; the
        engine then verifies or fails the fix as the issue clears or refires.
        Returns the resulting transition (``None`` if the issue does not exist).
        """
        return self._engine.apply_fix(issue_id, now, detail)

    def check(self, issue_id: int, *, now: Optional[Timestamp] = None) -> VerificationResult:
        """Report where the applied fix sits in its verification window.

        Reads the issue's current ``fix_state`` -- the engine owns the transitions
        -- and, while a fix is still ``applied``, decides ``PENDING`` vs ``EXPIRED``
        from the ``fix_applied`` event time plus the engine's ``fix_window_s``.
        ``now`` is required only to distinguish an open window from a lapsed one;
        without it an ``applied`` fix reports ``PENDING``.
        """
        issue = self._engine.repo.get_issue(issue_id)
        if issue is None:
            return VerificationResult(issue_id=issue_id, status=VerificationStatus.NOT_ARMED)

        armed_ts = self._engine.repo.last_event_ts(issue_id, EventKind.FIX_APPLIED)
        window_end = None if armed_ts is None else armed_ts + self._engine.cfg.fix_window_s
        resolved_ts = issue.resolved_ts if issue.state is IssueState.RESOLVED else None

        status = self._status(issue.fix_state, armed_ts, window_end, now)
        return VerificationResult(
            issue_id=issue_id,
            status=status,
            armed_ts=armed_ts,
            window_end_ts=window_end,
            resolved_ts=resolved_ts,
        )

    @staticmethod
    def _status(
        fix_state: Optional[FixState],
        armed_ts: Optional[Timestamp],
        window_end: Optional[Timestamp],
        now: Optional[Timestamp],
    ) -> VerificationStatus:
        if fix_state is FixState.VERIFIED:
            return VerificationStatus.VERIFIED
        if fix_state is FixState.FAILED:
            return VerificationStatus.FAILED
        if fix_state is FixState.APPLIED:
            # Still applied: open window -> pending, lapsed window -> expired
            # (resolved-but-unverified). Without ``now`` we cannot judge lapse, so
            # we report the in-progress state rather than guess it expired.
            if now is not None and window_end is not None and now > window_end:
                return VerificationStatus.EXPIRED
            return VerificationStatus.PENDING
        # PROPOSED or no fix recorded: the window was never armed.
        return VerificationStatus.NOT_ARMED
