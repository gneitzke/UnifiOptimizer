"""Daemon wiring and the ``/api/health`` alerts block (sections 12 and 20).

No test here emits a transition through a started dispatcher, so no lifespan test
can make a real outbound request; delivery behaviour is covered against the fake
transport in ``test_dispatcher.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from netadmin.config import Settings
from netadmin.integrations.alerts import build_alert_dispatcher
from netadmin.issues.engine import IssueEngine
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.server.main import DaemonComponents, create_app
from netadmin.server.runtime import DaemonState, build_health
from netadmin.store.repository import Repository

from .conftest import DISCORD_URL, FakeEngine, alerts_settings

CHANNELS = [{"name": "discord_ops", "type": "discord"}]


@pytest.fixture
def settings(tmp_db_path: Path) -> Settings:
    return alerts_settings(tmp_db_path, CHANNELS, enabled=False)


@pytest.fixture
def store(settings: Settings) -> Iterator[Repository]:
    repo = Repository.open(settings.db_path, site_id=settings.site_id)
    yield repo
    repo.close()


class StubDispatcher:
    """A dispatcher stand-in exposing only what health reads."""

    def __init__(self, block: dict[str, Any]) -> None:
        self._block = block

    def health(self) -> dict[str, Any]:
        return self._block


def _state(**kw: Any) -> DaemonState:
    state = DaemonState(started_ts=0, ready=True)
    for key, value in kw.items():
        setattr(state, key, value)
    return state


def _channel_block(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "discord_ops",
        "type": "discord",
        "configured": True,
        "status": "ok",
        "delivered": 3,
        "failed": 0,
        "dropped": 0,
        "digested": 0,
        "last_success_ts": 1_900_000_000,
        "last_error": None,
    }
    base.update(kw)
    return base


# --- health block ---------------------------------------------------------- #


def test_health_reports_an_honest_empty_block_without_a_dispatcher(settings, store) -> None:
    health = build_health(store, _state(), settings)
    assert health["alerts"] == {"enabled": False, "running": False, "channels": []}
    assert health["status"] == "ok"


def test_health_projects_per_channel_counters(settings, store) -> None:
    dispatcher = StubDispatcher(
        {"enabled": True, "running": True, "intake_dropped": 0, "channels": [_channel_block()]}
    )
    health = build_health(store, _state(alerts=dispatcher), settings)
    channel = health["alerts"]["channels"][0]
    assert channel["name"] == "discord_ops"
    assert channel["delivered"] == 3
    assert channel["status"] == "ok"
    assert health["status"] == "ok"


def test_a_failing_channel_degrades_overall_health(settings, store) -> None:
    """Notifications not reaching anyone is exactly what health exists to surface."""
    dispatcher = StubDispatcher(
        {
            "enabled": True,
            "running": True,
            "channels": [_channel_block(status="failing", failed=5, last_error="HTTP 500")],
        }
    )
    health = build_health(store, _state(alerts=dispatcher), settings)
    assert health["status"] == "degraded"


def test_an_inert_channel_does_not_degrade_health(settings, store) -> None:
    """Inert is a configuration choice, not a fault."""
    dispatcher = StubDispatcher(
        {
            "enabled": True,
            "running": True,
            "channels": [_channel_block(configured=False, status="inert", delivered=0)],
        }
    )
    health = build_health(store, _state(alerts=dispatcher), settings)
    assert health["status"] == "ok"


def test_an_unbuildable_dispatcher_surfaces_its_reason(settings, store) -> None:
    state = _state()
    state.mark_unavailable("alerts", RuntimeError("boom"))
    health = build_health(store, state, settings)
    assert health["alerts"]["enabled"] is False
    assert "RuntimeError" in health["alerts"]["detail"]
    assert health["status"] == "degraded"


def test_health_output_carries_no_delivery_url(settings, store, tmp_db_path) -> None:
    alert_settings = alerts_settings(tmp_db_path, CHANNELS, urls={"discord_ops": DISCORD_URL})
    dispatcher = build_alert_dispatcher(alert_settings, FakeEngine())
    health = build_health(store, _state(alerts=dispatcher), settings)
    assert DISCORD_URL not in repr(health["alerts"])
    assert "secret-token" not in repr(health["alerts"])
    assert health["alerts"]["channels"][0]["configured"] is True


# --- lifespan -------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lifespan_builds_the_dispatcher_even_when_disabled(tmp_db_path) -> None:
    settings = alerts_settings(tmp_db_path, CHANNELS, enabled=False)
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    engine = IssueEngine(StoreIssueRepository(store))
    app = create_app(
        settings=settings, store=store, issue_engine=engine, components=DaemonComponents()
    )
    try:
        async with app.router.lifespan_context(app):
            dispatcher = app.state.alerts
            assert dispatcher is not None
            assert dispatcher.running is False
            # Off means off: no engine callback beyond the WebSocket broadcaster.
            assert len(engine._callbacks) == 1  # noqa: SLF001 - asserting the no-op contract
            health = build_health(store, app.state.daemon, settings)
            assert health["alerts"]["enabled"] is False
            assert health["status"] != "degraded"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_a_configured_dispatcher(tmp_db_path) -> None:
    settings = alerts_settings(tmp_db_path, CHANNELS, urls={"discord_ops": DISCORD_URL})
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    engine = IssueEngine(StoreIssueRepository(store))
    app = create_app(
        settings=settings, store=store, issue_engine=engine, components=DaemonComponents()
    )
    try:
        async with app.router.lifespan_context(app):
            dispatcher = app.state.alerts
            assert dispatcher.running is True
            assert len(engine._callbacks) == 2  # noqa: SLF001 - broadcaster + alerts
            assert app.state.daemon.alerts is dispatcher
            health = build_health(store, app.state.daemon, settings)
            assert health["alerts"]["channels"][0]["name"] == "discord_ops"
        # Shutdown stops it; the callback stays registered but goes inert.
        assert app.state.alerts.running is False
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_dispatcher_that_cannot_be_built_does_not_down_the_daemon(
    tmp_db_path, monkeypatch
) -> None:
    settings = alerts_settings(tmp_db_path, CHANNELS, urls={"discord_ops": DISCORD_URL})
    store = Repository.open(settings.db_path, site_id=settings.site_id)

    import netadmin.integrations.alerts as alerts_pkg

    def _explode(*_args: Any, **_kw: Any) -> Any:
        raise RuntimeError("constructor fault")

    monkeypatch.setattr(alerts_pkg, "build_alert_dispatcher", _explode)

    app = create_app(settings=settings, store=store, components=DaemonComponents())
    try:
        async with app.router.lifespan_context(app):
            assert app.state.alerts is None
            assert app.state.daemon.ready is True  # the daemon still came up
            health = build_health(store, app.state.daemon, settings)
            assert health["status"] == "degraded"
            assert "RuntimeError" in health["alerts"]["detail"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_app_state_has_an_alerts_slot_without_a_lifespan(tmp_db_path) -> None:
    settings = alerts_settings(tmp_db_path, CHANNELS)
    app = create_app(settings=settings, components=DaemonComponents())
    assert app.state.alerts is None
