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
    await EventListener(FakeWs(events), repo, flush_interval=None).run()
    # newest stored ts == 1_721_600_180; pretend "now" is 2 h later.
    now = 1_721_600_180 + 2 * 3600
    ep = RecordingEndpoints(events)
    await catchup_events(repo, ep, now=now)
    # gap_hours(2) + 1 + margin(1) = 4: a narrow window, not the full backlog.
    assert ep.within_hours_seen == [4]


@pytest.mark.asyncio
async def test_catchup_unbounded_only_when_no_cursor(repo: Repository) -> None:
    # Empty store: no cursor yet, so the first sweep is unbounded (within=None),
    # then self-bounds once any event is stored.
    ep = RecordingEndpoints([])
    await catchup_events(repo, ep)
    assert ep.within_hours_seen == [None]
