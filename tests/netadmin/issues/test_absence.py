"""``process_cycle(absent=...)``: an issue whose subject left the network.

A departed client reports nothing, so no detector can ever say "the problem is
gone" about it. The engine accepts that fact as a fourth input and runs it
through the existing clear path, annotating the trail with
``reason: entity_absent`` — same transitions, same K streak, same reopen window,
one extra key in the event detail. These tests pin the routing, the annotation,
and the two precedences that keep it safe: a fire outranks it, and inhibition
freezes it.
"""

from __future__ import annotations

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, IssueState
from netadmin.issues.engine import REASON_ENTITY_ABSENT, IssueEngine, fingerprint
from netadmin.issues.models import EngineConfig

TS = 1_700_000_000

# M=1 so a finding confirms on its first fire; K=3 keeps the streak assertions short.
CFG = EngineConfig(default_m=1, default_k=3)


def _engine(repo) -> IssueEngine:
    return IssueEngine(repo, config=CFG)


def _detail(repo, issue_id: int, kind: str) -> dict:
    events = [e for e in repo.all_events(issue_id) if e.kind == kind]
    assert events, f"no {kind} event on issue {issue_id}"
    return events[-1].detail


# ---------------------------------------------------------------------------
# The clear path, with a reason
# ---------------------------------------------------------------------------
def test_absence_advances_the_streak_and_resolves_at_k(repo, make_finding) -> None:
    engine = _engine(repo)
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])  # active (M=1)

    engine.process_cycle(TS + 900, absent=[fp])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.RESOLVING
    assert issue.clear_streak == 1
    assert _detail(repo, issue.id, "resolving")["reason"] == REASON_ENTITY_ABSENT

    engine.process_cycle(TS + 1800, absent=[fp])
    engine.process_cycle(TS + 2700, absent=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None
    assert _detail(repo, issue.id, "resolved")["reason"] == REASON_ENTITY_ABSENT


def test_an_ordinary_clear_carries_no_reason(repo, make_finding) -> None:
    # The annotation must distinguish the two, so an evaluated clear stays bare.
    engine = _engine(repo)
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])
    engine.process_cycle(TS + 900, cleared=[fp])

    issue = repo.get_open_issue_by_fingerprint(fp)
    assert "reason" not in _detail(repo, issue.id, "resolving")


def test_absent_pending_issue_is_discarded(repo, make_finding) -> None:
    engine = IssueEngine(repo, config=EngineConfig(default_m=3, default_k=3))
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.PENDING

    engine.process_cycle(TS + 900, absent=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None
    assert repo.list_open_issues() == []


def test_a_fire_this_cycle_beats_absence(repo, make_finding) -> None:
    # The two verdicts disagree only when the client came back mid-cycle; the
    # observation wins over the bookkeeping.
    engine = _engine(repo)
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])

    engine.process_cycle(TS + 900, findings=[finding], absent=[fp])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.clear_streak == 0
    assert issue.occurrences == 2


def test_a_fingerprint_in_both_buckets_advances_once(repo, make_finding) -> None:
    engine = _engine(repo)
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])

    engine.process_cycle(TS + 900, cleared=[fp], absent=[fp])
    assert repo.get_open_issue_by_fingerprint(fp).clear_streak == 1


def test_absence_reopens_the_same_row_when_the_client_returns(repo, make_finding) -> None:
    engine = _engine(repo)
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])
    for offset in (900, 1800, 2700):
        engine.process_cycle(TS + offset, absent=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None

    engine.process_cycle(TS + 3600, findings=[finding])
    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue.state is IssueState.ACTIVE
    assert issue.first_seen_ts == TS, "the same row: age keeps counting"


# ---------------------------------------------------------------------------
# Inhibition: an AP outage must not mass-resolve its clients' issues
# ---------------------------------------------------------------------------
def test_a_downed_ap_freezes_its_absent_clients(repo, make_finding) -> None:
    """Every client of a dead AP is absent at once; none of them may resolve.

    This is the failure mode that makes absence dangerous — a power cut looks
    exactly like fifteen clients leaving — and the guard is the inhibition the
    clear path already runs, reached through the client's parentage.
    """
    engine = _engine(repo)
    ap = Entity(entity_type=EntityType.AP, native_id="ap-a", site_id="default", entity_id=1)
    repo.register_entity(ap)
    clients = []
    for n in range(3):
        entity = Entity(
            entity_type=EntityType.CLIENT,
            native_id=f"client-{n}",
            site_id="default",
            entity_id=10 + n,
            parent_id=1,
        )
        repo.register_entity(entity)
        clients.append(make_finding("wifi.sticky_client", entity=entity))
    engine.process_cycle(TS, findings=clients)

    down = make_finding("infra.device_down", entity=ap)
    engine.process_cycle(TS + 900, findings=[down])  # the AP goes down

    fps = [fingerprint(f) for f in clients]
    for cycle in range(1, 6):  # well past K=3
        engine.process_cycle(TS + 900 + cycle * 900, absent=fps)

    for fp in fps:
        issue = repo.get_open_issue_by_fingerprint(fp)
        assert issue is not None, "a client issue resolved while its AP was down"
        assert issue.state is IssueState.ACTIVE
        assert issue.clear_streak == 0


def test_a_downed_controller_freezes_absence_everywhere(repo, make_finding) -> None:
    # With the controller unreachable no client can be seen, so every one of them
    # looks departed. The global cause freezes the lot.
    engine = _engine(repo)
    finding = make_finding("wifi.sticky_client", native_id="client-a")
    fp = fingerprint(finding)
    engine.process_cycle(TS, findings=[finding])

    controller = make_finding("infra.controller_down", native_id="controller")
    engine.process_cycle(TS + 900, findings=[controller], absent=[fp])
    for cycle in range(2, 8):
        engine.process_cycle(TS + cycle * 900, absent=[fp])

    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue is not None and issue.clear_streak == 0


def test_absence_resumes_once_the_ap_is_back(repo, make_finding) -> None:
    engine = _engine(repo)
    ap = Entity(entity_type=EntityType.AP, native_id="ap-a", site_id="default", entity_id=1)
    repo.register_entity(ap)
    client = Entity(
        entity_type=EntityType.CLIENT,
        native_id="client-a",
        site_id="default",
        entity_id=10,
        parent_id=1,
    )
    repo.register_entity(client)
    finding = make_finding("wifi.sticky_client", entity=client)
    fp = fingerprint(finding)
    down = make_finding("infra.device_down", entity=ap)

    engine.process_cycle(TS, findings=[finding])
    engine.process_cycle(TS + 900, findings=[down], absent=[fp])
    assert repo.get_open_issue_by_fingerprint(fp).clear_streak == 0  # frozen

    # The AP recovers: its issue clears away, and the client's absence counts again.
    down_fp = fingerprint(down)
    for offset in (1800, 2700, 3600):
        engine.process_cycle(TS + offset, cleared=[down_fp])
    assert repo.get_open_issue_by_fingerprint(down_fp) is None

    engine.process_cycle(TS + 4500, absent=[fp])
    assert repo.get_open_issue_by_fingerprint(fp).clear_streak == 1
