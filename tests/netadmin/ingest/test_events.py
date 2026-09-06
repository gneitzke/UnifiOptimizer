"""Event pipeline: normalization, batch persistence, catch-up, supervisor.

Fixture-driven and offline. Controller event rows come from the recorded
``stat_event.json`` (MACs already randomized at record time); no test touches a
real controller. The store is a fresh migrated SQLite file per test.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
import pytest
import respx

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
from netadmin.detect.context import DetectorContext, EVENT_COVERAGE_MIN
from netadmin.detect.detectors.client import FlakyClientDetector
from netadmin.detect.engine import UNKNOWN
from tests.netadmin.detect.support import FakeBaselines, seed_coverage

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


# --------------------------------------------------------------------------- #
# P1 (queue-OVERFLOW boundary): the event that trips the ``_max_pending`` guard
# must not be lost when the capacity-triggered flush raises. With N one past the
# bound, the old code consumed the boundary event, ran the at-capacity flush
# (which raised, storage down), and dropped that one event BEFORE it was ever
# appended -- so rescue recovered N-1, permanently losing the last event that has
# no stat/event recovery source. All N must persist across the blip + restart.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_p1_overflow_boundary_event_survives_storage_blip_and_restart(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1001 with batch_size=100 -> _max_pending == max(1000, 400) == 1000, so the
    # last event (e1000) is the one that trips the overflow guard while storage
    # is down. Before the fix this event alone was stranded.
    N = 1001
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
            # Every flush fails: the first listener overflows its bounded queue
            # while storage is down and dies with the WHOLE batch uncommitted --
            # including the boundary event that trips ``_max_pending``.
            return EventListener(FakeWs(events), repo, flush_interval=None, batch_size=100)
        return EventListener(FakeWs([]), repo, flush_interval=None)

    sup: WsSupervisor

    async def fake_sleep(delay: float) -> None:
        storage["down"] = False
        if attempt["n"] >= 2:
            sup.stop()

    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=5, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=5.0)

    # Zero loss: all 1001 persist, INCLUDING the overflow-boundary event e1000,
    # and no rescued record is left stranded on the supervisor.
    stored = repo.read_events(0, 2_000_000_000)
    assert len(stored) == N
    assert {r["native_id"] for r in stored} == {f"e{i}" for i in range(N)}
    assert "e1000" in {r["native_id"] for r in stored}
    assert sup._pending == []


# --------------------------------------------------------------------------- #
# P1 (normalize-READ blip): normalize() does entity-resolution DB READS, and a
# WS event is appended to the batch only AFTER a successful normalize. A transient
# storage read error during that lookup raised straight out of the consumer loop,
# dropping the just-consumed event on the floor -- it never reached the batch the
# supervisor rescues, and a WS event has NO stat/event recovery source. The event
# must instead be retained and, once the read recovers, normalized and persisted:
# zero silently lost. A genuinely malformed payload may still be dropped.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_p1_normalize_read_blip_retains_and_recovers_event(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    N = 5
    events = [
        Event.model_validate(
            {
                "_id": f"n{i}",
                "key": "EVT_WU_Connected",
                "time": 1_721_600_000_000 + i,
                "user": CLIENT_MAC,  # forces an entity-resolution lookup
            }
        )
        for i in range(N)
    ]

    # The FIRST entity lookup during normalization raises (a transient read blip);
    # every later lookup succeeds. Mirrors the verifier exactly.
    real_find = repo.find_entity
    calls = {"n": 0}

    def flaky_find(etype: EntityType, mac: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_find(etype, mac)

    monkeypatch.setattr(repo, "find_entity", flaky_find)

    listener = EventListener(FakeWs(events), repo, flush_interval=None, batch_size=100)
    # Must NOT raise out: the read blip is recoverable, not fatal.
    await listener.run()

    # Zero loss: the event whose first lookup blipped is retained, re-normalized
    # once the read recovers, and persisted with all the rest.
    stored = repo.read_events(0, 2_000_000_000)
    assert len(stored) == N
    assert {r["native_id"] for r in stored} == {f"n{i}" for i in range(N)}
    # Nothing stranded in the raw retry buffer.
    assert listener.pending_raw_records() == []


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


# --------------------------------------------------------------------------- #
# P1 (SUSTAINED total storage failure): the rescued pending buffer must not grow
# without bound. The old supervisor appended each dead listener's retained batch
# to ``_pending`` and started another listener with NO aggregate ceiling, so with
# storage continuously down and an unlimited producer the aggregate grew 1001,
# 2002, 3003, 4004, 5005, 6006, ... until the process OOM'd and lost EVERYTHING.
# A system-wide cap must bound memory, count the (bounded, observable) loss, and
# still flush every retained survivor once storage returns.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_p1_sustained_storage_failure_bounds_aggregate_pending(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = 2500
    per_listener = 1001  # each listener overflows its own _max_pending (1000)
    restarts_under_outage = 6

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
        n = attempt["n"]
        # Unique ids per restart so nothing dedupes across attempts and every
        # produced event is a distinct row we could, in principle, lose.
        events = [
            Event.model_validate(
                {
                    "_id": f"a{n}-e{i}",
                    "key": "EVT_X",
                    "time": 1_721_600_000_000 + n * 10_000 + i,
                }
            )
            for i in range(per_listener)
        ]
        # storage is down -> this listener overflows its bounded queue and dies
        # with the WHOLE batch uncommitted, which the supervisor rescues.
        return EventListener(FakeWs(events), repo, flush_interval=None, batch_size=100)

    sup: WsSupervisor
    sizes: list[int] = []

    async def fake_sleep(delay: float) -> None:
        # Snapshot the aggregate pending after each death's rescue+clamp.
        sizes.append(len(sup._pending))
        if attempt["n"] >= restarts_under_outage:
            storage["down"] = False  # outage clears; final drain can now persist
            sup.stop()

    sup = WsSupervisor(
        factory,
        repo,
        backoff_base=0.0,
        backoff_max=0.0,
        max_restarts=50,
        pending_max=cap,
        sleep=fake_sleep,
    )
    await asyncio.wait_for(sup.run(), timeout=10.0)

    # Aggregate pending stayed BOUNDED -- it never grew 1001, 2002, 3003, ...
    assert max(sizes) <= cap
    assert sizes != [per_listener * (i + 1) for i in range(len(sizes))]
    # The loss is counted and exposed (not silent). Total produced is conserved:
    # survivors persisted + dropped == everything the producer emitted.
    produced = per_listener * restarts_under_outage
    assert sup.dropped > 0
    # Nothing stranded: once storage recovered the final drain persisted the whole
    # bounded survivor set, and _pending is empty.
    assert sup._pending == []
    stored = repo.read_events(0, 2_000_000_000)
    assert len(stored) == cap  # exactly the bounded survivors persisted
    assert len(stored) + sup.dropped == produced


# --------------------------------------------------------------------------- #
# B4 (positive-liveness redesign): event-source coverage is credited only across
# spans carrying WS liveness HEARTBEATS, never through end_ts on a still-open
# 'connected' row. These tests exercise the heartbeat mechanism end to end.
# --------------------------------------------------------------------------- #
def _seed_beats(listener: EventListener, start: int, end: int, *, step: int = 60) -> None:
    """Drive the listener's own heartbeat writer across ``[start, end]``.

    Goes through ``_maybe_heartbeat`` (not the repo directly) so the test proves
    the listener-side gate: heartbeats land only while ``connection_state`` is
    ``connected``. Both endpoints are guaranteed a beat.
    """
    t = start
    while t < end:
        listener._maybe_heartbeat(now=t)
        listener._last_heartbeat_ts = None  # allow the next explicit beat
        t += step
    listener._maybe_heartbeat(now=end)


def test_maybe_heartbeat_only_writes_while_connected(repo: Repository) -> None:
    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    # Not connected -> no positive evidence, no beat.
    listener.connection_state = "reconnecting"
    listener._maybe_heartbeat(now=1_000)
    assert [r for r in repo.read_poll_runs("ws", 0, 10_000) if r["error"] == "heartbeat"] == []
    # Connected -> a beat is recorded.
    listener.connection_state = "connected"
    listener._maybe_heartbeat(now=1_001)
    beats = [r for r in repo.read_poll_runs("ws", 0, 10_000) if r["error"] == "heartbeat"]
    assert len(beats) == 1 and int(beats[0]["ok"]) == 1


def test_a_shutdown_stops_heartbeats_downtime_not_covered(repo: Repository) -> None:
    """B4(a): a NORMAL SHUTDOWN cancels the listener with NO 'disconnected' close
    row. The heartbeats simply stop, so coverage ends at the last beat and the
    post-shutdown downtime is NOT credited -- unlike the old code, which ran a
    dangling 'connected' interval through end_ts (a false 100%)."""
    now = 8_000_000
    start = now - 3600
    shutdown = start + 300  # feed shut down 300 s into the hour
    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    listener.connection_state = "connected"
    _seed_beats(listener, start, shutdown, step=60)
    # Shutdown: the flusher is cancelled, beats stop. Deliberately write NO
    # 'disconnected'/close row -- correctness must not depend on one.
    cov = repo.observed_event_coverage(start, now)
    assert cov == pytest.approx(300 / 3600, abs=0.02)  # only the observed span
    assert cov < EVENT_COVERAGE_MIN  # -> the detector FREEZES, not clears


def test_a_flaky_not_falsely_cleared_after_shutdown(repo: Repository) -> None:
    """B4(a) end to end: client polling is healthy and the disconnect events have
    aged out, but the WS feed SHUT DOWN partway through the window (heartbeats
    stopped, no close row). A restart must not read the downtime as 'observed' and
    false-clear a real client.flaky issue -- the detector must FREEZE (UNKNOWN)."""
    now = 8_500_000
    start = now - 3600
    seed_coverage(repo, job="fast_sta", now=now, window_s=3600, interval_s=60)
    ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-flaky", site_id="default"), ts=now
    )
    repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT, native_id="cc:flaky", site_id="default",
            parent_id=ap, first_seen_ts=now - 100_000,
        ),
        ts=now,
    )
    # Feed observed only the first 300 s, then shut down. No disconnect events
    # remain (aged out). Event coverage is far below the floor.
    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    listener.connection_state = "connected"
    _seed_beats(listener, start, start + 300, step=60)
    ctx = DetectorContext(
        repo=repo, baselines=FakeBaselines(), now_ts=now, site_id="default", settings=None
    )
    assert FlakyClientDetector().evaluate(ctx) is UNKNOWN


def _trailing_failures(repo: Repository, job: str) -> int:
    """Mirror runtime._job_health: trailing consecutive non-ok poll_runs for a job."""
    rows = repo.read_poll_runs(job, 0, 2_000_000_000)
    n = 0
    for r in reversed(rows):
        if int(r["ok"]) == 1:
            break
        n += 1
    return n


def test_b_failed_then_recovered_flush_reopens_coverage_and_health(repo: Repository) -> None:
    """B4 new-bug: a failed periodic flush closes coverage/health (an ok=0
    storage-failed ws row), but a successful retry never reopened it under the old
    close-event design -- a connected socket with a committed event and empty
    queue read 0 coverage and 'failing' health. With positive liveness, the
    recovered flush resumes heartbeats: coverage reopens and the trailing failure
    clears."""
    now = 7_000_000
    start = now - 3600
    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    listener.connection_state = "connected"
    # Healthy, connected-and-draining span up to shortly before the stall.
    _seed_beats(listener, start, start + 3480, step=60)
    # A flush tick fails: storage-failed (ok=0) surfaces, NO heartbeat this tick.
    repo.record_poll_run(job="ws", ok=False, ts=start + 3500, error="storage-failed: locked", source="live")
    # While the failure is the latest ws row, health is 'failing'.
    assert _trailing_failures(repo, "ws") > 0
    # Flushing RECOVERS: the connected socket commits + empties its queue, so the
    # next ticks heartbeat again (the gap start+3480 -> start+3540 is under the
    # bridge bound, so coverage is continuous).
    listener._maybe_heartbeat(now=start + 3540)
    listener._last_heartbeat_ts = None
    listener._maybe_heartbeat(now=now - 1)
    # Coverage reopened -- effectively full across the window (not 0, not frozen).
    cov = repo.observed_event_coverage(start, now)
    assert cov >= EVENT_COVERAGE_MIN
    # Health cleared: a fresh successful ws heartbeat is now the latest row.
    assert _trailing_failures(repo, "ws") == 0


@pytest.mark.asyncio
async def test_c_periodic_flush_heartbeats_while_connected_and_covers(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B4(c): a genuinely healthy, connected, draining feed reads fully covered.
    Drive the REAL periodic-flush loop; while connected it must emit a steady
    stream of liveness heartbeats (empty queue included), yielding continuous
    coverage across their span."""
    import netadmin.ingest.events as evmod

    calls = {"n": 0}

    def fake_time() -> float:
        # Spread successive heartbeats 10 s apart (distinct, chainable) so the
        # loop's real sub-ms sleeps do not collapse them onto one second.
        calls["n"] += 1
        return 6_000_000 + calls["n"] * 10

    monkeypatch.setattr(evmod.time, "time", fake_time)

    listener = EventListener(FakeWs([]), repo, flush_interval=0.001, heartbeat_interval=1.0)
    listener.connection_state = "connected"
    task = asyncio.create_task(listener._periodic_flush())

    async def until_three_beats() -> None:
        while len([r for r in repo.read_poll_runs("ws", 0, 10_000_000) if r["error"] == "heartbeat"]) < 3:
            await asyncio.sleep(0.001)

    try:
        await asyncio.wait_for(until_three_beats(), timeout=3.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    beats = [r for r in repo.read_poll_runs("ws", 0, 10_000_000) if r["error"] == "heartbeat"]
    assert len(beats) >= 3
    lo, hi = int(beats[0]["ts"]), int(beats[-1]["ts"])
    # The span between the first and last beat is continuously covered.
    assert repo.observed_event_coverage(lo, hi + 1) > 0.9


def test_d_real_gap_still_freezes(repo: Repository) -> None:
    """B4(d): a real gap (feed down / not draining) leaves a coverage hole ->
    UNKNOWN. Two short heartbeat bursts with a long dead middle do not chain, so
    coverage stays far below the floor."""
    now = 9_000_000
    start = now - 3600
    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    listener.connection_state = "connected"
    _seed_beats(listener, start, start + 240, step=60)   # early burst
    # ... feed dead for most of the hour (no beats) ...
    _seed_beats(listener, now - 240, now - 1, step=60)   # late burst
    cov = repo.observed_event_coverage(start, now)
    assert cov < EVENT_COVERAGE_MIN
    assert cov < 0.2


# --------------------------------------------------------------------------- #
# D2 (normalization-read outage earns no false coverage): a WS event consumed off
# the socket whose entity-resolution READ is still failing sits RETAINED RAW,
# unprocessed. ``_drain_pending_raw`` swallows the read error and ``_flush``
# returns an empty batch, so the periodic loop used to emit a HEARTBEAT anyway --
# crediting observed coverage across a span of UNPROCESSED history. An event-based
# detector then reads that span as observed and can false-clear a live issue (the
# B4 harm). A heartbeat (positive liveness = connected AND draining successfully)
# must NOT fire while a raw event is stuck; coverage ends at the last truly-drained
# beat and resumes once the read recovers and the raw buffer empties.
# --------------------------------------------------------------------------- #
def test_d2_no_heartbeat_while_raw_event_pending_renormalization(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    real_find = repo.find_entity

    def locked_find(etype: EntityType, mac: str) -> Any:
        raise sqlite3.OperationalError("database is locked")

    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    listener.connection_state = "connected"

    # One consumed-but-unnormalized event is retained RAW because its entity read
    # blipped (exactly as the consumer loop does on a transient read error).
    ev = Event.model_validate(
        {"_id": "d2", "key": "EVT_WU_Connected", "time": 1_721_600_000_000, "user": CLIENT_MAC}
    )
    listener._pending_raw.append(ev)

    # The read stays down across the whole [100, 160] span. Each tick flushes
    # (raw stays stuck) then tries to beat.
    monkeypatch.setattr(repo, "find_entity", locked_find)
    for t in (100, 130, 160):
        listener._flush()  # drains raw -> still stuck, keeps _storage_error set
        listener._maybe_heartbeat(now=t)
        listener._last_heartbeat_ts = None  # remove rate-limit as the only guard

    beats = [r for r in repo.read_poll_runs("ws", 0, 10_000) if r["error"] == "heartbeat"]
    assert beats == []  # NO positive liveness while a raw event is unprocessed
    # The unprocessed span is NOT credited as observed -> detectors freeze there.
    assert repo.observed_event_coverage(100, 161) == 0.0
    assert listener._pending_raw  # still stuck

    # Reads recover: the raw event re-normalizes, the buffer empties, and the very
    # next tick resumes heartbeats.
    monkeypatch.setattr(repo, "find_entity", real_find)
    listener._flush()
    assert listener._pending_raw == []
    assert listener._storage_error is None
    listener._maybe_heartbeat(now=200)
    beats = [r for r in repo.read_poll_runs("ws", 0, 10_000) if r["error"] == "heartbeat"]
    assert len(beats) == 1 and int(beats[0]["ts"]) == 200
    # The retained event itself was persisted once the read came back.
    assert {r["native_id"] for r in repo.read_events(0, 2_000_000_000)} == {"d2"}


# --------------------------------------------------------------------------- #
# D5 (rescued events must not strand). (1) When storage recovers DURING a
# replacement listener's life, the events rescued from a prior dead listener must
# be persisted PROMPTLY -- not left queued until this new listener itself dies. The
# supervisor now drains rescued buffers whenever a listener proves storage healthy
# (a heartbeat), mid-life. The repro left persisted IDs == ['new'] with the rescued
# event still pending after recovery.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_d5_rescued_events_drain_during_replacement_listener_life(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    rescued_ev = Event.model_validate(
        {"_id": "rescued", "key": "EVT_X", "time": 1_721_600_000_000}
    )
    new_ev = Event.model_validate({"_id": "new", "key": "EVT_X", "time": 1_721_600_000_100})

    real_store = repo.record_events_enriching_entities
    storage = {"down": True}

    def flaky(rows: object) -> int:
        if storage["down"]:
            raise sqlite3.OperationalError("database is locked")
        return real_store(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", flaky)

    sup: WsSupervisor
    snap: dict[str, Any] = {}

    class _MidlifeRecovers:
        """Replacement listener: storage recovers during its life. It persists its
        OWN new event and, via the supervisor's healthy-drain hook, must flush the
        rescued events too -- all BEFORE it returns/dies."""

        def __init__(self) -> None:
            self.on_connection_state: Optional[Any] = None
            self.on_healthy_drain: Optional[Any] = None
            self.terminal_state: Optional[str] = None

        def pending_records(self) -> list[dict[str, Any]]:
            return []

        def pending_raw_records(self) -> list[Event]:
            return []

        async def run(self) -> int:
            if self.on_connection_state is not None:
                self.on_connection_state("connected")
            # Storage has recovered mid-life: this listener commits its own event ...
            storage["down"] = False
            record = EventNormalizer(repo).normalize(new_ev)
            assert record is not None
            repo.record_events_enriching_entities([record])
            # ... and signals a healthy drain (as a heartbeat would). The supervisor
            # must hand off the rescued events NOW.
            if self.on_healthy_drain is not None:
                self.on_healthy_drain()
            # Snapshot mid-life -- before this listener returns/dies.
            snap["pending"] = list(sup._pending)
            snap["stored"] = {r["native_id"] for r in repo.read_events(0, 2_000_000_000)}
            sup.stop()
            return 0

    attempt = {"n": 0}

    def factory() -> Any:
        attempt["n"] += 1
        if attempt["n"] == 1:
            # Buffers `rescued`, every flush fails -> dies with it uncommitted.
            return EventListener(FakeWs([rescued_ev]), repo, flush_interval=None)
        return _MidlifeRecovers()

    async def fake_sleep(delay: float) -> None:
        # Storage is STILL down at the start-of-loop drain for attempt 2, so the
        # rescued event is only drainable from inside the replacement's life.
        pass

    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=5, sleep=fake_sleep)
    await asyncio.wait_for(sup.run(), timeout=5.0)

    # Rescued event was persisted DURING the replacement's life (mid-life snapshot),
    # alongside the listener's own new event -- not left pending.
    assert snap["pending"] == []
    assert snap["stored"] == {"new", "rescued"}


# --------------------------------------------------------------------------- #
# D5 (2): cancellation/shutdown must drain rescued events, not strand them. The
# cancel path rescued the dying listener's batch then raised immediately, bypassing
# the post-loop drain -- so buffered events sat stranded through shutdown even when
# storage was healthy. A FINAL drain in a finally BEFORE propagating the cancel
# persists both the previously-rescued events and the dying listener's batch.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_d5_cancellation_drains_rescued_events(repo: Repository) -> None:
    normalizer = EventNormalizer(repo)
    rescued = normalizer.normalize(
        Event.model_validate({"_id": "rescued", "key": "EVT_X", "time": 1_721_600_000_000})
    )
    dying = normalizer.normalize(
        Event.model_validate({"_id": "dying", "key": "EVT_X", "time": 1_721_600_000_100})
    )
    assert rescued is not None and dying is not None

    sup: WsSupervisor
    started = asyncio.Event()

    class _Blocks:
        """A listener that connects, holds a buffered event, then blocks until the
        supervisor is cancelled (shutdown)."""

        def __init__(self) -> None:
            self.on_connection_state: Optional[Any] = None
            self.on_healthy_drain: Optional[Any] = None
            self.terminal_state: Optional[str] = None

        def pending_records(self) -> list[dict[str, Any]]:
            return [dying]

        def pending_raw_records(self) -> list[Event]:
            return []

        async def run(self) -> int:
            if self.on_connection_state is not None:
                self.on_connection_state("connected")
            started.set()
            await asyncio.Event().wait()  # block until cancelled
            return 0  # pragma: no cover

    sup = WsSupervisor(lambda: _Blocks(), repo, backoff_base=0.0)
    # An event rescued from an EARLIER listener is already queued on the supervisor.
    sup._pending = [rescued]

    task = asyncio.create_task(sup.run())
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Shutdown drained BOTH the previously-rescued event and the dying listener's
    # batch instead of stranding them.
    assert {r["native_id"] for r in repo.read_events(0, 2_000_000_000)} == {"rescued", "dying"}
    assert sup._pending == []


# --------------------------------------------------------------------------- #
# BUG#4 (heartbeat must not credit coverage over the SUPERVISOR's undrained
# buffers): a replacement listener with EMPTY local buffers used to emit
# heartbeats while the supervisor still held a rescued RAW event stuck behind a
# failing entity-resolution read -- the heartbeat was committed BEFORE the drain
# hook ran and the hook's failed reads were swallowed, so coverage was credited
# over a span of unprocessed history (the repro: 11 beats, ~0.997 coverage). A
# heartbeat asserts the whole pipeline is drained: it must be suppressed while ANY
# rescued event (normalized OR raw) is stuck upstream, not just in this listener's
# local buffer.
# --------------------------------------------------------------------------- #
def test_bug4_no_heartbeat_while_supervisor_holds_stuck_rescued_event(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    real_find = repo.find_entity

    def locked_find(etype: EntityType, mac: str) -> Any:
        raise sqlite3.OperationalError("database is locked")

    # A rescued RAW event lives on the SUPERVISOR; its entity read blips, so it
    # cannot be re-normalized/persisted while storage is down.
    stuck = Event.model_validate(
        {"_id": "stuck", "key": "EVT_WU_Connected", "time": 1_721_600_000_000, "user": CLIENT_MAC}
    )
    sup = WsSupervisor(lambda: EventListener(FakeWs([]), repo), repo, backoff_base=0.0)
    sup._pending_raw = [stuck]

    # A healthy, EMPTY replacement listener wired to the supervisor's drain +
    # pipeline-blocked hooks exactly as WsSupervisor.run() wires them.
    listener = EventListener(FakeWs([]), repo, flush_interval=None, heartbeat_interval=0.0)
    listener.connection_state = "connected"
    listener.on_healthy_drain = sup._drain_pending
    listener.pipeline_blocked = sup._pending_blocked

    monkeypatch.setattr(repo, "find_entity", locked_find)
    for t in (100, 130, 160, 190):
        listener._maybe_heartbeat(now=t)
        listener._last_heartbeat_ts = None  # rate-limit is not the guard under test

    beats = [r for r in repo.read_poll_runs("ws", 0, 10_000) if r["error"] == "heartbeat"]
    assert beats == []  # NO positive liveness while a rescued event is stuck
    assert repo.observed_event_coverage(100, 191) == 0.0
    assert sup._pending_raw  # the rescued raw event is still blocked upstream

    # Read recovers: the drain hook re-normalizes and persists the rescued event,
    # the pipeline is truly drained, and the very next beat is allowed.
    monkeypatch.setattr(repo, "find_entity", real_find)
    listener._maybe_heartbeat(now=300)
    beats = [r for r in repo.read_poll_runs("ws", 0, 10_000) if r["error"] == "heartbeat"]
    assert len(beats) == 1 and int(beats[0]["ts"]) == 300
    assert sup._pending_raw == [] and sup._pending == []
    assert {r["native_id"] for r in repo.read_events(0, 2_000_000_000)} == {"stuck"}


# --------------------------------------------------------------------------- #
# BUG#5 (shutdown DURING backoff loses the final drain): D5 fixed cancellation
# during ``listener.run()``, but a cancel landing during the between-listeners
# backoff ``sleep`` lands OUTSIDE that inline handler and used to unwind straight
# past the post-loop drain -- stranding a rescued event even though storage had
# recovered by shutdown. The whole supervise loop is now wrapped so a cancel
# anywhere triggers one best-effort final drain. Production SupervisorTask.stop()
# uses exactly this cancellation path.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_bug5_shutdown_during_backoff_drains_rescued_event(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    real = repo.record_events_enriching_entities
    storage = {"down": True}

    def flaky(rows: object) -> int:
        if storage["down"]:
            raise sqlite3.OperationalError("database is locked")
        return real(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", flaky)

    ev = Event.model_validate({"_id": "blip", "key": "EVT_X", "time": 1_721_600_000_000})

    attempt = {"n": 0}

    def factory() -> EventListener:
        attempt["n"] += 1
        # Attempt 1 buffers the event and dies with it uncommitted (storage down),
        # so the supervisor rescues it onto ``_pending``. It never gets to attempt 2.
        events = [ev] if attempt["n"] == 1 else []
        return EventListener(FakeWs(events), repo, flush_interval=None, batch_size=1)

    in_backoff = asyncio.Event()

    async def fake_sleep(delay: float) -> None:
        # Storage RECOVERS while the supervisor sits in the between-listeners
        # backoff; then we block here so the shutdown cancel lands MID-BACKOFF,
        # outside the inline run()-cancel handler.
        storage["down"] = False
        in_backoff.set()
        await asyncio.Event().wait()

    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=5, sleep=fake_sleep)
    task = asyncio.create_task(sup.run())
    await asyncio.wait_for(in_backoff.wait(), timeout=2.0)

    # Cancel mid-backoff (the production SupervisorTask.stop() path).
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The outer finally ran one final drain despite the cancel landing in the
    # backoff sleep: the rescued event is persisted, not stranded (repro: 0 stored).
    assert {r["native_id"] for r in repo.read_events(0, 2_000_000_000)} == {"blip"}
    assert sup._pending == []


# --------------------------------------------------------------------------- #
# BUG#6 (mixed rescue buffers violate the aggregate cap and evict NEWER events):
# ``_enforce_pending_bound`` capped the raw and normalized queues INDEPENDENTLY,
# so cap=3 with 3 old raw + 3 new normalized retained SIX; and recovery appends
# the old (re-normalized) raw events BEHIND the newer normalized ones, then a
# blind prefix delete discarded the NEWER events (4-6) while the old (1-3)
# survived. The cap must bound the AGGREGATE of both buffers, and drop-oldest must
# evict the genuinely oldest across BOTH buffers by event time.
# --------------------------------------------------------------------------- #
def test_bug6_aggregate_cap_evicts_true_oldest_across_both_buffers(
    repo: Repository,
) -> None:
    normalizer = EventNormalizer(repo)

    def norm(_id: str, ts_ms: int) -> dict[str, Any]:
        rec = normalizer.normalize(
            Event.model_validate({"_id": _id, "key": "EVT_X", "time": ts_ms})
        )
        assert rec is not None
        return rec

    # 3 OLD raw events (times 1-3 s) + 3 NEW normalized events (times 4-6 s).
    old_raw = [
        Event.model_validate({"_id": f"old{i}", "key": "EVT_X", "time": 1_721_600_000_000 + i * 1000})
        for i in (1, 2, 3)
    ]
    new_norm = [norm(f"new{i}", 1_721_600_000_000 + i * 1000) for i in (4, 5, 6)]

    sup = WsSupervisor(lambda: EventListener(FakeWs([]), repo), repo, pending_max=3)
    sup._pending = list(new_norm)
    sup._pending_raw = list(old_raw)

    # AGGREGATE cap: 6 retained across the two buffers must clamp to 3 -- the old
    # independent-cap logic left all SIX (3 + 3, each within its own cap of 3).
    sup._enforce_pending_bound()
    total = len(sup._pending) + len(sup._pending_raw)
    assert total == 3
    assert sup.dropped == 3
    # TRUE-OLDEST eviction: the three OLD raw events go; the three NEWER normalized
    # survive (not the reverse a blind prefix delete would produce).
    assert sup._pending_raw == []
    assert {r["native_id"] for r in sup._pending} == {"new4", "new5", "new6"}


def test_bug6_recovery_keeps_newer_events_not_prefix(
    repo: Repository,
) -> None:
    """The prompt's exact repro: recovery re-normalizes rescued RAW events and
    appends them BEHIND newer normalized ones; a prefix delete then discarded the
    newer survivors. With the aggregate/true-oldest fix the drain persists the
    NEWER events and drops the genuinely-oldest raw ones."""
    normalizer = EventNormalizer(repo)

    def norm(_id: str, ts_ms: int) -> dict[str, Any]:
        rec = normalizer.normalize(
            Event.model_validate({"_id": _id, "key": "EVT_X", "time": ts_ms})
        )
        assert rec is not None
        return rec

    old_raw = [
        Event.model_validate({"_id": f"old{i}", "key": "EVT_X", "time": 1_721_600_000_000 + i * 1000})
        for i in (1, 2, 3)
    ]
    new_norm = [norm(f"new{i}", 1_721_600_000_000 + i * 1000) for i in (4, 5, 6)]

    sup = WsSupervisor(lambda: EventListener(FakeWs([]), repo), repo, pending_max=3)
    # Newer normalized already queued; older raw arrives to be re-normalized behind
    # them on the healthy drain.
    sup._pending = list(new_norm)
    sup._pending_raw = list(old_raw)

    # Storage is healthy: the drain re-normalizes the raw events (appending them
    # behind the newer ones), clamps to the aggregate cap, and persists survivors.
    sup._drain_pending()

    stored = {r["native_id"] for r in repo.read_events(0, 2_000_000_000)}
    # The NEWER events survived and were persisted; the genuinely-oldest raw ones
    # were the ones dropped (repro persisted {old1, old2, old3} instead).
    assert stored == {"new4", "new5", "new6"}
    assert sup.dropped == 3
    assert sup._pending == [] and sup._pending_raw == []


# --------------------------------------------------------------------------- #
# BUG#5 (a storage failure during CANCELLATION masks CancelledError): cancel the
# real supervisor while its real listener holds one buffered event; the listener's
# teardown ``_flush()`` raises OperationalError, which -- from a ``finally`` --
# silently REPLACED the in-flight CancelledError. The supervisor then saw an
# ordinary listener death: with max_restarts=0 it returned normally
# (task.cancelled()==False) or, with retries, could RESTART -- and stop() was lost.
# Fix: a teardown flush failure during cancellation must never swallow/replace the
# CancelledError; the batch is retained (rescued), the final drain still runs, and
# the cancel ALWAYS propagates.
# --------------------------------------------------------------------------- #
class _YieldOneThenBlockWs:
    """WS double: yields ONE event, then blocks forever so the listener holds the
    event buffered (un-flushed) until the supervise task is cancelled."""

    def __init__(self, event: Event) -> None:
        self._event = event
        self.on_state: Optional[Any] = None
        self._stop = SimpleNamespaceStop()

    async def events(self) -> AsyncIterator[Event]:
        if self.on_state is not None:
            self.on_state("connected")
        yield self._event
        await asyncio.Event().wait()  # block until cancelled
        yield self._event  # pragma: no cover - never reached

    def stop(self) -> None:  # pragma: no cover - parity
        self._stop.set()


@pytest.mark.asyncio
async def test_bug5_cancel_during_teardown_flush_failure_still_propagates(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = repo.record_events_enriching_entities
    calls = {"n": 0}

    def flaky(rows: object) -> int:
        calls["n"] += 1
        # The listener's TEARDOWN flush (call #1, during cancellation) fails -- the
        # exact storage blip that used to mask the cancel. The supervisor's final
        # drain (call #2) then succeeds, proving the drain still ran.
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", flaky)

    buffered = Event.model_validate(
        {"_id": "buffered", "key": "EVT_X", "time": 1_721_600_000_000}
    )

    factory_calls = {"n": 0}

    def factory() -> EventListener:
        factory_calls["n"] += 1
        # batch_size high + no periodic flusher => the event sits un-flushed in the
        # batch until the teardown flush on cancellation.
        return EventListener(
            _YieldOneThenBlockWs(buffered), repo, flush_interval=None, batch_size=50
        )

    # max_restarts=0: were the cancel masked as an ordinary death, the loop would
    # return normally (task.cancelled()==False) rather than propagate the cancel.
    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=0)
    task = asyncio.create_task(sup.run())

    # Let the listener connect, consume+buffer the event, and reach the block.
    for _ in range(50):
        await asyncio.sleep(0)
        if sup.state == "connected":
            break
    assert sup.state == "connected"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation PROPAGATED (not a spurious normal return) ...
    assert task.cancelled()
    # ... exactly one listener was built (no restart) ...
    assert factory_calls["n"] == 1
    # ... the teardown flush DID fail (the masking scenario was exercised) ...
    assert calls["n"] >= 2
    # ... and the final drain still ran: the buffered event was rescued and
    # persisted, not stranded.
    assert {r["native_id"] for r in repo.read_events(0, 2_000_000_000)} == {"buffered"}
    assert sup._pending == []


# --------------------------------------------------------------------------- #
# FINDING#5 (round-12: the cancelling()-count check is DEFEATED by an
# already-cancelled awaited future). The round-11 fix above detected the
# "cancelling" state from ``current_task().cancelling() > 0``. But a
# CancelledError raised by awaiting an ALREADY-CANCELLED future carries
# cancelling()==0 -- the task itself was never ``.cancel()``ed, the cancel is
# merely flowing through it. With a teardown flush that also raises
# OperationalError, the old count-based check saw cancelling()==0, let the
# OperationalError REPLACE the CancelledError, and the supervisor returned
# normally (task.cancelled()==False) -- the cancel was swallowed. Fix (root
# cause): the listener catches ``asyncio.CancelledError`` in its OWN except
# clause (never consulting the cancelling() count), swallows a teardown storage
# error, and re-raises so the cancel ALWAYS propagates regardless of how it
# arose. The final drain still runs and the buffered event is rescued.
# --------------------------------------------------------------------------- #
class _YieldOneThenAwaitCancelledFutureWs:
    """WS double: yields ONE event, then awaits an ALREADY-CANCELLED future so the
    next drain step raises ``CancelledError`` with the supervise task's
    ``cancelling()`` count still 0 -- the exact FINDING#5 case that a count-based
    check misses. No external ``task.cancel()`` is used."""

    def __init__(self, event: Event) -> None:
        self._event = event
        self.on_state: Optional[Any] = None
        self._stop = SimpleNamespaceStop()

    async def events(self) -> AsyncIterator[Event]:
        if self.on_state is not None:
            self.on_state("connected")
        yield self._event
        # Await an already-cancelled future: raises CancelledError immediately,
        # WITHOUT the task ever being .cancel()ed -> current_task().cancelling()==0.
        fut: "asyncio.Future[None]" = asyncio.get_event_loop().create_future()
        fut.cancel()
        await fut
        yield self._event  # pragma: no cover - never reached

    def stop(self) -> None:  # pragma: no cover - parity
        self._stop.set()


@pytest.mark.asyncio
async def test_finding5_already_cancelled_future_with_teardown_failure_propagates(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = repo.record_events_enriching_entities
    calls = {"n": 0}

    def flaky(rows: object) -> int:
        calls["n"] += 1
        # Teardown flush (call #1, during the propagating cancel) fails -- the blip
        # that used to mask the cancel. The supervisor's final drain (call #2)
        # succeeds, proving the drain still ran.
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(repo, "record_events_enriching_entities", flaky)

    buffered = Event.model_validate(
        {"_id": "buffered", "key": "EVT_X", "time": 1_721_600_000_000}
    )

    factory_calls = {"n": 0}

    def factory() -> EventListener:
        factory_calls["n"] += 1
        return EventListener(
            _YieldOneThenAwaitCancelledFutureWs(buffered),
            repo,
            flush_interval=None,
            batch_size=50,
        )

    # max_restarts=0: were the cancel masked as an ordinary death, the loop would
    # return normally (task.cancelled()==False) rather than propagate the cancel.
    sup = WsSupervisor(factory, repo, backoff_base=0.0, max_restarts=0)
    task = asyncio.create_task(sup.run())

    # The cancel arises from INSIDE the drain loop (the already-cancelled future),
    # not an external task.cancel(); it must still propagate out of the task.
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation PROPAGATED even though cancelling()==0 (no external .cancel())...
    assert task.cancelled()
    # ... exactly one listener was built (no restart) ...
    assert factory_calls["n"] == 1
    # ... the teardown flush DID fail (the masking scenario was exercised) ...
    assert calls["n"] >= 2
    # ... and the final drain still ran: the buffered event was rescued and
    # persisted, not stranded.
    assert {r["native_id"] for r in repo.read_events(0, 2_000_000_000)} == {"buffered"}
    assert sup._pending == []


# --------------------------------------------------------------------------- #
# BUG#6 (an unrecognized GET response fabricates event coverage): through the real
# UnifiClient -> Endpoints -> catchup_events, an HTTP 200 body with no well-formed
# success payload (e.g. ``{"error": "upstream unavailable"}`` -- no "data" key)
# became ZERO events and 1.0 'complete' coverage for the window: the read helper
# defaulted a missing "data" to [] and catch-up booked the window complete. That
# fabricates event-source coverage and defeats every detector coverage gate. Fix:
# only a well-formed success response (data present, or meta.rc=ok) counts as a
# real read; anything else is a FAILED read, and catch-up records the window
# failed (never counted by observed_event_coverage), not complete. A genuinely
# empty ``{"data": []}`` still records complete.
# --------------------------------------------------------------------------- #
_BUG6_HOST = "https://ctrl6.test"
_BUG6_SITE = "default"
_BUG6_API = f"{_BUG6_HOST}/proxy/network/api/s/{_BUG6_SITE}"


def _bug6_mock_login() -> None:
    respx.get(f"{_BUG6_HOST}/proxy/network/").mock(return_value=httpx.Response(401))
    respx.post(f"{_BUG6_HOST}/api/auth/login").mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )


async def _bug6_endpoints() -> tuple[Any, Any]:
    from netadmin.ingest.unifi.client import UnifiClient
    from netadmin.ingest.unifi.endpoints import Endpoints

    client = UnifiClient(
        host=_BUG6_HOST, site=_BUG6_SITE, username="u", password="p",
        min_request_interval=0.0,
    )
    await client.connect()
    return client, Endpoints(client)


@pytest.mark.asyncio
@respx.mock
async def test_bug6_unrecognized_event_response_records_failed_not_complete(
    repo: Repository,
) -> None:
    from netadmin.ingest.unifi.auth import UnifiError

    _bug6_mock_login()
    # HTTP 200 but NOT a success envelope: an error body with no "data" key.
    respx.get(f"{_BUG6_API}/stat/event").mock(
        return_value=httpx.Response(200, json={"error": "upstream unavailable"})
    )
    client, ep = await _bug6_endpoints()
    now = 1_721_700_000

    # The read is a FAILURE, not a successful empty collection: it must surface so
    # the caller's poll firewall marks the cycle failed.
    with pytest.raises(UnifiError):
        await catchup_events(repo, ep, now=now)
    await client.aclose()

    # No 'complete' coverage was fabricated: the window reads 0.0 (a gap ->
    # detectors freeze to UNKNOWN), and a queryable FAILED hole was recorded.
    assert repo.observed_event_coverage(now - 3600, now) == 0.0
    assert repo.observed_event_coverage(now - 30 * 24 * 3600, now) == 0.0
    failed = repo.failed_ingest_coverage(kind="event_history", scope="site")
    assert failed and any(int(r["end_ts"]) == now for r in failed)


@pytest.mark.asyncio
@respx.mock
async def test_bug6_wellformed_empty_event_response_still_records_complete(
    repo: Repository,
) -> None:
    _bug6_mock_login()
    # A genuinely successful, authoritative EMPTY read still records complete.
    respx.get(f"{_BUG6_API}/stat/event").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    )
    client, ep = await _bug6_endpoints()
    now = 1_721_700_000

    inserted = await catchup_events(repo, ep, now=now)
    await client.aclose()

    assert inserted == 0
    # A well-formed empty read is a real observation: the window is credited.
    assert repo.observed_event_coverage(now - 3600, now) == 1.0
    assert repo.failed_ingest_coverage(kind="event_history", scope="site") == []
