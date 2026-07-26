"""Verifier: arm the issue-engine window, then read where the fix sits in it.

Driven against the real store + issue engine (through StoreIssueRepository), so the
verifier's reads exercise the same fix-verification machine the daemon uses. No
controller, no network -- only issue-lifecycle state.
"""

from __future__ import annotations

from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import EntityType, IssueState, Severity
from netadmin.fixes.models import VerificationStatus
from netadmin.fixes.verifier import Verifier
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.store_repository import StoreIssueRepository

T0 = 1_700_000_000
WINDOW_S = 48 * 3600


def _engine(store) -> IssueEngine:
    return IssueEngine(StoreIssueRepository(store))


def _finding() -> Finding:
    entity = Entity(entity_type=EntityType.RADIO, native_id="aa:bb:cc:00:00:01:ng", name="AP ng")
    return Finding(
        detector_key="wifi.channel_plan",
        entity=entity,
        severity=Severity.P3,
        title="channel-plan issue",
        dims={},
        evidence={},
    )


def _seed_active_issue(store, finding: Finding) -> int:
    """Insert an already-active issue whose fingerprint matches ``finding``."""
    fp = fingerprint(finding)
    return store.insert_issue(
        fingerprint=fp,
        detector_key=finding.detector_key,
        severity=finding.severity.value,
        state=IssueState.ACTIVE.value,
        first_seen_ts=T0,
        last_seen_ts=T0,
        title=finding.title,
        occurrences=3,
    )


# --------------------------------------------------------------------------- #
# arm
# --------------------------------------------------------------------------- #
def test_arm_records_fix_applied_and_sets_window(store):
    finding = _finding()
    issue_id = _seed_active_issue(store, finding)
    verifier = Verifier(_engine(store))

    transition = verifier.arm(issue_id, T0)

    assert transition is not None
    row = store.get_issue(issue_id)
    assert row["fix_state"] == "applied"
    events = [e for e in store.list_issue_events(issue_id) if e["kind"] == "fix_applied"]
    assert len(events) == 1

    result = verifier.check(issue_id, now=T0 + 10)
    assert result.status is VerificationStatus.PENDING
    assert result.armed_ts == T0
    assert result.window_end_ts == T0 + WINDOW_S


def test_arm_unknown_issue_returns_none(store):
    verifier = Verifier(_engine(store))
    assert verifier.arm(4242, T0) is None


# --------------------------------------------------------------------------- #
# check states
# --------------------------------------------------------------------------- #
def test_check_not_armed_before_any_fix(store):
    finding = _finding()
    issue_id = _seed_active_issue(store, finding)
    result = Verifier(_engine(store)).check(issue_id, now=T0)
    assert result.status is VerificationStatus.NOT_ARMED


def test_check_missing_issue_is_not_armed(store):
    result = Verifier(_engine(store)).check(999, now=T0)
    assert result.status is VerificationStatus.NOT_ARMED


def test_pending_becomes_expired_after_window(store):
    finding = _finding()
    issue_id = _seed_active_issue(store, finding)
    verifier = Verifier(_engine(store))
    verifier.arm(issue_id, T0)

    assert verifier.check(issue_id, now=T0 + WINDOW_S - 1).status is VerificationStatus.PENDING
    assert verifier.check(issue_id, now=T0 + WINDOW_S + 1).status is VerificationStatus.EXPIRED


def test_pending_without_now_stays_pending(store):
    finding = _finding()
    issue_id = _seed_active_issue(store, finding)
    verifier = Verifier(_engine(store))
    verifier.arm(issue_id, T0)
    assert verifier.check(issue_id).status is VerificationStatus.PENDING


def test_fix_verified_when_issue_resolves_in_window(store):
    finding = _finding()
    issue_id = _seed_active_issue(store, finding)
    engine = _engine(store)
    verifier = Verifier(engine)
    verifier.arm(issue_id, T0)

    # Clear K times inside the window -> engine resolves and, armed, marks verified.
    fp = fingerprint(finding)
    k = engine.cfg.k_for(finding.detector_key)
    for i in range(k):
        engine.process_cycle(T0 + 60 * (i + 1), cleared=[fp])

    row = store.get_issue(issue_id)
    assert row["state"] == "resolved"
    assert row["fix_state"] == "verified"

    result = verifier.check(issue_id, now=T0 + 3600)
    assert result.status is VerificationStatus.VERIFIED
    assert result.resolved_ts is not None


def test_fix_failed_when_issue_refires_during_resolving(store):
    finding = _finding()
    issue_id = _seed_active_issue(store, finding)
    engine = _engine(store)
    verifier = Verifier(engine)
    verifier.arm(issue_id, T0)

    fp = fingerprint(finding)
    # One clear -> resolving; then a fire snaps back to active and, armed, fails.
    engine.process_cycle(T0 + 60, cleared=[fp])
    engine.process_cycle(T0 + 120, findings=[finding])

    row = store.get_issue(issue_id)
    assert row["fix_state"] == "failed"
    assert verifier.check(issue_id, now=T0 + 200).status is VerificationStatus.FAILED
