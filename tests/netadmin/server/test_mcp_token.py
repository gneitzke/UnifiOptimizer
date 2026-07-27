"""Remote-MCP-token surface tests (ARCHITECTURE.md 18.4 Settings addendum).

The reveal + regenerate routes are how a user finds or rotates
``NETADMIN_MCP_TOKEN`` from Settings, beside the access-token section. Gating is
IDENTICAL to the access-token routes (see ``test_system_token.py``), credential
and all: the *API* bearer token (or a loopback peer, for reveal only) unlocks
both -- never the MCP token being managed, since a credential must not be able
to authorize its own rotation.

* ``GET /api/system/mcp-token`` (reveal) is the ONE more GET that is not open
  once an API token is configured -- it returns ``NETADMIN_MCP_TOKEN`` OR a
  loopback peer, and is open (but empty) on an unconfigured install.
* ``POST /api/system/mcp-token/regenerate`` mints + persists a new MCP token,
  gated by the *current API* token and rate limited with the controller writes
  (sharing that budget with the access-token regenerate). It writes only a TEMP
  secrets file here (never the real ``data/secrets.env``), and rotates the live
  ``Settings`` in place so an already-running mount's per-request token read
  locks to the new value with no restart.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Tuple

import httpx
import pytest

from netadmin.config import Settings
from netadmin.server.auth import ApiTokenAuthMiddleware, is_mcp_token_regenerate
from netadmin.server.main import DaemonComponents, create_app
from netadmin.server.mcp_mount import McpEndpoint
from netadmin.store.repository import Repository

API_TOKEN = "s3cr3t-api-test-token"
LOOPBACK = ("127.0.0.1", 5000)
REMOTE = ("10.1.2.3", 5555)

_REVEAL = "/api/system/mcp-token"
_REGEN = "/api/system/mcp-token/regenerate"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def secrets_path(tmp_path: Path) -> Path:
    """A TEMP secrets file -- regenerate writes here, never the real one."""
    return tmp_path / "secrets.env"


@pytest.fixture
def mcp_token_settings(tmp_db_path: Path) -> Settings:
    """An API-token-gated daemon with NO MCP token configured yet."""
    return Settings(
        _env_file=None, db_path=tmp_db_path, site_id="default", netadmin_api_token=API_TOKEN
    )


@pytest.fixture
def mcp_token_app(
    mcp_token_settings: Settings, seeded_store: Repository, secrets_path: Path
) -> Any:
    app = create_app(settings=mcp_token_settings, store=seeded_store, components=DaemonComponents())
    app.state.secrets_path = secrets_path
    return app


def _client(app: object, *, peer: Tuple[str, int] = LOOPBACK) -> httpx.AsyncClient:
    """An ASGI client whose ASGI peer (``scope['client']``) is ``peer``."""
    transport = httpx.ASGITransport(app=app, client=peer)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _ok_app(scope: Any, receive: Any, send: Any) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok"})


# --------------------------------------------------------------------------- #
# unit: classification
# --------------------------------------------------------------------------- #
def test_is_mcp_token_regenerate_matches_only_the_post_route() -> None:
    assert is_mcp_token_regenerate("POST", _REGEN)
    assert not is_mcp_token_regenerate("GET", _REGEN)
    assert not is_mcp_token_regenerate("POST", _REVEAL)
    assert not is_mcp_token_regenerate("POST", "/api/system/mcp-token/regenerate/extra")
    # And it must not accidentally match the access-token regenerate route.
    assert not is_mcp_token_regenerate("POST", "/api/system/token/regenerate")


# --------------------------------------------------------------------------- #
# reveal (GET /api/system/mcp-token)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reveal_from_remote_requires_the_api_token(mcp_token_app: object) -> None:
    async with _client(mcp_token_app, peer=REMOTE) as c:
        no_tok = await c.get(_REVEAL)
        wrong = await c.get(_REVEAL, headers={"Authorization": "Bearer nope"})
        good = await c.get(_REVEAL, headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert no_tok.status_code == 401
    assert wrong.status_code == 401
    assert good.status_code == 200
    # No NETADMIN_MCP_TOKEN configured yet: null, honestly.
    assert good.json() == {"token": None, "configured": False}


@pytest.mark.asyncio
async def test_reveal_from_loopback_needs_no_token(mcp_token_app: object) -> None:
    # Same on-box recovery path as the access-token reveal.
    async with _client(mcp_token_app, peer=LOOPBACK) as c:
        resp = await c.get(_REVEAL)
    assert resp.status_code == 200
    assert resp.json() == {"token": None, "configured": False}


@pytest.mark.asyncio
async def test_reveal_forwarded_loopback_still_requires_token(mcp_token_app: object) -> None:
    async with _client(mcp_token_app, peer=LOOPBACK) as c:
        resp = await c.get(_REVEAL, headers={"X-Forwarded-For": "203.0.113.9"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reveal_refuses_remote_even_when_api_unconfigured(app: object) -> None:
    # Regression: this route used to fall through the "no API token -> reads are
    # open" shortcut, so any LAN peer could read NETADMIN_MCP_TOKEN in plaintext.
    # The documented setup mints only the MCP token and never asks for an API
    # token, so that WAS the happy path. The MCP token guards the whole history
    # store; open network mutations must not imply an open credential reveal.
    async with _client(app, peer=REMOTE) as c:
        resp = await c.get(_REVEAL)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reveal_still_serves_loopback_when_api_unconfigured(app: object) -> None:
    # The other half of the same rule: the operator on the box keeps the
    # unauthenticated path, so `netadmin mcp-token` and a local browser still
    # work on an install that has no API token at all.
    async with _client(app, peer=LOOPBACK) as c:
        resp = await c.get(_REVEAL)
    assert resp.status_code == 200
    assert resp.json() == {"token": None, "configured": False}


@pytest.mark.asyncio
async def test_reveal_reports_a_configured_mcp_token(
    mcp_token_settings: Settings, seeded_store: Repository
) -> None:
    mcp_token_settings.netadmin_mcp_token = "existing-mcp-token"
    app = create_app(settings=mcp_token_settings, store=seeded_store, components=DaemonComponents())
    async with _client(app, peer=REMOTE) as c:
        resp = await c.get(_REVEAL, headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"token": "existing-mcp-token", "configured": True}


# --------------------------------------------------------------------------- #
# regenerate (POST /api/system/mcp-token/regenerate)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_regenerate_requires_the_current_api_token(mcp_token_app: object) -> None:
    async with _client(mcp_token_app, peer=REMOTE) as c:
        no_tok = await c.post(_REGEN)
        wrong = await c.post(_REGEN, headers={"Authorization": "Bearer nope"})
    assert no_tok.status_code == 401
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_regenerate_is_not_authorized_by_the_mcp_token_itself(
    mcp_token_settings: Settings, seeded_store: Repository, secrets_path: Path
) -> None:
    # The whole point: presenting the (old) MCP token must not rotate itself.
    mcp_token_settings.netadmin_mcp_token = "old-mcp-token"
    app = create_app(settings=mcp_token_settings, store=seeded_store, components=DaemonComponents())
    app.state.secrets_path = secrets_path
    async with _client(app, peer=REMOTE) as c:
        resp = await c.post(_REGEN, headers={"Authorization": "Bearer old-mcp-token"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_regenerate_has_no_loopback_bypass(mcp_token_app: object) -> None:
    # Reveal is loopback-recoverable; regenerate is a mutation and stays gated even
    # on the box, exactly like the access-token regenerate.
    async with _client(mcp_token_app, peer=LOOPBACK) as c:
        resp = await c.post(_REGEN)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_regenerate_mints_persists_and_rotates_with_no_restart(
    mcp_token_app: object, mcp_token_settings: Settings, secrets_path: Path
) -> None:
    endpoint = McpEndpoint(mcp_token_app)  # the /mcp gate; reads settings.mcp_token live
    assert endpoint.token is None  # nothing configured yet

    async with _client(mcp_token_app, peer=REMOTE) as c:
        resp = await c.post(_REGEN, headers={"Authorization": f"Bearer {API_TOKEN}"})
        assert resp.status_code == 200
        first_token = resp.json()["token"]
        assert first_token

        # Live settings updated in place, no restart: the /mcp gate sees it now.
        assert mcp_token_settings.mcp_token == first_token
        assert endpoint.token == first_token

        # Persisted to the TEMP secrets file (600), not the real one.
        assert secrets_path.exists()
        assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
        assert f"NETADMIN_MCP_TOKEN={first_token}" in secrets_path.read_text()

        # Rotate again: the mount's gate reflects the new value immediately, and
        # the just-superseded token is worthless the very next time it's checked.
        resp2 = await c.post(_REGEN, headers={"Authorization": f"Bearer {API_TOKEN}"})
        assert resp2.status_code == 200
        second_token = resp2.json()["token"]
        assert second_token != first_token
        assert endpoint.token == second_token
        assert mcp_token_settings.mcp_token == second_token

        reveal = await c.get(_REVEAL, headers={"Authorization": f"Bearer {API_TOKEN}"})
        assert reveal.json() == {"token": second_token, "configured": True}


@pytest.mark.asyncio
async def test_regenerate_is_rate_limited() -> None:
    # Shares the write-op limiter with the access-token regenerate and the
    # controller writes, so a leaked API token cannot churn either secret unbounded.
    mw = ApiTokenAuthMiddleware(_ok_app, token=API_TOKEN, write_max=1, write_window_s=60.0)
    hdr = {"Authorization": f"Bearer {API_TOKEN}"}
    transport = httpx.ASGITransport(app=mw, client=REMOTE)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        first = await c.post(_REGEN, headers=hdr)
        second = await c.post(_REGEN, headers=hdr)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_regenerate_refuses_remote_when_no_api_token_configured() -> None:
    # Regression, and the more damaging half of the reveal bug above: a remote
    # peer could not only READ the MCP token but rotate it, silently breaking
    # every configured Claude client. Rotation is not something an anonymous LAN
    # device may do just because network mutations happen to be unlocked.
    mw = ApiTokenAuthMiddleware(_ok_app, token=None)
    transport = httpx.ASGITransport(app=mw, client=REMOTE)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(_REGEN)
    assert resp.status_code == 401
