"""Event WebSocket: frame parsing, control-frame skipping, reconnect, SSL."""

from __future__ import annotations

import ssl
from types import SimpleNamespace

import httpx
import pytest
import respx
import websockets

from netadmin.ingest.unifi import ws as ws_module
from netadmin.ingest.unifi.client import UnifiClient
from netadmin.ingest.unifi.ws import EventListener

HOST = "https://ctrl.test"
SITE = "default"
OS_PROBE = f"{HOST}/proxy/network/"
OS_LOGIN = f"{HOST}/api/auth/login"

EVENT_FRAME = '{"meta": {"message": "events"}, "data": [{"key": "EVT_TEST", "_id": "1"}]}'
CONTROL_FRAME = '{"meta": {"message": "device:sync"}, "data": [{"mac": "02:00:00:00:00:01"}]}'


# --------------------------------------------------------------------------- #
# _parse (static, no controller)
# --------------------------------------------------------------------------- #
def test_parse_event_frame():
    events = EventListener._parse(EVENT_FRAME)
    assert len(events) == 1
    assert events[0].key == "EVT_TEST"


def test_parse_skips_control_frame():
    assert EventListener._parse(CONTROL_FRAME) == []


def test_parse_handles_bytes_and_garbage():
    assert EventListener._parse(EVENT_FRAME.encode())[0].key == "EVT_TEST"
    assert EventListener._parse("not json") == []
    assert EventListener._parse('{"data": 5}') == []


# --------------------------------------------------------------------------- #
# SSL context
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ssl_context_disables_verification_for_self_signed():
    client = UnifiClient(host=HOST, username="u", password="p", verify_ssl=False)
    listener = EventListener(client, verify_ssl=False)
    ctx = listener._ssl_context()
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
    await client.aclose()


# --------------------------------------------------------------------------- #
# Reconnect with capped backoff
# --------------------------------------------------------------------------- #
class _FakeSocket:
    def __init__(self, frames, *, raise_on_enter=None):
        self._frames = frames
        self._raise_on_enter = raise_on_enter

    async def __aenter__(self):
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for frame in self._frames:
            yield frame


@pytest.mark.asyncio
@respx.mock
async def test_reconnects_after_drop_then_yields(monkeypatch):
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(
            200,
            headers=[("X-CSRF-Token", "c"), ("set-cookie", "TOKEN=sess-abc; Path=/")],
            json={},
        )
    )

    calls = {"n": 0}
    sockets = [
        _FakeSocket([], raise_on_enter=websockets.WebSocketException("drop")),
        _FakeSocket([EVENT_FRAME]),
    ]

    def fake_connect(url, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        # Reused session material must be forwarded to the handshake.
        assert (
            "Cookie" in kwargs["additional_headers"] or "X-API-KEY" in kwargs["additional_headers"]
        )
        return sockets[min(idx, len(sockets) - 1)]

    monkeypatch.setattr(ws_module, "ws_connect", fake_connect)

    client = UnifiClient(host=HOST, username="u", password="p", verify_ssl=False)
    listener = EventListener(client, backoff_base=0.01, backoff_max=0.01)

    collected = []
    async for event in listener.events():
        collected.append(event)
        listener.stop()
        break

    assert calls["n"] >= 2  # dropped once, reconnected
    assert collected[0].key == "EVT_TEST"
    await client.aclose()


class _Handshake401(websockets.InvalidStatus):
    """A 401 handshake rejection, shaped like websockets.InvalidStatus (carries a
    ``.response.status_code``) without needing the real Response constructor."""

    def __init__(self, status: int = 401) -> None:  # noqa: D401 - trivial
        self.response = SimpleNamespace(status_code=status)


@pytest.mark.asyncio
@respx.mock
async def test_handshake_401_forces_relogin(monkeypatch):
    # The session token expires: the handshake 401s. Recovery must force a fresh
    # login (a second POST /api/auth/login), not a no-op connect() that would
    # reconnect forever with the same stale cookies.
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login_route = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(
            200,
            headers=[("X-CSRF-Token", "c"), ("set-cookie", "TOKEN=sess-abc; Path=/")],
            json={},
        )
    )

    calls = {"n": 0}
    sockets = [
        _FakeSocket([], raise_on_enter=_Handshake401(401)),  # first handshake rejected
        _FakeSocket([EVENT_FRAME]),  # succeeds after re-auth
    ]

    def fake_connect(url, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        return sockets[min(idx, len(sockets) - 1)]

    monkeypatch.setattr(ws_module, "ws_connect", fake_connect)

    client = UnifiClient(host=HOST, username="u", password="p", verify_ssl=False)
    listener = EventListener(client, backoff_base=0.01, backoff_max=0.01)

    collected = []
    async for event in listener.events():
        collected.append(event)
        listener.stop()
        break

    # Two logins: the initial connect(), then the forced re-auth after the 401.
    # A no-op connect() (the bug) would leave this at 1.
    assert login_route.call_count == 2
    assert collected[0].key == "EVT_TEST"
    await client.aclose()
