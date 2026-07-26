"""Fixtures and seeding helpers for the SLE test suite.

The helpers write directly through the repository so the SLE job is exercised
against the real store seam, not a mock. Counter-typed metrics (rx_bytes,
roam_count) are stored with an explicit GAUGE ``kind`` override so a test controls
the *stored per-interval delta rows* verbatim — which is exactly what the job
reads back — instead of having to feed cumulative values and reason about
delta/reset semantics that ``store`` already unit-tests elsewhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pytest

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import Repository, SampleReading

BUCKET = 300


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


def put(
    repo: Repository,
    entity_id: int,
    metric: str,
    points: Iterable[tuple[int, float]],
    *,
    verbatim: bool = True,
) -> None:
    """Insert samples ``(ts, value)``. ``verbatim`` forces GAUGE storage so counter
    metrics land as the exact delta rows given (see module docstring)."""
    kind = MetricKind.GAUGE if verbatim else None
    repo.record_samples(SampleReading(entity_id, metric, ts, val, kind=kind) for ts, val in points)


def seed_ap(repo: Repository, native_id: str = "ap-1", ts: int = 0) -> int:
    return repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=native_id, site_id="default", name=native_id),
        ts=ts,
    )


def seed_switch(repo: Repository, native_id: str = "sw-1", ts: int = 0) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.SWITCH, native_id=native_id, site_id="default", name=native_id
        ),
        ts=ts,
    )


def seed_gateway(repo: Repository, native_id: str = "gw-1", ts: int = 0) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.GATEWAY, native_id=native_id, site_id="default", name=native_id
        ),
        ts=ts,
    )


def seed_radio(repo: Repository, ap_id: int, native_id: str = "ap-1:ng", ts: int = 0) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id=native_id,
            site_id="default",
            parent_id=ap_id,
            name=native_id,
        ),
        ts=ts,
    )


def seed_client(
    repo: Repository,
    native_id: str,
    *,
    parent_id: Optional[int] = None,
    ts: int = 0,
) -> int:
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=native_id,
            site_id="default",
            name=native_id,
            parent_id=parent_id,
        ),
        ts=ts,
    )


def make_active(
    repo: Repository, client_id: int, bucket_ts: int, *, total_bytes: float = 50_000.0
) -> None:
    """Give a client enough traffic in a bucket to pass the activity gate."""
    put(
        repo,
        client_id,
        "rx_bytes",
        [(bucket_ts + 30, total_bytes / 2.0), (bucket_ts + 90, total_bytes / 2.0)],
    )


def rssi(repo: Repository, client_id: int, points: Iterable[tuple[int, float]]) -> None:
    put(repo, client_id, "rssi", points)
