"""Snooze/ack set mute flags only and never touch evaluation (section 7)."""

from __future__ import annotations

from netadmin.domain.types import IssueState
from netadmin.issues.engine import fingerprint
from netadmin.issues.models import EventKind

TS = 1_700_000_000
HOUR = 3600


def _to_active(engine, finding, *, start=TS):
    for i in range(3):
        engine.process_cycle(start + i * 60, findings=[finding])


def test_ack_sets_flag_and_records_event(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id

    engine.ack(issue_id, TS + 100)
    issue = repo.get_issue(issue_id)
    assert issue.ack_ts == TS + 100
    assert EventKind.ACKED in repo.event_kinds(issue_id)


def test_snooze_sets_flag_and_records_event(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id

    engine.snooze(issue_id, until_ts=TS + 8 * HOUR, now=TS + 100)
    issue = repo.get_issue(issue_id)
    assert issue.snooze_until_ts == TS + 8 * HOUR
    events = [e for e in repo.all_events(issue_id) if e.kind == EventKind.SNOOZED]
    assert events[-1].detail["until_ts"] == TS + 8 * HOUR


def test_snooze_does_not_block_resolve(engine, repo, make_finding) -> None:
    finding = make_finding()
    _to_active(engine, finding)
    fp = fingerprint(finding)
    issue_id = repo.get_open_issue_by_fingerprint(fp).id

    engine.snooze(issue_id, until_ts=TS + 10 * HOUR, now=TS + 100)
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])

    assert repo.get_open_issue_by_fingerprint(fp) is None
    assert repo.get_recent_resolved_issue(fp, 0).state is IssueState.RESOLVED


def test_ack_does_not_block_escalation(engine, repo, make_finding) -> None:
    finding = make_finding()
    engine.process_cycle(TS, findings=[finding])  # pending
    issue_id = repo.get_open_issue_by_fingerprint(fingerprint(finding)).id
    engine.ack(issue_id, TS + 10)

    engine.process_cycle(TS + 60, findings=[finding])
    engine.process_cycle(TS + 120, findings=[finding])
    assert repo.get_open_issue_by_fingerprint(fingerprint(finding)).state is IssueState.ACTIVE


def test_ack_snooze_noop_on_unknown_issue(engine) -> None:
    assert engine.ack(9999, TS) is None
    assert engine.snooze(9999, TS + 100, TS) is None
