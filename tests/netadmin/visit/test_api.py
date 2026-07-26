"""Background-run lifecycle for the ``/api/visit`` on-demand router.

The heavy :func:`run_visit` is stubbed (it is exercised for real in
``test_runner``); these tests assert the router's contract: POST launches a
background run, GET polls it from ``running`` to ``done`` and returns the report,
and a second POST while one is in flight is rejected.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from netadmin.server.main import DaemonComponents, create_app
from netadmin.server.routers import ondemand
from netadmin.visit.runner import STEP_ORDER, VisitReport, VisitStep

pytestmark = pytest.mark.asyncio


def _fake_report() -> VisitReport:
    return VisitReport(
        started_ts=1_900_000_000,
        finished_ts=1_900_000_010,
        window_start_ts=1_899_990_000,
        window_end_ts=1_900_000_000,
        site_id="default",
        lookback_days=2,
        controller_host="unifi.local",
        headline_score=0.9,
        sles={"sles": {}},
        issues=[{"detector_key": "wifi.channel_plan", "severity": "p3", "state": "active"}],
        issue_counts={"total": 1, "p1": 0, "p2": 0, "p3": 1, "open": 1},
        topology={"entity_count": 5, "by_type": {"ap": 1}, "devices": []},
        coverage=[],
        caveats=["thin live coverage"],
        steps=[VisitStep(id=s, label=lbl).to_dict() for s, lbl in STEP_ORDER],
        db_path="/tmp/visit.db",
    )


def _stub_run_visit(barrier: asyncio.Event):
    """A run_visit stub that streams progress, waits on a gate, then returns."""

    def _run(settings, *, lookback_days=None, progress=None):
        for sid, label in STEP_ORDER:
            step = VisitStep(id=sid, label=label, status="running")
            if progress:
                progress(step)
            step.status = "ok"
            if progress:
                progress(step)
        # Block until the test releases us, so the "running" state is observable.
        while not barrier.is_set():
            import time

            time.sleep(0.005)
        return _fake_report()

    return _run


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def app(visit_settings):
    return create_app(settings=visit_settings, components=DaemonComponents())


async def test_visit_idle_before_any_run(app):
    async with await _client(app) as c:
        resp = await c.get("/api/visit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


async def test_visit_run_lifecycle(app, visit_settings, monkeypatch):
    barrier = asyncio.Event()
    monkeypatch.setattr(ondemand, "run_visit", _stub_run_visit(barrier))

    async with await _client(app) as c:
        start = await c.post("/api/visit", json={"lookback_days": 2})
        assert start.status_code == 200
        body = start.json()
        assert body["status"] == "running"
        run_id = body["run_id"]
        assert [s["id"] for s in body["steps"]] == [sid for sid, _ in STEP_ORDER]

        # A concurrent run is refused while this one is in flight.
        conflict = await c.post("/api/visit", json={})
        assert conflict.status_code == 409

        # Release the stub and poll until the run completes.
        barrier.set()
        report = None
        for _ in range(200):
            await asyncio.sleep(0.02)
            poll = await c.get("/api/visit")
            data = poll.json()
            if data["status"] == "done":
                report = data
                break
        assert report is not None, "visit run never completed"
        assert report["run_id"] == run_id
        assert report["report"]["issue_counts"]["open"] == 1


async def test_visit_run_failure_is_reported(app, monkeypatch):
    def _boom(settings, *, lookback_days=None, progress=None):
        raise RuntimeError("controller unreachable")

    monkeypatch.setattr(ondemand, "run_visit", _boom)

    async with await _client(app) as c:
        await c.post("/api/visit", json={})
        data = None
        for _ in range(200):
            await asyncio.sleep(0.02)
            data = (await c.get("/api/visit")).json()
            if data["status"] in ("failed", "done"):
                break
        assert data is not None and data["status"] == "failed"
        assert "controller unreachable" in data["error"]


async def test_visit_lookback_is_bounded(app):
    async with await _client(app) as c:
        resp = await c.post("/api/visit", json={"lookback_days": 999})
    assert resp.status_code == 422
