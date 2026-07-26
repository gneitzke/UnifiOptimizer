"""Tests for the SLE extras: offender name resolution + per-SLE score timeseries."""

from __future__ import annotations

import time

import httpx
import pytest

from netadmin.server.main import DaemonComponents, create_app

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def app_with_minutes(settings, rich_store):
    now = int(time.time())
    ap_id = rich_store.ap_id
    # two adjacent 5-minute buckets so the timeseries has more than one point
    for offset in (600, 300):
        bucket = (now - (now % 300)) - offset
        rich_store.upsert_sle_minute(
            bucket_ts=bucket,
            sle="coverage",
            classifier="ok",
            entity_id=100,
            minutes=4.0,
            attributed_entity_id=ap_id,
        )
        rich_store.upsert_sle_minute(
            bucket_ts=bucket,
            sle="coverage",
            classifier="weak_signal",
            entity_id=100,
            minutes=1.0,
            attributed_entity_id=ap_id,
        )
    return create_app(settings=settings, store=rich_store, components=DaemonComponents())


async def test_offenders_carry_entity_names(app_with_minutes, rich_store) -> None:
    async with await _client(app_with_minutes) as c:
        resp = await c.get("/api/sle")
    body = resp.json()
    offenders = body["sles"]["coverage"]["top_offenders"]
    assert offenders[0]["attributed_entity_id"] == rich_store.ap_id
    assert offenders[0]["entity"]["name"] == "ap-office"


async def test_per_sle_timeseries_present_and_scored(app_with_minutes) -> None:
    async with await _client(app_with_minutes) as c:
        resp = await c.get("/api/sle", params={"buckets": 500})
    body = resp.json()
    series = body["sles"]["coverage"]["timeseries"]
    assert len(series) == 2  # the two seeded buckets, not folded together
    for point in series:
        assert set(point) == {"ts", "score", "ok_minutes", "total_minutes"}
        assert point["score"] == pytest.approx(4.0 / 5.0)
    # an SLE with no minutes has an empty (gap) series, never a fabricated line
    assert body["sles"]["roaming"]["timeseries"] == []


async def test_top_level_shape_unchanged(app_with_minutes) -> None:
    # adding timeseries lives inside each SLE; the top-level contract is stable
    async with await _client(app_with_minutes) as c:
        resp = await c.get("/api/sle")
    assert set(resp.json()) == {"start_ts", "end_ts", "headline", "weights", "sles"}
