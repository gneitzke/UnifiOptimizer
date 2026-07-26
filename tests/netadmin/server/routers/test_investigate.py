"""ASGI-transport tests for the LLM-investigator endpoints on the issues router."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from netadmin.config import Settings
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio

TOKEN = "s3cr3t-test-token"


@pytest.fixture(autouse=True)
def _isolate_dossier_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Keep the manual provider's dossier files out of the repo tree.

    The ``manual`` provider defaults to writing under ``<project>/investigations``;
    redirect every test in this module to a throwaway temp dir so a manual
    ``investigate`` never pollutes the working copy.
    """
    import netadmin.llm.manual as manual_mod

    tmp = tmp_path_factory.mktemp("dossiers")
    monkeypatch.setattr(manual_mod, "default_base_dir", lambda: tmp)


@pytest.fixture
def token_settings(tmp_db_path: Path) -> Settings:
    return Settings(
        _env_file=None, db_path=tmp_db_path, site_id="default", netadmin_api_token=TOKEN
    )


@pytest.fixture
def token_app(token_settings: Settings, seeded_store: Repository) -> Any:
    """An app with a configured token -- the investigate provider-gating tests."""
    return create_app(settings=token_settings, store=seeded_store, components=DaemonComponents())


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _active_issue_id(c: httpx.AsyncClient) -> int:
    listing = (await c.get("/api/issues", params={"state": "active"})).json()
    return listing["issues"][0]["id"]


async def test_list_providers(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with await _client(app) as c:
        resp = await c.get("/api/issues/investigate/providers")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["providers"]}
    assert names == {"manual", "copilot", "anthropic"}
    manual = next(p for p in resp.json()["providers"] if p["name"] == "manual")
    assert manual["available"] is True


async def test_manual_investigate_is_pending_then_listed(app: object) -> None:
    async with await _client(app) as c:
        issue_id = await _active_issue_id(c)
        created = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "manual"})
        assert created.status_code == 200
        inv = created.json()["investigation"]
        assert inv["status"] == "pending"
        assert inv["provider"] == "manual"
        assert "Investigation dossier" in inv["dossier_md"]

        listing = await c.get(f"/api/issues/{issue_id}/investigations")
        assert listing.status_code == 200
        assert listing.json()["count"] == 1

        # the 'investigated' event landed on the issue's trail
        detail = (await c.get(f"/api/issues/{issue_id}")).json()
        assert "investigated" in [e["kind"] for e in detail["events"]]


async def test_import_round_trip(app: object) -> None:
    async with await _client(app) as c:
        issue_id = await _active_issue_id(c)
        await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "manual"})
        response_md = "## Answers\n### Root cause\nA failing cable."
        imported = await c.post(
            f"/api/issues/{issue_id}/investigations/import", json={"text": response_md}
        )
        assert imported.status_code == 200
        inv = imported.json()["investigation"]
        assert inv["status"] == "answered"
        assert inv["response_md"] == response_md


async def test_import_requires_text(app: object) -> None:
    async with await _client(app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(f"/api/issues/{issue_id}/investigations/import", json={"text": ""})
    assert resp.status_code == 422


async def test_investigate_unknown_issue_404(app: object) -> None:
    async with await _client(app) as c:
        a = await c.post("/api/issues/999999/investigate", json={"provider": "manual"})
        b = await c.get("/api/issues/999999/investigations")
        d = await c.post("/api/issues/999999/investigations/import", json={"text": "x"})
    assert a.status_code == 404
    assert b.status_code == 404
    assert d.status_code == 404


async def test_investigate_anthropic_absent_key_is_400(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with await _client(app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "anthropic"})
    assert resp.status_code == 400


@respx.mock
async def test_investigate_anthropic_happy_path(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "## Answers\n### Root cause\nCable."}],
            },
        )
    )
    async with await _client(app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "anthropic"})
    assert resp.status_code == 200
    inv = resp.json()["investigation"]
    assert inv["status"] == "answered"
    assert inv["response_md"] == "## Answers\n### Root cause\nCable."


@respx.mock
async def test_investigate_provider_runtime_error_is_502(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(500, json={"error": {"message": "overloaded"}})
    )
    async with await _client(app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "anthropic"})
    assert resp.status_code == 502


# --------------------------------------------------------------------------- #
# provider-gated token enforcement (ARCHITECTURE.md 10 addendum)
#
# The investigate route is OPEN at the middleware; the token decision happens
# in the handler once it has read ``body.provider``. These run against
# ``token_app`` (a configured token) to prove: ``manual`` never needs it,
# ``copilot``/``anthropic`` 401 without it and succeed with it.
# --------------------------------------------------------------------------- #


async def test_manual_investigate_succeeds_without_a_token(token_app: object) -> None:
    async with await _client(token_app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "manual"})
    assert resp.status_code == 200
    assert resp.json()["investigation"]["provider"] == "manual"


async def test_anthropic_investigate_401s_without_a_token(
    token_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    async with await _client(token_app) as c:
        issue_id = await _active_issue_id(c)
        no_tok = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "anthropic"})
        wrong = await c.post(
            f"/api/issues/{issue_id}/investigate",
            json={"provider": "anthropic"},
            headers={"Authorization": "Bearer nope"},
        )
    assert no_tok.status_code == 401
    assert no_tok.headers.get("www-authenticate") == "Bearer"
    assert wrong.status_code == 401


async def test_copilot_investigate_401s_without_a_token(token_app: object) -> None:
    # The token gate runs BEFORE the provider-availability check, so this 401s
    # even though the Copilot CLI is not installed in the test environment (which
    # would otherwise 400).
    async with await _client(token_app) as c:
        issue_id = await _active_issue_id(c)
        no_tok = await c.post(f"/api/issues/{issue_id}/investigate", json={"provider": "copilot"})
    assert no_tok.status_code == 401


@respx.mock
async def test_anthropic_investigate_succeeds_with_the_token(
    token_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "## Answers\n### Root cause\nCable."}],
            },
        )
    )
    async with await _client(token_app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(
            f"/api/issues/{issue_id}/investigate",
            json={"provider": "anthropic"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 200
    assert resp.json()["investigation"]["status"] == "answered"


async def test_copilot_investigate_reaches_the_provider_with_the_token(
    token_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the token, the request clears the auth gate and reaches build_provider().
    # Force the CLI "not found" regardless of what is actually on this machine's
    # PATH (a dev box may well have `copilot`/`gh` installed) so this stays
    # deterministic and never shells out to a real CLI: it surfaces as the normal
    # 400 (unavailable provider) rather than a 401, proving the gate itself passed.
    import netadmin.llm.provider as provider_mod

    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(provider_mod.shutil, "which", lambda cmd: None)
    async with await _client(token_app) as c:
        issue_id = await _active_issue_id(c)
        resp = await c.post(
            f"/api/issues/{issue_id}/investigate",
            json={"provider": "copilot"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 400
