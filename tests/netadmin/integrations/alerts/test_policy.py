"""Alert policy: classification, filters, dedupe, and flood control (section 20).

All pure, all deterministic -- the token bucket runs on a hand-advanced clock, so
the 50-event storm is asserted exactly rather than approximately.
"""

from __future__ import annotations

from typing import Optional

import pytest

from netadmin.config import AlertChannelConfig
from netadmin.domain.types import IssueState
from netadmin.integrations.alerts.models import OPENED, REOPENED, RESOLVED, AlertEvent
from netadmin.integrations.alerts.policy import (
    DIGEST,
    SEND,
    SKIP,
    ChannelPolicy,
    TokenBucket,
    classify,
)
from netadmin.issues.models import EventKind

from .conftest import (
    FakeClock,
    detected_transition,
    make_transition,
    opened_transition,
    reopened_transition,
    resolved_transition,
    snapback_transition,
)


def _channel(**kw: object) -> AlertChannelConfig:
    base: dict[str, object] = {"name": "ops", "type": "webhook"}
    base.update(kw)
    return AlertChannelConfig(**base)  # type: ignore[arg-type]


def _policy(clock: FakeClock, **kw: object) -> ChannelPolicy:
    return ChannelPolicy(_channel(**kw), clock=clock)


def _event(event: str, transition_builder, **kw: object) -> AlertEvent:
    return AlertEvent(event=event, transition=transition_builder(**kw))


# --- classification: the full rules table ---------------------------------- #


@pytest.mark.parametrize(
    ("label", "transition", "expected"),
    [
        ("pending->active confirmation is the real open", opened_transition(), OPENED),
        ("reopen inside the window", reopened_transition(), REOPENED),
        ("resolve", resolved_transition(), RESOLVED),
        # Never notified:
        ("detected is unconfirmed noise", detected_transition(), None),
        ("flap snap-back was already announced", snapback_transition(), None),
        (
            "ack is bookkeeping",
            make_transition(EventKind.ACKED, from_state=None, to_state=None),
            None,
        ),
        (
            "snooze is bookkeeping",
            make_transition(EventKind.SNOOZED, from_state=None, to_state=None),
            None,
        ),
        (
            "investigate is bookkeeping",
            make_transition(EventKind.INVESTIGATED, from_state=None, to_state=None),
            None,
        ),
        (
            "fix_proposed is bookkeeping",
            make_transition(EventKind.FIX_PROPOSED, from_state=None, to_state=None),
            None,
        ),
        (
            "fix_applied is bookkeeping",
            make_transition(EventKind.FIX_APPLIED, from_state=None, to_state=None),
            None,
        ),
        (
            "fix_verified is bookkeeping",
            make_transition(EventKind.FIX_VERIFIED, from_state=None, to_state=None),
            None,
        ),
        (
            "fix_failed is bookkeeping",
            make_transition(EventKind.FIX_FAILED, from_state=None, to_state=None),
            None,
        ),
    ],
)
def test_classify_rules_table(label: str, transition, expected: Optional[str]) -> None:
    assert classify(transition) == expected, label


def test_escalated_without_m_reached_reason_is_not_an_open() -> None:
    """A future escalation reason must not silently start paging people."""
    transition = make_transition(
        EventKind.ESCALATED,
        from_state=IssueState.PENDING,
        to_state=IssueState.ACTIVE,
        detail={"reason": "something_new"},
    )
    assert classify(transition) is None


def test_escalated_to_a_non_active_state_is_not_an_open() -> None:
    transition = make_transition(
        EventKind.ESCALATED,
        from_state=IssueState.PENDING,
        to_state=IssueState.RESOLVING,
        detail={"reason": "m_reached"},
    )
    assert classify(transition) is None


# --- per-channel filters --------------------------------------------------- #


def test_min_severity_floor_filters_below_the_line() -> None:
    clock = FakeClock()
    policy = _policy(clock, min_severity="p2")
    assert policy.evaluate(_event(OPENED, opened_transition, severity="p1")) == SEND
    assert (
        policy.evaluate(_event(OPENED, opened_transition, severity="p2", fingerprint="b")) == SEND
    )
    assert (
        policy.evaluate(_event(OPENED, opened_transition, severity="p3", fingerprint="c")) == SKIP
    )


def test_event_allow_list_filters_unwanted_classes() -> None:
    clock = FakeClock()
    policy = _policy(clock, events=["opened"])
    assert policy.evaluate(_event(OPENED, opened_transition)) == SEND
    assert policy.evaluate(_event(REOPENED, reopened_transition, fingerprint="b")) == SKIP


def test_resolve_only_channel_never_announces_an_open() -> None:
    clock = FakeClock()
    policy = _policy(clock, events=["resolved"])
    assert policy.evaluate(_event(OPENED, opened_transition)) == SKIP
    # ...and because the open was never announced, the resolve is suppressed too.
    assert policy.evaluate(_event(RESOLVED, resolved_transition)) == SKIP


# --- dedupe ---------------------------------------------------------------- #


def test_open_is_announced_once() -> None:
    clock = FakeClock()
    policy = _policy(clock)
    assert policy.evaluate(_event(OPENED, opened_transition)) == SEND
    assert policy.evaluate(_event(OPENED, opened_transition)) == SKIP


def test_open_resolve_open_cycles_correctly() -> None:
    clock = FakeClock()
    policy = _policy(clock)
    assert policy.evaluate(_event(OPENED, opened_transition)) == SEND
    assert policy.evaluate(_event(RESOLVED, resolved_transition)) == SEND
    assert policy.evaluate(_event(OPENED, opened_transition)) == SEND
    assert policy.announced == 1


def test_resolve_without_a_matching_open_is_suppressed() -> None:
    clock = FakeClock()
    policy = _policy(clock)
    assert policy.evaluate(_event(RESOLVED, resolved_transition)) == SKIP


def test_reopen_after_resolve_is_announced_but_not_twice() -> None:
    clock = FakeClock()
    policy = _policy(clock)
    policy.evaluate(_event(OPENED, opened_transition))
    policy.evaluate(_event(RESOLVED, resolved_transition))
    assert policy.evaluate(_event(REOPENED, reopened_transition)) == SEND
    assert policy.evaluate(_event(REOPENED, reopened_transition)) == SKIP


def test_below_threshold_open_suppresses_its_own_resolve() -> None:
    """The case a global dedupe map would get wrong.

    A P3 issue never reaches a p2-floor channel, so its resolve must not either --
    otherwise the channel announces the end of something it never announced.
    """
    clock = FakeClock()
    policy = _policy(clock, min_severity="p2")
    assert policy.evaluate(_event(OPENED, opened_transition, severity="p3")) == SKIP
    assert policy.evaluate(_event(RESOLVED, resolved_transition, severity="p3")) == SKIP


def test_dedupe_map_is_bounded() -> None:
    clock = FakeClock()
    policy = ChannelPolicy(_channel(rate_limit_per_min=600), clock=clock, dedupe_max=8)
    for i in range(50):
        policy.evaluate(_event(OPENED, opened_transition, fingerprint=f"fp-{i}"))
    assert policy.announced == 8


# --- token bucket ---------------------------------------------------------- #


def test_token_bucket_starts_full_and_drains() -> None:
    clock = FakeClock()
    bucket = TokenBucket(10, clock=clock)
    assert [bucket.try_take() for _ in range(10)] == [True] * 10
    assert bucket.try_take() is False


def test_token_bucket_refills_at_the_configured_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(60, clock=clock)  # one token per second
    for _ in range(60):
        bucket.try_take()
    assert bucket.try_take() is False
    assert bucket.delay_until_token() == pytest.approx(1.0)
    clock.advance(1.0)
    assert bucket.try_take() is True


def test_token_bucket_never_exceeds_its_burst() -> None:
    clock = FakeClock()
    bucket = TokenBucket(5, clock=clock)
    clock.advance(3600)
    assert bucket.tokens == pytest.approx(5.0)


# --- digest coalescing ----------------------------------------------------- #


def test_storm_produces_burst_singles_then_one_digest() -> None:
    """50 events at a 10/min limit: 10 singles, 40 coalesced into ONE summary."""
    clock = FakeClock()
    policy = _policy(clock, rate_limit_per_min=10)

    decisions = [
        policy.evaluate(_event(OPENED, opened_transition, fingerprint=f"fp-{i}", severity="p2"))
        for i in range(50)
    ]
    assert decisions.count(SEND) == 10
    assert decisions.count(DIGEST) == 40

    for i, decision in enumerate(decisions):
        if decision == DIGEST:
            policy.buffer(_event(OPENED, opened_transition, fingerprint=f"fp-{i}", severity="p2"))
    assert policy.pending_digest == 40

    # No token yet, so no flush -- and the delay says exactly how long to wait.
    assert policy.take_digest() is None
    assert policy.next_flush_delay() == pytest.approx(6.0)

    clock.advance(6.0)
    summary = policy.take_digest()
    assert summary is not None
    assert summary.count == 40
    assert summary.by_event == {OPENED: 40}
    assert summary.by_severity == {"p2": 40}
    assert policy.pending_digest == 0
    # Exactly one digest: the second call has nothing left to send.
    assert policy.take_digest() is None


def test_digest_reports_worst_severity_and_a_title_sample() -> None:
    clock = FakeClock()
    policy = _policy(clock, rate_limit_per_min=1, min_severity="p3")
    policy.evaluate(_event(OPENED, opened_transition, fingerprint="a"))  # spends the token
    for i, severity in enumerate(("p3", "p1", "p2")):
        policy.buffer(
            _event(
                OPENED,
                opened_transition,
                fingerprint=f"fp-{i}",
                severity=severity,
                title=f"issue {severity}",
            )
        )
    summary = policy.take_digest(force=True)
    assert summary is not None
    assert summary.count == 3
    assert summary.worst_severity == "p1"
    # Sample leads with the worst thing in the batch, not the first thing.
    assert summary.top_titles[0] == "issue p1"


def test_next_flush_delay_is_none_with_nothing_pending() -> None:
    clock = FakeClock()
    policy = _policy(clock)
    assert policy.next_flush_delay() is None


def test_force_flush_releases_a_digest_without_a_token() -> None:
    """Shutdown must not discard a coalesced batch."""
    clock = FakeClock()
    policy = _policy(clock, rate_limit_per_min=1)
    policy.evaluate(_event(OPENED, opened_transition, fingerprint="a"))
    policy.buffer(_event(OPENED, opened_transition, fingerprint="b"))
    assert policy.take_digest() is None
    assert policy.take_digest(force=True) is not None
