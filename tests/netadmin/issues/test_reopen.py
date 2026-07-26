"""Reopen window: same fingerprint within 24 h of resolved reopens the original
row (parentage preserved, ``reopened_from`` set); outside the window it is a
fresh issue (section 7).
"""

from __future__ import annotations

from netadmin.domain.types import IssueState
from netadmin.issues.engine import fingerprint
from netadmin.issues.models import EventKind

TS = 1_700_000_000
HOUR = 3600
DAY = 24 * HOUR


def _drive_to_resolved(engine, repo, finding, *, start=TS):
    # 3 fires -> active, 6 clears -> resolved.
    for i in range(3):
        engine.process_cycle(start + i * 60, findings=[finding])
    for i in range(6):
        engine.process_cycle(start + 1000 + i * 60, cleared=[fingerprint(finding)])
    resolved = repo.get_recent_resolved_issue(fingerprint(finding), 0)
    assert resolved.state is IssueState.RESOLVED
    return resolved


def test_reopen_at_23h59_reuses_row(engine, repo, make_finding) -> None:
    finding = make_finding()
    resolved = _drive_to_resolved(engine, repo, finding)
    fp = fingerprint(finding)

    reopen_ts = resolved.resolved_ts + (23 * HOUR + 59 * 60)
    engine.process_cycle(reopen_ts, findings=[finding])

    reopened = repo.get_open_issue_by_fingerprint(fp)
    assert reopened is not None
    assert reopened.id == resolved.id  # same row
    assert reopened.state is IssueState.ACTIVE  # snaps straight to active
    assert reopened.resolved_ts is None
    assert reopened.clear_streak == 0
    assert reopened.reopened_from == resolved.id
    assert reopened.first_seen_ts == resolved.first_seen_ts  # age preserved
    assert reopened.occurrences == resolved.occurrences + 1
    assert EventKind.REOPENED in repo.event_kinds(reopened.id)


def test_reopen_at_exactly_24h_reuses_row(engine, repo, make_finding) -> None:
    finding = make_finding()
    resolved = _drive_to_resolved(engine, repo, finding)
    fp = fingerprint(finding)

    engine.process_cycle(resolved.resolved_ts + DAY, findings=[finding])  # boundary inclusive
    reopened = repo.get_open_issue_by_fingerprint(fp)
    assert reopened.id == resolved.id


def test_no_reopen_at_24h01_is_fresh_issue(engine, repo, make_finding) -> None:
    finding = make_finding()
    resolved = _drive_to_resolved(engine, repo, finding)
    fp = fingerprint(finding)

    fresh_ts = resolved.resolved_ts + (DAY + 60)
    engine.process_cycle(fresh_ts, findings=[finding])

    fresh = repo.get_open_issue_by_fingerprint(fp)
    assert fresh is not None
    assert fresh.id != resolved.id  # a brand-new row
    assert fresh.state is IssueState.PENDING  # must re-earn M
    assert fresh.occurrences == 1
    assert fresh.first_seen_ts == fresh_ts  # age restarts
    assert fresh.reopened_from is None


def test_reopen_preserves_parentage_across_resolve(engine, repo, make_finding) -> None:
    finding = make_finding(native_id="port-a", entity_id=42, parent_id=7)
    resolved = _drive_to_resolved(engine, repo, finding)

    engine.process_cycle(resolved.resolved_ts + HOUR, findings=[finding])
    reopened = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert reopened.entity_id == 42
    assert reopened.first_seen_ts == resolved.first_seen_ts
