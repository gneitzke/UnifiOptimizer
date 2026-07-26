"""First-run setup router: ``/api/setup/*`` (ARCHITECTURE.md 18).

The daemon must be usable by someone who has never touched ``data/secrets.env``.
On a fresh install the web app runs a setup flow that fingerprints the console,
validates a controller credential, writes it, mints the UI access token, and
hot-starts ingest -- so the only prerequisite is "the daemon is running".

Three endpoints, all reachable pre-auth **only while unconfigured** (the
auth middleware enforces that window; see :mod:`netadmin.server.auth`):

* ``GET  /api/setup/status``  -- the always-open discriminator the web app reads
  to choose the setup flow vs the token gate.
* ``POST /api/setup/detect``  -- read-only console fingerprint + the per-console
  API-key playbook + the console URL to open. Zero controller logins.
* ``POST /api/setup/connect`` -- validate the credential with a **read-only**
  probe, persist it to ``secrets.env`` (600, atomic), mint the UI token if none
  exists, hot-start ingest in the running process, and return the token **once**.

Security invariants (ARCHITECTURE.md 18, reviewed):

* Setup can never overwrite a live config: :func:`setup_connect` re-checks
  :func:`is_configured` and 409s the instant a controller credential or UI token
  already exists.
* The UniFi API key / password is written to the gitignored ``secrets.env`` and is
  **never** returned in any response and **never** logged.
* The UI token is returned exactly once, by design.
* The validation probe is read-only (auth + a single ``stat/device`` GET); setup
  can never mutate the controller.
"""

from __future__ import annotations

from secrets import token_urlsafe
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from netadmin import config as _config
from netadmin.config import Settings, write_secrets
from netadmin.ingest.unifi.auth import (
    TwoFactorRequired,
    UnifiAuthError,
    UnifiConnectionError,
    UnifiError,
)
from netadmin.ingest.unifi.client import UnifiClient
from netadmin.ingest.unifi.detect import (
    AUTH_API_KEY,
    KIND_LEGACY_SOFTWARE,
    KIND_UNKNOWN_UNIFI_OS,
    LEGACY_PORT,
    PLAYBOOK,
    ConsoleInfo,
    detect_console,
)
from netadmin.logging import get_logger
from netadmin.server.services.discovery import discover_consoles

router = APIRouter(prefix="/api/setup", tags=["setup"])
_log = get_logger("server.setup")

# The read-only endpoint the validation probe reads (section 5.1 read set). A GET;
# it never mutates the controller.
_PROBE_ENDPOINT = "stat/device"
_PROBE_TIMEOUT_S = 8.0
# CSPRNG token size for a minted UI access token (bytes of entropy).
_TOKEN_BYTES = 32


# --------------------------------------------------------------------------- #
# Setup state (read live off Settings so an in-process flip is seen at once)
# --------------------------------------------------------------------------- #
def is_configured(settings: Settings) -> bool:
    """Whether the daemon is configured -- i.e. setup is locked (ARCHITECTURE.md 18).

    True when a controller credential exists (host + api_key or user/pass) OR a UI
    access token exists. Read from a **live** ``Settings`` so the flip the connect
    handler makes in-process (mutating the settings object) is seen immediately by
    the status endpoint and the auth middleware -- no restart, no cache to bust.
    """
    return settings.unifi.is_configured or bool(settings.api_token)


def _controller_connected(app: Any) -> bool:
    """Best-effort "the daemon holds a live controller session" (ingest running).

    True once the collector scheduler has been built and started against the
    configured credentials and was not marked unavailable. It flips true right
    after a successful connect (which validated the credential with a real
    read-only controller read and hot-started ingest). Honest and cheap; it never
    fabricates a "connected" while nothing is running.
    """
    state = app.state.daemon
    return state.scheduler is not None and "scheduler" not in state.unavailable


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class DetectBody(BaseModel):
    host: str


class ConnectBody(BaseModel):
    host: str
    api_key: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    site: str = "default"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _error(status: int, code: str, message: str) -> JSONResponse:
    """A clean ``{ok:false}`` error body -- never a raw exception or stack."""
    return JSONResponse({"ok": False, "code": code, "error": message}, status_code=status)


# Line-breaking / terminating control chars a credential field must never carry:
# a newline/CR would split a ``secrets.env`` line into a second attacker-chosen
# KEY=VALUE assignment; a NUL truncates it. The writer rejects these too (defense
# in depth), but we catch them here first so setup returns a clean 400 instead of
# surfacing the writer's error, and so nothing is probed or persisted.
_FORBIDDEN_CREDENTIAL_CHARS = ("\n", "\r", "\x00")


def _has_forbidden_char(value: Optional[str]) -> bool:
    """Whether ``value`` carries a line-breaking / terminating control char."""
    return value is not None and any(ch in value for ch in _FORBIDDEN_CREDENTIAL_CHARS)


def _normalize_host(host: str) -> str:
    """Trim and give the host an explicit ``https://`` scheme (self-signed is fine)."""
    host = (host or "").strip().rstrip("/")
    if "://" not in host:
        host = f"https://{host}"
    return host


def _console_url(host: str, info: ConsoleInfo) -> str:
    """The URL the web app opens to reach this console's UI (new tab)."""
    normalized = _normalize_host(host)
    parts = urlsplit(normalized)
    scheme = parts.scheme or "https"
    hostname = parts.hostname or host
    if info.kind == KIND_LEGACY_SOFTWARE:
        netloc = f"{hostname}:{parts.port or LEGACY_PORT}"
    else:
        netloc = f"{hostname}:{parts.port}" if parts.port else hostname
    return urlunsplit((scheme, netloc, "/", "", ""))


def _playbook_view(info: ConsoleInfo) -> dict[str, Any]:
    """The per-console API-key (or cookie) steps for the detected console.

    Mirrors the CLI's choice in :func:`~netadmin.ingest.unifi.detect.format_console_report`:
    the API-key steps when the recommended auth is an API key, the local-admin
    cookie steps otherwise -- so the web guidance never drifts from the CLI.
    """
    play = PLAYBOOK.get(info.kind) or PLAYBOOK[KIND_UNKNOWN_UNIFI_OS]
    use_api_key = info.recommended_auth == AUTH_API_KEY and bool(play.api_key_steps)
    steps = play.api_key_steps if use_api_key else play.cookie_steps
    return {
        "label": play.label,
        "auth_mode": "api_key" if use_api_key else "cookie",
        "supports_api_key": play.supports_api_key,
        "api_key_status": info.api_key_status,
        "steps": list(steps),
    }


def _build_probe_client(
    *,
    host: str,
    site: str,
    api_key: Optional[str],
    username: Optional[str],
    password: Optional[str],
    timeout: float,
) -> UnifiClient:
    """Construct the (not-yet-connected) client the validation probe reads through.

    A seam so tests can drive the probe against a fake controller offline; the
    client connects lazily on its first request (section 5.1).
    """
    return UnifiClient(
        host=host,
        site=site,
        api_key=api_key,
        username=username,
        password=password,
        timeout=timeout,
    )


async def _validate_credential(
    *,
    host: str,
    site: str,
    api_key: Optional[str],
    username: Optional[str],
    password: Optional[str],
    timeout: float = _PROBE_TIMEOUT_S,
) -> Optional[tuple[str, str]]:
    """Validate a credential with a **read-only** probe; ``None`` on success.

    Authenticates the client, then issues a single ``stat/device`` GET (section
    5.1 read set) -- no write, no mutation, ever. On any failure it returns a
    ``(code, message)`` pair with a friendly, non-leaking message; it never raises
    a raw controller/transport error to the caller, and it never logs the
    credential value.
    """
    client = _build_probe_client(
        host=host,
        site=site,
        api_key=api_key,
        username=username,
        password=password,
        timeout=timeout,
    )
    try:
        await client.connect()
        await client.get_json(_PROBE_ENDPOINT)
    except TwoFactorRequired:
        return (
            "twofactor_required",
            "That account requires two-factor auth, which cannot be used for a "
            "non-interactive login. Create a local admin with 2FA off, or use an API key.",
        )
    except UnifiAuthError:
        return (
            "auth_failed",
            "The controller rejected those credentials. Double-check the API key "
            "(or username and password) and try again.",
        )
    except UnifiConnectionError:
        return (
            "unreachable",
            "Could not reach the controller at that address. Check the host/IP and "
            "that the console is online, then try again.",
        )
    except UnifiError:
        return (
            "probe_failed",
            "Could not validate against the controller. Check the host and credentials.",
        )
    except Exception:  # noqa: BLE001 - never leak a raw error (or the credential) out
        _log.warning("setup validation probe failed unexpectedly", exc_info=True)
        return (
            "probe_failed",
            "Could not validate against the controller. Check the host and credentials.",
        )
    finally:
        await client.aclose()
    return None


def _apply_credentials(
    settings: Settings,
    *,
    host: str,
    site: str,
    api_key: Optional[str],
    username: Optional[str],
    password: Optional[str],
    token: str,
) -> None:
    """Apply the freshly-written credentials to the live settings object in place.

    So the status endpoint, the auth middleware, and the hot-started ingest all
    read the new config immediately (the settings object is shared via
    ``app.state.settings``). An API-key connect clears any stale user/pass and
    vice-versa, so the two auth modes never bleed together.
    """
    settings.unifi_host = host
    settings.unifi_site = site
    if api_key:
        settings.unifi_api_key = api_key
        settings.unifi_username = None
        settings.unifi_password = None
    else:
        settings.unifi_username = username
        settings.unifi_password = password
        settings.unifi_api_key = None
    settings.netadmin_api_token = token


async def _hot_start(app: Any) -> None:
    """Build + start ingest in the running process (no restart). Imported lazily
    to avoid a circular import with the app factory."""
    from netadmin.server.main import start_ingest

    await start_ingest(app, rebuild=True)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/status")
async def setup_status(request: Request) -> dict[str, Any]:
    """The setup discriminator: ``{configured, controller_connected}``.

    Always reachable (the web app reads it on every load to branch between the
    setup flow and the token gate). Reflects live settings, so it flips to
    ``configured: true`` the moment a connect succeeds.
    """
    settings: Settings = request.app.state.settings
    return {
        "configured": is_configured(settings),
        "controller_connected": _controller_connected(request.app),
    }


@router.post("/scan")
async def setup_scan(request: Request) -> Any:
    """Scan the machine's own private LAN for a reachable UniFi console.

    A first-run assist: probes the common UniFi HTTPS ports (443/8443) across the
    daemon host's own RFC1918 /24(s) and confirms each open host with the read-only
    console fingerprint, so the web app can pre-fill a real controller address
    instead of asking the user to know their console's IP. Reachable pre-auth only
    while unconfigured (the setup window); 409s once configured, like the other
    write-ish setup routes. Read-only end to end: bare TCP connects plus the
    login-free fingerprint -- it never authenticates to or mutates a controller.

    Returns ``{ok, scanned: [<cidr>...], candidates: [...]}``. An empty
    ``candidates`` (or empty ``scanned`` on a host with no private network) is the
    honest "none found -- enter it manually" signal, never an error.
    """
    settings: Settings = request.app.state.settings
    if is_configured(settings):
        return _error(
            409,
            "already_configured",
            "This install is already configured. Change the controller credential "
            "in data/secrets.env instead.",
        )
    try:
        result = await discover_consoles()
    except Exception:  # noqa: BLE001 - a scan must degrade to "none found", never 500
        _log.warning("setup LAN scan failed unexpectedly", exc_info=True)
        return {"ok": True, "scanned": [], "candidates": []}
    return {"ok": True, **result.as_dict()}


@router.post("/detect")
async def setup_detect(request: Request, body: DetectBody) -> Any:
    """Read-only console fingerprint + the per-console setup playbook + console URL.

    Zero controller logins (:func:`detect_console` is GET/login-free). Tolerates an
    unreachable or unrecognised host honestly -- it returns ``kind: 'unreachable'``
    / ``'unknown_unifi_os'`` rather than erroring.
    """
    host = (body.host or "").strip()
    if not host:
        return _error(400, "invalid_host", "A controller host or IP is required.")
    info = await detect_console(host)
    return {
        "console": info.as_dict(),
        "playbook": _playbook_view(info),
        "console_url": _console_url(host, info),
    }


@router.post("/connect")
async def setup_connect(request: Request, body: ConnectBody) -> Any:
    """Validate + persist a controller credential, mint the UI token, hot-start ingest.

    Steps (ARCHITECTURE.md 18): re-check state (409 if already configured) ->
    validate the credential read-only -> write ``secrets.env`` (600, atomic) ->
    mint the UI token if absent -> apply to live settings -> hot-start ingest ->
    return ``{ok, ui_token}``. The UniFi key is never returned and never logged.
    """
    app = request.app
    settings: Settings = app.state.settings

    # (1) Setup can never overwrite a live config -- re-check and 409.
    if is_configured(settings):
        return _error(
            409,
            "already_configured",
            "This install is already configured. Change the controller credential "
            "in data/secrets.env instead.",
        )

    host = (body.host or "").strip()
    if not host:
        return _error(400, "invalid_host", "A controller host or IP is required.")
    api_key = (body.api_key or "").strip() or None
    username = (body.username or "").strip() or None
    password = body.password or None  # never strip a password
    site = (body.site or "default").strip() or "default"

    if not api_key and not (username and password):
        return _error(
            400,
            "missing_credential",
            "Provide an API key, or a username and password.",
        )

    # Reject a credential field carrying a line-breaking control char before it is
    # probed or written -- it can never be a valid host/key/user/password and, left
    # unchecked, a newline would inject a second key into secrets.env. Clean 400,
    # nothing persisted; the writer rejects it too as a last line of defense.
    if any(_has_forbidden_char(field) for field in (host, site, api_key, username, password)):
        return _error(
            400,
            "invalid_credential",
            "The host or credential contains an invalid control character. Remove any "
            "line breaks and try again.",
        )

    host = _normalize_host(host)

    # (2) Validate with a READ-ONLY probe; on failure write nothing.
    failure = await _validate_credential(
        host=host, site=site, api_key=api_key, username=username, password=password
    )
    if failure is not None:
        return _error(400, failure[0], failure[1])

    # (3) Persist the credential to secrets.env (600, atomic, other keys preserved).
    updates: dict[str, str] = {"UNIFI_HOST": host, "UNIFI_SITE": site}
    if api_key:
        updates["UNIFI_API_KEY"] = api_key
    else:
        updates["UNIFI_USERNAME"] = username  # type: ignore[assignment]
        updates["UNIFI_PASSWORD"] = password  # type: ignore[assignment]

    # (4) Mint the UI token if none exists (here it never does -- we 409'd otherwise).
    ui_token = settings.api_token or token_urlsafe(_TOKEN_BYTES)
    if not settings.api_token:
        updates["NETADMIN_API_TOKEN"] = ui_token

    write_secrets(updates, path=app.state.secrets_path or _config.SECRETS_ENV)

    # Apply to live settings so status / auth / ingest see the new config at once.
    _apply_credentials(
        settings,
        host=host,
        site=site,
        api_key=api_key,
        username=username,
        password=password,
        token=ui_token,
    )

    # (5) Hot-start ingest in the running process (no restart).
    await _hot_start(app)

    # The UI token is returned exactly once, by design. The UniFi key never is.
    return {"ok": True, "ui_token": ui_token}


__all__ = ["router", "is_configured"]
