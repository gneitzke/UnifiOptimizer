"""Inhibition freezes both directions while a cause is in effect (section 7):

* ``infra.controller_down`` (global) suppresses all other creation AND all other
  clear-streak advancement, but never itself.
* ``infra.device_down`` (children) suppresses only issues on entities parented
  under the downed device, resolved through ``parent_id``.
"""

from __future__ import annotations

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, IssueState
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.models import EngineConfig

TS = 1_700_000_000

# Cause detectors activate on the first fire in the real system.
CAUSE_CFG = EngineConfig(
    default_m=3,
    default_k=6,
    detector_m={"infra.controller_down": 1, "infra.device_down": 1},
)


def _engine(repo) -> IssueEngine:
    return IssueEngine(repo, config=CAUSE_CFG)


# ---------------------------------------------------------------------------
# controller_down (global) freezes creation
# ---------------------------------------------------------------------------
def test_controller_down_suppresses_creation_same_cycle(repo, make_finding) -> None:
    engine = _engine(repo)
    controller = make_finding("infra.controller_down", native_id="controller")
    victim = make_finding("wired.bad_cable", native_id="port-x")

    engine.process_cycle(TS, findings=[controller, victim])

    # controller_down issue exists; the victim was never created.
    assert repo.get_open_issue_by_fingerprint(fingerprint(controller)) is not None
    assert repo.get_open_issue_by_fingerprint(fingerprint(victim)) is None


def test_controller_down_freezes_creation_from_open_active_cause(repo, make_finding) -> None:
    engine = _engine(repo)
    controller = make_finding("infra.controller_down", native_id="controller")
    engine.process_cycle(TS, findings=[controller])  # active (M=1)

    # A later cycle with only the victim: the still-active cause freezes it.
    victim = make_finding("wired.bad_cable", native_id="port-x")
    engine.process_cycle(TS + 300, findings=[victim])
    assert repo.get_open_issue_by_fingerprint(fingerprint(victim)) is None


# ---------------------------------------------------------------------------
# controller_down (global) freezes clear-streak advancement
# ---------------------------------------------------------------------------
def test_controller_down_freezes_clear_streak(repo, make_finding) -> None:
    engine = _engine(repo)
    victim = make_finding("wired.bad_cable", native_id="port-x")
    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[victim])  # active
    fp = fingerprint(victim)
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE

    controller = make_finding("infra.controller_down", native_id="controller")
    # Absence of the victim during a controller outage must NOT advance clear.
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, findings=[controller], cleared=[fp])

    issue = repo.get_open_issue_by_fingerprint(fp)
    assert issue is not None
    assert issue.state is IssueState.ACTIVE  # never progressed to resolving
    assert issue.clear_streak == 0


def test_controller_down_itself_is_not_self_inhibited(repo, make_finding) -> None:
    engine = _engine(repo)
    controller = make_finding("infra.controller_down", native_id="controller")
    engine.process_cycle(TS, findings=[controller])
    fp = fingerprint(controller)
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE

    # The cause can still resolve (otherwise inhibition could never lift).
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None


def test_inhibition_lifts_after_controller_recovers(repo, make_finding) -> None:
    engine = _engine(repo)
    controller = make_finding("infra.controller_down", native_id="controller")
    engine.process_cycle(TS, findings=[controller])
    for i in range(6):
        engine.process_cycle(TS + 100 + i * 60, cleared=[fingerprint(controller)])
    assert repo.get_open_issue_by_fingerprint(fingerprint(controller)) is None

    victim = make_finding("wired.bad_cable", native_id="port-x")
    engine.process_cycle(TS + 5000, findings=[victim])
    assert repo.get_open_issue_by_fingerprint(fingerprint(victim)) is not None


# ---------------------------------------------------------------------------
# device_down (children) — entity parentage
# ---------------------------------------------------------------------------
def _register_switch_and_port(repo) -> tuple[Entity, Entity]:
    switch = Entity(entity_type=EntityType.SWITCH, native_id="sw-mac", entity_id=1)
    port = Entity(entity_type=EntityType.PORT, native_id="sw-mac:5", entity_id=2, parent_id=1)
    repo.register_entity(switch)
    repo.register_entity(port)
    return switch, port


def test_device_down_suppresses_child_creation(repo, make_finding) -> None:
    engine = _engine(repo)
    switch, port = _register_switch_and_port(repo)

    device_down = make_finding("infra.device_down", entity=switch)
    child_issue = make_finding("wired.bad_cable", entity=port)
    engine.process_cycle(TS, findings=[device_down, child_issue])

    assert repo.get_open_issue_by_fingerprint(fingerprint(device_down)) is not None
    assert repo.get_open_issue_by_fingerprint(fingerprint(child_issue)) is None


def test_device_down_does_not_suppress_unrelated_entity(repo, make_finding) -> None:
    engine = _engine(repo)
    switch, _ = _register_switch_and_port(repo)
    other = Entity(entity_type=EntityType.PORT, native_id="other:1", entity_id=99)
    repo.register_entity(other)

    device_down = make_finding("infra.device_down", entity=switch)
    unrelated = make_finding("wired.bad_cable", entity=other)
    engine.process_cycle(TS, findings=[device_down, unrelated])

    assert repo.get_open_issue_by_fingerprint(fingerprint(unrelated)) is not None


def test_device_down_freezes_child_clear_streak(repo, make_finding) -> None:
    engine = _engine(repo)
    switch, port = _register_switch_and_port(repo)

    child_issue = make_finding("wired.bad_cable", entity=port)
    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[child_issue])
    fp = fingerprint(child_issue)
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE

    device_down = make_finding("infra.device_down", entity=switch)
    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, findings=[device_down], cleared=[fp])

    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE


def test_device_down_child_freeze_uses_persisted_parentage(repo, make_finding) -> None:
    # The clear path resolves parentage via repo.get_entity, not the finding.
    engine = _engine(repo)
    switch, port = _register_switch_and_port(repo)

    # child finding without an inline parent_id; engine must walk the store.
    bare_port = Entity(entity_type=EntityType.PORT, native_id="sw-mac:5", entity_id=2)
    child_issue = make_finding("wired.bad_cable", entity=bare_port)
    for i in range(3):
        engine.process_cycle(TS + i * 60, findings=[child_issue])
    fp = fingerprint(child_issue)

    device_down = make_finding("infra.device_down", entity=switch)
    engine.process_cycle(TS + 1000, findings=[device_down], cleared=[fp])
    # frozen -> still active, streak not advanced
    assert repo.get_open_issue_by_fingerprint(fp).clear_streak == 0


def test_device_down_does_not_suppress_its_own_issue(repo, make_finding) -> None:
    engine = _engine(repo)
    switch, _ = _register_switch_and_port(repo)
    device_down = make_finding("infra.device_down", entity=switch)
    engine.process_cycle(TS, findings=[device_down])
    fp = fingerprint(device_down)
    assert repo.get_open_issue_by_fingerprint(fp).state is IssueState.ACTIVE

    for i in range(6):
        engine.process_cycle(TS + 1000 + i * 60, cleared=[fp])
    assert repo.get_open_issue_by_fingerprint(fp) is None
