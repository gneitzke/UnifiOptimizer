"""Generic UniFi console detection (read-only, login-free).

``detect_console(host)`` fingerprints whatever answers at ``host`` and returns a
:class:`ConsoleInfo` describing the console kind, its model (only when it can be
*read*, never guessed), whether X-API-KEY auth is available, and the auth
strategy to use. It spends **zero logins**: every probe is a GET or a documented
login-free read, so it is safe against a rate-limited CloudKey.

Two signals do the work:

* The UniFi OS discriminator from :mod:`netadmin.ingest.unifi.auth`
  (:func:`_is_unifi_os`): a UniFi OS console answers ``/proxy/network/``; a
  legacy self-hosted controller does not.
* Login-free status/system reads:

  - UniFi OS: ``GET /proxy/network/status`` exposes the Network application
    ``server_version`` without auth (it mirrors the legacy ``/status``), and
    ``GET /api/system`` exposes the console hardware model *when the firmware
    serves it pre-auth*. Many consoles gate ``/api/system`` behind a session, so
    the model may be unreadable — in which case the kind degrades to
    ``unknown_unifi_os`` rather than being guessed.
  - Legacy software controller: ``GET :8443/status`` returns
    ``{"meta": {"server_version": ...}}`` login-free.

The :data:`PLAYBOOK` dict — keyed by console kind — is the single source of truth
for the device-specific "how to create/find the API key" (or, where API keys are
not supported, the local-admin cookie path) steps. Both the ``netadmin detect``
CLI and the docs generator read it, so the guidance never drifts between them.

X-API-KEY landed in UniFi Network 9.0; :func:`_api_key_supported` gates on the
detected Network major version, so a console whose version cannot be read is
reported as *not* supporting API keys rather than being assumed to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from netadmin.ingest.unifi.auth import _is_unifi_os
from netadmin.logging import get_logger

logger = get_logger("ingest.unifi.detect")

# --------------------------------------------------------------------------- #
# Console kinds
# --------------------------------------------------------------------------- #
KIND_CLOUDKEY_GEN2 = "cloudkey_gen2"
KIND_CLOUDKEY_GEN2_PLUS = "cloudkey_gen2_plus"
KIND_UDM = "udm"
KIND_UDM_PRO = "udm_pro"
KIND_UDM_SE = "udm_se"
KIND_UDR = "udr"
KIND_UDW = "udw"
KIND_UCG = "ucg"
KIND_UNIFI_OS_SERVER = "unifi_os_server"
KIND_LEGACY_SOFTWARE = "legacy_software"
KIND_UNKNOWN_UNIFI_OS = "unknown_unifi_os"
KIND_UNREACHABLE = "unreachable"

# recommended_auth tokens (mirror the auth-strategy names in auth.py).
AUTH_API_KEY = "api_key"
AUTH_UNIFI_OS_COOKIE = "unifi_os_cookie"
AUTH_LEGACY_COOKIE = "legacy_cookie"
AUTH_NONE = "none"

# X-API-KEY (Network application API key) shipped in UniFi Network 9.0.
API_KEY_MIN_MAJOR = 9

# API-key availability, as far as a *read-only* probe can tell. On many UniFi OS
# consoles the Network version is not exposed without signing in, so support
# cannot be confirmed; on such a console the API-key path is still the correct
# modern route, reported as ``unknown`` (not ``supported``) so guidance stays honest.
APIKEY_SUPPORTED = "supported"  # version read, >= 9.0
APIKEY_UNSUPPORTED = "unsupported"  # version read, < 9.0 (no API keys)
APIKEY_UNKNOWN = "unknown"  # UniFi OS console, version unreadable login-free
APIKEY_UNREACHABLE = "unreachable"  # nothing answered the probe

# Default legacy self-hosted controller HTTPS port.
LEGACY_PORT = 8443


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ConsoleInfo:
    """What a read-only probe could learn about a console.

    ``model`` is populated only when the hardware model was actually read from
    the console; it stays ``None`` when the model endpoint needed auth (the kind
    is then ``unknown_unifi_os``). ``network_version`` is the UniFi Network
    application version when it could be read login-free.
    """

    kind: str
    is_unifi_os: bool
    api_key_supported: bool
    recommended_auth: str
    model: Optional[str] = None
    network_version: Optional[str] = None
    reachable: bool = True
    detail: Optional[str] = None

    @property
    def api_key_status(self) -> str:
        """Tri-state API-key availability derived from what the probe could read.

        ``supported`` when the Network version was read and is >= 9.0;
        ``unsupported`` when a read version is < 9.0; ``unknown`` on a UniFi OS
        console whose version could not be read login-free (the API-key path is
        still recommended, just unconfirmed); ``unreachable`` when nothing answered.
        """
        if not self.reachable:
            return APIKEY_UNREACHABLE
        if self.api_key_supported:
            return APIKEY_SUPPORTED
        if self.recommended_auth == AUTH_API_KEY:
            return APIKEY_UNKNOWN
        return APIKEY_UNSUPPORTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": self.model,
            "is_unifi_os": self.is_unifi_os,
            "network_version": self.network_version,
            "api_key_supported": self.api_key_supported,
            "api_key_status": self.api_key_status,
            "recommended_auth": self.recommended_auth,
            "reachable": self.reachable,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- #
# Playbook: single source of truth for per-kind setup guidance
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Playbook:
    """Device-specific setup guidance for one console kind.

    ``api_key_steps`` are shown when the detected Network version supports
    X-API-KEY; ``cookie_steps`` are the fallback (older Network, or a controller
    with no API-key support) using a local-only admin account.
    """

    label: str
    supports_api_key: bool
    api_key_steps: tuple[str, ...] = field(default_factory=tuple)
    cookie_steps: tuple[str, ...] = field(default_factory=tuple)


# The Network-app API key lives in the same place across every UniFi OS console;
# only the "how do I reach this console's UI" first step differs. These shared
# fragments keep the per-kind entries honest and DRY.
_APIKEY_TAIL = (
    "In the Network application, open Settings -> Control Plane -> Integrations.",
    "Click Create API Key, name it (e.g. 'unifioptimizer'), and copy the key — "
    "it is shown only once, at creation.",
)
_UNIFI_OS_COOKIE = (
    "If your Network version is below 9.0 (no API keys), create a local admin "
    "instead: Settings -> Admins & Users -> Add New Admin.",
    "Choose 'Restrict to local access only', set a password, and leave 2FA "
    "OFF (non-interactive login cannot pass a 2FA prompt).",
    "Use that username/password below; UnifiOptimizer logs in read-only via " "cookie + CSRF.",
)


def _os_playbook(label: str, reach: str) -> Playbook:
    return Playbook(
        label=label,
        supports_api_key=True,
        api_key_steps=(reach,) + _APIKEY_TAIL,
        cookie_steps=_UNIFI_OS_COOKIE,
    )


PLAYBOOK: dict[str, Playbook] = {
    KIND_CLOUDKEY_GEN2: _os_playbook(
        "UniFi CloudKey Gen2 (UCK-G2)",
        "Browse to https://<cloudkey-ip> and open the UniFi Network application.",
    ),
    KIND_CLOUDKEY_GEN2_PLUS: _os_playbook(
        "UniFi CloudKey Gen2 Plus (UCK-G2-Plus)",
        "Browse to https://<cloudkey-ip> and open the UniFi Network application.",
    ),
    KIND_UDM: _os_playbook(
        "UniFi Dream Machine (UDM)",
        "Browse to https://<console-ip> (or unifi.ui.com) and open the Network application.",
    ),
    KIND_UDM_PRO: _os_playbook(
        "UniFi Dream Machine Pro (UDM-Pro)",
        "Browse to https://<console-ip> (or unifi.ui.com) and open the Network application.",
    ),
    KIND_UDM_SE: _os_playbook(
        "UniFi Dream Machine SE (UDM-SE)",
        "Browse to https://<console-ip> (or unifi.ui.com) and open the Network application.",
    ),
    KIND_UDR: _os_playbook(
        "UniFi Dream Router (UDR)",
        "Browse to https://<udr-ip> or use the UniFi app, then open the Network application.",
    ),
    KIND_UDW: _os_playbook(
        "UniFi Dream Wall (UDW)",
        "Browse to https://<console-ip> (or unifi.ui.com) and open the Network application.",
    ),
    KIND_UCG: _os_playbook(
        "UniFi Cloud Gateway (UCG)",
        "Browse to https://<gateway-ip> (or unifi.ui.com) and open the Network application.",
    ),
    KIND_UNIFI_OS_SERVER: _os_playbook(
        "UniFi OS Server (self-hosted UniFi OS)",
        "Browse to your UniFi OS Server URL and open the Network application.",
    ),
    KIND_UNKNOWN_UNIFI_OS: _os_playbook(
        "UniFi OS console (model not readable without auth)",
        "Browse to https://<console-ip> (or unifi.ui.com) and open the Network application.",
    ),
    KIND_LEGACY_SOFTWARE: Playbook(
        label="Self-hosted UniFi Network controller (software)",
        supports_api_key=True,  # only on Network 9.0+; gated at runtime by version
        api_key_steps=("Open your controller UI at https://<host>:8443.",) + _APIKEY_TAIL,
        cookie_steps=(
            "This controller has no API-key support — use a local admin account.",
            "In the controller UI (https://<host>:8443): Settings -> Admins -> "
            "Add Admin, create a local admin with a password and 2FA OFF.",
            "Use that username/password below; UnifiOptimizer logs in read-only via "
            "/api/login + cookies.",
        ),
    ),
    KIND_UNREACHABLE: Playbook(
        label="No UniFi console detected",
        supports_api_key=False,
        cookie_steps=(
            "Nothing answered the read-only probe at this host.",
            "Check the host/IP, that you can reach it (https, self-signed cert is "
            "fine), and that a UniFi OS console (443) or legacy controller (8443) "
            "is listening, then re-run: netadmin detect --host <HOST>",
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Model classification (from a login-free system read)
# --------------------------------------------------------------------------- #
def _normalize(value: Optional[str]) -> str:
    """Uppercase, alphanumerics only: 'UCK-G2-Plus' -> 'UCKG2PLUS'."""
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()


def _match_token(token: str) -> Optional[str]:
    """Map one normalized model token to a console kind (specific first)."""
    if not token:
        return None
    is_ck = "UCK" in token or "CLOUDKEY" in token
    if is_ck and "PLUS" in token:
        return KIND_CLOUDKEY_GEN2_PLUS
    if token == "UCKP":  # legacy product code for the Gen2 Plus
        return KIND_CLOUDKEY_GEN2_PLUS
    if is_ck:
        return KIND_CLOUDKEY_GEN2
    if "UDMPROSE" in token or "UDMSE" in token:
        return KIND_UDM_SE
    if "UDMPRO" in token:
        return KIND_UDM_PRO
    if "UDM" in token or "DREAMMACHINE" in token:
        return KIND_UDM
    if "UDR" in token or "DREAMROUTER" in token:
        return KIND_UDR
    if "UDW" in token or "DREAMWALL" in token:
        return KIND_UDW
    if "UCG" in token or "CLOUDGATEWAY" in token:
        return KIND_UCG
    if "UNIFIOSSERVER" in token or "OSSERVER" in token or token == "UOS":
        return KIND_UNIFI_OS_SERVER
    return None


def classify_model(*candidates: Optional[str]) -> Optional[str]:
    """Return the console kind for the first candidate string that matches, else None."""
    for candidate in candidates:
        kind = _match_token(_normalize(candidate))
        if kind:
            return kind
    return None


# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #
def _version_major(version: Optional[str]) -> Optional[int]:
    if not version:
        return None
    match = re.match(r"\s*v?(\d+)", str(version))
    return int(match.group(1)) if match else None


def _api_key_supported(version: Optional[str]) -> bool:
    major = _version_major(version)
    return major is not None and major >= API_KEY_MIN_MAJOR


# --------------------------------------------------------------------------- #
# Probes (read-only, login-free)
# --------------------------------------------------------------------------- #
def _json(resp: httpx.Response) -> Optional[dict[str, Any]]:
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _server_version(payload: Optional[dict[str, Any]]) -> Optional[str]:
    if not payload:
        return None
    meta = payload.get("meta")
    if isinstance(meta, dict):
        version = meta.get("server_version")
        if version:
            return str(version)
    version = payload.get("server_version")
    return str(version) if version else None


async def _network_status_version(http: httpx.AsyncClient, host: str) -> Optional[str]:
    """UniFi OS Network app version from the login-free ``/proxy/network/status``."""
    try:
        resp = await http.get(f"{host}/proxy/network/status", follow_redirects=False)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    return _server_version(_json(resp))


async def _system_info(http: httpx.AsyncClient, host: str) -> Optional[dict[str, Any]]:
    """UniFi OS console hardware info from ``/api/system`` (None when it needs auth)."""
    try:
        resp = await http.get(f"{host}/api/system", follow_redirects=False)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    return _json(resp)


def _legacy_status_url(host: str) -> str:
    """The ``:8443/status`` URL for a legacy controller (keeps an explicit port)."""
    parts = urlsplit(host if "://" in host else f"https://{host}")
    scheme = parts.scheme or "https"
    hostname = parts.hostname or host
    port = parts.port or LEGACY_PORT
    return urlunsplit((scheme, f"{hostname}:{port}", "/status", "", ""))


async def _probe_legacy(http: httpx.AsyncClient, host: str) -> Optional[ConsoleInfo]:
    """Try the login-free legacy ``:8443/status``; return info if it answers."""
    url = _legacy_status_url(host)
    try:
        resp = await http.get(url, follow_redirects=False)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    version = _server_version(_json(resp))
    api_key = _api_key_supported(version)
    return ConsoleInfo(
        kind=KIND_LEGACY_SOFTWARE,
        model="UniFi Network (self-hosted)",
        is_unifi_os=False,
        network_version=version,
        api_key_supported=api_key,
        recommended_auth=AUTH_API_KEY if api_key else AUTH_LEGACY_COOKIE,
        reachable=True,
    )


def _model_display(sysinfo: dict[str, Any]) -> Optional[str]:
    hardware = sysinfo.get("hardware")
    if isinstance(hardware, dict):
        for key in ("name", "shortname"):
            value = hardware.get(key)
            if value:
                return str(value)
    for key in ("name", "device_type", "model"):
        value = sysinfo.get(key)
        if value:
            return str(value)
    return None


async def _detect_unifi_os(http: httpx.AsyncClient, host: str) -> ConsoleInfo:
    network_version = await _network_status_version(http, host)
    sysinfo = await _system_info(http, host)

    kind: Optional[str] = None
    model: Optional[str] = None
    detail: Optional[str] = None
    if sysinfo:
        hardware = sysinfo.get("hardware")
        hw = hardware if isinstance(hardware, dict) else {}
        kind = classify_model(
            hw.get("shortname"),
            hw.get("name"),
            sysinfo.get("name"),
            sysinfo.get("device_type"),
            sysinfo.get("model"),
        )
        model = _model_display(sysinfo)
    if kind is None:
        kind = KIND_UNKNOWN_UNIFI_OS
        if sysinfo is None:
            detail = "console model is not exposed without authentication"
        else:
            detail = "console model string was not recognized"
        model = None  # never report a model we could not positively identify

    api_key = _api_key_supported(network_version)
    # On a UniFi OS console whose Network version could not be read login-free
    # (common: /proxy/network/status is 401 without a session), the API-key path
    # is still the correct modern route — recommend it rather than downgrading to
    # cookie auth. Only a version we actually read as < 9.0 rules API keys out.
    if api_key or _version_major(network_version) is None:
        recommended_auth = AUTH_API_KEY
    else:
        recommended_auth = AUTH_UNIFI_OS_COOKIE
    return ConsoleInfo(
        kind=kind,
        model=model,
        is_unifi_os=True,
        network_version=network_version,
        api_key_supported=api_key,
        recommended_auth=recommended_auth,
        reachable=True,
        detail=detail,
    )


def _unreachable(host: str) -> ConsoleInfo:
    return ConsoleInfo(
        kind=KIND_UNREACHABLE,
        model=None,
        is_unifi_os=False,
        network_version=None,
        api_key_supported=False,
        recommended_auth=AUTH_NONE,
        reachable=False,
        detail="no UniFi console answered the read-only probe",
    )


def _normalize_host(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ValueError("host is required")
    if "://" not in host:
        host = f"https://{host}"
    return host


async def detect_console(
    host: str,
    *,
    http: Optional[httpx.AsyncClient] = None,
    timeout: float = 8.0,
) -> ConsoleInfo:
    """Fingerprint the console at ``host`` with read-only, login-free probes.

    Passing an ``http`` client reuses it (and leaves it open); otherwise a
    short-lived client with ``verify=False`` (self-signed controller certs) is
    created and closed here. Never raises on an unreachable/odd host — that is
    reported as ``kind='unreachable'`` — only a blank ``host`` raises.
    """
    host = _normalize_host(host)
    owns_client = http is None
    if http is None:
        http = httpx.AsyncClient(verify=False, timeout=timeout, follow_redirects=False)
    try:
        if await _is_unifi_os(http, host):
            return await _detect_unifi_os(http, host)
        legacy = await _probe_legacy(http, host)
        return legacy if legacy is not None else _unreachable(host)
    finally:
        if owns_client:
            await http.aclose()


# --------------------------------------------------------------------------- #
# Rendering (shared by the CLI and the docs generator)
# --------------------------------------------------------------------------- #
def secrets_env_lines(info: ConsoleInfo, host: str) -> list[str]:
    """The exact ``data/secrets.env`` lines for the recommended auth path."""
    base = _normalize_host(host)
    if info.recommended_auth == AUTH_API_KEY:
        return [
            f"UNIFI_HOST={base}",
            "UNIFI_API_KEY=<paste-your-api-key>",
            "UNIFI_SITE=default",
        ]
    if info.recommended_auth == AUTH_LEGACY_COOKIE:
        return [
            f"UNIFI_HOST={_legacy_status_url(base).rsplit('/status', 1)[0]}",
            "UNIFI_USERNAME=<local-admin>",
            "UNIFI_PASSWORD=<password>",
            "UNIFI_SITE=default",
        ]
    if info.recommended_auth == AUTH_UNIFI_OS_COOKIE:
        return [
            f"UNIFI_HOST={base}",
            "UNIFI_USERNAME=<local-admin>",
            "UNIFI_PASSWORD=<password>",
            "UNIFI_SITE=default",
        ]
    return ["# console unreachable — resolve the host first, then re-run detect"]


def format_console_report(info: ConsoleInfo, host: str) -> str:
    """Render the human-readable detection guide for ``netadmin detect``."""
    play = PLAYBOOK.get(info.kind, PLAYBOOK[KIND_UNKNOWN_UNIFI_OS])
    lines: list[str] = []
    lines.append(f"UnifiOptimizer console detection — {_normalize_host(host)}")
    lines.append("")

    console_label = play.label
    if info.model:
        console_label = f"{play.label} — reported model: {info.model}"
    lines.append(f"Console:      {console_label} ({info.kind})")
    lines.append(f"UniFi OS:     {'yes' if info.is_unifi_os else 'no'}")
    lines.append(f"Network:      {info.network_version or 'unknown'}")

    status = info.api_key_status
    if status == APIKEY_UNREACHABLE:
        lines.append("API-key auth: unknown (console unreachable)")
    elif status == APIKEY_SUPPORTED:
        lines.append("API-key auth: supported (Network 9.0+)")
    elif status == APIKEY_UNKNOWN:
        lines.append("API-key auth: likely (Network version not readable without signing in)")
    else:
        lines.append("API-key auth: not available (needs Network 9.0+)")
    lines.append(f"Recommended:  {info.recommended_auth}")
    if info.detail:
        lines.append(f"Note:         {info.detail}")
    if status == APIKEY_UNKNOWN:
        lines.append(
            "Note:         Could not read the Network version without signing in; "
            "API keys need Network 9.0+ (UniFi OS 4.x)."
        )
        lines.append(
            "              If Settings has no Integrations screen, update the "
            "console and re-run detect."
        )
    lines.append("")

    if info.recommended_auth == AUTH_API_KEY and play.api_key_steps:
        lines.append("Create the API key:")
        steps = play.api_key_steps
    else:
        lines.append("Set up authentication:")
        steps = play.cookie_steps
    for i, step in enumerate(steps, start=1):
        lines.append(f"  {i}. {step}")
    lines.append("")

    lines.append("Then put these lines in data/secrets.env:")
    for env_line in secrets_env_lines(info, host):
        lines.append(f"  {env_line}")
    return "\n".join(lines)


__all__ = [
    "ConsoleInfo",
    "Playbook",
    "PLAYBOOK",
    "detect_console",
    "classify_model",
    "format_console_report",
    "secrets_env_lines",
    "KIND_CLOUDKEY_GEN2",
    "KIND_CLOUDKEY_GEN2_PLUS",
    "KIND_UDM",
    "KIND_UDM_PRO",
    "KIND_UDM_SE",
    "KIND_UDR",
    "KIND_UDW",
    "KIND_UCG",
    "KIND_UNIFI_OS_SERVER",
    "KIND_LEGACY_SOFTWARE",
    "KIND_UNKNOWN_UNIFI_OS",
    "KIND_UNREACHABLE",
    "AUTH_API_KEY",
    "AUTH_UNIFI_OS_COOKIE",
    "AUTH_LEGACY_COOKIE",
    "AUTH_NONE",
    "APIKEY_SUPPORTED",
    "APIKEY_UNSUPPORTED",
    "APIKEY_UNKNOWN",
    "APIKEY_UNREACHABLE",
]
