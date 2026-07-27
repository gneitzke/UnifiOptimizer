"""ASGI-transport tests for the offender endpoints (section 17).

``GET /api/devices/offenders`` and ``GET /api/clients/offenders`` rank entities
by composite problem burden and resolve the ranked ids to names. Driven over
``httpx.ASGITransport`` against the ``rich_store`` app fixture, plus a purpose-
built store for the ranking assertions.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.server.main import DaemonComponents, create_app

pytestmark = pytest.mark.asyncio

BASE = 1_700_000_000


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_device_offenders_shape_and_names(rich_app) -> None:
    """The AP with an open P2 issue surfaces, resolved to its name."""
    async with await _client(rich_app) as c:
        resp = await c.get("/api/devices/offenders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_s"] == 86_400
    assert "weights" in body
    names = {o["entity"]["name"] for o in body["offenders"]}
    # ap-office holds the open P2 (fp-active); it is a device offender.
    assert "ap-office" in names
    ap = next(o for o in body["offenders"] if o["entity"]["name"] == "ap-office")
    assert ap["issue_counts"]["p2"] == 1
    assert ap["score"] > 0
    assert "components" in ap


async def test_device_offenders_publish_downtime_and_the_denominator(rich_app) -> None:
    """Two axes, never summed, plus the denominator every figure is read against.

    ``down_minutes`` is present on every entry (``null`` where that axis was not
    measured) and is absent from ``score``'s components, so no consumer can reach
    for a combined figure -- there isn't one (Gitea #38).
    """
    async with await _client(rich_app) as c:
        resp = await c.get("/api/devices/offenders")
    body = resp.json()
    assert "clients_in_window" in body
    assert isinstance(body["clients_in_window"], int)
    for off in body["offenders"]:
        assert "down_minutes" in off
        assert off["down_minutes"] is None or isinstance(off["down_minutes"], (int, float))
        assert set(off["components"]) == {"sle_minutes", "issues", "events"}


async def test_client_offenders_report_no_downtime_axis(rich_app) -> None:
    """A client has no state timeline, so its downtime is null, never 0."""
    async with await _client(rich_app) as c:
        resp = await c.get("/api/clients/offenders")
    body = resp.json()
    assert body["offenders"]
    assert all(off["down_minutes"] is None for off in body["offenders"])


async def test_client_offenders_resolves_names(rich_app) -> None:
    """The flaky client (open issue) shows up on the client leaderboard by name."""
    async with await _client(rich_app) as c:
        resp = await c.get("/api/clients/offenders")
    assert resp.status_code == 200
    body = resp.json()
    names = {o["entity"]["name"] for o in body["offenders"]}
    assert "iPhone" in names
    # A device never leaks onto the client surface.
    assert "ap-office" not in names


async def test_window_and_top_n_params(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/devices/offenders?window_s=3600&top_n=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_s"] == 3600
    assert len(body["offenders"]) <= 1


async def test_top_n_out_of_range_rejected(rich_app) -> None:
    async with await _client(rich_app) as c:
        resp = await c.get("/api/devices/offenders?top_n=0")
    assert resp.status_code == 422


def _empty_app(settings, tmp_db_path) -> Any:
    from netadmin.store.repository import Repository

    store = Repository.open(tmp_db_path, site_id=settings.site_id)
    store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:09", name="lonely-ap"),
        ts=BASE,
    )
    return create_app(settings=settings, store=store, components=DaemonComponents())


async def test_empty_offenders(settings, tmp_db_path) -> None:
    """A site with no burden returns an empty, well-formed leaderboard."""
    app = _empty_app(settings, tmp_db_path)
    async with await _client(app) as c:
        resp = await c.get("/api/devices/offenders")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["offenders"] == []
