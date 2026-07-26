"""JWT / CSRF salvage helpers and URL shapes (sync; no controller)."""

from __future__ import annotations

import httpx

from netadmin.ingest.unifi.auth import (
    LegacyCookieAuth,
    UnifiOsCookieAuth,
    csrf_from_cookies,
    csrf_from_jwt,
    parse_jwt,
)

HOST = "https://ctrl.test"
SITE = "default"


def test_parse_jwt_roundtrip(jwt_factory):
    tok = jwt_factory({"csrfToken": "abc", "sub": "admin"})
    assert parse_jwt(tok) == {"csrfToken": "abc", "sub": "admin"}


def test_parse_jwt_rejects_garbage():
    assert parse_jwt("not-a-jwt") is None
    assert parse_jwt("") is None
    assert parse_jwt("a.b") is None  # wrong segment count


def test_csrf_from_jwt_variants(jwt_factory):
    assert csrf_from_jwt(jwt_factory({"csrfToken": "T1"})) == "T1"
    assert csrf_from_jwt(jwt_factory({"csrf": "T2"})) == "T2"
    assert csrf_from_jwt(jwt_factory({"x_csrf_thing": "T3"})) == "T3"
    assert csrf_from_jwt(jwt_factory({"nothing": "here"})) is None


def test_csrf_from_cookies_prefers_direct_then_jwt(jwt_factory):
    direct = httpx.Cookies()
    direct.set("csrf_token", "direct-token")
    assert csrf_from_cookies(direct) == "direct-token"

    viajwt = httpx.Cookies()
    viajwt.set("TOKEN", jwt_factory({"csrfToken": "jwt-token"}))
    assert csrf_from_cookies(viajwt) == "jwt-token"

    assert csrf_from_cookies(httpx.Cookies()) is None


def test_unifi_os_url_shapes():
    strat = UnifiOsCookieAuth(HOST, SITE, "u", "p")
    assert strat.api_url(HOST, SITE, "stat/device") == (
        f"{HOST}/proxy/network/api/s/{SITE}/stat/device"
    )
    assert strat.ws_url(HOST, SITE) == f"wss://ctrl.test/proxy/network/wss/s/{SITE}/events"


def test_legacy_url_shapes():
    strat = LegacyCookieAuth(HOST, SITE, "u", "p")
    assert strat.api_url(HOST, SITE, "stat/device") == f"{HOST}/api/s/{SITE}/stat/device"
    assert strat.ws_url(HOST, SITE) == f"wss://ctrl.test/wss/s/{SITE}/events"
