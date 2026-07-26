"""Fix verification (section 7): fix_applied arms a 48 h window. Resolve inside
it -> fix_verified; a refire inside it -> fix_failed; a resolve after it expires
-> resolved-but-unverified (no fix event, fix_state stays applied).
"""

from __future__ import annotations

from netadmin.domain.types import FixState, IssueState
from netadmin.issues.engine import fingerprint
from netadmin.issues.models import EventKind

TS = 1_700_000_000
HOUR = 3600


def _to_active(engine, finding, *, start=TS):
    for i in range(3):
        engine.process_cycle(start + i * 60, findings=[finding])


def _clear_n(engine, fp, n, *, start, step=60):
    for i in range(n):
        engine.process_cycle(start + i * step, cleared=[fp])


# ---------------------------------------------------------------------------
# verified
# ---------------------------------------------------------------------------
def test_resolve_inside_window_marks_fix_verified(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)
    assert repo.get_open_issue_by_fingerprint(fp).fix_state is FixState.APPLIED

    _clear_n(engine, fp, 6, start=applied_at + HOUR)  # resolves well within 48 h
    resolved = repo.get_recent_resolved_issue(fp, 0)
    assert resolved.state is IssueState.RESOLVED
    assert resolved.fix_state is FixState.VERIFIED
    kinds = repo.event_kinds(resolved.id)
    assert kinds.count(EventKind.FIX_VERIFIED) == 1
    assert kinds.index(EventKind.RESOLVED) < kinds.index(EventKind.FIX_VERIFIED)


# ---------------------------------------------------------------------------
# failed
# ---------------------------------------------------------------------------
def test_refire_inside_window_marks_fix_failed(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)

    _clear_n(engine, fp, 3, start=applied_at + 60)  # starts resolving
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.RESOLVING

    engine.process_cycle(applied_at + HOUR, findings=[finding])  # refire, within window
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.fix_state is FixState.FAILED
    assert repo.event_kinds(issue.id).count(EventKind.FIX_FAILED) == 1


def test_active_still_firing_past_window_marks_fix_failed(engine, repo, make_finding) -> None:
    # The most common failure mode: a fix is applied while the issue is ACTIVE and
    # the condition never clears (it keeps firing every cycle). Once the window
    # lapses the fix must be marked FAILED -- not left stuck at APPLIED forever.
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)

    # Still firing inside the 48 h window: no premature failure verdict yet.
    engine.process_cycle(applied_at + HOUR, findings=[finding])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.fix_state is FixState.APPLIED
    assert EventKind.FIX_FAILED not in repo.event_kinds(issue.id)

    # Fire again after the window has lapsed -> fix_failed, emitted exactly once.
    engine.process_cycle(applied_at + 49 * HOUR, findings=[finding])
    engine.process_cycle(applied_at + 50 * HOUR, findings=[finding])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE  # still the same open problem
    assert issue.fix_state is FixState.FAILED
    assert repo.event_kinds(issue.id).count(EventKind.FIX_FAILED) == 1


def test_premature_verified_reopen_inside_window_marks_fix_failed(
    engine, repo, make_finding
) -> None:
    # A fix resolves the issue quickly (-> VERIFIED), but the problem returns while
    # still inside the fix window: the verification was premature and must be
    # downgraded to fix_failed on reopen.
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)
    _clear_n(engine, fp, 6, start=applied_at + HOUR)  # resolves -> VERIFIED
    resolved = repo.get_recent_resolved_issue(fp, 0)
    assert resolved.fix_state is FixState.VERIFIED

    # Refire ~5 h after apply: inside both the 24 h reopen window and 48 h fix
    # window -> reopen AND downgrade the premature verification.
    engine.process_cycle(applied_at + 5 * HOUR, findings=[finding])
    reopened = repo.get_open_issue_by_fingerprint(fp)
    assert reopened.id == resolved.id  # same row, reopened
    assert reopened.state is IssueState.ACTIVE
    assert reopened.fix_state is FixState.FAILED
    assert repo.event_kinds(reopened.id).count(EventKind.FIX_FAILED) == 1


def test_verified_recurrence_within_window_records_fix_failed(engine, repo, make_finding) -> None:
    # Finding 9: recurrence AFTER a fix was VERIFIED (not merely APPLIED), while
    # still inside the verification window, is a failed fix. The check must arm on
    # VERIFIED as well as APPLIED. Here the issue resolves -> VERIFIED, then the
    # very same problem fires again a few cycles later inside the window.
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)
    _clear_n(engine, fp, 6, start=applied_at + HOUR)  # resolves -> VERIFIED
    resolved = repo.get_recent_resolved_issue(fp, 0)
    assert resolved.fix_state is FixState.VERIFIED
    assert EventKind.FIX_FAILED not in repo.event_kinds(resolved.id)

    # Recurrence ~10 h after apply: inside both reopen and fix windows.
    engine.process_cycle(applied_at + 10 * HOUR, findings=[finding])
    reopened = repo.get_open_issue_by_fingerprint(fp)
    assert reopened.id == resolved.id
    assert reopened.state is IssueState.ACTIVE
    assert reopened.fix_state is FixState.FAILED  # verification was not real
    assert repo.event_kinds(reopened.id).count(EventKind.FIX_FAILED) == 1


def test_verified_reopen_after_fix_window_stays_verified(engine, repo, make_finding) -> None:
    # A refire long after the fix window (but still a reopen) is a fresh recurrence,
    # not a fix failure: the verified fix held for the whole window.
    finding = make_finding()
    # Widen the reopen window so the refire still reopens the row after >48 h.
    engine.cfg.reopen_window_s = 96 * HOUR
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)
    _clear_n(engine, fp, 6, start=applied_at + HOUR)  # resolves -> VERIFIED
    resolved = repo.get_recent_resolved_issue(fp, 0)
    assert resolved.fix_state is FixState.VERIFIED

    engine.process_cycle(applied_at + 60 * HOUR, findings=[finding])  # past 48 h fix window
    reopened = repo.get_open_issue_by_fingerprint(fp)
    assert reopened.id == resolved.id
    assert reopened.fix_state is FixState.VERIFIED  # not downgraded
    assert EventKind.FIX_FAILED not in repo.event_kinds(reopened.id)


def test_fix_failed_not_emitted_when_window_not_armed(engine, repo, make_finding) -> None:
    # No fix applied: a plain snap-back must not invent a fix_failed event.
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)
    _clear_n(engine, fp, 3, start=TS + 1000)
    engine.process_cycle(TS + 5000, findings=[finding])

    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.fix_state is None
    assert EventKind.FIX_FAILED not in repo.event_kinds(issue.id)


# ---------------------------------------------------------------------------
# expired-unverified
# ---------------------------------------------------------------------------
def test_resolve_after_window_is_unverified(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)

    applied_at = TS + 500
    engine.apply_fix(repo.get_open_issue_by_fingerprint(fp).id, applied_at)

    # 6 clears spaced 10 h apart: resolve lands ~60 h after apply, past the 48 h.
    _clear_n(engine, fp, 6, start=applied_at + 10 * HOUR, step=10 * HOUR)
    resolved = repo.get_recent_resolved_issue(fp, 0)
    assert resolved.state is IssueState.RESOLVED
    assert resolved.fix_state is FixState.APPLIED  # never verified
    assert EventKind.FIX_VERIFIED not in repo.event_kinds(resolved.id)


# ---------------------------------------------------------------------------
# fix trail plumbing
# ---------------------------------------------------------------------------
def test_propose_then_apply_records_trail(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id

    engine.propose_fix(issue_id, TS + 100, {"template": "port_cycle"})
    engine.apply_fix(issue_id, TS + 200, {"template": "port_cycle"})

    kinds = repo.event_kinds(issue_id)
    assert EventKind.FIX_PROPOSED in kinds
    assert EventKind.FIX_APPLIED in kinds
    assert repo.last_event_ts(issue_id, EventKind.FIX_APPLIED) == TS + 200
    assert repo.get_issue(issue_id).fix_state is FixState.APPLIED


def test_fix_methods_noop_on_unknown_issue(engine) -> None:
    assert engine.apply_fix(9999, TS) is None
    assert engine.propose_fix(9999, TS) is None
    assert engine.investigated(9999, TS) is None
