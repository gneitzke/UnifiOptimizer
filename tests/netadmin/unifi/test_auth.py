"""Auth-strategy detection, CSRF handling, and 2FA (ARCHITECTURE.md 5.1)."""

from __future__ import annotations

import httpx
import pytest
import respx

from netadmin.ingest.unifi.auth import (
    ApiKeyAuth,
    LegacyCookieAuth,
    TwoFactorRequired,
    UnifiAuthError,
    UnifiOsCookieAuth,
    resolve_strategy,
)

pytestmark = pytest.mark.asyncio

HOST = "https://ctrl.test"
SITE = "default"
HEALTH = f"{HOST}/proxy/network/api/s/{SITE}/stat/health"
OS_PROBE = f"{HOST}/proxy/network/"
OS_LOGIN = f"{HOST}/api/auth/login"
LEGACY_LOGIN = f"{HOST}/api/login"


# --------------------------------------------------------------------------- #
# Auto-detection order
# --------------------------------------------------------------------------- #
@respx.mock
async def test_api_key_preferred_and_header_attached():
    route = respx.get(HEALTH).mock(return_value=httpx.Response(200, json={"data": []}))
    async with httpx.AsyncClient() as http:
        strat = await resolve_strategy(
            http, host=HOST, site=SITE, username="u", password="p", api_key="KEY123"
        )
    assert isinstance(strat, ApiKeyAuth)
    assert route.calls.last.request.headers["X-API-KEY"] == "KEY123"


@respx.mock
async def test_api_key_rejected_falls_back_to_cookie():
    respx.get(HEALTH).mock(return_value=httpx.Response(401))
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "csrf-hdr"}, json={})
    )
    async with httpx.AsyncClient() as http:
        strat = await resolve_strategy(
            http, host=HOST, site=SITE, username="u", password="p", api_key="BAD"
        )
    assert isinstance(strat, UnifiOsCookieAuth)


@respx.mock
async def test_unifi_os_cookie_csrf_from_header():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, headers={"X-CSRF-Token": "hdr-tok"}, json={})
    )
    async with httpx.AsyncClient() as http:
        strat = await resolve_strategy(
            http, host=HOST, site=SITE, username="u", password="p", api_key=None
        )
    assert isinstance(strat, UnifiOsCookieAuth)
    # CSRF echoed on mutating verbs, absent on GET.
    assert strat.request_headers("POST")["X-CSRF-Token"] == "hdr-tok"
    assert "X-CSRF-Token" not in strat.request_headers("GET")


@respx.mock
async def test_unifi_os_cookie_csrf_from_jwt_when_header_absent(jwt_factory):
    """The CloudKey quirk: no X-CSRF-Token header, token rides in the JWT cookie."""
    jwt = jwt_factory({"csrfToken": "from-jwt"})
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(
            200, headers={"set-cookie": f"TOKEN={jwt}; Path=/; HttpOnly"}, json={}
        )
    )
    async with httpx.AsyncClient() as http:
        strat = await resolve_strategy(
            http, host=HOST, site=SITE, username="u", password="p", api_key=None
        )
    assert strat.request_headers("PUT")["X-CSRF-Token"] == "from-jwt"


@respx.mock
async def test_legacy_detection_when_no_proxy_path():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(404))
    login = respx.post(LEGACY_LOGIN).mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as http:
        strat = await resolve_strategy(
            http, host=HOST, site=SITE, username="u", password="p", api_key=None
        )
    assert isinstance(strat, LegacyCookieAuth)
    assert login.called
    # Legacy addresses endpoints without the /proxy/network prefix.
    assert strat.api_url(HOST, SITE, "stat/device") == f"{HOST}/api/s/{SITE}/stat/device"
    assert strat.ws_url(HOST, SITE) == f"wss://ctrl.test/wss/s/{SITE}/events"


@respx.mock
async def test_two_factor_required_raises():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(return_value=httpx.Response(499, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TwoFactorRequired):
            await resolve_strategy(
                http, host=HOST, site=SITE, username="u", password="p", api_key=None
            )


@respx.mock
async def test_two_factor_detected_from_body():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(
        return_value=httpx.Response(200, json={"errors": ["api.err.Ubic2faTokenRequired"]})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(TwoFactorRequired):
            await resolve_strategy(
                http, host=HOST, site=SITE, username="u", password="p", api_key=None
            )


@respx.mock
async def test_bad_credentials_raise_auth_error():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.post(OS_LOGIN).mock(return_value=httpx.Response(400, json={}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(UnifiAuthError):
            await resolve_strategy(
                http, host=HOST, site=SITE, username="u", password="bad", api_key=None
            )


async def test_no_credentials_raises():
    async with httpx.AsyncClient() as http:
        with pytest.raises(UnifiAuthError):
            await resolve_strategy(
                http, host=HOST, site=SITE, username=None, password=None, api_key=None
            )
