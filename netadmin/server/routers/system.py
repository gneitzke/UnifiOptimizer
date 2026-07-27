"""System router: ``GET /api/health``, the access-token surface, and the
self-update surface (ARCHITECTURE.md 12, 18.1, and 23).

``GET /api/health`` is the one endpoint an operator (or a probe) hits to know the
daemon is alive and honest: last-successful-poll age per collector job, WebSocket
listener state, per-job consecutive failures, database size, entity counts, and
uptime. The composition lives in :mod:`netadmin.server.runtime`; this router only
wires it to HTTP and always answers 200 with an honest body (a status endpoint
that refuses to answer when the system is unhealthy defeats its own purpose).

The two ``/system/token`` routes are how a user finds or rotates their access
token (ARCHITECTURE.md 18.1 Settings addendum) -- the token a just-in-time fix
prompt asks for. Their auth is enforced by the middleware, not here:

* ``GET /system/token`` (reveal) returns the current token. Sensitive, so the
  middleware gates it behind the bearer token OR a loopback peer.
* ``POST /system/token/regenerate`` mints a new token, persists it, and returns it
  once. A gated, rate-limited mutation (the middleware requires the *current*
  token before this runs).

The two ``/system/mcp-token`` routes are the same pair for the remote-MCP
credential (ARCHITECTURE.md 18.4 Settings addendum), so a Settings page can
manage ``NETADMIN_MCP_TOKEN`` beside the access token instead of only through
``netadmin mcp-token`` on the box. Gating is identical to their
``/system/token`` counterparts -- bearer-or-loopback reveal, gated + rate
limited regenerate -- except the credential that unlocks *both* is always the
API token, never the MCP token being managed:

* ``GET /system/mcp-token`` (reveal) returns the current ``NETADMIN_MCP_TOKEN``,
  or ``null`` when remote MCP is off.
* ``POST /system/mcp-token/regenerate`` mints a new MCP token, persists it, and
  applies it to the live ``Settings`` in place -- so the mount's per-request
  ``settings.mcp_token`` read (:class:`netadmin.server.mcp_mount.McpEndpoint`)
  locks to it immediately and the old token is refused on its very next ``/mcp``
  request, no daemon restart required. That immediacy only covers *rotating* an
  already-running mount, though: if this daemon booted with no
  ``NETADMIN_MCP_TOKEN`` at all, the mount's session manager was never built
  (:func:`netadmin.server.mcp_mount.start_mcp` runs once, at lifespan startup),
  so minting the *first* token here still needs a restart before ``/mcp``
  answers anything but 503.

The four ``/system/update*`` routes are the self-update banner's backend
(ARCHITECTURE.md 23):

* ``GET /system/update`` -- an open read: cached PyPI version-check result,
  detected install method, whether self-upgrade is even possible here, any
  skip/snooze dismissal, and the current upgrade's progress (if one is running or
  just finished).
* ``POST /system/update/dismiss`` -- ``{mode: skip | snooze}``, persisted in
  ``app_meta`` so the dismissal survives across browsers/devices.
* ``POST /system/update/check`` -- force a PyPI re-check right now.
* ``POST /system/update/apply`` -- ``{target_version}``, which must equal the
  currently advertised latest (killing a stale-tab race). Fails closed exactly
  like a controller mutation (:func:`netadmin.server.auth.is_system_update_apply`)
  regardless of the rest of the API's read posture, and 409s if self-upgrade is
  unsupported here or an upgrade is already in flight. On success it primes the
  journal with this daemon's own pid/argv/cwd/env and spawns the detached
  ``netadmin upgrade run`` runner (:mod:`netadmin.upgrade.runner`) -- through
  ``app.state.upgrade_spawner`` so tests never launch a real subprocess.

The health handler is ``async`` deliberately: the store's SQLite connection is
bound to the event-loop thread (one process, shared loop -- section 3), so it must
be read on that thread, not a threadpool worker. Local read queries are cheap and
do not warrant the executor (heavy analysis does; section 3).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from secrets import token_urlsafe
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from netadmin import __version__
from netadmin.config import SECRETS_ENV, Settings, write_secrets
from netadmin.logging import get_logger
from netadmin.server.runtime import build_health
from netadmin.upgrade.checker import SKIP_VERSION_KEY, SNOOZE_UNTIL_KEY, read_cached_status
from netadmin.upgrade.detect import detect_install_method
from netadmin.upgrade.journal import (
    PHASE_STARTING,
    UpgradeJournal,
    journal_path_for,
    read_journal,
    write_journal,
)

_log = get_logger("server.routers.system")

router = APIRouter(prefix="/api", tags=["system"])

# CSPRNG entropy for a regenerated access token; matches the first-run mint
# (netadmin/server/routers/setup.py) so both paths produce the same shape.
_TOKEN_BYTES = 32

# Snooze duration for POST /system/update/dismiss {mode: "snooze"} (ARCHITECTURE.md 23).
_SNOOZE_SECONDS = 7 * 86_400

_RELEASE_URL_FMT = "https://pypi.org/project/unifioptimizer/{version}/"


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Daemon health snapshot (section 12). Never raises; missing data is UNKNOWN."""
    app = request.app
    return build_health(app.state.store, app.state.daemon, app.state.settings)


@router.get("/system/token")
async def reveal_token(request: Request) -> dict[str, Any]:
    """Reveal the current access token (ARCHITECTURE.md 18.1).

    The auth middleware has already enforced access (bearer token or loopback); this
    handler only reads the token back so Settings can show it. ``token`` is ``null``
    on an unconfigured / open install (there is nothing to reveal).
    """
    token = request.app.state.settings.api_token
    return {"token": token, "configured": token is not None}


@router.post("/system/token/regenerate")
async def regenerate_token(request: Request) -> dict[str, Any]:
    """Mint a new access token, persist it, and return it once (ARCHITECTURE.md 18.1).

    Gated + rate limited by the middleware, which required the *current* token
    before this ran. The new token is written to ``secrets.env`` (600, atomic, every
    other key preserved) and applied to the live ``Settings`` in place, so the
    middleware's token provider locks to it immediately -- the browser must store
    the returned value to keep applying fixes.
    """
    app = request.app
    settings = app.state.settings
    new_token = token_urlsafe(_TOKEN_BYTES)
    write_secrets(
        {"NETADMIN_API_TOKEN": new_token},
        path=getattr(app.state, "secrets_path", None) or SECRETS_ENV,
    )
    settings.netadmin_api_token = new_token
    return {"token": new_token}


@router.get("/system/mcp-token")
async def reveal_mcp_token(request: Request) -> dict[str, Any]:
    """Reveal the configured remote-MCP token (ARCHITECTURE.md 18.4).

    Same shape as :func:`reveal_token`: the auth middleware has already enforced
    access (the *API* bearer token, or a loopback peer) before this handler ever
    runs; it only reads ``NETADMIN_MCP_TOKEN`` back so Settings can show it.
    ``token`` is ``null`` when unset -- remote MCP is simply off, and ``/mcp``
    answers 404.
    """
    token = request.app.state.settings.mcp_token
    return {"token": token, "configured": token is not None}


@router.post("/system/mcp-token/regenerate")
async def regenerate_mcp_token(request: Request) -> dict[str, Any]:
    """Mint a new remote-MCP token, persist it, and return it once (ARCHITECTURE.md 18.4).

    Gated + rate limited by the middleware exactly like :func:`regenerate_token`,
    using the *API* token as the credential -- rotating a read-only credential
    must not be able to authorize its own rotation. Written to ``secrets.env``
    (600, atomic, every other key preserved) and applied to the live ``Settings``
    in place, so an already-running mount's per-request token read
    (:class:`netadmin.server.mcp_mount.McpEndpoint`) locks to it immediately: the
    old token is refused on its very next ``/mcp`` request, no restart. A mount
    that was never started because no token existed at boot stays down until a
    restart either way -- see the module docstring.
    """
    app = request.app
    settings = app.state.settings
    new_token = token_urlsafe(_TOKEN_BYTES)
    write_secrets(
        {"NETADMIN_MCP_TOKEN": new_token},
        path=getattr(app.state, "secrets_path", None) or SECRETS_ENV,
    )
    settings.netadmin_mcp_token = new_token
    return {"token": new_token}


# --------------------------------------------------------------------------- #
# Self-update (ARCHITECTURE.md 23)
# --------------------------------------------------------------------------- #


class DismissRequest(BaseModel):
    mode: Literal["skip", "snooze"]


class ApplyRequest(BaseModel):
    target_version: str


def _upgrade_state_payload(journal: Optional[UpgradeJournal]) -> Optional[dict[str, Any]]:
    """The public projection of the journal: never the recorded pid/argv/cwd/env,
    which are internal restart plumbing (and, for env, potentially sensitive)."""
    if journal is None:
        return None
    return {
        "phase": journal.phase,
        "target_version": journal.target_version,
        "from_version": journal.from_version,
        "started_ts": journal.started_ts,
        "updated_ts": journal.updated_ts,
        "error": journal.error,
    }


def _update_status_payload(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    store = request.app.state.store
    status = read_cached_status(store)
    info = detect_install_method()
    journal = read_journal(journal_path_for(settings.db_path))
    skip = store.get_app_meta(SKIP_VERSION_KEY)
    snooze_raw = store.get_app_meta(SNOOZE_UNTIL_KEY)
    snoozed_until = int(snooze_raw) if snooze_raw is not None else None
    release_url = (
        _RELEASE_URL_FMT.format(version=status.latest_version) if status.latest_version else None
    )
    return {
        "current_version": status.current_version,
        "latest_version": status.latest_version,
        "update_available": status.update_available,
        "install_method": info.method,
        "variant": info.variant,
        "self_upgrade_supported": info.self_upgrade_supported,
        "checked_ts": status.checked_ts,
        "skipped_version": skip,
        "snoozed_until": snoozed_until,
        "upgrade_state": _upgrade_state_payload(journal),
        "release_url": release_url,
    }


@router.get("/system/update")
async def get_update_status(request: Request) -> dict[str, Any]:
    """Open read (ARCHITECTURE.md 23): everything the banner needs to render."""
    return _update_status_payload(request)


@router.post("/system/update/dismiss")
async def dismiss_update(request: Request, body: DismissRequest) -> dict[str, Any]:
    """Skip or snooze the currently advertised update, server-side (ARCHITECTURE.md 23).

    ``skip`` records the *currently advertised* latest version (not one from the
    request body -- there is nothing else it could sensibly mean) so a later,
    newer release still shows the banner. ``snooze`` reopens the banner after
    7 days regardless of whether a newer version has since appeared. Both are
    no-ops with nothing to dismiss (no latest version cached yet), which is a
    normal state, not an error.
    """
    store = request.app.state.store
    if body.mode == "skip":
        status = read_cached_status(store)
        if status.latest_version is not None:
            store.set_app_meta(SKIP_VERSION_KEY, status.latest_version)
    else:
        store.set_app_meta(SNOOZE_UNTIL_KEY, str(int(time.time()) + _SNOOZE_SECONDS))
    return _update_status_payload(request)


@router.post("/system/update/check")
async def force_check_update(request: Request) -> dict[str, Any]:
    """Force a PyPI re-check right now (ARCHITECTURE.md 23), bypassing the interval."""
    checker = getattr(request.app.state, "version_checker", None)
    if checker is not None:
        await checker.check_now()
    return _update_status_payload(request)


def _spawn_upgrade_runner(target_version: str) -> None:
    """Launch ``netadmin upgrade run --target <target_version>`` detached.

    ``start_new_session=True`` puts it in its own session so it survives the very
    ``SIGTERM`` this runner sends the current daemon partway through (section 23).
    Uses this process's own interpreter (``sys.executable``) and current
    environment/cwd -- correct because this only ever runs when
    ``self_upgrade_supported`` is true, i.e. this process IS the live pip venv.
    """
    subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no untrusted input
        [sys.executable, "-m", "netadmin.cli", "upgrade", "run", "--target", target_version],
        cwd=os.getcwd(),
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


@router.post("/system/update/apply")
async def apply_update(request: Request, body: ApplyRequest) -> dict[str, Any]:
    """Kick off the pip self-upgrade runner (ARCHITECTURE.md 23).

    Gated fail-closed by the middleware exactly like a controller mutation
    (:func:`netadmin.server.auth.is_system_update_apply`) before this handler ever
    runs. Three more refusals live here, all 409 (a conflict with the daemon's
    current state, not a client input error): ``target_version`` must equal the
    currently advertised latest (a stale banner in another tab must not silently
    apply whatever it last saw); this install must actually support self-upgrade;
    and no upgrade may already be in flight. On success the journal is primed with
    THIS request's own process facts (pid/argv/cwd/env) -- the runner has no other
    way to learn how to stop and restart this exact daemon -- and the detached
    runner is spawned through ``app.state.upgrade_spawner`` (a real
    :func:`_spawn_upgrade_runner` in production; tests inject a recorder so no
    subprocess is ever actually launched).
    """
    app = request.app
    settings: Settings = app.state.settings
    store = app.state.store

    status = read_cached_status(store)
    if status.latest_version is None or body.target_version != status.latest_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"target_version {body.target_version!r} does not match the currently "
                f"advertised latest version ({status.latest_version!r})"
            ),
        )

    info = detect_install_method()
    if not info.self_upgrade_supported:
        raise HTTPException(
            status_code=409,
            detail=f"self-upgrade is not supported on this install (method={info.method})",
        )

    jpath = journal_path_for(settings.db_path)
    existing = read_journal(jpath)
    now = int(time.time())
    if existing is not None and existing.is_active(now):
        raise HTTPException(status_code=409, detail="an upgrade is already in flight")
    if existing is not None and existing.is_abandoned(now):
        # A runner that was killed, OOMed, or lost to a reboot leaves its phase
        # frozen. Refusing forever would mean one crash permanently disables
        # upgrading, so take the record over rather than stranding the install.
        _log.warning(
            "taking over an abandoned upgrade journal (phase=%s, target=%s, runner_pid=%s)",
            existing.phase,
            existing.target_version,
            existing.runner_pid,
        )

    journal = UpgradeJournal(
        phase=PHASE_STARTING,
        target_version=body.target_version,
        from_version=__version__,
        started_ts=now,
        updated_ts=now,
        daemon_pid=os.getpid(),
        daemon_argv=list(sys.argv),
        daemon_cwd=os.getcwd(),
        daemon_env=dict(os.environ),
    )
    write_journal(jpath, journal)

    spawner = getattr(app.state, "upgrade_spawner", None) or _spawn_upgrade_runner
    spawner(body.target_version)

    return _update_status_payload(request)


__all__ = ["router"]
