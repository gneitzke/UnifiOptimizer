"""UnifiClient transport behavior: retries, single re-login, pacing, envelope."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx

from netadmin.ingest.unifi.auth import UnifiError
from netadmin.ingest.unifi.client import UnifiClient

pytestmark = pytest.mark.asyncio

HOST = "https://ctrl.test"
SITE = "default"
OS_PROBE = f"{HOST}/proxy/network/"
OS_LOGIN = f"{HOST}/api/auth/login"
DEVICE = f"{HOST}/proxy/network/api/s/{SITE}/stat/device"


def _client(**kw) -> UnifiClient:
    params = dict(
        host=HOST,
        site=SITE,
        username="u",
        password="p",
        verify_ssl=False,
        backoff_base=0.01,
        backoff_max=0.02,
        min_request_interval=0.0,
    )
    params.update(kw)
    return UnifiClient(**params)


def _mock_login(csrf: str = "csrf") -> None:
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": csrf}, json={})
    )


@respx.mock
async def test_connect_is_idempotent():
    _mock_login()
    login = respx.routes[1]
    client = _client()
    s1 = await client.connect()
    s2 = await client.connect()
    assert s1 is s2
    assert login.call_count == 1  # second connect does not re-login
    await client.aclose()


@respx.mock
async def test_retries_on_5xx_then_succeeds():
    _mock_login()
    route = respx.get(DEVICE).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"data": [{"mac": "02:00:00:00:00:01"}]}),
        ]
    )
    client = _client(max_retries=3)
    data = await client.get_data("stat/device")
    assert route.call_count == 3
    assert data == [{"mac": "02:00:00:00:00:01"}]
    await client.aclose()


@respx.mock
async def test_retries_exhausted_raises():
    _mock_login()
    respx.get(DEVICE).mock(return_value=httpx.Response(503))
    client = _client(max_retries=2)
    with pytest.raises(UnifiError):
        await client.get_data("stat/device")
    await client.aclose()


@respx.mock
async def test_connect_error_is_retried():
    _mock_login()
    route = respx.get(DEVICE).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"data": []}),
        ]
    )
    client = _client(max_retries=3)
    assert await client.get_data("stat/device") == []
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_single_relogin_on_401_then_succeeds():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )
    device = respx.get(DEVICE).mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"data": [{"ok": 1}]}),
        ]
    )
    client = _client()
    data = await client.get_data("stat/device")
    assert data == [{"ok": 1}]
    assert device.call_count == 2
    assert login.call_count == 2  # initial login + exactly one re-login
    await client.aclose()


@respx.mock
async def test_concurrent_401s_collapse_to_one_relogin():
    # A burst of concurrent callers all authenticated under the same login epoch
    # must produce exactly ONE re-login, not one per caller -- the CloudKey
    # rate-limits logins hard. Drive it deterministically: five concurrent
    # _relogin() calls all observing epoch 1.
    _mock_login()
    login = respx.routes[1]
    client = _client()
    await client.connect()  # initial login -> epoch 1, one login call
    assert login.call_count == 1
    epoch = client._login_epoch

    await asyncio.gather(*[client._relogin(epoch) for _ in range(5)])

    assert client._login_epoch == epoch + 1  # exactly one epoch advance
    assert login.call_count == 2  # initial + exactly one re-login for the burst
    await client.aclose()


@respx.mock
async def test_relogin_without_epoch_always_reauths():
    # The WS listener's explicit relogin() passes no epoch and must always force a
    # fresh login even while the strategy still looks authenticated.
    _mock_login()
    login = respx.routes[1]
    client = _client()
    await client.connect()
    assert login.call_count == 1
    await client.relogin()
    assert login.call_count == 2
    await client.aclose()


@respx.mock
async def test_persistent_401_fails_after_one_relogin():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )
    respx.get(DEVICE).mock(return_value=httpx.Response(401))
    client = _client()
    with pytest.raises(UnifiError):
        await client.get_data("stat/device")
    assert login.call_count == 2  # one re-login attempt, then give up
    await client.aclose()


@respx.mock
async def test_gentle_pacing_spaces_requests():
    _mock_login()
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"data": []}))
    client = _client(min_request_interval=0.05)
    await client.connect()
    start = time.monotonic()
    for _ in range(3):
        await client.get_data("stat/device")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.08  # ~0.05s between the 3 GETs
    await client.aclose()


@respx.mock
async def test_envelope_unwrapping():
    _mock_login()
    client = _client()
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"data": {"single": 1}}))
    assert await client.get_data("stat/device") == [{"single": 1}]

    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"meta": {"rc": "ok"}}))
    assert await client.get_data("stat/device") == []
    await client.aclose()


@respx.mock
async def test_csrf_echoed_on_post():
    _mock_login(csrf="echo-me")
    report = respx.post(f"{HOST}/proxy/network/api/s/{SITE}/stat/report/hourly.ap").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = _client()
    await client.post_data("stat/report/hourly.ap", {"attrs": ["time"]})
    assert report.calls.last.request.headers["X-CSRF-Token"] == "echo-me"
    await client.aclose()


# --------------------------------------------------------------------------- #
# ws_strategy: the events WebSocket needs a COOKIE session, never the API key
# (UniFi accepts an API-key WS handshake then closes 1000 with no frames -- the
# root cause of a two-day event-ingestion outage). Gitea #57.
# --------------------------------------------------------------------------- #


@respx.mock
async def test_ws_strategy_uses_cookie_even_when_rest_uses_api_key():
    """REST authenticates by API key, but the WS handshake must carry a cookie.

    The controller closes an API-key events socket immediately, so a client with
    an API key AND a username/password logs in for a cookie session and hands the
    WS the Cookie header, not X-API-KEY.
    """
    respx.get(f"{HOST}/proxy/network/api/s/{SITE}/stat/health").mock(
        return_value=httpx.Response(200, json={"data": []})
    )  # API-key verification
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(
            200,
            headers=[("X-CSRF-Token", "csrf"), ("set-cookie", "TOKEN=sess-abc; Path=/")],
            json={},
        )
    )  # the cookie login the WS strategy must perform

    client = _client(api_key="KEY123")
    rest = await client.connect()
    assert type(rest).__name__ == "ApiKeyAuth"  # REST prefers the key

    ws = await client.ws_strategy()
    assert type(ws).__name__ in ("UnifiOsCookieAuth", "LegacyCookieAuth")
    headers = ws.ws_headers(client.ws_cookies)
    assert "Cookie" in headers and "X-API-KEY" not in headers
    # Isolation (Gitea #57 follow-up): the WS cookie session must NOT land on the
    # REST client, or its CSRF/TOKEN state 403s the older cookie-checked REST
    # endpoints while the API key still works elsewhere.
    assert "TOKEN" not in client.http.cookies
    assert "TOKEN" in client.ws_cookies
    await client.aclose()


@respx.mock
async def test_ws_strategy_reuses_rest_cookie_session():
    """When REST is already cookie-based, the WS shares that one session."""
    _mock_login()
    client = _client()  # username/password only -> cookie REST
    rest = await client.connect()
    ws = await client.ws_strategy()
    assert ws is rest  # one session serves both
    await client.aclose()


@respx.mock
async def test_ws_strategy_api_key_only_degrades_with_guidance():
    """An API key with no username/password cannot subscribe to events.

    It must raise clear guidance so the listener stops cleanly (history and
    detection keep working) rather than looping against a socket that can never
    authenticate.
    """
    from netadmin.ingest.unifi.auth import UnifiAuthError

    respx.get(f"{HOST}/proxy/network/api/s/{SITE}/stat/health").mock(
        return_value=httpx.Response(200, json={"data": []})
    )  # API-key verification
    client = _client(username=None, password=None, api_key="KEY123")
    await client.connect()
    with pytest.raises(UnifiAuthError, match="username and password"):
        await client.ws_strategy()
    await client.aclose()
