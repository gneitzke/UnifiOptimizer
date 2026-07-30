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


# --------------------------------------------------------------------------- #
# No self-inflicted DoS: a controller that accepts the handshake then closes the
# events subscription with zero frames must NOT be reconnected at a fixed rate.
# This is the 46-hour once-per-second storm regression; the backoff must grow.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@respx.mock
async def test_accept_then_empty_close_backs_off_instead_of_storming(monkeypatch):
    """The reconnect rate is bounded even when every connect yields no events.

    Reproduces the live incident: the handshake succeeds (no 401), the socket
    closes immediately with zero frames, and the loop reconnects. If the backoff
    resets on connect (the bug) the sleeps stay at the base forever -- a DoS on
    the controller. They must grow exponentially instead.
    """
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(
            200,
            headers=[("X-CSRF-Token", "c"), ("set-cookie", "TOKEN=sess-abc; Path=/")],
            json={},
        )
    )

    # Every connection: accepted, zero frames, clean close (the pathological case).
    def fake_connect(url, **kwargs):
        return _FakeSocket([])

    monkeypatch.setattr(ws_module, "ws_connect", fake_connect)

    # Capture every backoff sleep without actually waiting, and stop after enough
    # reconnects to see the curve.
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 6:
            listener.stop()

    monkeypatch.setattr(ws_module.asyncio, "sleep", fake_sleep)

    client = UnifiClient(host=HOST, username="u", password="p", verify_ssl=False)
    listener = EventListener(client, backoff_base=1.0, backoff_max=60.0, empty_reauth_threshold=100)

    async for _ in listener.events():  # pragma: no cover - no events ever arrive
        break

    # The proof: consecutive empty connects grow the sleep (1, 2, 4, 8, ...),
    # they are strictly increasing until the cap, and none is stuck at the base.
    assert sleeps[:4] == [1.0, 2.0, 4.0, 8.0], sleeps
    assert all(b <= 60.0 for b in sleeps)
    assert sleeps[-1] > sleeps[0]  # never a flat once-per-base storm
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_repeated_empty_close_forces_reauth(monkeypatch):
    """A clean close never 401s, so after N empty connects a fresh login fires.

    This is how the loop recovers from a stale session the controller accepts at
    the HTTP layer but rejects for events -- the exact state the live daemon was
    stuck in, unable to re-auth because nothing ever returned 401/403.
    """
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login_route = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(
            200,
            headers=[("X-CSRF-Token", "c"), ("set-cookie", "TOKEN=sess-abc; Path=/")],
            json={},
        )
    )

    calls = {"n": 0}

    def fake_connect(url, **kwargs):
        calls["n"] += 1
        # First three connects: accepted, empty, closed. Fourth (post-reauth): real event.
        if calls["n"] <= 3:
            return _FakeSocket([])
        return _FakeSocket([EVENT_FRAME])

    monkeypatch.setattr(ws_module, "ws_connect", fake_connect)

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(ws_module.asyncio, "sleep", fake_sleep)

    client = UnifiClient(host=HOST, username="u", password="p", verify_ssl=False)
    listener = EventListener(client, backoff_base=0.01, empty_reauth_threshold=3)

    collected = []
    async for event in listener.events():
        collected.append(event)
        listener.stop()
        break

    # Initial login + one forced relogin after 3 empty connects (the bug would
    # never relogin on a clean close, looping forever with stale cookies).
    assert login_route.call_count == 2
    assert collected[0].key == "EVT_TEST"
    await client.aclose()
