"""UnifiClient transport behavior: retries, single re-login, pacing, envelope."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx

from netadmin.ingest.unifi.auth import (
    UnifiAmbiguousOutcomeError,
    UnifiAuthCooldownError,
    UnifiError,
)
from netadmin.ingest.unifi.client import UnifiClient

pytestmark = pytest.mark.asyncio

HOST = "https://ctrl.test"
SITE = "default"
OS_PROBE = f"{HOST}/proxy/network/"
OS_LOGIN = f"{HOST}/api/auth/login"
DEVICE = f"{HOST}/proxy/network/api/s/{SITE}/stat/device"
HEALTH = f"{HOST}/proxy/network/api/s/{SITE}/stat/health"


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
async def test_concurrent_auth_429_shares_retry_after_cooldown():
    """One rejected login establishes a shared cooldown for the whole client."""
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "60"}, json={})
    )
    client = _client()

    results = await asyncio.gather(*[client.connect() for _ in range(4)], return_exceptions=True)

    assert login.call_count == 1
    assert all(isinstance(exc, UnifiAuthCooldownError) for exc in results)
    assert all(0 < exc.retry_after <= 60 for exc in results)

    with pytest.raises(UnifiAuthCooldownError) as caught:
        await client.connect()
    assert caught.value.retry_after > 0
    assert login.call_count == 1  # subsequent caller fails before another request
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
async def test_mutation_401_is_not_replayed_and_is_ambiguous():
    # C2: a 401 on a MUTATION must NEVER be re-dispatched. Re-logging in and
    # retrying the write could fire it a second time (the session could have been
    # invalidated only after the controller accepted the change). Only a GET is
    # re-dispatched after a re-login; a non-GET 401 surfaces as an ambiguous outcome
    # with exactly ONE dispatch and no re-login.
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )
    put = respx.put(f"{HOST}/proxy/network/api/s/{SITE}/rest/device/abc").mock(
        return_value=httpx.Response(401)
    )
    client = _client()
    with pytest.raises(UnifiAmbiguousOutcomeError):
        await client.request("PUT", "rest/device/abc", json_body={"x": 1}, allow_mutation=True)
    assert put.call_count == 1  # exactly ONE dispatch -- the write is never replayed
    assert login.call_count == 1  # initial connect only; no re-login for a mutation 401
    await client.aclose()


# --------------------------------------------------------------------------- #
# #w12a-1: a mutation 401 must classify by its ENVELOPE, not blanket-ambiguous.
# A 401 carrying a PARSED controller rejection (meta.rc=error) is a DEFINITIVE
# rejection the controller confirmed -- it must surface as such (the response is
# returned for the writer's normal classification), NOT laundered into ambiguous,
# and STILL never re-dispatched (single dispatch, no re-login). Only a 401 with no
# parseable rejection envelope stays ambiguous. Both PUT and POST reproduce.
# --------------------------------------------------------------------------- #
@respx.mock
@pytest.mark.parametrize("verb", ["PUT", "POST"])
async def test_mutation_401_with_parsed_rejection_is_definitive_single_dispatch(verb):
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )
    url = f"{HOST}/proxy/network/api/s/{SITE}/rest/device/abc"
    rejection = {"meta": {"rc": "error", "msg": "api.err.LoginRequired"}, "data": []}
    route = getattr(respx, verb.lower())(url).mock(
        return_value=httpx.Response(401, json=rejection)
    )
    client = _client()
    # A DEFINITIVE rejection: request() does NOT raise ambiguous -- it returns the
    # 401 response so the writer's envelope classification reports a definitive
    # ok=False rejection (the applier then resolves it as a clean 'failed', leaving
    # no unresolved mutation blocking later work).
    resp = await client.request(verb, "rest/device/abc", json_body={"x": 1}, allow_mutation=True)
    assert resp.status_code == 401
    from netadmin.ingest.unifi.client import envelope_error

    assert envelope_error(resp.json()) is not None  # the writer will see the rejection
    assert route.call_count == 1  # single dispatch -- the write is never replayed
    assert login.call_count == 1  # no re-login on a mutation 401
    await client.aclose()


@respx.mock
@pytest.mark.parametrize("verb", ["PUT", "POST"])
async def test_mutation_401_unparseable_stays_ambiguous_single_dispatch(verb):
    # No parseable rejection envelope (an HTML/junk 401): the session was dropped and
    # the write may or may not have landed -- genuinely AMBIGUOUS, single dispatch.
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )
    url = f"{HOST}/proxy/network/api/s/{SITE}/rest/device/abc"
    route = getattr(respx, verb.lower())(url).mock(
        return_value=httpx.Response(401, text="<html>session lost</html>")
    )
    client = _client()
    with pytest.raises(UnifiAmbiguousOutcomeError):
        await client.request(verb, "rest/device/abc", json_body={"x": 1}, allow_mutation=True)
    assert route.call_count == 1  # single dispatch -- never replayed
    assert login.call_count == 1  # no re-login on a mutation 401
    await client.aclose()


@respx.mock
async def test_get_401_still_relogs_in_and_retries():
    # The GET path is unchanged: a 401 re-logs in once and re-dispatches (idempotent).
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    login = respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "c"}, json={})
    )
    device = respx.get(DEVICE).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"data": [{"ok": 1}]})]
    )
    client = _client()
    assert await client.get_data("stat/device") == [{"ok": 1}]
    assert device.call_count == 2  # re-dispatched once after re-login
    assert login.call_count == 2
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
    # #w12a-2: a well-formed read is a ``data`` field that is an ACTUAL LIST. A
    # single dict body (``{"data": {...}}``) is NOT a list, so it is no longer a
    # valid read -- it must raise, not be silently wrapped into one row. Likewise a
    # success envelope with NO ``data`` (``{"meta":{"rc":"ok"}}``) is not a read of
    # zero rows: rc=ok without a real data list is not a valid read and must raise.
    _mock_login()
    client = _client()
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"data": {"single": 1}}))
    with pytest.raises(UnifiError, match="not a list"):
        await client.get_data("stat/device")

    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"meta": {"rc": "ok"}}))
    with pytest.raises(UnifiError, match="well-formed data list"):
        await client.get_data("stat/device")

    # A genuine empty-but-successful read still succeeds: a real (empty) list.
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []}))
    assert await client.get_data("stat/device") == []
    await client.aclose()


# --------------------------------------------------------------------------- #
# #w12a-2: a malformed successful-LOOKING read must FAIL (raise UnifiError), so a
# caller like event catch-up records a failed hole rather than crediting 1.0
# 'complete' coverage over a window it never actually read. A read is well-formed
# ONLY with an actual ``data`` list AND (if meta present) meta.rc == "ok".
# --------------------------------------------------------------------------- #
@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"meta": {"rc": "ok"}},                       # rc=ok but NO data list
        {"rc": "ok", "data": None},                   # data present but null
        {"rc": "ok", "data": False},                  # data present but false
        {"meta": {"rc": "pending"}, "data": []},      # a real list but meta.rc != ok
    ],
    ids=["rc-ok-no-data", "data-null", "data-false", "rc-pending"],
)
async def test_malformed_successful_looking_read_raises(body):
    _mock_login()
    client = _client()
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(UnifiError):
        await client.get_data("stat/device")
    await client.aclose()


@respx.mock
async def test_genuine_empty_list_read_is_complete():
    # The control: a real ``{"meta":{"rc":"ok"},"data":[]}`` (and a bare
    # ``{"data":[...]}``) still read as a completed, empty-or-populated result.
    _mock_login()
    client = _client()
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []}))
    assert await client.get_data("stat/device") == []
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"data": [{"ok": 1}]}))
    assert await client.get_data("stat/device") == [{"ok": 1}]
    await client.aclose()


@respx.mock
async def test_csrf_echoed_on_mutation():
    # GET-only contract (S1): the collector never POSTs, so CSRF echoing is now
    # exercised on the one permitted non-GET path -- an approved mutation
    # (allow_mutation=True). UniFi OS requires X-CSRF-Token on every mutating verb.
    _mock_login(csrf="echo-me")
    cmd = respx.post(f"{HOST}/proxy/network/api/s/{SITE}/cmd/devmgr").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    )
    client = _client()
    await client.request("POST", "cmd/devmgr", json_body={"cmd": "x"}, allow_mutation=True)
    assert cmd.calls.last.request.headers["X-CSRF-Token"] == "echo-me"
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


# --------------------------------------------------------------------------- #
# S1 -- GET-only contract enforced at the collector transport boundary.
# The collector may issue ONLY idempotent GETs; the sole permitted non-GET is an
# approved mutation (allow_mutation=True), which exactly one object sets -- the
# fix writer. A non-GET without that flag is refused before a socket opens.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_request_refuses_non_get_without_allow_mutation():
    _mock_login()
    post_route = respx.post(DEVICE).mock(return_value=httpx.Response(200, json={"data": []}))
    put_route = respx.put(DEVICE).mock(return_value=httpx.Response(200, json={"data": []}))
    client = _client()
    await client.connect()

    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(UnifiError, match="GET-only contract"):
            await client.request(verb, "stat/device", json_body={"x": 1})
    # Nothing was ever dispatched: the refusal happens before the socket.
    assert not post_route.called and not put_route.called
    await client.aclose()


@respx.mock
async def test_request_allows_get_and_approved_mutation():
    # The escape hatch works for the writer: allow_mutation=True lets a POST through.
    _mock_login()
    respx.get(DEVICE).mock(return_value=httpx.Response(200, json={"data": []}))
    cmd = respx.post(f"{HOST}/proxy/network/api/s/{SITE}/cmd/devmgr").mock(
        return_value=httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    )
    client = _client()
    assert await client.get_data("stat/device") == []
    resp = await client.request("POST", "cmd/devmgr", json_body={"cmd": "x"}, allow_mutation=True)
    assert resp.status_code == 200 and cmd.called
    await client.aclose()


# --------------------------------------------------------------------------- #
# C2 -- mutations are never auto-retried on an ambiguous (lost-response)
# transport failure. One lost POST must produce exactly ONE dispatch, not four.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_mutation_not_retried_on_ambiguous_transport_error():
    _mock_login()
    route = respx.post(f"{HOST}/proxy/network/api/s/{SITE}/cmd/devmgr").mock(
        side_effect=httpx.ReadTimeout("lost response")
    )
    client = _client(max_retries=3)
    with pytest.raises(UnifiAmbiguousOutcomeError, match="outcome unknown"):
        await client.request("POST", "cmd/devmgr", json_body={"cmd": "x"}, allow_mutation=True)
    assert route.call_count == 1  # exactly one dispatch -- no 4x fan-out
    await client.aclose()


@respx.mock
async def test_d6_mutation_post_send_read_error_is_ambiguous_single_dispatch():
    # Verifier round 9, D6: an httpx.ReadError (socket fault AFTER the request was
    # sent) on a MUTATION means the outcome is UNKNOWN -- the write may have landed.
    # It must surface as UnifiAmbiguousOutcomeError with exactly ONE dispatch, never
    # leak out as an unhandled/failed exception and never be retried.
    _mock_login()
    route = respx.put(f"{HOST}/proxy/network/api/s/{SITE}/rest/device/abc").mock(
        side_effect=httpx.ReadError("connection reset after send")
    )
    client = _client(max_retries=3)
    with pytest.raises(UnifiAmbiguousOutcomeError, match="outcome unknown"):
        await client.request("PUT", "rest/device/abc", json_body={"x": 1}, allow_mutation=True)
    assert route.call_count == 1  # exactly one dispatch -- never replayed
    await client.aclose()


@respx.mock
async def test_d6_get_read_error_is_still_retried():
    # Symmetric: a ReadError on an idempotent GET is safe to retry (as for ReadTimeout).
    _mock_login()
    route = respx.get(DEVICE).mock(
        side_effect=[
            httpx.ReadError("boom"),
            httpx.Response(200, json={"data": [{"ok": 1}]}),
        ]
    )
    client = _client(max_retries=3)
    assert await client.get_data("stat/device") == [{"ok": 1}]
    assert route.call_count == 2
    await client.aclose()


# --------------------------------------------------------------------------- #
# #w10a-1 -- a post-send RESPONSE-DECODING failure on a MUTATION is AMBIGUOUS.
# The controller answered HTTP 200 (the request was fully sent), but the body is
# undecodable (corrupt gzip / truncated stream). httpx raises httpx.DecodingError
# while READING the body -- a RequestError that is NOT a TransportError and so is
# absent from _RETRYABLE_EXC. The write may have landed, so it must surface as an
# ambiguous outcome (exactly ONE dispatch, never a clean failure), not leak out as
# an unhandled DecodingError the applier records as "failed".
# --------------------------------------------------------------------------- #
@respx.mock
async def test_w10a1_mutation_response_decode_error_is_ambiguous_single_dispatch():
    _mock_login()
    # httpx raises DecodingError while READING/decoding the response body (corrupt
    # gzip) -- AFTER the PUT was fully sent and the controller answered. Simulated
    # exactly like the D6 ReadError tests, via a side_effect the transport raises.
    route = respx.put(f"{HOST}/proxy/network/api/s/{SITE}/rest/device/abc").mock(
        side_effect=httpx.DecodingError("Error -3 while decompressing data")
    )
    client = _client(max_retries=3)
    with pytest.raises(UnifiAmbiguousOutcomeError, match="read/decoded"):
        await client.request("PUT", "rest/device/abc", json_body={"x": 1}, allow_mutation=True)
    assert route.call_count == 1  # exactly one dispatch -- never replayed
    await client.aclose()


@respx.mock
async def test_w10a1_get_response_decode_error_keeps_existing_behavior():
    # Symmetric guard: a decode failure on an idempotent GET keeps its EXISTING
    # behavior -- it propagates as httpx.DecodingError (not silently swallowed as an
    # ambiguous mutation outcome). GET reads are unaffected by the mutation-only rule.
    _mock_login()
    route = respx.get(DEVICE).mock(
        side_effect=httpx.DecodingError("Error -3 while decompressing data")
    )
    client = _client(max_retries=3)
    with pytest.raises(httpx.DecodingError):
        await client.get_data("stat/device")
    assert route.call_count == 1  # a decode error is not a retryable transport error
    await client.aclose()


@respx.mock
async def test_get_still_retried_on_transport_error():
    # The C2 fix must not break GET retries: a GET recovers after transient errors.
    _mock_login()
    route = respx.get(DEVICE).mock(
        side_effect=[
            httpx.ReadTimeout("boom"),
            httpx.ReadTimeout("boom"),
            httpx.Response(200, json={"data": [{"ok": 1}]}),
        ]
    )
    client = _client(max_retries=3)
    assert await client.get_data("stat/device") == [{"ok": 1}]
    assert route.call_count == 3
    await client.aclose()


# --------------------------------------------------------------------------- #
# S4 -- the controller credential (X-API-KEY) is never forwarded across a
# cross-origin redirect. follow_redirects=False keeps the secret on-controller.
# --------------------------------------------------------------------------- #
@respx.mock
async def test_api_key_not_forwarded_on_cross_origin_redirect():
    respx.get(HEALTH).mock(return_value=httpx.Response(200, json={"data": []}))  # key verify
    device = respx.get(DEVICE).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.test/steal"})
    )
    evil = respx.get("https://evil.test/steal").mock(return_value=httpx.Response(200, json={}))

    client = _client(api_key="KEY123")
    await client.connect()
    resp = await client.request("GET", "stat/device")

    assert resp.status_code == 302  # redirect surfaced, not chased
    assert not evil.called  # the other origin was never contacted
    # The API key rode the on-controller request, and only that one.
    assert device.calls.last.request.headers.get("X-API-KEY") == "KEY123"
    await client.aclose()


# --------------------------------------------------------------------------- #
# R1 -- an explicit error envelope is a failure even on HTTP 200 (read side).
# --------------------------------------------------------------------------- #
@respx.mock
async def test_error_envelope_fails_despite_http_200():
    _mock_login()
    respx.get(DEVICE).mock(
        return_value=httpx.Response(
            200, json={"meta": {"rc": "error", "msg": "api.err.InvalidObject"}, "data": []}
        )
    )
    client = _client()
    with pytest.raises(UnifiError, match="api.err.InvalidObject"):
        await client.get_data("stat/device")  # must NOT read as an empty-but-ok result
    await client.aclose()
