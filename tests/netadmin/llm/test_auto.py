"""Auto-investigation of confirmed issues (section 21).

Every test drives the worker on a fake clock, so the settle wait, the storm
window and the hourly/daily buckets are exercised at their real configured values
without a single second of real waiting. The store and the issue engine are the
real ones (a temp SQLite file), because the idempotency contract this feature
rests on is "the investigations table is the source of truth" -- faking the
repository would fake away the thing under test.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import pytest

from netadmin.config import Settings
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import EntityType, IssueState, Severity
from netadmin.issues.engine import IssueEngine
from netadmin.issues.models import EngineConfig, EventKind, Transition
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.llm import auto as auto_mod
from netadmin.llm import service
from netadmin.llm.auto import AutoInvestigator
from netadmin.llm.provider import ProviderRuntimeError, ProviderUnavailableError
from netadmin.store.repository import Repository

BASE_TS = 1_700_000_000


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
class FakeClock:
    """A monotonic clock that only advances when something sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += float(seconds)
        await asyncio.sleep(0)  # yield so the loop stays cooperative


class StubProvider:
    """A blocking provider returning a fixed answer, counting its calls."""

    name = "anthropic"
    blocking = True

    def __init__(self, answer: str = "## Answers\n### Root cause\nBad uplink.") -> None:
        self.answer = answer
        self.calls = 0

    def investigate(self, dossier: str) -> Optional[str]:
        self.calls += 1
        return self.answer


class ExplodingProvider:
    """A blocking provider that is available but fails mid-answer."""

    name = "anthropic"
    blocking = True

    def __init__(self) -> None:
        self.calls = 0

    def investigate(self, dossier: str) -> Optional[str]:
        self.calls += 1
        raise ProviderRuntimeError("upstream 500")


def _settings(tmp_db_path: Path, **auto: object) -> Settings:
    block: dict[str, object] = {
        "enabled": True,
        "provider": "manual",
        "severities": ["p1"],
        "settle_s": 120,
        "storm_threshold": 5,
        "storm_window_s": 300,
        "max_per_hour": 4,
        "max_per_day": 12,
        "fallback_to_manual": True,
    }
    block.update(auto)
    return Settings(_env_file=None, db_path=tmp_db_path, investigate={"auto": block})


def _store_engine(tmp_db_path: Path) -> tuple[Repository, IssueEngine]:
    store = Repository.open(tmp_db_path, site_id="default")
    return store, IssueEngine(StoreIssueRepository(store))


def _add_issue(
    store: Repository,
    *,
    fingerprint: str = "fp-1",
    severity: str = "p1",
    state: str = "active",
) -> int:
    return store.insert_issue(
        fingerprint=fingerprint,
        detector_key="wired.bad_cable",
        severity=severity,
        state=state,
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 60,
        title=f"trouble on {fingerprint}",
        evidence={"rx_errors_per_min": 42},
    )


def _activation(
    issue_id: int,
    *,
    severity: Severity = Severity.P1,
    to_state: Optional[IssueState] = IssueState.ACTIVE,
    kind: str = EventKind.ESCALATED,
    from_state: Optional[IssueState] = IssueState.PENDING,
) -> Transition:
    return Transition(
        issue_id=issue_id,
        fingerprint=f"fp-{issue_id}",
        detector_key="wired.bad_cable",
        severity=severity,
        title="trouble",
        kind=kind,
        ts=BASE_TS,
        from_state=from_state,
        to_state=to_state,
    )


def _build(
    store: Repository,
    engine: IssueEngine,
    settings: Settings,
    clock: FakeClock,
    tmp_path: Path,
) -> AutoInvestigator:
    return AutoInvestigator(
        store,
        engine,
        settings,
        clock=clock,
        sleeper=clock.sleep,
        base_dir=tmp_path / "dossiers",
    )


async def _drain(inv: AutoInvestigator, timeout: float = 10.0) -> None:
    """Wait for the worker to finish everything currently queued."""
    await asyncio.wait_for(inv._queue.join(), timeout)


def _events(store: Repository, issue_id: int, kind: str) -> list[dict]:
    """Matching events with ``detail`` decoded (the store returns it as raw JSON)."""
    out: list[dict] = []
    for row in store.list_issue_events(issue_id):
        if row["kind"] != kind:
            continue
        event = dict(row)
        raw = event.get("detail")
        event["detail"] = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        out.append(event)
    return out


# --------------------------------------------------------------------------- #
# 1. the happy path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_p1_activation_runs_exactly_one_investigation(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(store, engine, _settings(tmp_db_path, provider="anthropic"), clock, tmp_path)
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))
        await _drain(inv)

        rows = store.list_investigations(issue_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "answered"
        assert rows[0]["provider"] == "anthropic"
        assert "Bad uplink" in rows[0]["response_md"]
        assert stub.calls == 1
        assert inv.counters.ran == 1

        # the trail says a machine asked, not a human
        investigated = _events(store, issue_id, EventKind.INVESTIGATED)
        assert investigated, "no investigated event recorded"
        assert all(e["detail"].get("trigger") == "auto" for e in investigated)
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 2. what must never trigger
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_p2_and_pending_transitions_are_ignored(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(store, engine, _settings(tmp_db_path, provider="anthropic"), clock, tmp_path)
    try:
        p2 = _add_issue(store, fingerprint="fp-p2", severity="p2")
        pending = _add_issue(store, fingerprint="fp-pending", state="pending")
        await inv.start()

        # a P2 activation: right shape, wrong severity
        inv.on_transition(_activation(p2, severity=Severity.P2))
        # a P1 detection that is still only pending: never triggers
        inv.on_transition(
            _activation(
                pending,
                to_state=IssueState.PENDING,
                kind=EventKind.DETECTED,
                from_state=None,
            )
        )
        await _drain(inv)

        assert store.list_investigations(p2) == []
        assert store.list_investigations(pending) == []
        assert inv.counters.queued == 0
        assert stub.calls == 0
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 3. settle window
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_issue_resolving_inside_settle_costs_nothing(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(store, engine, _settings(tmp_db_path, provider="anthropic"), clock, tmp_path)
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))

        # The worker is parked in its settle sleep; the issue clears underneath it.
        await asyncio.sleep(0)
        store.update_issue(issue_id, state="resolved", resolved_ts=BASE_TS + 30)
        await _drain(inv)

        assert store.list_investigations(issue_id) == []
        assert stub.calls == 0
        assert inv.counters.skipped_settled == 1
        assert inv.counters.ran == 0
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 4. + 10. idempotency, in-process and across a restart
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_second_activation_is_skipped_as_duplicate(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(store, engine, _settings(tmp_db_path, provider="anthropic"), clock, tmp_path)
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))
        await _drain(inv)

        # the issue resolves, then reopens: same id, same dossier, no second spend
        inv.on_transition(_activation(issue_id, kind=EventKind.REOPENED))
        await _drain(inv)

        assert len(store.list_investigations(issue_id)) == 1
        assert stub.calls == 1
        assert inv.counters.skipped_duplicate == 1
    finally:
        await inv.stop()
        store.close()


@pytest.mark.asyncio
async def test_restart_with_an_existing_row_does_not_re_run(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    settings = _settings(tmp_db_path, provider="anthropic")

    first = _build(store, engine, settings, clock, tmp_path)
    try:
        issue_id = _add_issue(store)
        await first.start()
        first.on_transition(_activation(issue_id))
        await _drain(first)
        assert len(store.list_investigations(issue_id)) == 1
    finally:
        await first.stop()

    # A brand-new investigator with brand-new in-memory state: the durable row is
    # the only thing stopping a second run, which is exactly the point.
    second = _build(store, engine, settings, FakeClock(), tmp_path)
    try:
        await second.start()
        second.on_transition(_activation(issue_id))
        await _drain(second)

        assert len(store.list_investigations(issue_id)) == 1
        assert stub.calls == 1
        assert second.counters.skipped_duplicate == 1
    finally:
        await second.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 5. a human clicking during the settle window
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_manual_click_during_settle_wins_and_auto_skips(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(store, engine, _settings(tmp_db_path, provider="anthropic"), clock, tmp_path)
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))
        await asyncio.sleep(0)

        # A human hits the button while the worker is settling: the API path writes
        # its own row through the same store.
        store.insert_investigation(
            issue_id=issue_id,
            provider="manual",
            dossier_md="# clicked by a human",
            status="pending",
            ts=BASE_TS + 5,
        )
        await _drain(inv)

        rows = store.list_investigations(issue_id)
        assert len(rows) == 1, "auto must not add a second row behind a human"
        assert rows[0]["provider"] == "manual"
        assert stub.calls == 0
        assert inv.counters.skipped_duplicate == 1
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 6. storm
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_storm_investigates_only_incident_roots(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(
        store,
        engine,
        _settings(tmp_db_path, provider="anthropic", settle_s=10, max_per_hour=50, max_per_day=50),
        clock,
        tmp_path,
    )
    try:
        issue_ids = [_add_issue(store, fingerprint=f"fp-{n}") for n in range(8)]
        root = issue_ids[0]
        incident_id = store.insert_incident(
            fingerprint="inc-1",
            root_issue_id=root,
            severity="p1",
            state="active",
            first_seen_ts=BASE_TS,
            last_seen_ts=BASE_TS + 60,
            title="uplink incident",
        )
        store.replace_incident_members(
            incident_id,
            [{"issue_id": root, "role": "root", "rule": "topology", "rationale": "uplink"}]
            + [
                {"issue_id": i, "role": "symptom", "rule": "topology", "rationale": "downstream"}
                for i in issue_ids[1:]
            ],
        )

        await inv.start()
        for issue_id in issue_ids:  # 8 triggers, threshold 5 -> a storm
            inv.on_transition(_activation(issue_id))
        await _drain(inv)

        assert len(store.list_investigations(root)) == 1, "the root must be investigated"
        for symptom in issue_ids[1:]:
            assert store.list_investigations(symptom) == [], f"symptom {symptom} was investigated"
        assert stub.calls == 1
        assert inv.counters.skipped_storm == 7
    finally:
        await inv.stop()
        store.close()


@pytest.mark.asyncio
async def test_uncorrelated_issues_survive_a_storm(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storm suppresses symptoms of a known cause, not unrelated problems."""
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(
        store,
        engine,
        _settings(tmp_db_path, provider="anthropic", settle_s=10, max_per_hour=50, max_per_day=50),
        clock,
        tmp_path,
    )
    try:
        issue_ids = [_add_issue(store, fingerprint=f"fp-{n}") for n in range(8)]
        await inv.start()
        for issue_id in issue_ids:
            inv.on_transition(_activation(issue_id))
        await _drain(inv)

        # None of them belong to an incident, so each is judged on its own merits.
        assert all(len(store.list_investigations(i)) == 1 for i in issue_ids)
        assert inv.counters.skipped_storm == 0
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 7. spend caps
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_hourly_cap_is_enforced_and_resets_after_the_window(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(
        store,
        engine,
        _settings(
            tmp_db_path,
            provider="anthropic",
            settle_s=0,
            max_per_hour=2,
            max_per_day=12,
            storm_threshold=1000,
        ),
        clock,
        tmp_path,
    )
    try:
        issue_ids = [_add_issue(store, fingerprint=f"fp-{n}") for n in range(4)]
        await inv.start()
        for issue_id in issue_ids:
            inv.on_transition(_activation(issue_id))
        await _drain(inv)

        assert stub.calls == 2, "the hourly cap must be a hard ceiling"
        assert inv.counters.skipped_cap == 2
        assert inv.counters.ran == 2

        # An hour later the bucket has drained; a new issue investigates again.
        clock.now += 3601.0
        later = _add_issue(store, fingerprint="fp-later")
        inv.on_transition(_activation(later))
        await _drain(inv)

        assert stub.calls == 3
        assert len(store.list_investigations(later)) == 1
    finally:
        await inv.stop()
        store.close()


@pytest.mark.asyncio
async def test_daily_cap_holds_across_hour_boundaries(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    stub = StubProvider()
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: stub)
    clock = FakeClock()
    inv = _build(
        store,
        engine,
        _settings(
            tmp_db_path,
            provider="anthropic",
            settle_s=0,
            max_per_hour=1,
            max_per_day=3,
            storm_threshold=1000,
        ),
        clock,
        tmp_path,
    )
    try:
        await inv.start()
        # One per hour for five hours: the hourly bucket always allows it, but the
        # daily ceiling of 3 must still bite.
        for n in range(5):
            issue_id = _add_issue(store, fingerprint=f"fp-h{n}")
            inv.on_transition(_activation(issue_id))
            await _drain(inv)
            clock.now += 3601.0

        assert stub.calls == 3
        assert inv.counters.ran == 3
        assert inv.counters.skipped_cap == 2
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 8. an unavailable paid provider
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unavailable_provider_falls_back_to_a_manual_dossier(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)

    def _factory(name: str, **kwargs: object) -> object:
        if name == "anthropic":
            raise ProviderUnavailableError("ANTHROPIC_API_KEY not set in the environment.")
        from netadmin.llm.manual import ManualProvider

        return ManualProvider(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "build_provider", _factory)
    clock = FakeClock()
    inv = _build(store, engine, _settings(tmp_db_path, provider="anthropic"), clock, tmp_path)
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))
        await _drain(inv)

        rows = store.list_investigations(issue_id)
        assert len(rows) == 1
        assert rows[0]["provider"] == "manual"
        assert rows[0]["status"] == "pending", "an honest 'awaiting import', not a fake answer"
        assert rows[0]["dossier_md"], "the fallback still compiles a real dossier"
        assert inv.counters.ran == 1
    finally:
        await inv.stop()
        store.close()


@pytest.mark.asyncio
async def test_unavailable_provider_without_fallback_skips(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)

    def _factory(name: str, **kwargs: object) -> object:
        raise ProviderUnavailableError("copilot CLI not found on PATH")

    monkeypatch.setattr(service, "build_provider", _factory)
    clock = FakeClock()
    inv = _build(
        store,
        engine,
        _settings(tmp_db_path, provider="copilot", fallback_to_manual=False),
        clock,
        tmp_path,
    )
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))
        await _drain(inv)

        assert store.list_investigations(issue_id) == []
        assert inv.counters.failed == 1
        assert inv.counters.ran == 0
        assert "copilot CLI not found" in (inv.counters.last_error or "")
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 9. a provider that blows up mid-answer
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_provider_failure_leaves_pending_row_and_worker_alive(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, engine = _store_engine(tmp_db_path)
    exploding = ExplodingProvider()
    good = StubProvider()
    providers: list[object] = [exploding, good]
    monkeypatch.setattr(service, "build_provider", lambda *a, **k: providers.pop(0))

    clock = FakeClock()
    inv = _build(
        store,
        engine,
        _settings(tmp_db_path, provider="anthropic", storm_threshold=1000),
        clock,
        tmp_path,
    )
    try:
        bad = _add_issue(store, fingerprint="fp-bad")
        nxt = _add_issue(store, fingerprint="fp-next")
        await inv.start()
        inv.on_transition(_activation(bad))
        await _drain(inv)

        rows = store.list_investigations(bad)
        assert len(rows) == 1
        assert rows[0]["status"] == "pending", "the dossier survives a provider failure"
        assert rows[0]["response_md"] is None
        assert inv.counters.failed == 1

        # the failure event is recorded honestly in the trail
        failed = [
            e
            for e in _events(store, bad, EventKind.INVESTIGATED)
            if e["detail"].get("status") == "failed"
        ]
        assert failed and failed[0]["detail"]["trigger"] == "auto"

        # and the worker is still alive for the next issue
        assert inv.running
        inv.on_transition(_activation(nxt))
        await _drain(inv)
        assert len(store.list_investigations(nxt)) == 1
        assert good.calls == 1
    finally:
        await inv.stop()
        store.close()


# --------------------------------------------------------------------------- #
# 11. configuration
# --------------------------------------------------------------------------- #
def test_auto_investigate_defaults_are_off_and_free(tmp_db_path: Path) -> None:
    settings = Settings(_env_file=None, db_path=tmp_db_path)
    auto = settings.investigate.auto
    assert auto.enabled is False, "auto-investigation must be off by default"
    assert auto.provider == "manual", "the default provider must never cost money"
    assert auto.severities == ["p1"]
    assert auto.fallback_to_manual is True


def test_bad_provider_name_is_rejected_at_startup(tmp_db_path: Path) -> None:
    with pytest.raises(Exception) as excinfo:
        Settings(
            _env_file=None,
            db_path=tmp_db_path,
            investigate={"auto": {"enabled": True, "provider": "gpt-9"}},
        )
    assert "gpt-9" in str(excinfo.value)


def test_bad_severity_is_rejected_at_startup(tmp_db_path: Path) -> None:
    with pytest.raises(Exception) as excinfo:
        Settings(
            _env_file=None,
            db_path=tmp_db_path,
            investigate={"auto": {"enabled": True, "severities": ["p0"]}},
        )
    assert "p0" in str(excinfo.value)


def test_disabled_investigator_registers_nothing(tmp_db_path: Path, tmp_path: Path) -> None:
    store, engine = _store_engine(tmp_db_path)
    try:
        settings = _settings(tmp_db_path, enabled=False)
        inv = _build(store, engine, settings, FakeClock(), tmp_path)
        before = len(engine._callbacks)
        asyncio.run(inv.start())
        assert len(engine._callbacks) == before, "a disabled investigator must not subscribe"
        assert inv.running is False
        assert inv.health()["enabled"] is False
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# 12. the worker must never block the loop the detect cycles run on
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_slow_provider_never_blocks_the_event_loop(
    tmp_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow provider runs in a thread, so engine work on the loop stays fast.

    The detect passes drive the issue engine on the daemon's single event loop. If
    a provider's network call ran there, one slow model would stall detection for
    the whole network -- the failure this test exists to catch.
    """
    store, engine = _store_engine(tmp_db_path)
    provider_seconds = 0.6
    released = asyncio.Event()
    loop = asyncio.get_running_loop()

    class SlowProvider:
        name = "anthropic"
        blocking = True

        def investigate(self, dossier: str) -> str:
            loop.call_soon_threadsafe(released.set)
            time.sleep(provider_seconds)  # a blocking network call, faithfully
            return "## Answers\nslow but done"

    monkeypatch.setattr(service, "build_provider", lambda *a, **k: SlowProvider())
    clock = FakeClock()
    inv = _build(
        store, engine, _settings(tmp_db_path, provider="anthropic", settle_s=0), clock, tmp_path
    )
    try:
        issue_id = _add_issue(store)
        await inv.start()
        inv.on_transition(_activation(issue_id))

        # Wait until the provider is genuinely executing, then time engine cycles.
        await asyncio.wait_for(released.wait(), timeout=5.0)
        started = time.perf_counter()
        for _ in range(20):
            engine.process_cycle(BASE_TS + 900)
            await asyncio.sleep(0)
        elapsed = time.perf_counter() - started

        assert elapsed < provider_seconds / 2, (
            f"20 engine cycles took {elapsed:.3f}s while a {provider_seconds}s provider "
            "was running: the provider is blocking the event loop"
        )
        await _drain(inv)
        assert store.list_investigations(issue_id)[0]["status"] == "answered"
    finally:
        await inv.stop()
        store.close()


def test_module_exports_the_auto_trigger_label() -> None:
    assert auto_mod.AUTO_TRIGGER == "auto"


# --------------------------------------------------------------------------- #
# 13. restart inside one process
# --------------------------------------------------------------------------- #
def _p1_finding() -> Finding:
    return Finding(
        detector_key="wired.bad_cable",
        entity=Entity(
            entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:01", site_id="default"
        ),
        severity=Severity.P1,
        title="rx_errors climbing",
    )


@pytest.mark.asyncio
async def test_stop_unregisters_the_engine_callback(tmp_db_path: Path, tmp_path: Path) -> None:
    """Inert is not enough: a callback left attached is delivered twice after a
    restart, because the engine appends unconditionally."""
    store, engine = _store_engine(tmp_db_path)
    inv = _build(store, engine, _settings(tmp_db_path), FakeClock(), tmp_path)
    try:
        await inv.start()
        assert engine._callbacks.count(inv.on_transition) == 1
        await inv.stop()
        assert inv.on_transition not in engine._callbacks
    finally:
        await inv.stop()
        store.close()


@pytest.mark.asyncio
async def test_restart_investigates_each_activation_exactly_once(
    tmp_db_path: Path, tmp_path: Path
) -> None:
    """A stop/start cycle leaves one subscription, so one activation buys one
    dossier -- not two triggers and not a second row."""
    store = Repository.open(tmp_db_path, site_id="default")
    # M = 1 so a single fire is a confirmed activation: pending -> active in one
    # cycle, which is the transition the investigator subscribes to.
    engine = IssueEngine(StoreIssueRepository(store), config=EngineConfig(default_m=1))
    inv = _build(store, engine, _settings(tmp_db_path, settle_s=0), FakeClock(), tmp_path)
    try:
        await inv.start()
        await inv.stop()
        await inv.start()
        assert engine._callbacks.count(inv.on_transition) == 1

        transitions = engine.process_cycle(BASE_TS, findings=[_p1_finding()])
        kinds = [t.kind for t in transitions]
        assert kinds == [EventKind.DETECTED, EventKind.ESCALATED]
        issue_id = transitions[-1].issue_id
        await _drain(inv)

        assert inv.counters.queued == 1
        assert inv.counters.ran == 1
        assert len(store.list_investigations(issue_id)) == 1
    finally:
        await inv.stop()
        store.close()


@pytest.mark.asyncio
async def test_repeated_start_stop_cycles_never_accumulate_callbacks(
    tmp_db_path: Path, tmp_path: Path
) -> None:
    store, engine = _store_engine(tmp_db_path)
    inv = _build(store, engine, _settings(tmp_db_path), FakeClock(), tmp_path)
    try:
        for _ in range(3):
            await inv.start()
            await inv.start()  # a second start is a no-op, not a second subscription
            assert engine._callbacks.count(inv.on_transition) == 1
            await inv.stop()
            await inv.stop()  # stopping twice is safe
            assert inv.on_transition not in engine._callbacks
    finally:
        store.close()


@pytest.mark.asyncio
async def test_stop_clears_queued_triggers_and_their_bookkeeping(
    tmp_db_path: Path, tmp_path: Path
) -> None:
    """A cancelled worker must not leave an id marked enqueued forever, and the
    queue's unfinished count must land back on zero so a later wait cannot hang."""
    store, engine = _store_engine(tmp_db_path)
    inv = _build(store, engine, _settings(tmp_db_path), FakeClock(), tmp_path)
    try:
        issue_id = _add_issue(store)
        inv._enqueued.add(issue_id)
        inv._queue.put_nowait(issue_id)

        await inv.stop()

        assert inv._queue.empty()
        assert inv._enqueued == set()
        await asyncio.wait_for(inv._queue.join(), timeout=1.0)
    finally:
        store.close()
