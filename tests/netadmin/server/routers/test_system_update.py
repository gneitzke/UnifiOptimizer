"""The self-update API surface (docs/ARCHITECTURE.md section 23):
``GET/POST /api/system/update*``.

Fully offline over ``httpx.ASGITransport``: ``detect_install_method`` is
monkeypatched (no real filesystem/interpreter probing needed), the PyPI version
check is simulated by writing directly to ``app_meta`` (mirrors
``tests/netadmin/upgrade/test_checker.py``), and ``POST .../apply`` never spawns
a real subprocess -- ``app.state.upgrade_spawner`` is always a recorder here.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest

from netadmin import __version__
from netadmin.config import Settings
from netadmin.server.auth import ApiTokenAuthMiddleware, is_system_update_apply
from netadmin.server.main import DaemonComponents, create_app
from netadmin.server.routers import system as system_router
from netadmin.store.repository import Repository
from netadmin.upgrade.journal import (
    PHASE_STARTING,
    PHASE_SWAPPING,
    UpgradeJournal,
    journal_path_for,
    read_journal,
    write_journal,
)

TOKEN = "s3cr3t-test-token"
_UPDATE = "/api/system/update"
_DISMISS = "/api/system/update/dismiss"
_CHECK = "/api/system/update/check"
_APPLY = "/api/system/update/apply"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def token_settings(tmp_db_path: Path) -> Settings:
    return Settings(
        _env_file=None, db_path=tmp_db_path, site_id="default", netadmin_api_token=TOKEN
    )


@pytest.fixture
def token_store(token_settings: Settings) -> Any:
    store = Repository.open(token_settings.db_path, site_id=token_settings.site_id)
    yield store
    store.close()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records calls that WOULD have spawned the runner; never launches one."""
    calls: list[str] = []
    return calls


@pytest.fixture
def token_app(token_settings: Settings, token_store: Any, spawned: list[str]) -> Any:
    app = create_app(settings=token_settings, store=token_store, components=DaemonComponents())
    app.state.upgrade_spawner = lambda target_version: spawned.append(target_version)
    return app


def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=("10.1.2.3", 5555))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _install_info(
    monkeypatch: pytest.MonkeyPatch,
    *,
    method: str = "pip",
    variant: Optional[str] = None,
    self_upgrade_supported: bool = True,
) -> None:
    from netadmin.upgrade.detect import InstallInfo

    monkeypatch.setattr(
        system_router,
        "detect_install_method",
        lambda: InstallInfo(
            method=method, variant=variant, self_upgrade_supported=self_upgrade_supported
        ),
    )


# --------------------------------------------------------------------------- #
# GET /api/system/update
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_update_shape_and_defaults(
    token_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    async with _client(token_app) as c:
        resp = await c.get(_UPDATE)
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "current_version",
        "latest_version",
        "update_available",
        "install_method",
        "variant",
        "self_upgrade_supported",
        "checked_ts",
        "skipped_version",
        "snoozed_until",
        "upgrade_state",
        "release_url",
    ):
        assert key in body
    assert body["current_version"] == __version__
    assert body["latest_version"] is None
    assert body["update_available"] is False
    assert body["install_method"] == "pip"
    assert body["self_upgrade_supported"] is True
    assert body["skipped_version"] is None
    assert body["snoozed_until"] is None
    assert body["upgrade_state"] is None
    assert body["release_url"] is None


@pytest.mark.asyncio
async def test_get_update_is_open_even_with_a_token_configured(token_app: object) -> None:
    # No Authorization header at all -- GET reads are open once configured (18.1).
    async with _client(token_app) as c:
        resp = await c.get(_UPDATE)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_update_reflects_cached_version_and_release_url(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "9.9.9")
    token_store.set_app_meta("update.checked_ts", "1700000000")

    async with _client(token_app) as c:
        resp = await c.get(_UPDATE)
    body = resp.json()
    assert body["latest_version"] == "9.9.9"
    assert body["update_available"] is True
    assert body["checked_ts"] == 1700000000
    assert body["release_url"] == "https://pypi.org/project/unifioptimizer/9.9.9/"


@pytest.mark.asyncio
async def test_get_update_reports_install_method_and_variant(
    token_app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch, method="container", variant="macmini", self_upgrade_supported=False)
    async with _client(token_app) as c:
        resp = await c.get(_UPDATE)
    body = resp.json()
    assert body["install_method"] == "container"
    assert body["variant"] == "macmini"
    assert body["self_upgrade_supported"] is False


@pytest.mark.asyncio
async def test_get_update_surfaces_upgrade_state_from_the_journal(
    token_app: object, token_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    journal = UpgradeJournal(
        phase=PHASE_SWAPPING,
        target_version="0.4.0",
        from_version="0.3.0",
        started_ts=1000,
        updated_ts=2000,
        daemon_pid=123,
        daemon_argv=["/venv/bin/netadmin", "daemon"],
        daemon_cwd="/srv",
        daemon_env={"SECRET": "shhh"},
    )
    write_journal(journal_path_for(token_settings.db_path), journal)

    async with _client(token_app) as c:
        resp = await c.get(_UPDATE)
    state = resp.json()["upgrade_state"]
    assert state == {
        "phase": PHASE_SWAPPING,
        "target_version": "0.4.0",
        "from_version": "0.3.0",
        "started_ts": 1000,
        "updated_ts": 2000,
        "error": None,
    }
    # Internal restart plumbing (pid/argv/cwd/env) is never exposed over the API.
    assert "daemon_pid" not in state
    assert "daemon_env" not in state
    assert "shhh" not in resp.text


# --------------------------------------------------------------------------- #
# POST /api/system/update/dismiss
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dismiss_skip_records_the_currently_advertised_version(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "9.9.9")

    async with _client(token_app) as c:
        resp = await c.post(_DISMISS, json={"mode": "skip"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["skipped_version"] == "9.9.9"
    assert token_store.get_app_meta("update.skip_version") == "9.9.9"


@pytest.mark.asyncio
async def test_dismiss_skip_with_nothing_cached_is_a_harmless_noop(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    async with _client(token_app) as c:
        resp = await c.post(_DISMISS, json={"mode": "skip"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["skipped_version"] is None


@pytest.mark.asyncio
async def test_dismiss_snooze_sets_a_seven_day_window(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    before = int(time.time())
    async with _client(token_app) as c:
        resp = await c.post(_DISMISS, json={"mode": "snooze"}, headers=_auth())
    after = int(time.time())
    snoozed_until = resp.json()["snoozed_until"]
    assert before + 7 * 86400 <= snoozed_until <= after + 7 * 86400 + 1


@pytest.mark.asyncio
async def test_dismiss_requires_the_token_once_configured(token_app: object) -> None:
    async with _client(token_app) as c:
        resp = await c.post(_DISMISS, json={"mode": "skip"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dismiss_rejects_an_unknown_mode(token_app: object) -> None:
    async with _client(token_app) as c:
        resp = await c.post(_DISMISS, json={"mode": "delete-forever"}, headers=_auth())
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /api/system/update/check
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_check_forces_a_refresh_through_the_live_checker(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)

    class FakeChecker:
        def __init__(self) -> None:
            self.called = 0

        async def check_now(self) -> None:
            self.called += 1
            token_store.set_app_meta("update.latest_version", "7.7.7")
            token_store.set_app_meta("update.checked_ts", "42")

    fake = FakeChecker()
    token_app.state.version_checker = fake

    async with _client(token_app) as c:
        resp = await c.post(_CHECK, headers=_auth())
    assert resp.status_code == 200
    assert fake.called == 1
    assert resp.json()["latest_version"] == "7.7.7"


@pytest.mark.asyncio
async def test_check_without_a_live_checker_falls_back_to_cache(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_info(monkeypatch)
    token_app.state.version_checker = None  # e.g. the lifespan never ran
    token_store.set_app_meta("update.latest_version", "1.2.3")

    async with _client(token_app) as c:
        resp = await c.post(_CHECK, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["latest_version"] == "1.2.3"


# --------------------------------------------------------------------------- #
# POST /api/system/update/apply: fail-closed auth
# --------------------------------------------------------------------------- #


def test_is_system_update_apply_classifies_only_the_one_route() -> None:
    assert is_system_update_apply("POST", _APPLY)
    assert not is_system_update_apply("GET", _APPLY)
    assert not is_system_update_apply("POST", _UPDATE)
    assert not is_system_update_apply("POST", _APPLY + "/extra")


async def _ok_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.asyncio
async def test_apply_fails_closed_with_no_token_configured_at_all() -> None:
    # An unconfigured/open install must NOT let a stray POST trigger a self-upgrade.
    mw = ApiTokenAuthMiddleware(_ok_app, token=None)
    transport = httpx.ASGITransport(app=mw, client=("10.1.2.3", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(_APPLY, json={"target_version": "0.4.0"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "mutation_locked"


@pytest.mark.asyncio
async def test_apply_requires_the_token_when_one_is_configured() -> None:
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN)
    transport = httpx.ASGITransport(app=mw, client=("10.1.2.3", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        no_tok = await c.post(_APPLY, json={"target_version": "0.4.0"})
        wrong = await c.post(_APPLY, json={"target_version": "0.4.0"}, headers=_auth("nope"))
        good = await c.post(_APPLY, json={"target_version": "0.4.0"}, headers=_auth())
    assert no_tok.status_code == 401
    assert wrong.status_code == 401
    assert good.status_code == 200


@pytest.mark.asyncio
async def test_apply_is_rate_limited_like_a_controller_mutation() -> None:
    mw = ApiTokenAuthMiddleware(_ok_app, token=TOKEN, write_max=1, write_window_s=60.0)
    transport = httpx.ASGITransport(app=mw, client=("10.1.2.3", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        first = await c.post(_APPLY, json={"target_version": "0.4.0"}, headers=_auth())
        second = await c.post(_APPLY, json={"target_version": "0.4.0"}, headers=_auth())
    assert first.status_code == 200
    assert second.status_code == 429


# --------------------------------------------------------------------------- #
# POST /api/system/update/apply: handler behaviour (token present throughout)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_apply_409s_on_stale_target_version(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch, spawned: list[str]
) -> None:
    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "1.0.0")
    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "0.9.0"}, headers=_auth())
    assert resp.status_code == 409
    assert spawned == []


@pytest.mark.asyncio
async def test_apply_409s_when_nothing_has_ever_been_checked(
    token_app: object, monkeypatch: pytest.MonkeyPatch, spawned: list[str]
) -> None:
    _install_info(monkeypatch)
    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "1.0.0"}, headers=_auth())
    assert resp.status_code == 409
    assert spawned == []


@pytest.mark.asyncio
async def test_apply_409s_when_self_upgrade_is_unsupported(
    token_app: object, token_store: Any, monkeypatch: pytest.MonkeyPatch, spawned: list[str]
) -> None:
    _install_info(monkeypatch, method="addon", self_upgrade_supported=False)
    token_store.set_app_meta("update.latest_version", "1.0.0")
    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "1.0.0"}, headers=_auth())
    assert resp.status_code == 409
    assert "not supported" in resp.json()["detail"]
    assert spawned == []


@pytest.mark.asyncio
async def test_apply_409s_when_an_upgrade_is_already_in_flight(
    token_app: object,
    token_settings: Settings,
    token_store: Any,
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[str],
) -> None:
    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "1.0.0")
    write_journal(
        journal_path_for(token_settings.db_path),
        UpgradeJournal(
            phase=PHASE_SWAPPING,
            target_version="1.0.0",
            from_version="0.9.0",
            started_ts=1,
            # A genuinely LIVE upgrade: touched just now, by a process that exists.
            # (An ancient timestamp with no runner pid is an abandoned record, which
            # is deliberately takeable-over; see the abandoned-journal test below.)
            updated_ts=int(time.time()),
            runner_pid=os.getpid(),
        ),
    )
    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "1.0.0"}, headers=_auth())
    assert resp.status_code == 409
    assert "already in flight" in resp.json()["detail"]
    assert spawned == []


@pytest.mark.asyncio
async def test_apply_success_primes_the_journal_and_spawns_the_runner(
    token_app: object,
    token_settings: Settings,
    token_store: Any,
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[str],
) -> None:
    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "1.0.0")

    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "1.0.0"}, headers=_auth())
    assert resp.status_code == 200
    assert spawned == ["1.0.0"]

    journal = read_journal(journal_path_for(token_settings.db_path))
    assert journal is not None
    assert journal.phase == PHASE_STARTING
    assert journal.target_version == "1.0.0"
    assert journal.from_version == __version__
    assert journal.daemon_argv  # something was recorded
    assert journal.daemon_cwd


@pytest.mark.asyncio
async def test_apply_a_second_time_after_a_completed_journal_is_allowed(
    token_app: object,
    token_settings: Settings,
    token_store: Any,
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[str],
) -> None:
    from netadmin.upgrade.journal import PHASE_DONE

    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "1.0.0")
    write_journal(
        journal_path_for(token_settings.db_path),
        UpgradeJournal(
            phase=PHASE_DONE,
            target_version="0.9.0",
            from_version="0.8.0",
            started_ts=1,
            updated_ts=2,
        ),
    )
    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "1.0.0"}, headers=_auth())
    assert resp.status_code == 200
    assert spawned == ["1.0.0"]


@pytest.mark.asyncio
async def test_apply_takes_over_an_abandoned_upgrade_journal(
    token_app: object,
    token_settings: Settings,
    token_store: Any,
    monkeypatch: pytest.MonkeyPatch,
    spawned: list[str],
) -> None:
    """A killed runner must not disable upgrading forever.

    A SIGKILL, an OOM kill or a reboot mid-upgrade freezes the phase. Treating that
    as in-flight forever means one crash permanently bricks the upgrade mechanism,
    which is its own kind of brick.
    """
    _install_info(monkeypatch)
    token_store.set_app_meta("update.latest_version", "1.0.0")
    write_journal(
        journal_path_for(token_settings.db_path),
        UpgradeJournal(
            phase=PHASE_SWAPPING,
            target_version="1.0.0",
            from_version="0.9.0",
            started_ts=1,
            updated_ts=2,  # ancient
            runner_pid=999_999,  # and no such process
        ),
    )
    async with _client(token_app) as c:
        resp = await c.post(_APPLY, json={"target_version": "1.0.0"}, headers=_auth())

    assert resp.status_code == 200, resp.text
    assert spawned, "the runner must actually be spawned after taking over"
