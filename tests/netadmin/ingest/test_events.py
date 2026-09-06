"""Event pipeline: normalization, batch persistence, catch-up, supervisor.

Fixture-driven and offline. Controller event rows come from the recorded
``stat_event.json`` (MACs already randomized at record time); no test touches a
real controller. The store is a fresh migrated SQLite file per test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.ingest.events import (
    EventListener,
    EventNormalizer,
    WsSupervisor,
    catchup_events,
    newest_stored_event_ts,
)
from netadmin.ingest.unifi.models import Event
from netadmin.store.repository import Repository

FIXTURE = Path(__file__).parents[1] / "unifi" / "fixtures" / "stat_event.json"

# MACs present in the fixture, and how they should be interned.
CLIENT_MAC = "02:00:aa:bb:cc:01"
AP_TO_MAC = "02:00:11:22:33:01"
AP_FROM_MAC = "02:00:11:22:33:02"
SWITCH_MAC = "02:00:11:22:33:08"
GATEWAY_MAC = "02:00:11:22:33:09"

_SEED = [
    (EntityType.CLIENT, CLIENT_MAC, "client-a"),
    (EntityType.AP, AP_TO_MAC, "ap-1"),
    (EntityType.AP, AP_FROM_MAC, "ap-2"),
    (EntityType.SWITCH, SWITCH_MAC, "sw-core"),
    (EntityType.GATEWAY, GATEWAY_MAC, "gw-1"),
]

# Wide read window that covers every fixture timestamp (~1.72e9).
FULL = (0, 2_000_000_000)


def load_events() -> list[Event]:
    data = json.loads(FIXTURE.read_text())["data"]
    return [Event.model_validate(row) for row in data]


def event_by_key(key: str) -> Event:
    return next(e for e in load_events() if e.key == key)


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    """A migrated store pre-seeded with the fixture's device/client entities."""
    r = Repository.open(tmp_db_path)
    for etype, mac, name in _SEED:
        r.upsert_entity(Entity(entity_type=etype, native_id=mac, name=name), ts=1_000_000)
    yield r
    r.close()


def entity_id(repo: Repository, etype: EntityType, mac: str) -> int:
    row = repo.find_entity(etype, mac)
    assert row is not None
    return int(row["entity_id"])


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeWs:
    """Stand-in for ``unifi.ws.EventListener``: yields a fixed event list."""

    def __init__(self, events: list[Event], *, fail: Optional[Exception] = None) -> None:
        self._events = events
        self._fail = fail

    async def events(self) -> AsyncIterator[Event]:
        for event in self._events:
            yield event
        if self._fail is not None:
            raise self._fail

    def stop(self) -> None:  # pragma: no cover - parity with the real listener
        pass


class FakeEndpoints:
    """Stand-in for ``unifi.endpoints.Endpoints`` exposing ``stat_event``."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self.calls = 0

    async def stat_event(
        self, *, within_hours: Optional[int] = None, max_events: Optional[int] = None
    ) -> list[Event]:
        self.calls += 1
        return list(self._events)


# --------------------------------------------------------------------------- #
# Normalization: timestamps
# --------------------------------------------------------------------------- #
def test_ms_to_seconds_conversion(repo: Repository) -> None:
    rec = EventNormalizer(repo).normalize(event_by_key("EVT_WU_Roam"))
    assert rec is not None
    assert rec["ts"] == 1_721_600_000  # 1721600000000 ms -> s


def test_seconds_timestamp_passthrough(repo: Repository) -> None:
    ev = Event.model_validate({"key": "EVT_X", "time": 1_721_600_000})
    rec = EventNormalizer(repo).normalize(ev)
    assert rec is not None and rec["ts"] == 1_721_600_000


def test_datetime_fallback_when_no_numeric_time(repo: Repository) -> None:
    ev = Event.model_validate({"key": "EVT_X", "datetime": "2024-07-21T22:13:20Z"})
    rec = EventNormalizer(repo).normalize(ev)
    assert rec is not None and rec["ts"] == 1_721_600_000


def test_unstorable_events_return_none(repo: Repository) -> None:
    norm = EventNormalizer(repo)
    assert norm.normalize(Event.model_validate({"time": 1_000_000_000_000})) is None
    assert norm.normalize(Event.model_validate({"key": "EVT_X"})) is None


# --------------------------------------------------------------------------- #
# Normalization: entity resolution
# --------------------------------------------------------------------------- #
def test_roam_entity_is_client_related_is_from_ap(repo: Repository) -> None:
    rec = EventNormalizer(repo).normalize(event_by_key("EVT_WU_Roam"))
    assert rec is not None
    assert rec["entity_id"] == entity_id(repo, EntityType.CLIENT, CLIENT_MAC)
    # related is the *from* AP, not the destination AP.
    assert rec["related_entity_id"] == entity_id(repo, EntityType.AP, AP_FROM_MAC)
    assert rec["related_entity_id"] != entity_id(repo, EntityType.AP, AP_TO_MAC)


def test_switch_event_resolves_to_switch(repo: Repository) -> None:
    rec = EventNormalizer(repo).normalize(event_by_key("EVT_SW_PoeOverload"))
    assert rec is not None
    assert rec["entity_id"] == entity_id(repo, EntityType.SWITCH, SWITCH_MAC)
    assert rec["related_entity_id"] is None


def test_ap_event_resolves_to_ap(repo: Repository) -> None:
    rec = EventNormalizer(repo).normalize(event_by_key("EVT_AP_RadarDetected"))
    assert rec is not None
    assert rec["entity_id"] == entity_id(repo, EntityType.AP, AP_TO_MAC)


def test_gateway_event_resolves_to_gateway(repo: Repository) -> None:
    rec = EventNormalizer(repo).normalize(event_by_key("EVT_GW_WANTransition"))
    assert rec is not None
    assert rec["entity_id"] == entity_id(repo, EntityType.GATEWAY, GATEWAY_MAC)


def test_unknown_mac_stored_with_null_entity(repo: Repository) -> None:
    ev = Event.model_validate(
        {"_id": "zz", "key": "EVT_AP_Lost", "time": 1_721_600_000_000, "ap": "02:00:99:99:99:99"}
    )
    rec = EventNormalizer(repo).normalize(ev)
    assert rec is not None
    assert rec["entity_id"] is None
    # It still persists (tolerated, not dropped).
    assert repo.record_event(**rec) is not None
    rows = repo.read_events(*FULL)
    assert any(r["key"] == "EVT_AP_Lost" and r["entity_id"] is None for r in rows)


# --------------------------------------------------------------------------- #
# Normalization: dedupe key
# --------------------------------------------------------------------------- #
def test_dedupe_key_uses_controller_id(repo: Repository) -> None:
    ev = Event.model_validate({"_id": "abc123", "key": "EVT_X", "time": 1_721_600_000_000})
    rec = EventNormalizer(repo).normalize(ev)
    assert rec is not None and rec["native_id"] == "abc123"


def test_dedupe_key_hashes_when_no_id(repo: Repository) -> None:
    ev = Event.model_validate({"key": "EVT_X", "time": 1_721_600_000_000, "ap": AP_TO_MAC})
    norm = EventNormalizer(repo)
    first = norm.normalize(ev)
    second = norm.normalize(ev)
    assert first is not None and second is not None
    assert first["native_id"].startswith("h:")
    assert first["native_id"] == second["native_id"]  # stable across calls


def test_dedupe_key_disambiguates_same_second_distinct_msg(repo: Repository) -> None:
    # Two DISTINCT events sharing a second, key and client must not collide: the
    # bare (ts,key,mac) key silently dropped the second. msg disambiguates them
    # while still collapsing a genuine WS/catch-up twin (identical msg).
    base: dict[str, Any] = {
        "key": "EVT_WU_Disconnected",
        "time": 1_721_600_000_000,
        "user": CLIENT_MAC,
    }
    norm = EventNormalizer(repo)
    a = norm.normalize(Event.model_validate({**base, "msg": "reason A"}))
    b = norm.normalize(Event.model_validate({**base, "msg": "reason B"}))
    a_twin = norm.normalize(Event.model_validate({**base, "msg": "reason A"}))
    assert a is not None and b is not None and a_twin is not None
    assert a["native_id"] != b["native_id"]  # distinct events kept apart
    assert a["native_id"] == a_twin["native_id"]  # identical twin still collapses


# --------------------------------------------------------------------------- #
# EventListener consumer
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_listener_writes_all_events(repo: Repository) -> None:
    listener = EventListener(FakeWs(load_events()), repo, flush_interval=None, batch_size=2)
    written = await listener.run()
    assert written == 4
    assert len(repo.read_events(*FULL)) == 4


@pytest.mark.asyncio
async def test_listener_flushes_remainder_below_batch_size(repo: Repository) -> None:
    # batch_size larger than the stream: nothing flushes mid-loop, all on exit.
    listener = EventListener(FakeWs(load_events()), repo, flush_interval=None, batch_size=100)
    written = await listener.run()
    assert written == 4
    assert len(repo.read_events(*FULL)) == 4


@pytest.mark.asyncio
async def test_listener_periodic_flusher_lifecycle(repo: Repository) -> None:
    # A live flush_interval must not hang or double-write; the flusher task is
    # created and cleanly cancelled on exit.
    listener = EventListener(FakeWs(load_events()), repo, flush_interval=0.01, batch_size=100)
    written = await asyncio.wait_for(listener.run(), timeout=2.0)
    assert written == 4
    assert len(repo.read_events(*FULL)) == 4


@pytest.mark.asyncio
async def test_listener_dedupes_within_stream(repo: Repository) -> None:
    # Same event twice in the stream collapses to one row (native-id dedupe).
    doubled = load_events() + load_events()
    listener = EventListener(FakeWs(doubled), repo, flush_interval=None, batch_size=3)
    written = await listener.run()
    assert written == 4
    assert len(repo.read_events(*FULL)) == 4


# --------------------------------------------------------------------------- #
# Catch-up + WS/catch-up overlap dedupe
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_catchup_inserts_on_empty_store(repo: Repository) -> None:
    inserted = await catchup_events(repo, FakeEndpoints(load_events()))
    assert inserted == 4
    assert len(repo.read_events(*FULL)) == 4


@pytest.mark.asyncio
async def test_catchup_dedupes_against_ws_writes(repo: Repository) -> None:
    events = load_events()
    await EventListener(FakeWs(events), repo, flush_interval=None).run()
    assert len(repo.read_events(*FULL)) == 4
    # since_ts=0 defeats the cursor, so every event is offered to the store and
    # rejected purely by native-id dedupe -- the real overlap guard.
    inserted = await catchup_events(repo, FakeEndpoints(events), since_ts=0)
    assert inserted == 0
    assert len(repo.read_events(*FULL)) == 4


@pytest.mark.asyncio
async def test_catchup_cursor_skips_already_captured(repo: Repository) -> None:
    # Cursor at the third fixture ts keeps only events at/after it.
    inserted = await catchup_events(repo, FakeEndpoints(load_events()), since_ts=1_721_600_120)
    assert inserted == 2  # ts 1721600120 and 1721600180


@pytest.mark.asyncio
async def test_catchup_uses_stored_cursor_by_default(repo: Repository) -> None:
    events = load_events()
    await EventListener(FakeWs(events), repo, flush_interval=None).run()
    # Default cursor = newest stored ts; only events strictly older are trimmed,
    # and the newest itself dedupes -> nothing new.
    inserted = await catchup_events(repo, FakeEndpoints(events))
    assert inserted == 0


# --------------------------------------------------------------------------- #
# Cursor helper
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_newest_stored_event_ts(repo: Repository) -> None:
    assert newest_stored_event_ts(repo) is None
    await EventListener(FakeWs(load_events()), repo, flush_interval=None).run()
    assert newest_stored_event_ts(repo) == 1_721_600_180


# --------------------------------------------------------------------------- #
# Supervisor
# --------------------------------------------------------------------------- #
class _FlakyListener:
    """Pops a queued outcome per ``run``: an Exception raises, an int returns."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def run(self) -> int:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return int(outcome)


@pytest.mark.asyncio
async def test_supervisor_restarts_with_capped_backoff(repo: Repository) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    listener = _FlakyListener([RuntimeError("boom1"), RuntimeError("boom2"), 5])
    sup = WsSupervisor(
        lambda: listener,
        repo,
        backoff_base=1.0,
        backoff_max=10.0,
        max_restarts=2,
        sleep=fake_sleep,
    )
    await sup.run()

    # Two failures then a clean run before giving up at the restart cap.
    assert listener.calls == 3
    # Exponential: base, then doubled.
    assert sleeps == [1.0, 2.0]

    rows = repo.read_poll_runs("ws", *FULL)
    started = [r for r in rows if r["error"] == "started"]
    failed = [r for r in rows if r["ok"] == 0]
    clean = [r for r in rows if r["error"] == "stopped" and r["ok"] == 1]
    assert len(started) == 3  # one per attempt
    assert len(failed) == 2  # the two RuntimeErrors
    assert len(clean) == 1  # the clean third run
    assert all("RuntimeError" in r["error"] for r in failed)


@pytest.mark.asyncio
async def test_supervisor_stop_ends_after_current_attempt(repo: Repository) -> None:
    async def fake_sleep(delay: float) -> None:  # pragma: no cover - never reached
        raise AssertionError("stop() should end the loop before any backoff sleep")

    sup: WsSupervisor

    class _StopOnRun:
        async def run(self) -> int:
            sup.stop()
            return 0

    sup = WsSupervisor(lambda: _StopOnRun(), repo, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=2.0)

    rows = repo.read_poll_runs("ws", *FULL)
    assert any(r["error"] == "started" for r in rows)
    assert any(r["error"] == "stopped" and r["ok"] == 1 for r in rows)


@pytest.mark.asyncio
async def test_supervisor_records_transition_for_real_listener_death(repo: Repository) -> None:
    # An EventListener wrapping a failing WS generator: the supervisor catches
    # the death and records a not-ok ws poll_run.
    sup: WsSupervisor

    def factory() -> EventListener:
        ws = FakeWs(load_events(), fail=ConnectionResetError("socket gone"))
        return EventListener(ws, repo, flush_interval=None)

    async def fake_sleep(delay: float) -> None:
        sup.stop()  # let it die once, then stop

    sup = WsSupervisor(factory, repo, backoff_base=0.0, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=2.0)

    # The four events written before the drop are persisted.
    assert len(repo.read_events(*FULL)) == 4
    rows = repo.read_poll_runs("ws", *FULL)
    assert any(r["ok"] == 0 and "ConnectionResetError" in r["error"] for r in rows)


# --------------------------------------------------------------------------- #
# Finding: entity resolution must not negative-cache a not-yet-created entity
# --------------------------------------------------------------------------- #
def test_resolve_does_not_negative_cache_late_created_entity(repo: Repository) -> None:
    # A client's first frame (assoc) arrives before stat/sta creates its entity.
    norm = EventNormalizer(repo)  # one long-lived normalizer, as under WsSupervisor
    mac = "02:00:aa:bb:cc:99"  # NOT in the seeded inventory yet

    first = norm.normalize(
        Event.model_validate({"key": "EVT_WU_Connected", "time": 1_721_600_000_000, "user": mac})
    )
    assert first is not None and first["entity_id"] is None  # unresolved, tolerated

    # stat/sta later discovers the client and creates its entity.
    repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id=mac, name="late-client"), ts=1_000_000
    )

    second = norm.normalize(
        Event.model_validate({"key": "EVT_WU_Disconnected", "time": 1_721_600_060_000, "user": mac})
    )
    assert second is not None
    # NOT stranded at NULL: the miss was never cached, so it re-resolves.
    assert second["entity_id"] == entity_id(repo, EntityType.CLIENT, mac)


# --------------------------------------------------------------------------- #
# Finding: catch-up must bound stat/event, not page the whole backlog each cycle
# --------------------------------------------------------------------------- #
class RecordingEndpoints:
    """Captures the ``within_hours`` each ``stat_event`` call was made with."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self.within_hours_seen: list[Optional[int]] = []

    async def stat_event(
        self, *, within_hours: Optional[int] = None, max_events: Optional[int] = None
    ) -> list[Event]:
        self.within_hours_seen.append(within_hours)
        return list(self._events)


@pytest.mark.asyncio
async def test_catchup_bounds_within_hours_from_cursor(repo: Repository) -> None:
    events = load_events()
    # A completed HISTORY read, not a newer live arrival, is the bounded cursor.
    repo.record_ingest_coverage(
        kind="event_history", scope="site", interval="retained",
        start_ts=1_721_599_000, end_ts=1_721_600_180, status="complete",
    )
    # coverage end == 1_721_600_180; pretend "now" is 2 h later.
    now = 1_721_600_180 + 2 * 3600
    ep = RecordingEndpoints(events)
    await catchup_events(repo, ep, now=now)
    # gap_hours(2) + 1 + margin(1) = 4: a narrow window, not the full backlog.
    assert ep.within_hours_seen == [4]


# --------------------------------------------------------------------------- #
# C3/C7/R2 regressions
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c3_catchup_recovers_gap_older_than_live_event(repo: Repository) -> None:
    live = Event.model_validate({"_id": "live-300", "key": "EVT_X", "time": 300_000})
    missing = Event.model_validate({"_id": "miss-200", "key": "EVT_X", "time": 200_000})
    newer = Event.model_validate({"_id": "hist-400", "key": "EVT_X", "time": 400_000})
    await EventListener(FakeWs([live]), repo, flush_interval=None).run()

    inserted = await catchup_events(repo, FakeEndpoints([missing, live, newer]), now=400)

    assert inserted == 2
    assert {r["native_id"] for r in repo.read_events(0, 500_000)} == {
        "miss-200", "live-300", "hist-400"
    }


def test_c7_duplicate_replay_fills_pre_inventory_entity(repo: Repository) -> None:
    mac = "02:00:aa:bb:cc:88"
    event = Event.model_validate(
        {"_id": "late-link", "key": "EVT_WU_Connected", "time": 1_721_600_000_000, "user": mac}
    )
    normalizer = EventNormalizer(repo)
    first = normalizer.normalize(event)
    assert first is not None and first["entity_id"] is None
    assert repo.record_events_enriching_entities([first]) == 1
    eid = repo.upsert_entity(Entity(entity_type=EntityType.CLIENT, native_id=mac), ts=1)

    replay = normalizer.normalize(event)
    assert replay is not None and replay["entity_id"] == eid
    assert repo.record_events_enriching_entities([replay]) == 0
    assert repo.read_events(*FULL)[0]["entity_id"] == eid


def test_r2_failed_flush_keeps_batch_for_retry(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    event = Event.model_validate({"_id": "flush-keep", "key": "EVT_X", "time": 1_721_600_000_000})
    listener = EventListener(FakeWs([]), repo, flush_interval=None)
    record = EventNormalizer(repo).normalize(event)
    assert record is not None
    listener._batch.append(record)
    real = repo.record_events_enriching_entities
    calls = 0

    def locked(rows: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            import sqlite3

            raise sqlite3.OperationalError("database is locked")
        return real(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", locked)
    with pytest.raises(Exception, match="locked"):
        listener._flush()
    assert len(listener._batch) == 1
    assert listener._flush() == 1
    assert listener._batch == []


@pytest.mark.asyncio
async def test_catchup_unbounded_only_when_no_cursor(repo: Repository) -> None:
    # Empty store: no cursor yet, so the first sweep is unbounded (within=None),
    # then self-bounds once any event is stored.
    ep = RecordingEndpoints([])
    await catchup_events(repo, ep)
    assert ep.within_hours_seen == [None]


# --------------------------------------------------------------------------- #
# C3: recorded coverage must be clamped to the window actually fetched. A
# bounded 1 h fetch must NOT book coverage for the untouched day behind it.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c3_bounded_fetch_clamps_recorded_coverage(repo: Repository) -> None:
    now = 1_721_700_000
    day_ago = now - 24 * 3600
    # A stale completed coverage cursor a day in the past (the catch-up baseline).
    repo.record_ingest_coverage(
        kind="event_history", scope="site", interval="retained",
        start_ts=day_ago - 3600, end_ts=day_ago, status="complete",
    )
    # A caller-pinned 1 h bounded fetch (empty result is fine: coverage is what
    # we assert, not inserts).
    await catchup_events(repo, FakeEndpoints([]), within_hours=1, now=now)

    # The last hour we actually read is fully covered...
    assert repo.observed_event_coverage(now - 3600, now) == 1.0
    # ...but the preceding day we did NOT read must not be reported covered.
    # (The bug recorded [cursor .. now] complete, making this 1.0.)
    assert repo.observed_event_coverage(now - 12 * 3600, now - 2 * 3600) == 0.0


# --------------------------------------------------------------------------- #
# R3: supervisor/health state must reflect the ACTUAL socket state, reported up
# from the listener -- never assumed because a task exists.
# --------------------------------------------------------------------------- #
class _ScriptedWs:
    """A ws-layer double that drives ``on_state`` through a scripted sequence."""

    def __init__(self, states: list[str]) -> None:
        self._states = states
        self.on_state = None  # set by the events.EventListener wrapper
        self._stop = SimpleNamespaceStop()

    async def events(self):  # async generator that yields no events
        for state in self._states:
            if self.on_state is not None:
                self.on_state(state)
        return
        yield  # pragma: no cover - marks this an async generator

    def stop(self) -> None:  # pragma: no cover - parity
        self._stop.set()


class SimpleNamespaceStop:
    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:  # pragma: no cover - parity
        self._set = True

    def is_set(self) -> bool:
        return self._set


@pytest.mark.asyncio
async def test_r3_events_listener_relays_socket_state(repo: Repository) -> None:
    # The events-layer listener must forward the ws socket's state changes to the
    # supervisor hook (on_connection_state) and cache the latest.
    ws = _ScriptedWs(["connected", "reconnecting"])
    listener = EventListener(ws, repo, flush_interval=None)
    seen: list[str] = []
    listener.on_connection_state = seen.append
    await listener.run()
    assert seen == ["connected", "reconnecting"]
    assert listener.connection_state == "reconnecting"


@pytest.mark.asyncio
async def test_r3_supervisor_never_connected_without_handshake(repo: Repository) -> None:
    # A listener whose run never reports a handshake must leave the supervisor
    # "reconnecting" -- NOT "connected" (the bug pre-declared connected).
    observed: list[str] = []
    sup: WsSupervisor

    class _NeverHandshake:
        def __init__(self) -> None:
            self.on_connection_state = None
            self.terminal_state = None

        async def run(self) -> int:
            observed.append(sup.state)  # supervisor state while a task exists, pre-handshake
            sup.stop()
            return 0

    async def fake_sleep(delay: float) -> None:  # pragma: no cover - stop ends first
        pass

    sup = WsSupervisor(lambda: _NeverHandshake(), repo, backoff_base=0.0, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=2.0)
    assert observed == ["reconnecting"]  # never "connected"


@pytest.mark.asyncio
async def test_r3_supervisor_state_flips_connected_then_reconnecting(repo: Repository) -> None:
    # A handshake then a mid-run disconnect must move the supervisor connected ->
    # reconnecting, driven by the listener's callbacks.
    seen: list[str] = []
    sup: WsSupervisor

    class _Flaky:
        def __init__(self) -> None:
            self.on_connection_state = None
            self.terminal_state = None

        async def run(self) -> int:
            self.on_connection_state("connected")
            seen.append(sup.state)
            self.on_connection_state("reconnecting")
            seen.append(sup.state)
            sup.stop()
            return 0

    async def fake_sleep(delay: float) -> None:  # pragma: no cover - stop ends first
        pass

    sup = WsSupervisor(lambda: _Flaky(), repo, backoff_base=0.0, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=2.0)
    assert seen == ["connected", "reconnecting"]


# --------------------------------------------------------------------------- #
# R2: buffered-but-uncommitted events must survive a listener restart. A storage
# blip that kills the listener then recovers must not drop the pending batch --
# WS events have no stat/event recovery source.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_r2_pending_batch_survives_storage_blip_and_restart(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    N = 1000
    events = [
        Event.model_validate({"_id": f"e{i}", "key": "EVT_X", "time": 1_721_600_000_000 + i})
        for i in range(N)
    ]

    real = repo.record_events_enriching_entities
    storage = {"down": True}

    def flaky(rows: object) -> int:
        if storage["down"]:
            import sqlite3

            raise sqlite3.OperationalError("database is locked")
        return real(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", flaky)

    attempt = {"n": 0}

    def factory() -> EventListener:
        attempt["n"] += 1
        if attempt["n"] == 1:
            # First listener buffers all N, but every flush fails: it dies with
            # the whole batch uncommitted.
            return EventListener(FakeWs(events), repo, flush_interval=None, batch_size=100)
        # Later listeners have nothing new to add.
        return EventListener(FakeWs([]), repo, flush_interval=None)

    sup: WsSupervisor

    async def fake_sleep(delay: float) -> None:
        # Storage recovers during the backoff after the first death; stop once the
        # supervisor has had a restart to drain the rescued batch.
        storage["down"] = False
        if attempt["n"] >= 2:
            sup.stop()

    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=5, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=5.0)

    # Every buffered event is eventually persisted -- zero loss across the blip +
    # listener replacement (the bug persisted ZERO of the 1000).
    stored = repo.read_events(0, 2_000_000_000)
    assert len(stored) == N
    assert {r["native_id"] for r in stored} == {f"e{i}" for i in range(N)}


@pytest.mark.asyncio
async def test_r2_pending_survives_when_health_accounting_also_fails(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2 residual (P1): the terminal health-accounting write must never strand
    buffered events. The old code wrote ``_record(terminal)`` BEFORE rescuing the
    dead listener's pending batch, so when BOTH the event store AND the poll_runs
    accounting write raise OperationalError, the accounting raise propagated out
    of the loop and the rescue was skipped -> 0 rescued / N stranded. The prior
    1000-event test passed because it failed ONLY event writes, not accounting.
    """
    N = 7
    events = [
        Event.model_validate({"_id": f"s{i}", "key": "EVT_X", "time": 1_721_600_000_000 + i})
        for i in range(N)
    ]

    real_store = repo.record_events_enriching_entities
    real_poll = repo.record_poll_run
    storage = {"down": True}

    def flaky_store(rows: object) -> int:
        if storage["down"]:
            import sqlite3

            raise sqlite3.OperationalError("database is locked")
        return real_store(rows)  # type: ignore[arg-type]

    def flaky_poll(**kwargs: object) -> None:
        # Health accounting fails for the SAME outage that kills event writes.
        if storage["down"]:
            import sqlite3

            raise sqlite3.OperationalError("database is locked")
        return real_poll(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", flaky_store)
    monkeypatch.setattr(repo, "record_poll_run", flaky_poll)

    attempt = {"n": 0}

    def factory() -> EventListener:
        attempt["n"] += 1
        if attempt["n"] == 1:
            return EventListener(FakeWs(events), repo, flush_interval=None, batch_size=100)
        return EventListener(FakeWs([]), repo, flush_interval=None)

    sup: WsSupervisor

    async def fake_sleep(delay: float) -> None:
        # The outage clears during the backoff after the first death.
        storage["down"] = False
        if attempt["n"] >= 2:
            sup.stop()

    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=5, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=5.0)

    # 0 stranded: every buffered event is persisted despite the accounting write
    # failing in lockstep with the event store during the outage.
    stored = repo.read_events(0, 2_000_000_000)
    assert len(stored) == N
    assert {r["native_id"] for r in stored} == {f"s{i}" for i in range(N)}
