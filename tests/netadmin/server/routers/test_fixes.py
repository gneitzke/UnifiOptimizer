"""ASGI-transport tests for the fixes router — the full lifecycle over HTTP.

The controller seams are injected fakes (``app.state.fix_seams``): a
:class:`FakeDeviceReader` for the read-only device snapshot and a
:class:`FakeControllerWriter` for the one mutation an apply sends. No lifespan
runs, no socket opens. The writer's ``calls`` list is the load-bearing proof that
a GET fix-plan sends nothing and only a confirmed apply mutates.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.fixes.reader import FakeDeviceReader
from netadmin.fixes.service import FixSeams
from netadmin.fixes.writer import FakeControllerWriter
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

NOW = 1_700_000_000
AP_MAC = "aa:bb:cc:00:00:01"
AP_ID = "60a1b2c3d4e5f60000000001"
# The mutating fix routes fail closed without a token (ARCHITECTURE.md 12), so the
# lifecycle app configures one and the client presents it on every request.
TOKEN = "router-fix-token"


def _ap_device() -> dict:
    return {
        "_id": AP_ID,
        "mac": AP_MAC,
        "type": "uap",
        "radio_table": [
            {"radio": "ng", "channel": 3, "ht": 20, "tx_power_mode": "high"},
            {"radio": "na", "channel": 36, "ht": 80, "tx_power_mode": "auto"},
        ],
    }


@pytest.fixture
def fix_env(settings):
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=AP_MAC, name="Office AP"), ts=NOW
    )
    radio = store.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id=f"{AP_MAC}:ng",
            name="Office AP ng",
            parent_id=ap,
            meta={"band": "ng"},
        ),
        ts=NOW,
    )
    issue_id = store.insert_issue(
        fingerprint="fp-channel",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="2.4 GHz off 1/6/11 on Office AP",
        entity_id=radio,
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    reader = FakeDeviceReader({AP_MAC: _ap_device()})
    writer = FakeControllerWriter()
    tokened = settings.model_copy(update={"netadmin_api_token": TOKEN})
    app = create_app(settings=tokened, store=store, components=DaemonComponents())
    app.state.fix_seams = FixSeams(reader=reader, writer=writer)
    yield SimpleNamespace(app=app, store=store, issue_id=issue_id, reader=reader, writer=writer)
    store.close()


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


# --------------------------------------------------------------------------- #
# GET fix-plan (dry-run) is inert
# --------------------------------------------------------------------------- #
async def test_fix_plan_renders_and_sends_nothing(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.get(f"/api/issues/{fix_env.issue_id}/fix-plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["manual_action_required"] is False
    assert body["steps"][0]["endpoint"] == f"rest/device/{AP_ID}"
    ng = next(r for r in body["steps"][0]["payload"]["radio_table"] if r["radio"] == "ng")
    assert ng["channel"] == 1
    assert body["confirm_token"]
    # A dry-run GET sends no mutation and writes no ledger row.
    assert fix_env.writer.call_count == 0
    assert body["changes"] == []


async def test_fix_plan_unknown_issue_is_404(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.get("/api/issues/999999/fix-plan")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# GET fix-history: DB-only, never a device read (gitea #26)
# --------------------------------------------------------------------------- #
async def test_fix_history_before_any_apply_is_empty_and_touches_no_device(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.get(f"/api/issues/{fix_env.issue_id}/fix-history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fix_state"] is None
    assert body["changes"] == []
    assert body["verification"]["status"] == "not_armed"
    # No dry-run was ever built for this call: the reader was never touched.
    assert fix_env.reader.calls == []
    assert fix_env.writer.call_count == 0


async def test_fix_history_unknown_issue_is_404(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.get("/api/issues/999999/fix-history")
    assert resp.status_code == 404


async def test_fix_history_after_apply_matches_fix_plan_without_a_new_device_read(
    fix_env,
) -> None:
    async with await _client(fix_env.app) as c:
        plan = (await c.get(f"/api/issues/{fix_env.issue_id}/fix-plan")).json()
        await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/apply",
            json={"confirm": True, "confirm_token": plan["confirm_token"]},
        )
        reads_after_apply = len(fix_env.reader.calls)
        resp = await c.get(f"/api/issues/{fix_env.issue_id}/fix-history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fix_state"] == "applied"
    assert body["verification"]["status"] == "pending"
    assert len(body["changes"]) == 1
    assert body["changes"][0]["status"] == "applied"
    # The history read is store-only: it didn't add another device read.
    assert len(fix_env.reader.calls) == reads_after_apply


# --------------------------------------------------------------------------- #
# POST apply is gated
# --------------------------------------------------------------------------- #
async def test_apply_requires_confirm_true(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        plan = (await c.get(f"/api/issues/{fix_env.issue_id}/fix-plan")).json()
        resp = await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/apply",
            json={"confirm": False, "confirm_token": plan["confirm_token"]},
        )
    assert resp.status_code == 400
    assert fix_env.writer.call_count == 0


async def test_apply_missing_confirm_field_is_422(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/apply",
            json={"confirm_token": "x"},
        )
    assert resp.status_code == 422
    assert fix_env.writer.call_count == 0


async def test_apply_wrong_token_is_409(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/apply",
            json={"confirm": True, "confirm_token": "deadbeef"},
        )
    assert resp.status_code == 409
    assert fix_env.writer.call_count == 0


async def test_apply_confirmed_mutates_once_and_arms_verification(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        plan = (await c.get(f"/api/issues/{fix_env.issue_id}/fix-plan")).json()
        resp = await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/apply",
            json={"confirm": True, "confirm_token": plan["confirm_token"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["fix_state"] == "applied"
    assert body["verification"]["status"] == "pending"
    assert len(body["changes"]) == 1
    assert body["changes"][0]["status"] == "applied"

    # Exactly one mutation reached the (fake) writer, carrying the retuned channel.
    assert fix_env.writer.call_count == 1
    sent = fix_env.writer.calls[0]
    assert sent.method == "PUT"
    assert sent.endpoint == f"rest/device/{AP_ID}"
    ng = next(r for r in sent.body["radio_table"] if r["radio"] == "ng")
    assert ng["channel"] == 1


async def test_apply_then_revert_restores_before_state(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        plan = (await c.get(f"/api/issues/{fix_env.issue_id}/fix-plan")).json()
        applied = (
            await c.post(
                f"/api/issues/{fix_env.issue_id}/fix/apply",
                json={"confirm": True, "confirm_token": plan["confirm_token"]},
            )
        ).json()
        change_id = applied["change_ids"][0]
        resp = await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/revert",
            json={"change_id": change_id},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["change"]["status"] == "reverted"
    # The revert PUT restored the original 2.4 GHz channel.
    last = fix_env.writer.calls[-1]
    ng = next(r for r in last.body["radio_table"] if r["radio"] == "ng")
    assert ng["channel"] == 3


async def test_revert_change_not_on_issue_is_404(fix_env) -> None:
    async with await _client(fix_env.app) as c:
        resp = await c.post(
            f"/api/issues/{fix_env.issue_id}/fix/revert",
            json={"change_id": 99999},
        )
    assert resp.status_code == 404


async def test_fixes_503_when_store_absent(settings) -> None:
    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with await _client(app) as c:
        resp = await c.get("/api/issues/1/fix-plan")
    assert resp.status_code == 503


async def test_change_rows_name_the_device_they_touched(fix_env) -> None:
    """A joint band re-plan ledgers one row per radio; each must name its device.

    Without this the UI renders N identical "Channel change / applied" cards and
    the operator cannot tell which AP a Revert button belongs to.
    """
    fix_env.store.insert_change(
        issue_id=fix_env.issue_id,
        entity_id=next(
            e["entity_id"] for e in fix_env.store.list_entities() if e["native_id"] == AP_MAC
        ),
        action="channel_change",
        before={"endpoint": f"rest/device/{AP_ID}", "body": {}},
        after={"endpoint": f"rest/device/{AP_ID}", "body": {}},
        status="applied",
        ts=NOW,
    )
    async with await _client(fix_env.app) as c:
        resp = await c.get(f"/api/issues/{fix_env.issue_id}/fix-plan")
    change = resp.json()["changes"][0]
    assert change["entity_name"] == "Office AP"
    assert change["entity_native_id"] == AP_MAC
