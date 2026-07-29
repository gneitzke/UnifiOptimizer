"""Operator suppression (Gitea #49): the engine hooks and the read-time derivation.

Suppress/unsuppress are ``ack``/``snooze``'s siblings — they set fields, write an
event, emit, and touch no state-machine branch. Suppression is DERIVED from the
fields at read time (:mod:`netadmin.issues.suppression`), so expiry and
severity-escalation void it with no engine write. These tests pin both halves:
the engine mutation and the derivation truth table.
"""

from __future__ import annotations

from netadmin.domain.types import IssueState, Severity
from netadmin.issues.engine import fingerprint
from netadmin.issues.models import EventKind
from netadmin.issues.suppression import is_suppressed, issue_is_suppressed

TS = 1_700_000_000
HOUR = 3600
DAY = 24 * HOUR


def _to_active(engine, finding, *, start=TS):
    for i in range(3):
        engine.process_cycle(start + i * 60, findings=[finding])


# --------------------------------------------------------------------------- #
# Engine hooks
# --------------------------------------------------------------------------- #


def test_suppress_sets_fields_and_records_event(engine, repo, make_finding) -> None:
    finding = make_finding(severity=Severity.P2)
    _to_active(engine, finding)
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id

    tr = engine.suppress(issue_id, TS + 100, until_ts=TS + 8 * HOUR)
    issue = repo.get_issue(issue_id)
    assert issue.suppressed_ts == TS + 100
    assert issue.suppress_until_ts == TS + 8 * HOUR
    assert issue.suppressed_severity is Severity.P2

    events = [e for e in repo.all_events(issue_id) if e.kind == EventKind.SUPPRESSED]
    assert events[-1].detail["until_ts"] == TS + 8 * HOUR
    assert events[-1].detail["severity"] == "p2"
    assert events[-1].detail["source"] == "operator"
    # The emitted transition carries the suppressed flag for the alert gate.
    assert tr.suppressed is True


def test_suppress_indefinite_has_null_expiry(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id

    engine.suppress(issue_id, TS + 100)  # no until_ts -> "until I unsuppress"
    issue = repo.get_issue(issue_id)
    assert issue.suppress_until_ts is None
    event = [e for e in repo.all_events(issue_id) if e.kind == EventKind.SUPPRESSED][-1]
    assert "until_ts" not in event.detail


def test_unsuppress_clears_fields_and_records_event(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id
    engine.suppress(issue_id, TS + 100)

    tr = engine.unsuppress(issue_id, TS + 200)
    issue = repo.get_issue(issue_id)
    assert issue.suppressed_ts is None
    assert issue.suppress_until_ts is None
    assert issue.suppressed_severity is None
    assert EventKind.UNSUPPRESSED in repo.event_kinds(issue_id)
    assert tr.suppressed is False


def test_suppress_unsuppress_noop_on_unknown_issue(engine) -> None:
    assert engine.suppress(9999, TS) is None
    assert engine.unsuppress(9999, TS) is None


def test_engine_transitions_of_a_suppressed_issue_carry_the_flag(
    engine, repo, make_finding
) -> None:
    """A later lifecycle transition of a suppressed issue carries ``suppressed`` so
    the alert gate needs no DB read — here the first clean check (-> RESOLVING)."""
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)
    issue_id = repo.get_open_issue_by_fingerprint(fp).id
    engine.suppress(issue_id, TS + 100)

    (transition,) = engine.process_cycle(TS + 1000, cleared=[fp])
    assert transition.kind == EventKind.RESOLVING
    assert transition.suppressed is True


def test_suppress_does_not_block_escalation(engine, repo, make_finding) -> None:
    finding = make_finding()
    engine.process_cycle(TS, findings=[finding])  # pending
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id
    engine.suppress(issue_id, TS + 10)

    engine.process_cycle(TS + 60, findings=[finding])
    engine.process_cycle(TS + 120, findings=[finding])
    assert repo.get_open_issue_by_fingerprint(fingerprint(finding)).state is IssueState.ACTIVE


def test_suppress_does_not_block_resolve_or_clear_streak(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)
    issue_id = repo.get_open_issue_by_fingerprint(fp).id
    engine.suppress(issue_id, TS + 100)

    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None
    assert repo.get_recent_resolved_issue(fp, 0).state is IssueState.RESOLVED


# --------------------------------------------------------------------------- #
# Row identity: survives a same-row reopen, a new row starts fresh
# --------------------------------------------------------------------------- #


def test_suppression_survives_same_row_reopen(engine, repo, make_finding) -> None:
    """A refire inside the 24h reopen window reuses the same row, so the mute the
    operator set on the flapping issue survives exactly the flapping it targets."""
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)
    issue_id = repo.get_open_issue_by_fingerprint(fp).id
    engine.suppress(issue_id, TS + 100)

    # Resolve it (K=6 clean checks).
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None

    # Refire 2h later — inside the 24h window -> same row reopens.
    engine.process_cycle(TS + 2 * HOUR, findings=[finding])
    reopened = repo.get_open_issue_by_fingerprint(fp)
    assert reopened.id == issue_id  # same row
    assert reopened.suppressed_ts == TS + 100  # mute preserved
    assert issue_is_suppressed(reopened, TS + 2 * HOUR) is True


def test_new_row_after_reopen_window_starts_unsuppressed(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)
    issue_id = repo.get_open_issue_by_fingerprint(fp).id
    engine.suppress(issue_id, TS + 100)
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])

    # Refire 25h later — past the window -> a brand-new row, a fresh claim.
    engine.process_cycle(TS + 25 * HOUR, findings=[finding])
    fresh = repo.get_open_issue_by_fingerprint(fp)
    assert fresh.id != issue_id
    assert fresh.suppressed_ts is None
    assert issue_is_suppressed(fresh, TS + 25 * HOUR) is False


# --------------------------------------------------------------------------- #
# Escalation void, through the engine's real refire path
# --------------------------------------------------------------------------- #


def test_severity_escalation_voids_suppression(engine, repo, make_finding) -> None:
    """A suppressed P3 that the detector re-fires as P1 is new information: the
    suppression is void by derivation, with no engine write and no new event."""
    low = make_finding(severity=Severity.P3)
    _to_active(engine, low)
    fp = fingerprint(low)
    issue_id = repo.get_open_issue_by_fingerprint(fp).id
    engine.suppress(issue_id, TS + 100)
    assert issue_is_suppressed(repo.get_issue(issue_id), TS + 200) is True

    # Same fingerprint (severity is not in the hash), refired at P1.
    high = make_finding(severity=Severity.P1)
    engine.process_cycle(TS + 300, findings=[high])
    escalated = repo.get_issue(issue_id)
    assert escalated.severity is Severity.P1
    assert escalated.suppressed_severity is Severity.P3  # unchanged: no engine write
    assert issue_is_suppressed(escalated, TS + 400) is False  # voided by derivation

    # The operator may re-suppress at the new severity.
    engine.suppress(issue_id, TS + 500)
    assert repo.get_issue(issue_id).suppressed_severity is Severity.P1
    assert issue_is_suppressed(repo.get_issue(issue_id), TS + 600) is True


# --------------------------------------------------------------------------- #
# Derivation truth table (pure helper)
# --------------------------------------------------------------------------- #


def test_derivation_truth_table() -> None:
    common = dict(suppressed_severity="p3", severity="p3")
    # Not suppressed at all.
    assert is_suppressed(suppressed_ts=None, suppress_until_ts=None, now=TS, **common) is False
    # Indefinite: no expiry, same severity.
    assert is_suppressed(suppressed_ts=TS, suppress_until_ts=None, now=TS + DAY, **common) is True
    # Timed, still inside the window.
    assert (
        is_suppressed(suppressed_ts=TS, suppress_until_ts=TS + HOUR, now=TS + 60, **common) is True
    )
    # Timed, expired (now == until is expired; the mute covers [ts, until)).
    assert (
        is_suppressed(suppressed_ts=TS, suppress_until_ts=TS + HOUR, now=TS + HOUR, **common)
        is False
    )
    # Escalation void: current severity more severe than captured.
    assert (
        is_suppressed(
            suppressed_ts=TS,
            suppress_until_ts=None,
            suppressed_severity="p3",
            severity="p1",
            now=TS,
        )
        is False
    )
    # De-escalation (p1 captured, now p3) stays suppressed — only escalation voids.
    assert (
        is_suppressed(
            suppressed_ts=TS,
            suppress_until_ts=None,
            suppressed_severity="p1",
            severity="p3",
            now=TS,
        )
        is True
    )
