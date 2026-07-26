"""ASGI-transport tests for GET /api/health, plus build_health unit coverage."""

from __future__ import annotations

import time

import httpx
import pytest

from netadmin import __version__
from netadmin.server.runtime import UNKNOWN, DaemonState, build_health

pytestmark_async = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_endpoint_shape(app: object) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "status",
        "ready",
        "version",
        "uptime_s",
        "db",
        "entities",
        "jobs",
        "websocket",
        "backfill",
    ):
        assert key in body
    assert body["version"] == __version__
    # seeded store has two entities
    assert body["entities"]["total"] == 2
    assert body["entities"]["by_type"] == {"ap": 1, "switch": 1}
    # every real collector + probe job appears, all UNKNOWN with no poll_runs yet
    job_names = {j["job"] for j in body["jobs"]}
    assert {
        "fast_device",
        "fast_sta",
        "fast_health",
        "events_catchup",
        "reports_5min",
        "probe.dns",
        "probe.gw_rtt",
    } <= job_names
    assert all(j["status"] == UNKNOWN for j in body["jobs"])
    # db file exists and has size; websocket unknown (no supervisor injected)
    assert body["db"]["size_bytes"] is not None and body["db"]["size_bytes"] > 0
    assert body["websocket"]["state"] == UNKNOWN


def test_build_health_reports_last_poll_age_and_failures(seeded_store, settings) -> None:
    now = 2_000_000_000
    # fast_device: last success 120s ago, then two failures after it
    seeded_store.record_poll_run(job="fast_device", ok=True, ts=now - 300)
    seeded_store.record_poll_run(job="fast_device", ok=True, ts=now - 120)
    seeded_store.record_poll_run(job="fast_device", ok=False, ts=now - 60, error="timeout")
    seeded_store.record_poll_run(job="fast_device", ok=False, ts=now - 30, error="timeout")

    state = DaemonState(started_ts=now - 1000, ready=True)
    health = build_health(seeded_store, state, settings, now=now)

    device = next(j for j in health["jobs"] if j["job"] == "fast_device")
    assert device["last_success_age_s"] == 120
    assert device["consecutive_failures"] == 2
    assert device["status"] == "failing"
    # a job that only ever succeeded recently is ok
    seeded_store.record_poll_run(job="fast_sta", ok=True, ts=now - 10)
    health = build_health(seeded_store, state, settings, now=now)
    sta = next(j for j in health["jobs"] if j["job"] == "fast_sta")
    assert sta["status"] == "ok"
    assert sta["consecutive_failures"] == 0
    # overall degraded because fast_device is failing
    assert health["status"] == "degraded"


def test_build_health_reports_the_running_package_version(seeded_store, settings) -> None:
    """The self-update banner (section 23) needs an honest current version, never
    a hardcoded literal -- this is the field it reads on the daemon side."""
    state = DaemonState(started_ts=0, ready=True)
    health = build_health(seeded_store, state, settings, now=0)
    assert health["version"] == __version__


def test_build_health_stale_job(seeded_store, settings) -> None:
    now = 2_000_000_000
    # fast_health cadence is 60s; a success 10 minutes ago is stale (> 2.5x cadence).
    # This is the phantom-name regression: before the fix _job_interval returned
    # None for the real job id and the staleness branch never fired -> 'ok'.
    seeded_store.record_poll_run(job="fast_health", ok=True, ts=now - 600)
    state = DaemonState(started_ts=now - 1000, ready=True)
    health = build_health(seeded_store, state, settings, now=now)
    job = next(j for j in health["jobs"] if j["job"] == "fast_health")
    assert job["status"] == "stale"
    assert health["status"] == "degraded"


def test_build_health_probe_job_staleness_fires(seeded_store, settings) -> None:
    # Probe jobs must surface in health AND go stale: probe.gw_rtt cadence is the
    # 60s probe_s, so a success 10 minutes ago is stale. Before the fix probe jobs
    # were absent from the job registry entirely.
    now = 2_000_000_000
    seeded_store.record_poll_run(job="probe.gw_rtt", ok=True, ts=now - 600)
    state = DaemonState(started_ts=now - 1000, ready=True)
    health = build_health(seeded_store, state, settings, now=now)
    job = next(j for j in health["jobs"] if j["job"] == "probe.gw_rtt")
    assert job["interval_s"] == 60
    assert job["status"] == "stale"


def test_build_health_starting_before_ready(seeded_store, settings) -> None:
    state = DaemonState(started_ts=int(time.time()), ready=False)
    health = build_health(seeded_store, state, settings)
    assert health["status"] == "starting"
    assert health["ready"] is False


def test_build_health_reports_detect_and_sle_jobs(seeded_store, settings) -> None:
    # /api/health gains the detection tiers + baseline + SLE minute job rows.
    now = 2_000_000_000
    state = DaemonState(started_ts=now - 1000, ready=True)
    health = build_health(seeded_store, state, settings, now=now)
    by_job = {j["job"]: j for j in health["jobs"]}
    for job in (
        "detect_fast",
        "detect_window",
        "detect_daily",
        "baseline",
        "sle_minutes",
        "correlate",
    ):
        assert job in by_job, f"{job} missing from /api/health"
        assert by_job[job]["status"] == UNKNOWN  # never run yet
    # cadences resolve from the detect / sle / correlate settings sections, not poll
    assert by_job["detect_window"]["interval_s"] == settings.detect.window_s
    assert by_job["baseline"]["interval_s"] == settings.detect.baseline_s
    assert by_job["sle_minutes"]["interval_s"] == settings.sle.minutes_s
    assert by_job["correlate"]["interval_s"] == settings.correlate.interval_s

    # a detect pass that recorded a recent success reports ok; a stale one degrades
    seeded_store.record_poll_run(job="detect_window", ok=True, ts=now - 30)
    health = build_health(seeded_store, state, settings, now=now)
    assert next(j for j in health["jobs"] if j["job"] == "detect_window")["status"] == "ok"


def test_build_health_unknown_store(settings) -> None:
    state = DaemonState(started_ts=1, ready=True)
    health = build_health(None, state, settings, now=100)
    assert health["entities"]["total"] == UNKNOWN
    assert all(j["status"] == UNKNOWN for j in health["jobs"])
    assert health["uptime_s"] == 99


def test_build_health_prefers_collector_status_snapshot(seeded_store, settings) -> None:
    now = 2_000_000_000
    # A collector reporting real job ids (fast_device) with a live counter.
    snapshot = {
        "consecutive_failures": {"fast_device": 3, "fast_sta": 0},
        "last_ok_ts": {"fast_device": None, "fast_sta": now - 30},
        "last_run_ts": {"fast_device": now - 10, "fast_sta": now - 30},
    }

    class Status:
        def snapshot(self) -> dict:
            return snapshot

    state = DaemonState(started_ts=now - 100, ready=True, collector_status=Status())
    health = build_health(seeded_store, state, settings, now=now)
    names = {j["job"]: j for j in health["jobs"]}
    # real collector job ids surface even though they differ from DEFAULT_JOBS
    assert names["fast_device"]["status"] == "failing"
    assert names["fast_device"]["consecutive_failures"] == 3
    assert names["fast_sta"]["status"] == "ok"
    assert names["fast_sta"]["last_success_age_s"] == 30
    assert health["status"] == "degraded"


def test_build_health_unavailable_component_is_degraded(seeded_store, settings) -> None:
    state = DaemonState(started_ts=1, ready=True)
    state.unavailable["scheduler"] = "ImportError: no collector yet"
    health = build_health(seeded_store, state, settings, now=100)
    assert health["status"] == "degraded"
    assert health["components"]["scheduler"].startswith("unavailable:")
