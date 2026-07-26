"""on_transition callbacks: fired for every transition, fire-and-forget, and a
raising callback never affects the engine or the other callbacks (section 7).
"""

from __future__ import annotations

from netadmin.domain.types import IssueState
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.models import EngineConfig, EventKind, Transition

TS = 1_700_000_000


def test_callbacks_receive_transitions(repo, make_finding) -> None:
    seen: list[Transition] = []
    engine = IssueEngine(repo, on_transition=[seen.append])

    finding = make_finding()
    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[finding])

    kinds = [t.kind for t in seen]
    assert kinds == [EventKind.DETECTED, EventKind.ESCALATED]
    escalation = seen[-1]
    assert escalation.from_state is IssueState.PENDING
    assert escalation.to_state is IssueState.ACTIVE
    assert escalation.fingerprint == fingerprint(finding)
    assert escalation.detector_key == "wired.bad_cable"


def test_raising_callback_is_isolated(repo, make_finding) -> None:
    calls: list[str] = []

    def boom(_transition: Transition) -> None:
        calls.append("boom")
        raise RuntimeError("callback blew up")

    def good(transition: Transition) -> None:
        calls.append(transition.kind)

    engine = IssueEngine(repo, config=EngineConfig(default_m=1), on_transition=[boom, good])

    finding = make_finding()
    # Must not raise despite the first callback throwing.
    engine.process_cycle(TS, findings=[finding])

    # Engine state advanced correctly...
    issue = repo.get_open_issue_by_fingerprint(fingerprint(finding))
    assert issue.state is IssueState.ACTIVE
    # ...and the second callback still ran for both events.
    assert calls.count("boom") == 2
    assert EventKind.DETECTED in calls
    assert EventKind.ESCALATED in calls


def test_add_callback_registers_after_construction(repo, make_finding) -> None:
    seen: list[Transition] = []
    engine = IssueEngine(repo, config=EngineConfig(default_m=1))
    engine.add_callback(seen.append)

    engine.process_cycle(TS, findings=[make_finding()])
    assert [t.kind for t in seen] == [EventKind.DETECTED, EventKind.ESCALATED]


def test_process_cycle_returns_same_transitions_as_callbacks(repo, make_finding) -> None:
    seen: list[Transition] = []
    engine = IssueEngine(repo, config=EngineConfig(default_m=1), on_transition=[seen.append])
    returned = engine.process_cycle(TS, findings=[make_finding()])
    assert [t.kind for t in returned] == [t.kind for t in seen]


# --- unregistering --------------------------------------------------------- #


def test_remove_callback_stops_delivery(repo, make_finding) -> None:
    seen: list[Transition] = []
    engine = IssueEngine(repo, config=EngineConfig(default_m=1), on_transition=[seen.append])

    assert engine.remove_callback(seen.append) is True
    engine.process_cycle(TS, findings=[make_finding()])
    assert seen == []


def test_remove_callback_is_idempotent(repo, make_finding) -> None:
    """Safe on a subscriber that never started, and on a second stop."""
    seen: list[Transition] = []
    engine = IssueEngine(repo, config=EngineConfig(default_m=1))

    assert engine.remove_callback(seen.append) is False  # never registered
    engine.add_callback(seen.append)
    assert engine.remove_callback(seen.append) is True
    assert engine.remove_callback(seen.append) is False  # stopped twice

    engine.process_cycle(TS, findings=[make_finding()])
    assert seen == []


def test_remove_callback_drops_every_duplicate_registration(repo, make_finding) -> None:
    """A subscriber that double-registered before it was fixed still detaches fully."""
    seen: list[Transition] = []
    engine = IssueEngine(repo, config=EngineConfig(default_m=1))
    engine.add_callback(seen.append)
    engine.add_callback(seen.append)

    assert engine.remove_callback(seen.append) is True
    engine.process_cycle(TS, findings=[make_finding()])
    assert seen == []


def test_bound_method_of_a_restarted_subscriber_is_removable(repo, make_finding) -> None:
    """The three daemon consumers subscribe ``self.on_transition``, a fresh bound
    method object each time. Removal must match on the instance, not on identity."""

    class Subscriber:
        def __init__(self) -> None:
            self.seen: list[Transition] = []

        def on_transition(self, transition: Transition) -> None:
            self.seen.append(transition)

    sub = Subscriber()
    engine = IssueEngine(repo, config=EngineConfig(default_m=1))
    engine.add_callback(sub.on_transition)
    assert engine.remove_callback(sub.on_transition) is True

    engine.process_cycle(TS, findings=[make_finding()])
    assert sub.seen == []


def test_callback_may_unregister_itself_mid_notify(repo, make_finding) -> None:
    """A callback that detaches itself gets the transition it is handling, no more,
    and the callbacks after it in the list still run."""
    first: list[str] = []
    second: list[str] = []

    def once(transition: Transition) -> None:
        first.append(transition.kind)
        engine.remove_callback(once)

    engine = IssueEngine(repo, config=EngineConfig(default_m=1))
    engine.add_callback(once)
    engine.add_callback(lambda t: second.append(t.kind))

    engine.process_cycle(TS, findings=[make_finding()])

    assert first == [EventKind.DETECTED]
    assert second == [EventKind.DETECTED, EventKind.ESCALATED]
