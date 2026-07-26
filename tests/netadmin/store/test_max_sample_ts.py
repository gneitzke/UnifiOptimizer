"""Coverage for Repository.max_sample_ts (backfill's per-scope gap cursor)."""

from __future__ import annotations

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.metrics import MetricKind
from netadmin.store.repository import Repository, SampleReading


def test_max_sample_ts_returns_newest_per_entity_type(repo: Repository) -> None:
    ap = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01"), ts=100
    )
    repo.record_samples(
        [
            SampleReading(entity_id=ap, metric="num_sta", ts=100, value=5.0),
            SampleReading(entity_id=ap, metric="num_sta", ts=250, value=6.0),
        ]
    )
    assert repo.max_sample_ts(EntityType.AP) == 250
    # accepts the raw type string too
    assert repo.max_sample_ts("ap") == 250


def test_max_sample_ts_none_when_no_samples(repo: Repository) -> None:
    repo.upsert_entity(Entity(entity_type=EntityType.GATEWAY, native_id="aa:bb:cc:00:00:g1"), ts=1)
    assert repo.max_sample_ts(EntityType.GATEWAY) is None
    assert repo.max_sample_ts(EntityType.CLIENT) is None


def test_max_sample_ts_for_metrics_ignores_live_series(repo: Repository) -> None:
    # The report-backfill gap-cursor regression: an AP entity has a RECENT live
    # series (cpu, written every 60s) and an OLD report-only series (rx_bytes,
    # only ever backfilled). Anchoring the report gap on the whole entity type
    # (max_sample_ts) reads the recent cpu ts and the gap looks permanently
    # closed; anchoring on the report metric reads the honest old rx_bytes ts.
    ap = repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:01"), ts=1)
    repo.record_samples([SampleReading(entity_id=ap, metric="cpu", ts=9_000, value=12.0)])
    # Backfill stores report byte totals verbatim (kind=GAUGE), so a row lands on
    # the first reading -- exactly the report-only data the gap cursor anchors on.
    repo.record_samples(
        [SampleReading(entity_id=ap, metric="rx_bytes", ts=1_000, value=1.0, kind=MetricKind.GAUGE)]
    )

    assert repo.max_sample_ts(EntityType.AP) == 9_000  # dominated by the live series
    assert repo.max_sample_ts_for_metrics(EntityType.AP, ["rx_bytes", "tx_bytes"]) == 1_000
    repo.record_samples(
        [SampleReading(entity_id=ap, metric="rx_bytes", ts=1_120, value=2.0, kind=MetricKind.GAUGE)]
    )
    assert repo.max_sample_ts_for_metrics(EntityType.AP, ["rx_bytes"]) == 1_120


def test_max_sample_ts_for_metrics_none_and_empty(repo: Repository) -> None:
    ap = repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id="aa:bb:cc:00:00:02"), ts=1)
    repo.record_samples([SampleReading(entity_id=ap, metric="num_sta", ts=500, value=3.0)])
    # No sample for the requested metrics -> None (fresh install -> full history).
    assert repo.max_sample_ts_for_metrics(EntityType.AP, ["rx_bytes"]) is None
    # Empty metric set is None, never a query over every series.
    assert repo.max_sample_ts_for_metrics(EntityType.AP, []) is None
