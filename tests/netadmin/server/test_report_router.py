"""ASGI-transport tests for ``GET /api/report`` (the report assembler surface).

The router is a thin edge over :func:`netadmin.report.build_report`: it resolves
the window and serialises the model. These tests confirm the endpoint is an open
read (18.1), returns every top-level section, accepts a ``window_s`` override,
clamps out-of-range windows, and stays honest on an empty store.
"""

from __future__ import annotations

import time

import httpx
import pytest

from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

_SECTIONS = {
    "cover",
    "executive_summary",
    "scope",
    "inventory",
    "topology",
    "health",
    "rf",
    "clients",
    "findings",
    "roadmap",
    "appendix",
}


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_report_returns_all_sections(app) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/report")
    assert resp.status_code == 200
    body = resp.json()
    assert _SECTIONS.issubset(body.keys())
    # The seeded_store fixture has an active P2 issue -> at least one finding.
    assert isinstance(body["findings"], list)
    sc = body["executive_summary"]["scorecard"]
    assert sum(sc["findings_by_severity"].values()) == sc["total_findings"]


async def test_report_is_open_read_without_token(settings, seeded_store) -> None:
    # A configured daemon (token set) still serves GET /api/report unauthenticated.
    settings.netadmin_api_token = "secret-token"
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/report")
    assert resp.status_code == 200


async def test_report_window_override(app) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/report", params={"window_s": 3_600})
    assert resp.status_code == 200
    window = resp.json()["cover"]["window"]
    assert window["duration_s"] == 3_600
    assert window["label"] == "1 hour"


async def test_report_rejects_out_of_range_window(app) -> None:
    async with await _client(app) as c:
        too_small = await c.get("/api/report", params={"window_s": 60})
        too_big = await c.get("/api/report", params={"window_s": 10**12})
    assert too_small.status_code == 422
    assert too_big.status_code == 422


async def test_report_empty_store_is_honest(settings, tmp_db_path) -> None:
    store = Repository.open(tmp_db_path, site_id=settings.site_id)
    try:
        app = create_app(settings=settings, store=store, components=DaemonComponents())
        async with await _client(app) as c:
            resp = await c.get("/api/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["findings"] == []
        assert body["health"]["headline_score"] is None
        assert body["executive_summary"]["scorecard"]["posture"] == "insufficient data"
    finally:
        store.close()


async def test_report_generated_ts_is_recent(app) -> None:
    before = int(time.time())
    async with await _client(app) as c:
        resp = await c.get("/api/report")
    body = resp.json()
    assert body["generated_ts"] >= before
    assert body["cover"]["tool"] == "UnifiOptimizer"
