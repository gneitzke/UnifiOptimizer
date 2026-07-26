"""GET /api/sle: the confidence-floor / quiet-pass / not-measurable overlay.

Covers what a bare ``sle_minutes`` GROUP BY cannot tell apart on its own (see
netadmin.server.routers.sle._measurability): connect reported "not measurable on
this controller" only when it has no data AND the event pipeline itself looks
dead, never merely because it has no data; roaming reported a confirmed "quiet
pass" only when coverage's own exposure proves clients were observable.
"""

from __future__ import annotations

import time

import httpx
import pytest

from netadmin.server.main import DaemonComponents, create_app
from netadmin.store.repository import Repository

pytestmark = pytest.mark.asyncio


async def _client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_connect_not_measurable_when_event_pipeline_looks_dead(app, seeded_store) -> None:
    # seeded_store has zero events (max_event_ts() is None) and no sle_minutes at
    # all for connect -> the honest "not measurable" state, not a bare "no data".
    async with await _client(app) as c:
        resp = await c.get("/api/sle")
    body = resp.json()

    connect = body["sles"]["connect"]
    assert connect["score"] is None
    assert connect["measurable"] is False
    assert connect["unmeasurable_reason"] == "connection events unavailable"
    assert "connect" in body["excluded_not_measurable"]
    assert "connect" not in body["excluded_no_data"]


async def test_connect_stays_no_data_when_event_pipeline_is_alive(
    app, seeded_store: Repository
) -> None:
    # An unrelated event landed recently -> the pipeline is alive, so connect's
    # own absence of data is a real (if unmeasured) gap, not a broken pipeline.
    now = int(time.time())
    seeded_store.record_event(ts=now - 10, key="ANOMALY_SOMETHING", native_id="evt-anomaly-1")

    async with await _client(app) as c:
        resp = await c.get("/api/sle")
    body = resp.json()

    connect = body["sles"]["connect"]
    assert connect["score"] is None
    assert connect["measurable"] is True
    assert connect["unmeasurable_reason"] is None
    assert "connect" in body["excluded_no_data"]
    assert "connect" not in body["excluded_not_measurable"]


async def test_connect_with_real_data_is_never_marked_not_measurable(
    app, seeded_store: Repository
) -> None:
    # No events at all (the pipeline looks dead by the same signal as the first
    # test), but connect DID score something this time (e.g. a link-local-IP
    # DHCP failure, which needs no lifecycle event) -- real evidence must never
    # be overridden by the not-measurable heuristic.
    now = int(time.time())
    bucket = (now - (now % 300)) - 300
    seeded_store.upsert_sle_minute(
        bucket_ts=bucket, sle="connect", classifier="dhcp", entity_id=100, minutes=5.0
    )

    async with await _client(app) as c:
        resp = await c.get("/api/sle")
    body = resp.json()

    connect = body["sles"]["connect"]
    assert connect["score"] == 0.0
    assert connect["measurable"] is True
    assert connect["unmeasurable_reason"] is None


async def test_roaming_quiet_pass_when_coverage_confirms_clients_were_observable(
    app, seeded_store: Repository
) -> None:
    now = int(time.time())
    bucket = (now - (now % 300)) - 300
    # A one-bucket-wide window: coverage judged the ENTIRE window (100% exposure)
    # -> roaming's absence of any row reads as a confirmed quiet pass, not a gap.
    seeded_store.upsert_sle_minute(
        bucket_ts=bucket, sle="coverage", classifier="ok", entity_id=100, minutes=5.0
    )

    async with await _client(app) as c:
        resp = await c.get("/api/sle", params={"start": bucket, "end": bucket + 300})
    body = resp.json()

    roaming = body["sles"]["roaming"]
    assert roaming["score"] is None
    assert roaming["quiet_pass"] is True
    assert roaming["measurable"] is True  # quiet_pass never implies unmeasurable


async def test_roaming_not_quiet_pass_without_coverage_exposure(app, seeded_store) -> None:
    # Nothing seeded at all: coverage itself has no exposure, so roaming's
    # absence of data cannot be read as a confirmed quiet pass -- it is a real
    # measurement gap (the neutral "insufficient data" state, not a green one).
    async with await _client(app) as c:
        resp = await c.get("/api/sle")
    body = resp.json()

    roaming = body["sles"]["roaming"]
    assert roaming["score"] is None
    assert roaming["quiet_pass"] is False


async def test_window_buckets_matches_default_24h_window(app, seeded_store) -> None:
    async with await _client(app) as c:
        resp = await c.get("/api/sle")
    body = resp.json()
    assert body["window_buckets"] == 288  # 86_400s / 300s
