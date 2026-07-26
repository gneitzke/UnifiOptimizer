"""Exhaustive state-machine coverage: pending/active/resolving/resolved, the
exact M and K boundaries, pending discard, resolving snap-back, upsert
bookkeeping, and UNKNOWN-advances-nothing (section 7).
"""

from __future__ import annotations

from netadmin.domain.types import IssueState, Severity
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.models import EngineConfig
from netadmin.issues.models import EventKind as _EventKind

TS = 1_700_000_000


def _fire_n(engine, finding, n, *, start=TS, step=60):
    for i in range(n):
        engine.process_cycle(start + i * step, findings=[finding])


# ---------------------------------------------------------------------------
# pending -> active at exactly M
# ---------------------------------------------------------------------------
def test_pending_created_on_first_fire(engine, repo, make_finding) -> None:
    finding = make_finding()
    engine.process_cycle(TS, findings=[finding])

    issue = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue is not None
    assert issue.state is IssueState.PENDING
    assert issue.occurrences == 1
    assert issue.first_seen_ts == TS
    assert issue.last_seen_ts == TS
    assert repo.event_kinds(issue.id) == [_EventKind.DETECTED]


def test_pending_until_m_minus_one(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 2)  # default M = 3

    issue = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue.state is IssueState.PENDING
    assert issue.occurrences == 2
    assert _EventKind.ESCALATED not in repo.event_kinds(issue.id)


def test_escalates_to_active_on_mth_fire(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)  # exactly M

    issue = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue.state is IssueState.ACTIVE
    assert issue.occurrences == 3
    assert issue.last_seen_ts == TS + 2 * 60
    kinds = repo.event_kinds(issue.id)
    assert kinds == [_EventKind.DETECTED, _EventKind.ESCALATED]


def test_m_equals_one_activates_immediately(repo, make_finding) -> None:
    cfg = EngineConfig(detector_m={"infra.controller_down": 1})
    engine = IssueEngine(repo, config=cfg)
    finding = make_finding("infra.controller_down", native_id="controller")
    engine.process_cycle(TS, findings=[finding])

    issue = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue.state is IssueState.ACTIVE
    assert repo.event_kinds(issue.id) == [_EventKind.DETECTED, _EventKind.ESCALATED]


# ---------------------------------------------------------------------------
# active -> resolving -> resolved at exactly K
# ---------------------------------------------------------------------------
def test_first_clear_moves_active_to_resolving(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)

    engine.process_cycle(TS + 1000, cleared=[fp])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.RESOLVING
    assert issue.clear_streak == 1


def test_resolving_until_k_minus_one(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)

    for i in range(5):  # default K = 6
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])

    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.RESOLVING
    assert issue.clear_streak == 5


def test_resolves_on_kth_clear(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)

    for i in range(6):  # exactly K
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])

    assert repo.get_open_issue_by_fingerprint(fp) is None  # no longer open
    resolved = repo.get_recent_resolved_issue(fp, 0)
    assert resolved.state is IssueState.RESOLVED
    assert resolved.resolved_ts == TS + 1000 + 5 * 60
    assert repo.event_kinds(resolved.id).count(_EventKind.RESOLVED) == 1


def test_k_equals_one_resolves_on_first_clear(repo, make_finding) -> None:
    cfg = EngineConfig(default_m=1, default_k=1)
    engine = IssueEngine(repo, config=cfg)
    finding = make_finding()
    engine.process_cycle(TS, findings=[finding])  # active immediately (M=1)
    fp = fingerprint(finding)

    engine.process_cycle(TS + 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None
    assert repo.get_recent_resolved_issue(fp, 0) is not None


# ---------------------------------------------------------------------------
# resolving snap-back
# ---------------------------------------------------------------------------
def test_fire_during_resolving_snaps_to_active_and_resets_streak(
    engine, repo, make_finding
) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)
    for i in range(3):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp).clear_streak == 3

    engine.process_cycle(TS + 2000, findings=[finding])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.clear_streak == 0
    assert issue.occurrences == 4
    # snap-back emits an ``escalated`` event with the resolving origin.
    escalations = [e for e in repo.all_events(issue.id) if e.kind == _EventKind.ESCALATED]
    assert escalations[-1].detail["reason"] == "refire_during_resolving"


def test_resolving_survives_gap_then_resumes(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)
    for i in range(3):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])

    # UNKNOWN gap must not reset the streak, then clears resume where they left off.
    engine.process_cycle(TS + 5000, unknown=[fp])
    assert repo.get_open_issue_by_fingerprint(fp).clear_streak == 3
    for i in range(3):
        engine.process_cycle(TS + 6000 + i * 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None  # 3 + 3 == K


# ---------------------------------------------------------------------------
# pending discard
# ---------------------------------------------------------------------------
def test_clear_discards_unconfirmed_pending(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 2)  # still pending
    fp = fingerprint(finding)
    pending = repo.get_open_issue_by_fingerprint(fp)
    assert pending.state is IssueState.PENDING

    engine.process_cycle(TS + 500, cleared=[fp])
    # Discarded entirely, not resolved -> not reopenable, and its events go too.
    assert repo.get_open_issue_by_fingerprint(fp) is None
    assert repo.get_recent_resolved_issue(fp, 0) is None
    assert repo.all_events(pending.id) == []


def test_discarded_pending_refire_re_earns_m(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 2)
    fp = fingerprint(finding)
    engine.process_cycle(TS + 500, cleared=[fp])  # discard

    engine.process_cycle(TS + 1000, findings=[finding])  # fresh pending
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.PENDING
    assert issue.occurrences == 1  # counter reset by the discard
    assert issue.first_seen_ts == TS + 1000


# ---------------------------------------------------------------------------
# UNKNOWN advances nothing in either direction
# ---------------------------------------------------------------------------
def test_unknown_does_not_advance_pending(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 2)
    fp = fingerprint(finding)

    engine.process_cycle(TS + 500, unknown=[fp])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.PENDING
    assert issue.occurrences == 2  # unchanged

    engine.process_cycle(TS + 600, findings=[finding])  # the streak was preserved
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE


def test_unknown_does_not_advance_active_toward_resolve(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)

    engine.process_cycle(TS + 1000, unknown=[fp])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.clear_streak == 0


# ---------------------------------------------------------------------------
# upsert bookkeeping: occurrences / last_seen / evidence / clear_streak
# ---------------------------------------------------------------------------
def test_upsert_bookkeeping_on_refire(engine, repo, make_finding) -> None:
    fp = None
    for i, rate in enumerate([10, 20, 30]):
        finding = make_finding(evidence={"rx_err_rate": rate}, severity=Severity.P2)
        engine.process_cycle(TS + i * 60, findings=[finding])
        fp = fingerprint(finding)

    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.occurrences == 3
    assert issue.last_seen_ts == TS + 2 * 60
    assert issue.evidence == {"rx_err_rate": 30}  # refreshed to latest


def test_clear_does_not_touch_occurrences_or_last_seen(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)
    before = repo.get_open_issue_by_fingerprint(fp)

    engine.process_cycle(TS + 9999, cleared=[fp])
    after = repo.get_open_issue_by_fingerprint(fp)
    assert after.occurrences == before.occurrences
    assert after.last_seen_ts == before.last_seen_ts  # last_seen tracks fires only


def test_refire_refreshes_severity_and_title(engine, repo, make_finding) -> None:
    _fire_n(engine, make_finding(severity=Severity.P2, title="mild"), 3)
    finding = make_finding(severity=Severity.P1, title="now critical")
    engine.process_cycle(TS + 5000, findings=[finding])

    issue = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue.severity is Severity.P1
    assert issue.title == "now critical"


def test_clear_on_unknown_fingerprint_is_noop(engine, repo) -> None:
    # No matching open issue -> nothing happens, no crash.
    result = engine.process_cycle(TS, cleared=["deadbeef"])
    assert result == []
    assert repo.list_open_issues() == []


def test_process_cycle_fire_wins_over_clear_same_fingerprint(engine, repo, make_finding) -> None:
    finding = make_finding()
    _fire_n(engine, finding, 3)
    fp = fingerprint(finding)

    # Same cycle lists the fp as both fired and cleared; the fire must win.
    engine.process_cycle(TS + 1000, findings=[finding], cleared=[fp])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.clear_streak == 0
