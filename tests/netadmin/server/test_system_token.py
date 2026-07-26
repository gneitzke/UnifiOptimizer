"""Access-token surface tests (ARCHITECTURE.md 18.1 Settings addendum).

The reveal + regenerate routes are how a user finds or rotates the access token a
just-in-time fix prompt asks for. Auth is enforced by the middleware, so these
cover both the middleware gating and the endpoint behaviour, fully offline over
``httpx.ASGITransport``:

* ``GET /api/system/token`` (reveal) is the ONE GET that is not open once
  configured -- it returns the bearer token OR a loopback peer, and is open (but
  empty) on an unconfigured install.
* ``POST /api/system/token/regenerate`` mints + persists a new token, gated by the
  *current* token and rate limited with the controller writes. It never leaks the
  token to a caller that could not already read it, and it writes only a TEMP
  secrets file here (never the real ``data/secrets.env``).
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Tuple

import httpx
import pytest

from netadmin.config import Settings
from netadmin.server.auth import ApiTokenAuthMiddleware, _is_loopback, is_token_regenerate
from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

TOKEN = "s3cr3t-test-token"
LOOPBACK = ("127.0.0.1", 5000)
REMOTE = ("10.1.2.3", 5555)

_REVEAL = "/api/system/token"
_REGEN = "/api/system/token/regenerate"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def secrets_path(tmp_path: Path) -> Path:
    """A TEMP secrets file -- regenerate writes here, never the real one."""
    return tmp_path / "secrets.env"


@pytest.fixture
def token_settings(tmp_db_path: Path) -> Settings:
    return Settings(
        _env_file=None, db_path=tmp_db_path, site_id="default", netadmin_api_token=TOKEN
    )


@pytest.fixture
def token_app(token_settings: Settings, seeded_store: Repository, secrets_path: Path) -> Any:
    app = create_app(settings=token_settings, store=seeded_store, components=DaemonComponents())
    app.state.secrets_path = secrets_path
    return app


def _client(app: object, *, peer: Tuple[str, int] = LOOPBACK) -> httpx.AsyncClient:
    """An ASGI client whose ASGI peer (``scope['client']``) is ``peer``.

    The reveal's loopback bypass keys off that peer, so tests pin it explicitly
    rather than depending on the transport default.
    """
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
# unit: classification + loopback detection
# --------------------------------------------------------------------------- #
def test_is_token_regenerate_matches_only_the_post_route() -> None:
    assert is_token_regenerate("POST", _REGEN)
    assert not is_token_regenerate("GET", _REGEN)
    assert not is_token_regenerate("POST", _REVEAL)
    assert not is_token_regenerate("POST", "/api/system/token/regenerate/extra")


def test_is_loopback_classifies_peer_and_forwarding() -> None:
    assert _is_loopback({"headers": [], "client": ("127.0.0.1", 1)})
    assert _is_loopback({"headers": [], "client": ("127.5.5.5", 1)})
    assert _is_loopback({"headers": [], "client": ("::1", 1)})
    assert not _is_loopback({"headers": [], "client": ("10.0.0.9", 1)})
    assert not _is_loopback({"headers": [], "client": None})
    # A forwarded request is never loopback: behind a proxy the peer is the proxy.
    assert not _is_loopback(
        {"headers": [(b"x-forwarded-for", b"10.0.0.9")], "client": ("127.0.0.1", 1)}
    )


# --------------------------------------------------------------------------- #
# reveal (GET /api/system/token)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reveal_from_remote_requires_the_token(token_app: object) -> None:
    async with _client(token_app, peer=REMOTE) as c:
        no_tok = await c.get(_REVEAL)
        wrong = await c.get(_REVEAL, headers={"Authorization": "Bearer nope"})
        good = await c.get(_REVEAL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert no_tok.status_code == 401
    assert wrong.status_code == 401
    assert good.status_code == 200
    assert good.json() == {"token": TOKEN, "configured": True}


@pytest.mark.asyncio
async def test_reveal_from_loopback_needs_no_token(token_app: object) -> None:
    # The on-box recovery path: the operator at the console reads the token back.
    async with _client(token_app, peer=LOOPBACK) as c:
        resp = await c.get(_REVEAL)
    assert resp.status_code == 200
    assert resp.json()["token"] == TOKEN


@pytest.mark.asyncio
async def test_reveal_forwarded_loopback_still_requires_token(token_app: object) -> None:
    # A reverse-proxied request arrives from localhost but is really remote: the
    # loopback bypass must NOT fire, so it still needs the token.
    async with _client(token_app, peer=LOOPBACK) as c:
        resp = await c.get(_REVEAL, headers={"X-Forwarded-For": "203.0.113.9"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reveal_is_open_but_empty_when_unconfigured(
    app: object,
) -> None:
    # No token configured -> the API is open; reveal answers honestly with null.
    async with _client(app, peer=REMOTE) as c:
        resp = await c.get(_REVEAL)
    assert resp.status_code == 200
    assert resp.json() == {"token": None, "configured": False}


# --------------------------------------------------------------------------- #
# regenerate (POST /api/system/token/regenerate)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_regenerate_requires_the_current_token(token_app: object) -> None:
    async with _client(token_app, peer=REMOTE) as c:
        no_tok = await c.post(_REGEN)
        wrong = await c.post(_REGEN, headers={"Authorization": "Bearer nope"})
    assert no_tok.status_code == 401
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_regenerate_has_no_loopback_bypass(token_app: object) -> None:
    # Reveal is loopback-recoverable; regenerate is a mutation and stays gated even
    # on the box -- you reveal the token first, then rotate with it.
    async with _client(token_app, peer=LOOPBACK) as c:
        resp = await c.post(_REGEN)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_regenerate_mints_persists_and_rotates(
    token_app: object, token_settings: Settings, secrets_path: Path
) -> None:
    async with _client(token_app, peer=REMOTE) as c:
        resp = await c.post(_REGEN, headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        new_token = resp.json()["token"]
        assert new_token and new_token != TOKEN

        # Live settings rotated in place, so the middleware locks to the new token.
        assert token_settings.api_token == new_token
        # Persisted to the TEMP secrets file (600), not the real one.
        assert secrets_path.exists()
        assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
        assert f"NETADMIN_API_TOKEN={new_token}" in secrets_path.read_text()

        # The old token no longer authorises a mutation; the new one does.
        old = await c.post(_REGEN, headers={"Authorization": f"Bearer {TOKEN}"})
        assert old.status_code == 401
        reveal = await c.get(_REVEAL, headers={"Authorization": f"Bearer {new_token}"})
        assert reveal.status_code == 200
        assert reveal.json()["token"] == new_token


@pytest.mark.asyncio
async def test_regenerate_is_rate_limited() -> None:
    # Middleware-level: regenerate shares the write-op limiter with the controller
    # writes, so a leaked token cannot churn the secret unbounded.
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN, write_max=1, write_window_s=60.0)
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    transport = httpx.ASGITransport(app=mw, client=REMOTE)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        first = await c.post(_REGEN, headers=hdr)
        second = await c.post(_REGEN, headers=hdr)
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "rate_limited"


@pytest.mark.asyncio
async def test_regenerate_open_when_no_token_configured() -> None:
    # An unconfigured/open install has no token to require; regenerate falls through
    # the open shortcut (the endpoint would mint the first token). Middleware-level.
    mw = ApiTokenAuthMiddleware(_ok_app, token=None)
    transport = httpx.ASGITransport(app=mw, client=REMOTE)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(_REGEN)
    assert resp.status_code == 200
