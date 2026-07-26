"""Integration: drive the engine against the *real* SQLite store.

Phase 0 built the store and the issue engine in parallel, so their issue-facing
surfaces drifted. The production reconciliation is a single deliberate adapter,
:class:`~netadmin.issues.store_repository.StoreIssueRepository`, that a
composition root constructs to run the engine against the real database. This
test drives findings through the engine and asserts they persist correctly via
that production adapter and real SQLite -- no test-only bridge.
"""

from __future__ import annotations

import pytest

from netadmin.domain.types import FixState, IssueState
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.models import EngineConfig, EventKind, IssueRepository

TS = 1_700_000_000
HOUR = 3600


@pytest.fixture
def store_repo(tmp_path):
    from netadmin.issues.store_repository import StoreIssueRepository
    from netadmin.store.repository import Repository

    store = Repository.open(tmp_path / "netadmin.db")
    try:
        yield StoreIssueRepository(store)
    finally:
        store.close()


def _engine(store_repo) -> IssueEngine:
    return IssueEngine(store_repo, config=EngineConfig(default_m=3, default_k=6))


def test_adapter_satisfies_protocol(store_repo) -> None:
    assert isinstance(store_repo, IssueRepository)  # runtime_checkable structural check


def test_full_lifecycle_on_real_store(store_repo, make_finding) -> None:
    engine = _engine(store_repo)
    finding = make_finding(native_id="integration-port")
    fp = fingerprint(finding)

    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[finding])
    assert store_repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE

    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    assert store_repo.get_open_issue_by_fingerprint(fp) is None
    resolved = store_repo.get_recent_resolved_issue(fp, 0)
    assert resolved.state is IssueState.RESOLVED


def test_pending_discard_deletes_row_on_real_store(store_repo, make_finding) -> None:
    engine = _engine(store_repo)
    finding = make_finding(native_id="blip-port")
    fp = fingerprint(finding)

    engine.process_cycle(TS, findings=[finding])
    engine.process_cycle(TS + 60, findings=[finding])  # still pending
    pending = store_repo.get_open_issue_by_fingerprint(fp)
    assert pending.state is IssueState.PENDING

    engine.process_cycle(TS + 120, cleared=[fp])  # discard
    assert store_repo.get_open_issue_by_fingerprint(fp) is None
    assert store_repo.get_recent_resolved_issue(fp, 0) is None
    assert store_repo.get_issue(pending.id) is None  # row actually deleted


def test_reopen_within_window_on_real_store(store_repo, make_finding) -> None:
    engine = _engine(store_repo)
    finding = make_finding(native_id="flappy-port")
    fp = fingerprint(finding)

    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[finding])
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    resolved = store_repo.get_recent_resolved_issue(fp, 0)

    engine.process_cycle(resolved.resolved_ts + HOUR, findings=[finding])
    reopened = store_repo.get_open_issue_by_fingerprint(fp)
    assert reopened.id == resolved.id
    assert reopened.state is IssueState.ACTIVE
    assert reopened.first_seen_ts == resolved.first_seen_ts
    assert reopened.reopened_from == resolved.id


def test_fix_verified_uses_real_event_trail(store_repo, make_finding) -> None:
    engine = _engine(store_repo)
    finding = make_finding(native_id="fixme-port")
    fp = fingerprint(finding)

    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[finding])
    issue_id = store_repo.get_open_issue_by_fingerprint(fp).id

    applied_at = TS + 500
    engine.apply_fix(issue_id, applied_at)
    # last_event_ts (read back from the real issue_events table) drives arming.
    assert store_repo.last_event_ts(issue_id, EventKind.FIX_APPLIED) == applied_at

    for i in range(6):
        engine.process_cycle(applied_at + HOUR + i * 60, cleared=[fp])
    resolved = store_repo.get_recent_resolved_issue(fp, 0)
    assert resolved.state is IssueState.RESOLVED
    assert resolved.fix_state is FixState.VERIFIED


def test_site_scoped_finding_persists_without_an_entity(store_repo, make_finding) -> None:
    """A site-scoped issue (wifi.neighbor_density on an ``rf_env`` pseudo-entity)
    carries no ``entity_id``: the column is nullable and the fingerprint is anchored
    by the finding's ``native_id`` + dims, not by a stored row."""
    engine = _engine(store_repo)
    finding = make_finding(
        "wifi.neighbor_density",
        native_id="rf:2.4",
        entity_id=None,
        dims={"band": "2.4"},
        title="7 neighbouring networks share our 2.4 GHz channels",
    )
    for i in range(3):
        engine.process_cycle(TS + i * HOUR, findings=[finding])

    issue = store_repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue is not None
    assert issue.entity_id is None
    assert issue.state is IssueState.ACTIVE
