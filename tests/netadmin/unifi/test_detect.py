"""Fixture-driven UniFi console detection (netadmin.ingest.unifi.detect).

Every probe response is synthetic and mocked with ``respx``; no test ever
touches a live controller. Covers per-kind model classification, the login-free
version reads, and the unknown / unreachable / auth-gated degradation paths.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from netadmin.ingest.unifi.detect import (
    APIKEY_UNKNOWN,
    AUTH_API_KEY,
    AUTH_LEGACY_COOKIE,
    AUTH_NONE,
    AUTH_UNIFI_OS_COOKIE,
    KIND_CLOUDKEY_GEN2,
    KIND_CLOUDKEY_GEN2_PLUS,
    KIND_LEGACY_SOFTWARE,
    KIND_UCG,
    KIND_UDM,
    KIND_UDM_PRO,
    KIND_UDM_SE,
    KIND_UDR,
    KIND_UDW,
    KIND_UNIFI_OS_SERVER,
    KIND_UNKNOWN_UNIFI_OS,
    KIND_UNREACHABLE,
    detect_console,
    format_console_report,
)

pytestmark = pytest.mark.asyncio

HOST = "https://ctrl.test"
OS_PROBE = f"{HOST}/proxy/network/"
NET_STATUS = f"{HOST}/proxy/network/status"
SYSTEM = f"{HOST}/api/system"
LEGACY_STATUS = f"{HOST}:8443/status"


def _mock_os(*, network_version="9.0.114", system=None, probe_status=401):
    """Wire the UniFi OS probe + login-free reads. ``system`` is the /api/system body."""
    respx.get(OS_PROBE).mock(return_value=httpx.Response(probe_status))
    respx.get(NET_STATUS).mock(
        return_value=httpx.Response(200, json={"meta": {"server_version": network_version}})
    )
    if system is None:
        respx.get(SYSTEM).mock(return_value=httpx.Response(401))
    else:
        respx.get(SYSTEM).mock(return_value=httpx.Response(200, json=system))


def _sys(shortname, name):
    return {"name": name, "hardware": {"shortname": shortname, "name": name}}


# --------------------------------------------------------------------------- #
# Per-kind detection from synthetic probe responses
# --------------------------------------------------------------------------- #
@respx.mock
@pytest.mark.parametrize(
    "shortname,name,expected_kind",
    [
        ("UDMPRO", "UniFi Dream Machine Pro", KIND_UDM_PRO),
        ("UDMPROSE", "UniFi Dream Machine SE", KIND_UDM_SE),
        ("UDM", "UniFi Dream Machine", KIND_UDM),
        ("UDR", "UniFi Dream Router", KIND_UDR),
        ("UDW", "UniFi Dream Wall", KIND_UDW),
        ("UCG-Ultra", "UniFi Cloud Gateway Ultra", KIND_UCG),
        ("UCK-G2-Plus", "UniFi CloudKey Gen2 Plus", KIND_CLOUDKEY_GEN2_PLUS),
        ("UCK-G2", "UniFi CloudKey Gen2", KIND_CLOUDKEY_GEN2),
        ("UniFiOSServer", "UniFi OS Server", KIND_UNIFI_OS_SERVER),
    ],
)
async def test_detect_unifi_os_per_kind(shortname, name, expected_kind):
    _mock_os(system=_sys(shortname, name))
    info = await detect_console(HOST)
    assert info.kind == expected_kind
    assert info.is_unifi_os is True
    assert info.model == name
    assert info.network_version == "9.0.114"
    assert info.api_key_supported is True
    assert info.recommended_auth == AUTH_API_KEY
    assert info.reachable is True


@respx.mock
async def test_detect_unifi_os_below_9_recommends_cookie():
    _mock_os(network_version="8.6.9", system=_sys("UDMPRO", "UniFi Dream Machine Pro"))
    info = await detect_console(HOST)
    assert info.kind == KIND_UDM_PRO
    assert info.api_key_supported is False
    assert info.recommended_auth == AUTH_UNIFI_OS_COOKIE


@respx.mock
async def test_detect_unifi_os_model_gated_by_auth_is_unknown():
    # /api/system needs auth (401) -> model unreadable, but OS + version still known.
    _mock_os(network_version="9.1.0", system=None)
    info = await detect_console(HOST)
    assert info.kind == KIND_UNKNOWN_UNIFI_OS
    assert info.model is None
    assert info.is_unifi_os is True
    assert info.network_version == "9.1.0"
    assert info.api_key_supported is True
    assert info.recommended_auth == AUTH_API_KEY
    assert info.detail and "without authentication" in info.detail


@respx.mock
async def test_detect_unifi_os_unrecognized_model_is_unknown():
    _mock_os(system=_sys("UXG-Future", "UniFi Next-Gen Thing"))
    info = await detect_console(HOST)
    assert info.kind == KIND_UNKNOWN_UNIFI_OS
    assert info.model is None  # never report a model it could not positively map
    assert info.detail and "not recognized" in info.detail


@respx.mock
async def test_detect_unifi_os_version_unreadable_recommends_api_key():
    # OS confirmed and model read, but the Network status read needs auth (401) so
    # the version is unknown. API keys can't be *confirmed* (api_key_supported False),
    # but the API-key path is still the correct modern route on a UniFi OS console,
    # so it is recommended with an honest 'unknown' status rather than downgraded to
    # cookie auth. (Real-world: a CloudKey Gen2+ whose /proxy/network/status is 401.)
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.get(NET_STATUS).mock(return_value=httpx.Response(401))
    respx.get(SYSTEM).mock(
        return_value=httpx.Response(200, json=_sys("UDMPRO", "UniFi Dream Machine Pro"))
    )
    info = await detect_console(HOST)
    assert info.kind == KIND_UDM_PRO
    assert info.network_version is None
    assert info.api_key_supported is False
    assert info.api_key_status == APIKEY_UNKNOWN
    assert info.recommended_auth == AUTH_API_KEY


# --------------------------------------------------------------------------- #
# Real-controller replay (sanitized) — CloudKey Gen2 Plus at 192.0.2.10 (TEST-NET, sanitized)
# --------------------------------------------------------------------------- #
# Captured from a live, read-only `netadmin detect` probe of the production
# CloudKey (MAC / GUIDs scrubbed). Pins the real-world case the conservative
# version-gating originally got wrong: /proxy/network/status is 401 (the Network
# version is not readable without a session), but /api/system serves the UCKP
# hardware model login-free. Detection must still name the console AND recommend
# the API-key path (the console does support API keys), not downgrade to cookie.
REAL_UCKP_SYSTEM = {
    "hardware": {"shortname": "UCKP"},
    "name": "Cloud-Key-Gen2-Plus",
    "mac": "AABBCCDDEEFF",
    "directConnectDomain": None,
    "deviceState": "setup",
    "deviceErrorCode": None,
    "uidb": {
        "guid": "00000000-0000-0000-0000-000000000000",
        "id": "00000000-0000-0000-0000-000000000000",
        "images": {"default": "0" * 32, "nopadding": "0" * 32, "topology": "0" * 32},
    },
    "debugEnabled": False,
    "cloudConnected": True,
    "isOrHasGatewayInLAN": False,
    "remoteAccessEnabled": True,
    "hasInternet": True,
    "isSingleUser": False,
    "isSsoEnabled": True,
}


@respx.mock
async def test_detect_real_cloudkey_gen2_plus_recommends_api_key():
    host = "https://192.0.2.10:8443"
    respx.get(f"{host}/proxy/network/").mock(
        return_value=httpx.Response(401, json={"error": {"code": 401, "message": "Unauthorized"}})
    )
    respx.get(f"{host}/proxy/network/status").mock(
        return_value=httpx.Response(401, json={"error": {"code": 401, "message": "Unauthorized"}})
    )
    respx.get(f"{host}/api/system").mock(return_value=httpx.Response(200, json=REAL_UCKP_SYSTEM))

    info = await detect_console(host)
    assert info.kind == KIND_CLOUDKEY_GEN2_PLUS
    assert info.is_unifi_os is True
    assert info.model == "UCKP"
    assert info.network_version is None  # not readable login-free
    assert info.api_key_supported is False  # cannot be *confirmed* without a version
    assert info.api_key_status == APIKEY_UNKNOWN
    assert info.recommended_auth == AUTH_API_KEY  # but API key is still the right path

    report = format_console_report(info, host)
    assert "CloudKey Gen2 Plus" in report
    assert "Create the API key:" in report
    assert "Control Plane -> Integrations" in report
    assert "UNIFI_API_KEY=<paste-your-api-key>" in report
    assert "UNIFI_USERNAME" not in report  # not the cookie path


# --------------------------------------------------------------------------- #
# Legacy software controller
# --------------------------------------------------------------------------- #
@respx.mock
async def test_detect_legacy_software_controller():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(404))
    respx.get(LEGACY_STATUS).mock(
        return_value=httpx.Response(200, json={"meta": {"server_version": "7.5.176", "up": True}})
    )
    info = await detect_console(HOST)
    assert info.kind == KIND_LEGACY_SOFTWARE
    assert info.is_unifi_os is False
    assert info.network_version == "7.5.176"
    assert info.api_key_supported is False
    assert info.recommended_auth == AUTH_LEGACY_COOKIE


@respx.mock
async def test_detect_legacy_software_9x_supports_api_key():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(404))
    respx.get(LEGACY_STATUS).mock(
        return_value=httpx.Response(200, json={"meta": {"server_version": "9.0.0"}})
    )
    info = await detect_console(HOST)
    assert info.kind == KIND_LEGACY_SOFTWARE
    assert info.api_key_supported is True
    assert info.recommended_auth == AUTH_API_KEY


@respx.mock
async def test_detect_legacy_on_explicit_8443_host():
    host = "https://ctrl.test:8443"
    respx.get(f"{host}/proxy/network/").mock(return_value=httpx.Response(404))
    respx.get(f"{host}/status").mock(
        return_value=httpx.Response(200, json={"meta": {"server_version": "7.4.162"}})
    )
    info = await detect_console(host)
    assert info.kind == KIND_LEGACY_SOFTWARE
    assert info.network_version == "7.4.162"


# --------------------------------------------------------------------------- #
# Unreachable / degradation
# --------------------------------------------------------------------------- #
@respx.mock
async def test_detect_unreachable_when_nothing_answers():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(404))
    respx.get(LEGACY_STATUS).mock(side_effect=httpx.ConnectError("refused"))
    info = await detect_console(HOST)
    assert info.kind == KIND_UNREACHABLE
    assert info.reachable is False
    assert info.recommended_auth == AUTH_NONE
    assert info.api_key_supported is False


@respx.mock
async def test_detect_never_crashes_on_transport_error():
    respx.get(OS_PROBE).mock(side_effect=httpx.ConnectTimeout("timeout"))
    respx.get(LEGACY_STATUS).mock(side_effect=httpx.ConnectTimeout("timeout"))
    info = await detect_console(HOST)
    assert info.kind == KIND_UNREACHABLE


async def test_detect_blank_host_raises():
    with pytest.raises(ValueError):
        await detect_console("   ")


@respx.mock
async def test_detect_adds_https_scheme_when_missing():
    respx.get(OS_PROBE).mock(return_value=httpx.Response(401))
    respx.get(NET_STATUS).mock(
        return_value=httpx.Response(200, json={"meta": {"server_version": "9.0.0"}})
    )
    respx.get(SYSTEM).mock(return_value=httpx.Response(401))
    info = await detect_console("ctrl.test")  # no scheme
    assert info.is_unifi_os is True
    assert info.kind == KIND_UNKNOWN_UNIFI_OS
