"""Tests for GET /api/metrics/window and the server-side downsample math."""

from __future__ import annotations

import httpx
import pytest

from netadmin.server.routers.metrics import downsample

from ..conftest import BASE_TS


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_window_returns_downsampled_buckets(rich_app) -> None:
    ap_id = rich_app.state.store.ap_id
    async with await _client(rich_app) as c:
        resp = await c.get(
            "/api/metrics/window",
            params={
                "entity_id": ap_id,
                "metric": "cpu",
                "seconds": 1200,
                "points": 5,
                "end": BASE_TS + 661,
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "raw"
    assert body["raw_count"] == 12
    assert 1 <= len(body["buckets"]) <= 5
    for b in body["buckets"]:
        assert set(b) == {"ts", "min", "max", "avg", "n"}
        assert b["min"] <= b["avg"] <= b["max"]


@pytest.mark.asyncio
async def test_window_404_for_unknown_metric(rich_app) -> None:
    ap_id = rich_app.state.store.ap_id
    async with await _client(rich_app) as c:
        resp = await c.get("/api/metrics/window", params={"entity_id": ap_id, "metric": "nope"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_window_404_for_unknown_entity(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/metrics/window", params={"entity_id": 999999, "metric": "cpu"})
    assert resp.status_code == 404


# --- downsample pure-function tests --------------------------------------- #


def test_downsample_passthrough_when_small() -> None:
    rows = [{"ts": 0, "value": 1.0}, {"ts": 10, "value": 3.0}]
    out = downsample(rows, points=10, start_ts=0, end_ts=20)
    assert out == [
        {"ts": 0, "min": 1.0, "max": 1.0, "avg": 1.0, "n": 1},
        {"ts": 10, "min": 3.0, "max": 3.0, "avg": 3.0, "n": 1},
    ]


def test_downsample_folds_and_weights_average() -> None:
    # 4 raw points into 2 buckets over [0, 100): idx = ts/50
    rows = [
        {"ts": 0, "value": 0.0},
        {"ts": 10, "value": 10.0},
        {"ts": 60, "value": 20.0},
        {"ts": 90, "value": 40.0},
    ]
    out = downsample(rows, points=2, start_ts=0, end_ts=100)
    assert len(out) == 2
    first, second = out
    assert first["ts"] == 0 and first["min"] == 0.0 and first["max"] == 10.0
    assert first["avg"] == pytest.approx(5.0) and first["n"] == 2
    assert second["ts"] == 60 and second["min"] == 20.0 and second["max"] == 40.0
    assert second["avg"] == pytest.approx(30.0)


def test_downsample_omits_empty_buckets_as_gaps() -> None:
    # All samples cluster at the start; middle/late buckets stay empty (gaps).
    rows = [{"ts": 0, "value": 1.0}, {"ts": 1, "value": 2.0}, {"ts": 2, "value": 3.0}]
    out = downsample(rows, points=10, start_ts=0, end_ts=1000)
    # only the buckets that actually held samples are emitted, never interpolated
    assert len(out) <= 3
    assert all(b["n"] >= 1 for b in out)


def test_downsample_weights_rollup_rows_by_n() -> None:
    # Rollup-shaped rows carry their own n/min/max/avg; folding must weight by n.
    rows = [
        {"ts": 0, "n": 4, "min": 0.0, "max": 8.0, "avg": 4.0},
        {"ts": 5, "n": 1, "min": 100.0, "max": 100.0, "avg": 100.0},
    ]
    out = downsample(rows, points=1, start_ts=0, end_ts=10)
    assert len(out) == 1
    folded = out[0]
    assert folded["n"] == 5
    assert folded["min"] == 0.0 and folded["max"] == 100.0
    assert folded["avg"] == pytest.approx((4 * 4.0 + 1 * 100.0) / 5)
