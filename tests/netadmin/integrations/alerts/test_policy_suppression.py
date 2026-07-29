"""Suppression gates open-class alerts but never leaks a resolve (Gitea #49).

An OPENED/REOPENED on a suppressed issue must not announce; a RESOLVED must still
pass classify() so the dedupe layer can close a loop it opened before the issue
was suppressed — and, by the same rule, drop a resolve for a fingerprint it never
announced. This pins that the gate lives in pure classify() and that the
dedupe invariant stays leak-free across suppress-then-resolve.
"""

from __future__ import annotations

import dataclasses

from netadmin.integrations.alerts.models import OPENED, RESOLVED, AlertEvent
from netadmin.integrations.alerts.policy import SEND, SKIP, ChannelPolicy, classify

from .conftest import FakeClock, opened_transition, reopened_transition, resolved_transition
from .test_policy import _channel


def _suppressed(transition):
    return dataclasses.replace(transition, suppressed=True)


def test_suppressed_open_class_events_are_never_classified() -> None:
    assert classify(_suppressed(opened_transition())) is None
    assert classify(_suppressed(reopened_transition())) is None


def test_suppressed_resolved_still_classifies() -> None:
    # RESOLVED passes through even when suppressed; the dedupe layer decides
    # whether it actually goes out (only if the open was announced).
    assert classify(_suppressed(resolved_transition())) == RESOLVED


def test_unsuppressed_events_classify_as_before() -> None:
    assert classify(opened_transition()) == OPENED
    assert classify(resolved_transition()) == RESOLVED


def test_suppressed_open_then_resolve_does_not_leak() -> None:
    """A suppressed issue that opens (gated) and later resolves must send nothing:
    the channel never announced the open, so the dedupe layer drops the resolve."""
    policy = ChannelPolicy(_channel(), clock=FakeClock())

    # The OPENED is gated in classify -> the channel never even sees an event.
    assert classify(_suppressed(opened_transition())) is None

    # The later RESOLVED does classify, but for a fingerprint this channel never
    # announced -> dedupe drops it. Nothing leaks.
    resolve = AlertEvent(event=RESOLVED, transition=resolved_transition())
    assert policy.evaluate(resolve) == SKIP
    assert policy.announced == 0


def test_open_announced_before_suppression_still_gets_its_resolve() -> None:
    """An issue announced open, THEN suppressed, THEN resolved: the resolve closes
    the loop the channel actually opened (RESOLVED deliberately passes classify)."""
    policy = ChannelPolicy(_channel(), clock=FakeClock())

    opened = AlertEvent(event=OPENED, transition=opened_transition())
    assert policy.evaluate(opened) == SEND
    assert policy.announced == 1

    # Now suppressed and resolved — the resolve still classifies and still sends,
    # clearing the announced entry so nothing lingers forever.
    assert classify(_suppressed(resolved_transition())) == RESOLVED
    resolve = AlertEvent(event=RESOLVED, transition=resolved_transition())
    assert policy.evaluate(resolve) == SEND
    assert policy.announced == 0
