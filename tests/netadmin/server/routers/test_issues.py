"""ASGI-transport tests for the read-only issues router."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_list_issues_returns_all(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    fps = {i["fingerprint"] for i in body["issues"]}
    assert fps == {"fp-active", "fp-pending"}
    # evidence is decoded to an object, not left a JSON string
    active = next(i for i in body["issues"] if i["fingerprint"] == "fp-active")
    assert active["evidence"] == {"rx_errors_per_min": 42}


async def test_list_issues_filter_by_state(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues", params={"state": "active"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["issues"][0]["fingerprint"] == "fp-active"


async def test_list_issues_filter_by_severity(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues", params={"severity": "p3"})
    body = resp.json()
    assert body["count"] == 1
    assert body["issues"][0]["fingerprint"] == "fp-pending"


async def test_list_issues_invalid_state_is_422(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues", params={"state": "bogus"})
    assert resp.status_code == 422


async def test_issue_detail_includes_event_trail(app: object) -> None:
    async with await _client(app) as c:
        listing = (await c.get("/api/issues", params={"state": "active"})).json()
        issue_id = listing["issues"][0]["id"]
        resp = await c.get(f"/api/issues/{issue_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issue"]["fingerprint"] == "fp-active"
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["detected", "escalated"]
    # detail JSON decoded to objects and ordered by ts
    assert body["events"][0]["detail"] == {"m": 1}
    assert body["events"][1]["detail"] == {"m": 3}


async def test_issue_detail_unknown_is_404(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/issues/999999")
    assert resp.status_code == 404


async def test_issues_503_when_store_absent(settings: object) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/issues")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# The lifecycle block: clear threshold + recurrence (Gitea #39)
# ---------------------------------------------------------------------------


def _reset_events(store: object, issue_id: int, n: int, *, now: int) -> None:
    """``n`` clear-streak resets on this issue, inside the 7-day window."""
    for i in range(n):
        store.record_issue_event(
            issue_id,
            "escalated",
            ts=now - 3_600 * (i + 1),
            detail={"reason": "refire_during_resolving"},
        )


async def _issues_by_fingerprint(app: object) -> dict:
    async with await _client(app) as c:
        body = (await c.get("/api/issues")).json()
    return {i["fingerprint"]: i for i in body["issues"]}


async def test_list_carries_lifecycle_with_the_engine_clear_threshold(app: object) -> None:
    issues = await _issues_by_fingerprint(app)
    lifecycle = issues["fp-active"]["lifecycle"]
    # K comes from the engine config, not a literal in the router.
    assert lifecycle["clear_k"] == 6
    assert lifecycle["streak_resets_7d"] == 0
    assert lifecycle["recurring"] is False


async def test_lifecycle_clear_k_follows_a_detector_k_override(
    settings: object, seeded_store: object
) -> None:
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.models import EngineConfig
    from netadmin.issues.store_repository import StoreIssueRepository
    from netadmin.server.main import DaemonComponents, create_app

    engine = IssueEngine(
        StoreIssueRepository(seeded_store),
        config=EngineConfig(detector_k={"wired.bad_cable": 2}),
    )
    app = create_app(
        settings=settings,
        store=seeded_store,
        issue_engine=engine,
        components=DaemonComponents(),
    )

    issues = await _issues_by_fingerprint(app)
    assert issues["fp-active"]["lifecycle"]["clear_k"] == 2
    # The override is per detector: the other row keeps the default.
    assert issues["fp-pending"]["lifecycle"]["clear_k"] == 6


async def test_recurring_flips_at_exactly_two_resets(
    app: object, seeded_store: object, now: int
) -> None:
    issues = await _issues_by_fingerprint(app)
    issue_id = issues["fp-active"]["id"]

    _reset_events(seeded_store, issue_id, 1, now=now)
    one = (await _issues_by_fingerprint(app))["fp-active"]["lifecycle"]
    assert one["streak_resets_7d"] == 1
    assert one["recurring"] is False

    _reset_events(seeded_store, issue_id, 1, now=now - 7_200)
    two = (await _issues_by_fingerprint(app))["fp-active"]["lifecycle"]
    assert two["streak_resets_7d"] == 2
    assert two["recurring"] is True


async def test_recurrence_ignores_occurrences_and_stale_resets(
    app: object, seeded_store: object, now: int
) -> None:
    """A steady burner is not recurring; last month's flapping does not count.

    `occurrences` climbs on every fire cycle, so it cannot tell a condition that
    keeps coming back from one that never stopped. The label reads the event
    trail instead (Gitea #39).
    """
    issues = await _issues_by_fingerprint(app)
    issue_id = issues["fp-active"]["id"]

    # Burning steadily for days, and it flapped a fortnight ago -- neither is
    # what "recurring" means.
    seeded_store.update_issue(issue_id, occurrences=98)
    _reset_events(seeded_store, issue_id, 4, now=now - 14 * 86_400)

    row = (await _issues_by_fingerprint(app))["fp-active"]
    assert row["occurrences"] == 98
    assert row["lifecycle"]["streak_resets_7d"] == 0
    assert row["lifecycle"]["recurring"] is False


async def test_issue_detail_carries_the_same_lifecycle_block(
    app: object, seeded_store: object, now: int
) -> None:
    issues = await _issues_by_fingerprint(app)
    issue_id = issues["fp-active"]["id"]
    _reset_events(seeded_store, issue_id, 3, now=now)

    async with await _client(app) as c:
        body = (await c.get(f"/api/issues/{issue_id}")).json()

    assert body["issue"]["lifecycle"] == {
        "clear_k": 6,
        "streak_resets_7d": 3,
        "recurring": True,
    }


async def test_a_real_oscillation_reads_back_as_recurring(
    settings: object, seeded_store: object, now: int
) -> None:
    """Drive the state machine itself, then read the label off the API.

    This is the contract test for the derivation: the engine writes
    ``escalated`` / ``reason: refire_during_resolving`` when a refire kills a
    clear streak, and the read model counts exactly those rows. Renaming the
    reason without updating the query fails here rather than silently reporting
    every recurring issue as ordinary.
    """
    from netadmin.domain.entities import Entity, Finding
    from netadmin.domain.types import EntityType, Severity
    from netadmin.issues.engine import IssueEngine, fingerprint
    from netadmin.issues.store_repository import StoreIssueRepository
    from netadmin.server.main import DaemonComponents, create_app

    engine = IssueEngine(StoreIssueRepository(seeded_store))
    entity = Entity(
        entity_type=EntityType.CLIENT,
        native_id="11:22:33:44:55:66",
        name="Laptop",
        site_id="default",
    )
    entity.entity_id = seeded_store.upsert_entity(entity, ts=now - 86_400)
    finding = Finding(
        detector_key="wifi.sticky_client",
        entity=entity,
        severity=Severity.P2,
        title="Laptop is stuck on a far AP",
        evidence={"median_rssi": -74},
    )
    fp = fingerprint(finding)

    ts = now - 86_400
    # Confirm it (M=3), then oscillate: three clean checks, a refire, three more,
    # a refire. Two killed streaks, and it never reaches K=6.
    for _ in range(3):
        ts += 900
        engine.process_cycle(ts, findings=[finding])
    for _ in range(2):
        for _ in range(3):
            ts += 900
            engine.process_cycle(ts, cleared=[fp])
        ts += 900
        engine.process_cycle(ts, findings=[finding])

    app = create_app(
        settings=settings,
        store=seeded_store,
        issue_engine=engine,
        components=DaemonComponents(),
    )
    row = (await _issues_by_fingerprint(app))[fp]

    assert row["state"] == "active"
    assert row["clear_streak"] == 0
    assert row["lifecycle"]["streak_resets_7d"] == 2
    assert row["lifecycle"]["recurring"] is True


async def test_a_first_time_clear_is_not_recurring(
    settings: object, seeded_store: object, now: int
) -> None:
    """The same machine, clearing normally: resolving, in progress, no label."""
    from netadmin.domain.entities import Entity, Finding
    from netadmin.domain.types import EntityType, Severity
    from netadmin.issues.engine import IssueEngine, fingerprint
    from netadmin.issues.store_repository import StoreIssueRepository
    from netadmin.server.main import DaemonComponents, create_app

    engine = IssueEngine(StoreIssueRepository(seeded_store))
    entity = Entity(
        entity_type=EntityType.CLIENT,
        native_id="11:22:33:44:55:77",
        name="Tablet",
        site_id="default",
    )
    entity.entity_id = seeded_store.upsert_entity(entity, ts=now - 86_400)
    finding = Finding(
        detector_key="wifi.sticky_client",
        entity=entity,
        severity=Severity.P2,
        title="Tablet is stuck on a far AP",
        evidence={"median_rssi": -74},
    )
    fp = fingerprint(finding)

    ts = now - 86_400
    for _ in range(3):
        ts += 900
        engine.process_cycle(ts, findings=[finding])
    for _ in range(3):
        ts += 900
        engine.process_cycle(ts, cleared=[fp])

    app = create_app(
        settings=settings,
        store=seeded_store,
        issue_engine=engine,
        components=DaemonComponents(),
    )
    row = (await _issues_by_fingerprint(app))[fp]

    assert row["state"] == "resolving"
    # The list can now say how far along it is: 3 of 6.
    assert row["clear_streak"] == 3
    assert row["lifecycle"]["clear_k"] == 6
    assert row["lifecycle"]["streak_resets_7d"] == 0
    assert row["lifecycle"]["recurring"] is False
