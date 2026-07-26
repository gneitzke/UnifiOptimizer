"""Unit tests for the offender ranking (ARCHITECTURE.md section 17).

Drives :func:`netadmin.analytics.rank_offenders` over a real migrated store
seeded with SLE minutes, issues, and events, asserting the composite ranking
math, severity weighting, window filtering, empty-window handling, and the
settings weight override — all against the pure GROUP BYs in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.analytics.offenders import (
    DEFAULT_OFFENDER_WEIGHTS,
    OffenderScore,
    load_offender_weights,
    rank_offenders,
)
from netadmin.config import Settings
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository

BASE = 1_700_000_000
DEVICE_TYPES = (EntityType.AP.value, EntityType.SWITCH.value, EntityType.GATEWAY.value)
CLIENT_TYPES = (EntityType.CLIENT.value,)


@pytest.fixture
def repo(tmp_db_path: Path) -> Repository:
    r = Repository.open(tmp_db_path)
    yield r
    r.close()


def _ap(repo: Repository, mac: str, name: str) -> int:
    return repo.upsert_entity(Entity(entity_type=EntityType.AP, native_id=mac, name=name), ts=BASE)


def _client(repo: Repository, mac: str, name: str) -> int:
    return repo.upsert_entity(
        Entity(entity_type=EntityType.CLIENT, native_id=mac, name=name), ts=BASE
    )


def _fail_minute(
    repo: Repository, bucket_ts: int, entity: int, attributed: int, minutes: float
) -> None:
    repo.upsert_sle_minute(
        bucket_ts=bucket_ts,
        sle="coverage",
        classifier="weak_signal",
        entity_id=entity,
        attributed_entity_id=attributed,
        minutes=minutes,
    )


# ---------------------------------------------------------------------------- #
# Ranking math
# ---------------------------------------------------------------------------- #


def test_worst_device_ranks_first(repo: Repository) -> None:
    """The AP with the most failed-minutes + severe issues tops the list."""
    bad = _ap(repo, "aa:bb:cc:00:00:01", "ap-bad")
    mild = _ap(repo, "aa:bb:cc:00:00:02", "ap-mild")
    clientp = _client(repo, "11:22:33:44:55:01", "phone")
    client2 = _client(repo, "11:22:33:44:55:02", "laptop")

    # ap-bad: 500 failed client-minutes attributed to it + a P1 issue.
    _fail_minute(repo, BASE, clientp, bad, 500.0)
    repo.insert_issue(
        fingerprint="fp-bad",
        detector_key="wifi.airtime_saturation",
        severity="p1",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="airtime",
        entity_id=bad,
    )
    # ap-mild: 50 failed minutes (a different client, so no PK collision with the
    # cell above) + a P3 issue.
    _fail_minute(repo, BASE, client2, mild, 50.0)
    repo.insert_issue(
        fingerprint="fp-mild",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="chan",
        entity_id=mild,
    )

    ranked = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600)
    assert [r.entity_id for r in ranked] == [bad, mild]
    assert isinstance(ranked[0], OffenderScore)
    # score = 1.0*500 + 30*1 = 530 for the bad AP.
    assert ranked[0].score == pytest.approx(530.0)
    assert ranked[0].fail_minutes == pytest.approx(500.0)
    assert ranked[0].issue_counts == {"p1": 1, "p2": 0, "p3": 0, "total": 1}
    # score = 1.0*50 + 3*1 = 53 for the mild AP.
    assert ranked[1].score == pytest.approx(53.0)


def test_severity_weighting_orders_issue_only_devices(repo: Repository) -> None:
    """With no SLE minutes, one P1 outranks several P3s (p1 > p2 > p3)."""
    a = _ap(repo, "aa:bb:cc:00:00:01", "one-p1")
    b = _ap(repo, "aa:bb:cc:00:00:02", "three-p3")
    repo.insert_issue(
        fingerprint="fp-a",
        detector_key="d",
        severity="p1",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="t",
        entity_id=a,
    )
    for i in range(3):
        repo.insert_issue(
            fingerprint=f"fp-b{i}",
            detector_key="d",
            severity="p3",
            state="active",
            first_seen_ts=BASE,
            last_seen_ts=BASE,
            title="t",
            entity_id=b,
        )
    ranked = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600)
    # 30 (one P1) > 9 (three P3s).
    assert [r.entity_id for r in ranked] == [a, b]
    assert ranked[0].score == pytest.approx(30.0)
    assert ranked[1].score == pytest.approx(9.0)


def test_resolved_issues_do_not_count(repo: Repository) -> None:
    """Only open issues contribute; a resolved one adds nothing."""
    a = _ap(repo, "aa:bb:cc:00:00:01", "ap")
    repo.insert_issue(
        fingerprint="fp-open",
        detector_key="d",
        severity="p2",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="t",
        entity_id=a,
    )
    repo.insert_issue(
        fingerprint="fp-done",
        detector_key="d",
        severity="p1",
        state="resolved",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="t",
        entity_id=a,
        resolved_ts=BASE,
    )
    ranked = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600)
    assert len(ranked) == 1
    assert ranked[0].score == pytest.approx(10.0)  # p2 only
    assert ranked[0].issue_counts["p1"] == 0


# ---------------------------------------------------------------------------- #
# Event volume / client surface
# ---------------------------------------------------------------------------- #


def test_client_offenders_rank_by_event_volume(repo: Repository) -> None:
    """Disconnect/roam volume ranks the churniest client; devices are excluded."""
    ap = _ap(repo, "aa:bb:cc:00:00:01", "ap")
    flaky = _client(repo, "11:22:33:44:55:01", "flaky")
    calm = _client(repo, "11:22:33:44:55:02", "calm")

    for i in range(10):
        repo.record_event(
            ts=BASE + i,
            key="EVT_WU_Disconnected",
            entity_id=flaky,
            native_id=f"d-flaky-{i}",
            related_entity_id=ap,
        )
    for i in range(2):
        repo.record_event(
            ts=BASE + i,
            key="EVT_WU_Roam",
            entity_id=calm,
            native_id=f"r-calm-{i}",
            related_entity_id=ap,
        )

    ranked = rank_offenders(repo, CLIENT_TYPES, BASE - 60, BASE + 3600)
    assert [r.entity_id for r in ranked] == [flaky, calm]
    assert ranked[0].event_count == 10
    assert ranked[0].score == pytest.approx(5.0)  # 0.5 * 10
    # The AP the disconnects reference is NOT a client offender (no guessing).
    assert ap not in {r.entity_id for r in ranked}


def test_device_surface_excludes_clients(repo: Repository) -> None:
    """A client with issues never appears on the devices leaderboard."""
    ap = _ap(repo, "aa:bb:cc:00:00:01", "ap")
    cli = _client(repo, "11:22:33:44:55:01", "phone")
    repo.insert_issue(
        fingerprint="fp-ap",
        detector_key="d",
        severity="p2",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="t",
        entity_id=ap,
    )
    repo.insert_issue(
        fingerprint="fp-cli",
        detector_key="d",
        severity="p1",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="t",
        entity_id=cli,
    )
    ranked = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600)
    assert [r.entity_id for r in ranked] == [ap]


# ---------------------------------------------------------------------------- #
# Window filtering, empty windows, top_n
# ---------------------------------------------------------------------------- #


def test_window_filters_sle_minutes_and_events(repo: Repository) -> None:
    """Failed minutes and events outside the window are excluded."""
    ap = _ap(repo, "aa:bb:cc:00:00:01", "ap")
    cli = _client(repo, "11:22:33:44:55:01", "phone")
    # In-window burden.
    _fail_minute(repo, BASE + 100, cli, ap, 30.0)
    repo.record_event(ts=BASE + 100, key="EVT_WU_Roam", entity_id=cli, native_id="in")
    # Out-of-window burden (well before the window).
    _fail_minute(repo, BASE - 10_000, cli, ap, 999.0)
    repo.record_event(ts=BASE - 10_000, key="EVT_WU_Roam", entity_id=cli, native_id="out")

    dev = rank_offenders(repo, DEVICE_TYPES, BASE, BASE + 3600)
    assert dev[0].fail_minutes == pytest.approx(30.0)  # the 999 is out of window
    clir = rank_offenders(repo, CLIENT_TYPES, BASE, BASE + 3600)
    assert clir[0].event_count == 1  # only the in-window roam


def test_empty_window_returns_empty(repo: Repository) -> None:
    """A window with no failed minutes / issues / events yields no offenders."""
    ap = _ap(repo, "aa:bb:cc:00:00:01", "ap")
    cli = _client(repo, "11:22:33:44:55:01", "phone")
    _fail_minute(repo, BASE, cli, ap, 100.0)
    # Query a window that predates all data.
    assert rank_offenders(repo, DEVICE_TYPES, BASE - 20_000, BASE - 10_000) == []


def test_no_data_at_all_returns_empty(repo: Repository) -> None:
    assert rank_offenders(repo, DEVICE_TYPES, BASE, BASE + 3600) == []


def test_top_n_caps_results(repo: Repository) -> None:
    for i in range(5):
        ap = _ap(repo, f"aa:bb:cc:00:00:0{i}", f"ap{i}")
        repo.insert_issue(
            fingerprint=f"fp{i}",
            detector_key="d",
            severity="p2",
            state="active",
            first_seen_ts=BASE,
            last_seen_ts=BASE,
            title="t",
            entity_id=ap,
        )
    ranked = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600, top_n=2)
    assert len(ranked) == 2
    # top_n<=0 returns all.
    assert len(rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600, top_n=0)) == 5


def test_tiebreak_is_deterministic(repo: Repository) -> None:
    """Equal scores order by entity_id ascending (reproducible, no jitter)."""
    ids = []
    for i in range(3):
        ap = _ap(repo, f"aa:bb:cc:00:00:0{i}", f"ap{i}")
        ids.append(ap)
        repo.insert_issue(
            fingerprint=f"fp{i}",
            detector_key="d",
            severity="p2",
            state="active",
            first_seen_ts=BASE,
            last_seen_ts=BASE,
            title="t",
            entity_id=ap,
        )
    ranked = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600)
    assert [r.entity_id for r in ranked] == sorted(ids)


# ---------------------------------------------------------------------------- #
# Weight override
# ---------------------------------------------------------------------------- #


def test_load_offender_weights_defaults() -> None:
    assert load_offender_weights(None) == DEFAULT_OFFENDER_WEIGHTS
    assert load_offender_weights(None) is not DEFAULT_OFFENDER_WEIGHTS  # a copy


def test_settings_override_weights(repo: Repository) -> None:
    """A weight override from settings.thresholds retunes the ranking."""
    a = _ap(repo, "aa:bb:cc:00:00:01", "sle-heavy")
    b = _ap(repo, "aa:bb:cc:00:00:02", "issue-heavy")
    cli = _client(repo, "11:22:33:44:55:01", "phone")
    _fail_minute(repo, BASE, cli, a, 40.0)  # default: 40 pts
    repo.insert_issue(
        fingerprint="fp-b",
        detector_key="d",
        severity="p2",
        state="active",
        first_seen_ts=BASE,
        last_seen_ts=BASE,
        title="t",
        entity_id=b,
    )  # default: 10 pts

    # Default: sle-heavy (40) > issue-heavy (10).
    default = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600)
    assert [r.entity_id for r in default] == [a, b]

    # Crank the P2 weight up so the issue-heavy AP wins.
    settings = Settings(_env_file=None, thresholds={"offenders": {"weights": {"issue_p2": 100.0}}})
    tuned = rank_offenders(repo, DEVICE_TYPES, BASE - 60, BASE + 3600, settings=settings)
    assert [r.entity_id for r in tuned] == [b, a]
    assert tuned[0].score == pytest.approx(100.0)


def test_malformed_override_falls_back(repo: Repository) -> None:
    settings = Settings(_env_file=None, thresholds={"offenders": {"weights": {"issue_p1": "nope"}}})
    assert load_offender_weights(settings)["issue_p1"] == DEFAULT_OFFENDER_WEIGHTS["issue_p1"]
