"""ASGI-transport tests for GET /api/sle (score + classifier breakdown)."""

from __future__ import annotations

import time

import httpx
import pytest

from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _seed_minutes(store: Repository, bucket_ts: int) -> None:
    # coverage: 4 ok minutes on ap 1, 1 weak_signal minute pinned on ap 1
    store.upsert_sle_minute(
        bucket_ts=bucket_ts,
        sle="coverage",
        classifier="ok",
        entity_id=100,
        minutes=4.0,
        attributed_entity_id=1,
    )
    store.upsert_sle_minute(
        bucket_ts=bucket_ts,
        sle="coverage",
        classifier="weak_signal",
        entity_id=100,
        minutes=1.0,
        attributed_entity_id=1,
    )
    # capacity: all fail, pinned on the radio (entity 2)
    store.upsert_sle_minute(
        bucket_ts=bucket_ts,
        sle="capacity",
        classifier="non_wifi_util",
        entity_id=100,
        minutes=5.0,
        attributed_entity_id=2,
    )


@pytest.fixture
def app_with_minutes(settings, seeded_store):
    now = int(time.time())
    bucket = (now - (now % 300)) - 300
    _seed_minutes(seeded_store, bucket)
    return create_app(settings=settings, store=seeded_store, components=DaemonComponents())


async def test_sle_endpoint_shape_and_scores(app_with_minutes) -> None:
    async with await _client(app_with_minutes) as c:
        resp = await c.get("/api/sle")
    assert resp.status_code == 200
    body = resp.json()

    assert set(body) == {"start_ts", "end_ts", "headline", "weights", "sles"}
    # every canonical SLE is present; silent ones report score null (no data)
    for sle in ("coverage", "roaming", "capacity", "connect", "wan", "infra"):
        assert sle in body["sles"]
    assert body["sles"]["roaming"]["score"] is None

    coverage = body["sles"]["coverage"]
    assert coverage["score"] == pytest.approx(4.0 / 5.0)  # 4 ok / 5 total
    assert coverage["classifiers"] == {"ok": 4.0, "weak_signal": 1.0}
    # the failed minute is pinned on the AP (entity 1)
    assert coverage["top_offenders"][0]["attributed_entity_id"] == 1

    capacity = body["sles"]["capacity"]
    assert capacity["score"] == 0.0  # all fail
    assert capacity["top_offenders"][0]["attributed_entity_id"] == 2  # the radio

    # headline blends only the SLEs that had data (coverage + capacity here)
    assert body["headline"] is not None
    assert 0.0 <= body["headline"] <= 1.0


async def test_sle_endpoint_explicit_window(app_with_minutes) -> None:
    async with await _client(app_with_minutes) as c:
        # a window in the far past has no minutes -> all scores null, headline null
        resp = await c.get("/api/sle", params={"start": 1000, "end": 2000})
    assert resp.status_code == 200
    body = resp.json()
    assert body["start_ts"] == 1000 and body["end_ts"] == 2000
    assert body["headline"] is None
    assert all(s["score"] is None for s in body["sles"].values())


async def test_sle_endpoint_rejects_inverted_window(app_with_minutes) -> None:
    async with await _client(app_with_minutes) as c:
        resp = await c.get("/api/sle", params={"start": 2000, "end": 1000})
    assert resp.status_code == 422
