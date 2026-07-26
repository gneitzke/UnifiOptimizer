"""Lifespan startup/shutdown tests with injected fake subsystems."""

from __future__ import annotations

import asyncio

import pytest

from netadmin.server.main import DaemonComponents, build_default_components, create_app
from netadmin.server.runtime import DaemonState

from .conftest import FakeScheduler, FakeSupervisor

pytestmark = pytest.mark.asyncio


async def test_lifespan_starts_and_stops_all_components(settings, seeded_store) -> None:
    sched = FakeScheduler()
    ws = FakeSupervisor(state="connected")
    probes = FakeSupervisor()
    backfill_ran = asyncio.Event()

    async def backfill() -> None:
        backfill_ran.set()

    comps = DaemonComponents(scheduler=sched, ws_supervisor=ws, probes=probes, backfill=backfill)
    app = create_app(settings=settings, store=seeded_store, components=comps)

    async with app.router.lifespan_context(app):
        assert sched.started is True
        assert ws.started is True
        assert probes.started is True
        assert app.state.daemon.ready is True
        await asyncio.wait_for(backfill_ran.wait(), timeout=1.0)
        # give the backfill task a tick to record completion
        await asyncio.sleep(0)
        assert app.state.daemon.backfill_status in ("running", "done")

    # after shutdown
    assert app.state.daemon.ready is False
    assert sched.shutdown_called is True
    assert sched.shutdown_wait is False
    assert ws.stopped is True
    assert probes.stopped is True


async def test_lifespan_reports_ws_state_in_health(settings, seeded_store) -> None:
    ws = FakeSupervisor(state="connected")
    comps = DaemonComponents(scheduler=FakeScheduler(), ws_supervisor=ws)
    app = create_app(settings=settings, store=seeded_store, components=comps)

    async with app.router.lifespan_context(app):
        from netadmin.server.runtime import build_health

        health = build_health(seeded_store, app.state.daemon, settings)
        assert health["websocket"]["state"] == "connected"
        assert health["components"]["ws_supervisor"] == "ok"
        assert health["components"]["probes"] == "UNKNOWN"


async def test_lifespan_opens_and_closes_store_when_not_injected(settings) -> None:
    app = create_app(settings=settings, store=None, components=DaemonComponents())
    async with app.router.lifespan_context(app):
        assert app.state.store is not None
        store = app.state.store
        assert app.state.issue_engine is not None
    # store the lifespan owns is closed on shutdown
    with pytest.raises(Exception):
        store.connection.execute("SELECT 1")


async def test_component_start_failure_is_recorded_not_fatal(settings, seeded_store) -> None:
    class BadSupervisor:
        async def start(self) -> None:
            raise RuntimeError("boom")

        async def stop(self) -> None:  # pragma: no cover - never started
            pass

    comps = DaemonComponents(ws_supervisor=BadSupervisor())
    app = create_app(settings=settings, store=seeded_store, components=comps)
    async with app.router.lifespan_context(app):
        assert app.state.daemon.ready is True
        assert "ws_supervisor" in app.state.daemon.unavailable


async def test_lifespan_starts_and_stops_the_version_checker(settings, seeded_store) -> None:
    """Section 23 foundation: the checker is built and started by the lifespan,
    and stopped cleanly on shutdown (its background task fully torn down)."""
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())

    async with app.router.lifespan_context(app):
        checker = app.state.version_checker
        assert checker is not None
        assert checker.running is True

    assert checker.running is False


async def test_version_checker_disabled_by_config_still_boots_cleanly(
    settings, seeded_store
) -> None:
    disabled_updates = settings.updates.model_copy(update={"check": False})
    settings = settings.model_copy(update={"updates": disabled_updates})
    app = create_app(settings=settings, store=seeded_store, components=DaemonComponents())

    async with app.router.lifespan_context(app):
        checker = app.state.version_checker
        assert checker is not None
        assert checker.running is False  # disabled: start() was a no-op


async def test_build_default_components_tolerates_absent_peers(settings, seeded_store) -> None:
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.store_repository import StoreIssueRepository

    engine = IssueEngine(StoreIssueRepository(seeded_store))
    state = DaemonState(started_ts=0)
    comps = build_default_components(settings, seeded_store, engine, state)
    # peers not merged yet -> everything unavailable, nothing raised
    assert isinstance(comps, DaemonComponents)
    assert "scheduler" in state.unavailable
    assert comps.scheduler is None
