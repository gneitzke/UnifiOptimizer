"""wifi.* detectors on synthetic radios/clients/events/samples.

Each detector gets, at minimum, a firing case, a confounder-suppressed case, and
an UNKNOWN-coverage case (ARCHITECTURE.md sections 4 & 6). Fixtures are built from
the real temp-DB :class:`Repository` so the detectors run against the same store
seam they use in production.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from netadmin.detect.context import DetectorContext
from netadmin.detect.detectors.wifi import (
    KEY_AIRTIME_SATURATION,
    KEY_BAND_STEERING,
    KEY_CHANNEL_PLAN,
    KEY_DFS_RECURRING,
    KEY_LEGACY_RATES,
    KEY_MESH_UPLINK,
    KEY_MIN_RSSI_MISCONFIG,
    KEY_NEIGHBOR_DENSITY,
    KEY_PINGPONG_ROAMER,
    KEY_ROAM_QUALITY,
    KEY_ROGUE_AP,
    KEY_STICKY_CLIENT,
    KEY_TX_POWER_LOUD,
    ROGUE_BSS_TYPE,
    AirtimeSaturationDetector,
    BandSteeringDetector,
    ChannelPlanDetector,
    DfsRecurringDetector,
    LegacyRatesDetector,
    MeshUplinkDetector,
    MinRssiMisconfigDetector,
    NeighborDensityDetector,
    PingpongRoamerDetector,
    RoamQualityDetector,
    RogueApDetector,
    StickyClientDetector,
    TxPowerLoudDetector,
    _neighbor_rssi_dbm,
)
from netadmin.detect.engine import UNKNOWN, DetectorResult
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType, Severity
from netadmin.issues.engine import fingerprint
from netadmin.store.repository import Repository, SampleReading
from tests.netadmin.detect.support import FakeBaselines

NOW = 4_000_000
DAY = 86_400


# ---------------------------------------------------------------------- #
# Fixture builders
# ---------------------------------------------------------------------- #
def _ctx(repo: Repository, *, settings=None, now: int = NOW) -> DetectorContext:
    return DetectorContext(
        repo=repo, baselines=FakeBaselines(), now_ts=now, site_id="default", settings=settings
    )


def _settings(key: str, **overrides) -> SimpleNamespace:
    return SimpleNamespace(thresholds={key: overrides}, poll=None)


def seed_cov(repo: Repository, *, now: int = NOW, jobs=("fast_device", "fast_sta")) -> None:
    """Full live coverage for the given jobs over the last 600 s (10 polls each)."""
    for job in jobs:
        ts = now - 600 + 60
        while ts <= now:
            repo.record_poll_run(job=job, ok=True, ts=ts)
            ts += 60


def seed_low_cov(repo: Repository, *, now: int = NOW, jobs=("fast_device", "fast_sta")) -> None:
    """Only two polls in the 600 s window -> coverage 0.2 < 0.5 -> UNKNOWN."""
    for job in jobs:
        repo.record_poll_run(job=job, ok=True, ts=now - 120)
        repo.record_poll_run(job=job, ok=True, ts=now - 60)


def mk_ap(
    repo: Repository,
    native_id: str,
    *,
    name: Optional[str] = None,
    meta: Optional[dict] = None,
    uplink_type: Optional[str] = None,
    uplink_hops: Optional[int] = None,
    now: int = NOW,
) -> int:
    eid = repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=native_id, name=name, meta=meta or {}),
        ts=now,
    )
    if uplink_type is not None:
        repo.record_state_change(eid, "uplink_type", uplink_type, ts=now)
    if uplink_hops is not None:
        repo.record_state_change(eid, "uplink_hops", str(uplink_hops), ts=now)
    return eid


def mk_radio(
    repo: Repository,
    native_id: str,
    parent_id: int,
    *,
    band: str,
    ht: Optional[int] = None,
    channel: Optional[int] = None,
    meta_extra: Optional[dict] = None,
    now: int = NOW,
) -> int:
    meta = {"band": band}
    if ht is not None:
        meta["ht"] = ht
    if meta_extra:
        meta.update(meta_extra)
    eid = repo.upsert_entity(
        Entity(entity_type=EntityType.RADIO, native_id=native_id, parent_id=parent_id, meta=meta),
        ts=now,
    )
    if channel is not None:
        repo.record_state_change(eid, "channel", str(channel), ts=now)
    return eid


def mk_client(
    repo: Repository,
    native_id: str,
    *,
    parent_id: Optional[int] = None,
    is_wired: bool = False,
    essid: str = "wifi",
    ap_mac: Optional[str] = None,
    band_history: Optional[list] = None,
    now: int = NOW,
) -> int:
    eid = repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=native_id,
            parent_id=parent_id,
            meta={"is_wired": is_wired, "essid": essid},
        ),
        ts=now,
    )
    if ap_mac is not None:
        repo.record_state_change(eid, "ap_mac", ap_mac, ts=now)
    if band_history:
        for i, band in enumerate(band_history):
            repo.record_state_change(eid, "band", band, ts=now - (len(band_history) - i) * 100)
    return eid


def gauge(
    repo: Repository,
    entity_id: int,
    metric: str,
    values: list[float],
    *,
    step: int = 60,
    now: int = NOW,
) -> None:
    """Seed gauge samples ending just before ``now`` (all inside a 600 s window)."""
    n = len(values)
    repo.record_samples(
        SampleReading(entity_id, metric, now - (n - i) * step, float(v))
        for i, v in enumerate(values)
    )


def roam(repo: Repository, client_id: int, ts: int, from_ap_id: Optional[int]) -> None:
    repo.record_event(ts=ts, key="EVT_WU_Roam", entity_id=client_id, related_entity_id=from_ap_id)


# ====================================================================== #
# wifi.sticky_client
# ====================================================================== #
#: When the sticky client roams off its old AP onto the far one it is stuck to.
STICKY_ROAM_TS = NOW - 1800


def mk_recommendable_ap(
    repo: Repository,
    native_id: str,
    *,
    name: Optional[str] = None,
    cu_total: float = 12.0,
    now: int = NOW,
) -> int:
    """An AP with a quiet radio: judged, and judged fit to be recommended.

    The candidate screen refuses to recommend an AP it cannot judge, so a
    candidate needs a radio reporting real airtime before it can be anything but
    excluded.
    """
    ap = mk_ap(repo, native_id, name=name, now=now)
    radio = mk_radio(repo, f"{native_id}:na", ap, band="na", now=now)
    gauge(repo, radio, "cu_total", [cu_total] * 8, now=now)
    return ap


def _prior_attachment(
    repo: Repository,
    client_id: int,
    ap_mac: str,
    *,
    rssi: float,
    samples: int = 6,
    since: int = NOW - 6 * 3600,
    until: int = STICKY_ROAM_TS,
) -> None:
    """Attach ``client_id`` to ``ap_mac`` for a stretch, with RSSI measured there."""
    repo.record_state_change(client_id, "ap_mac", ap_mac, ts=since)
    step = (until - since) // (samples + 1)
    repo.record_samples(
        SampleReading(client_id, "rssi", since + step * (i + 1), rssi) for i in range(samples)
    )


def _sticky_client(
    repo: Repository,
    native_id: str,
    ap_mac: str,
    *,
    better: bool,
    tx_rate_kbps: float = 12_000.0,
    prior_rssi: float = -58.0,
) -> int:
    """A client sustained-weak on ``ap_mac``, with or without a real prior AP.

    ``better=True`` gives it a recorded attachment to ``ap-good`` whose RSSI
    samples were taken *during* that attachment: the only thing that can prove
    another AP served this client better.
    """
    cid = mk_client(repo, native_id)
    if better:
        _prior_attachment(repo, cid, "ap-good", rssi=prior_rssi)
    repo.record_state_change(cid, "ap_mac", ap_mac, ts=STICKY_ROAM_TS)
    gauge(repo, cid, "rssi", [-82.0] * 8)
    # tx_rate is stored in kbps (netadmin.ingest.mapping.METRICS), never Mbps.
    gauge(repo, cid, "tx_rate", [tx_rate_kbps] * 8)
    return cid


def test_sticky_client_fires_with_better_ap(repo: Repository) -> None:
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good", name="Hallway")
    _sticky_client(repo, "cli-1", "ap-far", better=True)

    findings = StickyClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_STICKY_CLIENT
    assert f.severity is Severity.P3
    assert "better_ap_exists" in f.confounders_checked
    assert f.evidence["better_ap"] == "ap-good"
    assert f.evidence["better_ap_name"] == "Hallway"
    assert f.evidence["better_ap_median_rssi"] == -58.0
    # Only the six readings taken while the client was on ap-good count: the
    # eight weak ones after the roam belong to the AP it is stuck on.
    assert f.evidence["better_ap_samples"] == 6


def test_sticky_client_names_the_ap_its_reported_rssi_was_measured_on(repo: Repository) -> None:
    """The recommended AP and the dBm figure beside it must be one fact (Gitea #42).

    The old code paired the *alphabetically first* AP in the roam trail with the
    client's best RSSI *anywhere* in the window, so it recommended ap-aaa at a
    -40 dBm reading the client took while sitting on the AP it is stuck to.
    """
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-aaa")
    mk_recommendable_ap(repo, "ap-good")
    cid = mk_client(repo, "cli-1")
    # Two prior attachments. ap-aaa sorts first and is genuinely better than the
    # current -82 dBm, but ap-good is better still.
    _prior_attachment(repo, cid, "ap-aaa", rssi=-70.0, since=NOW - 6 * 3600, until=NOW - 4 * 3600)
    _prior_attachment(repo, cid, "ap-good", rssi=-58.0, since=NOW - 4 * 3600)
    repo.record_state_change(cid, "ap_mac", "ap-far", ts=STICKY_ROAM_TS)
    gauge(repo, cid, "rssi", [-82.0] * 8)
    # A one-sample transient the client took *on the AP it is stuck to*, outside
    # the analysis window. It is the best reading in the history window and says
    # nothing about any other AP.
    repo.record_samples([SampleReading(cid, "rssi", NOW - 1200, -40.0)])

    (f,) = StickyClientDetector().evaluate(_ctx(repo))
    assert f.evidence["better_ap"] == "ap-good"
    assert f.evidence["better_ap_median_rssi"] == -58.0


def test_sticky_client_never_recommends_a_congested_ap(repo: Repository) -> None:
    """A stronger radio in a full cell is not somewhere better to be."""
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-busy", cu_total=70.0)  # over the 50% degraded line
    mk_recommendable_ap(repo, "ap-good")
    cid = mk_client(repo, "cli-1")
    _prior_attachment(repo, cid, "ap-busy", rssi=-50.0, since=NOW - 6 * 3600, until=NOW - 4 * 3600)
    _prior_attachment(repo, cid, "ap-good", rssi=-58.0, since=NOW - 4 * 3600)
    repo.record_state_change(cid, "ap_mac", "ap-far", ts=STICKY_ROAM_TS)
    gauge(repo, cid, "rssi", [-82.0] * 8)

    (f,) = StickyClientDetector().evaluate(_ctx(repo))
    assert f.evidence["better_ap"] == "ap-good"  # not the stronger, saturated one
    assert "candidate_ap_health_screened" in f.confounders_checked


def test_sticky_client_suppressed_when_the_only_candidate_is_congested(repo: Repository) -> None:
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good", cu_total=70.0)
    _sticky_client(repo, "cli-1", "ap-far", better=True)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_never_recommends_a_weak_mesh_uplink(repo: Repository) -> None:
    """A better radio behind a marginal backhaul is not better."""
    seed_cov(repo)
    meshed = mk_recommendable_ap(repo, "ap-good")
    repo.record_state_change(meshed, "uplink_type", "wireless", ts=NOW)
    gauge(repo, meshed, "uplink_rssi", [-72.0] * 8)  # under the -65 dBm warn line
    _sticky_client(repo, "cli-1", "ap-far", better=True)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_never_recommends_an_ap_it_cannot_judge(repo: Repository) -> None:
    """No airtime data for a candidate is not a clean bill of health.

    ``fast_device`` can be gapped while ``fast_sta`` (which gates this detector)
    is healthy, and an empty airtime window reads as "not congested" to anything
    that only looks at the median. Silence about a candidate costs a suppressed
    finding; guessing about it costs a client steered into a full cell.
    """
    seed_cov(repo)
    ap = mk_ap(repo, "ap-good")
    mk_radio(repo, "ap-good:na", ap, band="na")  # a radio, but no cu_total samples
    _sticky_client(repo, "cli-1", "ap-far", better=True)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_never_recommends_an_unjudgeable_mesh_backhaul(repo: Repository) -> None:
    """Same rule on the other disqualifier: meshed, with no uplink readings."""
    seed_cov(repo)
    meshed = mk_recommendable_ap(repo, "ap-good")
    repo.record_state_change(meshed, "uplink_type", "wireless", ts=NOW)
    _sticky_client(repo, "cli-1", "ap-far", better=True)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_recommends_a_wired_ap_with_meshing_enabled(repo: Repository) -> None:
    """``wired_with_mesh_enabled`` is a latent-failover note, not a bad backhaul."""
    seed_cov(repo)
    ap = mk_recommendable_ap(repo, "ap-good")
    repo.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id="ap-good", meta={"mesh_enabled": True}),
        ts=NOW,
    )
    repo.record_state_change(ap, "uplink_type", "wire", ts=NOW)
    _sticky_client(repo, "cli-1", "ap-far", better=True)
    (f,) = StickyClientDetector().evaluate(_ctx(repo))
    assert f.evidence["better_ap"] == "ap-good"


def test_sticky_client_no_better_ap_is_said_honestly(repo: Repository) -> None:
    """No better AP means no finding, not an invented one (Gitea #42).

    This client has a real prior attachment and a strong reading in its history,
    but the reading was taken where it is now and its signal on the other AP was
    no better. The old code paired the two and fabricated a recommendation.
    """
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    cid = _sticky_client(repo, "cli-1", "ap-far", better=True, prior_rssi=-80.0)
    repo.record_samples([SampleReading(cid, "rssi", NOW - 1200, -45.0)])

    result = StickyClientDetector().evaluate(_ctx(repo))
    assert result == []  # an evaluated clear...
    assert not isinstance(result, DetectorResult)  # ...not a per-entity gap


def test_sticky_client_under_sampled_candidate_never_qualifies(repo: Repository) -> None:
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    cid = mk_client(repo, "cli-1")
    # Two readings on ap-good: a walk-past, not an attachment worth a median.
    _prior_attachment(repo, cid, "ap-good", rssi=-55.0, samples=2)
    repo.record_state_change(cid, "ap_mac", "ap-far", ts=STICKY_ROAM_TS)
    gauge(repo, cid, "rssi", [-82.0] * 8)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_unknown_ap_mac_is_never_recommended(repo: Repository) -> None:
    """A wired stretch writes the *switch* MAC to the same ap_mac attribute."""
    seed_cov(repo)
    cid = mk_client(repo, "cli-1")
    _prior_attachment(repo, cid, "sw-rack", rssi=-50.0)  # no AP entity by that MAC
    repo.record_state_change(cid, "ap_mac", "ap-far", ts=STICKY_ROAM_TS)
    gauge(repo, cid, "rssi", [-82.0] * 8)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_interval_join_splits_at_each_roam(repo: Repository) -> None:
    """Two separate stints on one AP sum; the stint in between does not count."""
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    mk_recommendable_ap(repo, "ap-other")
    cid = mk_client(repo, "cli-1")
    _prior_attachment(
        repo, cid, "ap-good", rssi=-58.0, samples=4, since=NOW - 6 * 3600, until=NOW - 5 * 3600
    )
    _prior_attachment(
        repo, cid, "ap-other", rssi=-30.0, samples=5, since=NOW - 5 * 3600, until=NOW - 4 * 3600
    )
    _prior_attachment(repo, cid, "ap-good", rssi=-56.0, samples=3, since=NOW - 4 * 3600)
    repo.record_state_change(cid, "ap_mac", "ap-far", ts=STICKY_ROAM_TS)
    gauge(repo, cid, "rssi", [-82.0] * 8)

    (f,) = StickyClientDetector().evaluate(_ctx(repo))
    # ap-other's -30 dBm stint is the strongest signal in the window; the client
    # is only offered it if the join is right about who measured what.
    assert f.evidence["better_ap"] == "ap-other"
    assert f.evidence["better_ap_samples"] == 5
    assert f.evidence["better_ap_median_rssi"] == -30.0


def test_sticky_client_suppressed_without_better_ap(repo: Repository) -> None:
    seed_cov(repo)
    # Sustained weak RSSI but no historically-better AP -> coverage hole, not sticky.
    mk_recommendable_ap(repo, "ap-good")
    _sticky_client(repo, "cli-1", "ap-far", better=False)
    assert StickyClientDetector().evaluate(_ctx(repo)) == []


def test_sticky_client_one_issue_per_client_across_aps(repo: Repository) -> None:
    """A client bouncing between two far APs is one sticky issue, not two (Gitea #40)."""
    seed_cov(repo)
    later = NOW + 600
    seed_cov(repo, now=later)
    mk_recommendable_ap(repo, "ap-good")
    cid = _sticky_client(repo, "cli-1", "ap-far-1", better=True)
    (before,) = StickyClientDetector().evaluate(_ctx(repo))

    # It bounces to the other far AP, still weak, still with nowhere better than
    # ap-good to be.
    repo.record_state_change(cid, "ap_mac", "ap-far-2", ts=NOW + 60)
    gauge(repo, cid, "rssi", [-82.0] * 8, now=later)
    gauge(repo, cid, "tx_rate", [12_000.0] * 8, now=later)
    (after,) = StickyClientDetector().evaluate(_ctx(repo, now=later))

    assert before.evidence["current_ap"] == "ap-far-1"
    assert after.evidence["current_ap"] == "ap-far-2"  # evidence follows the client...
    assert fingerprint(after) == fingerprint(before)  # ...its identity does not


def test_sticky_client_clustered_is_p2(repo: Repository) -> None:
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    for i in range(3):
        _sticky_client(repo, f"cli-{i}", "ap-far", better=True)
    findings = StickyClientDetector().evaluate(_ctx(repo))
    assert len(findings) == 3
    assert all(f.severity is Severity.P2 for f in findings)
    assert all(f.evidence["clustered_on_ap"] for f in findings)


def test_sticky_client_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    _sticky_client(repo, "cli-1", "ap-far", better=True)
    assert StickyClientDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_sticky_client_rate_confounder_reads_kbps_as_mbps(repo: Repository) -> None:
    """The rate confounder must judge Mbps, not the raw kbps the store holds.

    A real 1152.9 Mbps link arrives as 1_152_900; compared straight against the
    24 Mbps threshold it would have had to be 24 kbps to corroborate, so the
    check was dead for every sticky client ever raised (Gitea #41).
    """
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    # 1_152_900 kbps = 1152.9 Mbps: a fast client, nothing to corroborate.
    _sticky_client(repo, "cli-fast", "ap-far", better=True, tx_rate_kbps=1_152_900.0)
    (fast,) = StickyClientDetector().evaluate(_ctx(repo))
    assert fast.evidence["median_tx_rate_mbps"] == 1152.9
    assert fast.evidence["low_rate_corroborated"] is False


def test_sticky_client_low_rate_corroborates(repo: Repository) -> None:
    seed_cov(repo)
    mk_recommendable_ap(repo, "ap-good")
    # 12_000 kbps = 12 Mbps, under the 24 Mbps floor -> the confounder fires.
    _sticky_client(repo, "cli-slow", "ap-far", better=True, tx_rate_kbps=12_000.0)
    (slow,) = StickyClientDetector().evaluate(_ctx(repo))
    assert slow.evidence["median_tx_rate_mbps"] == 12.0
    assert slow.evidence["low_rate_corroborated"] is True
    assert "low_rate_corroborated" in slow.confounders_checked


# ====================================================================== #
# wifi.pingpong_roamer
# ====================================================================== #
def test_pingpong_fires_on_meraki_burst(repo: Repository) -> None:
    seed_cov(repo)
    ap_a = mk_ap(repo, "ap-a")
    ap_b = mk_ap(repo, "ap-b")
    cid = mk_client(repo, "cli-1")
    for i, ts in enumerate((NOW - 40, NOW - 30, NOW - 20, NOW - 10, NOW - 1)):
        roam(repo, cid, ts, ap_a if i % 2 == 0 else ap_b)

    findings = PingpongRoamerDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.P2
    assert f.evidence["reason"] == "meraki_burst"
    assert f.evidence["distinct_aps"] == 2
    assert "two_ap_bounce_not_walk" in f.confounders_checked


def test_pingpong_fires_on_definite_rate(repo: Repository) -> None:
    seed_cov(repo)
    ap_a = mk_ap(repo, "ap-a")
    ap_b = mk_ap(repo, "ap-b")
    cid = mk_client(repo, "cli-1")
    # 15 roams spaced 100 s apart (no burst) -> rate 15/h >= definite (12/h).
    for i in range(15):
        roam(repo, cid, NOW - 3500 + i * 100, ap_a if i % 2 == 0 else ap_b)

    findings = PingpongRoamerDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["reason"] == "rate_definite"


def test_pingpong_suppressed_for_walk_through(repo: Repository) -> None:
    seed_cov(repo)
    aps = [mk_ap(repo, f"ap-{i}") for i in range(3)]
    cid = mk_client(repo, "cli-1")
    # Three roams across three distinct APs, spaced 20 s: not a 2-AP bounce, low rate.
    for i, ap in enumerate(aps):
        roam(repo, cid, NOW - 200 + i * 20, ap)
    assert PingpongRoamerDetector().evaluate(_ctx(repo)) == []


def test_pingpong_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap_a = mk_ap(repo, "ap-a")
    cid = mk_client(repo, "cli-1")
    for ts in (NOW - 40, NOW - 30, NOW - 20, NOW - 10):
        roam(repo, cid, ts, ap_a)
    assert PingpongRoamerDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.roam_quality
# ====================================================================== #
def _roam_pair(repo: Repository, cid: int, roam_ts: int, before: float, after: float) -> None:
    repo.record_samples(
        [
            SampleReading(cid, "rssi", roam_ts - 60, before),
            SampleReading(cid, "rssi", roam_ts - 30, before),
            SampleReading(cid, "rssi", roam_ts + 30, after),
            SampleReading(cid, "rssi", roam_ts + 60, after),
        ]
    )
    roam(repo, cid, roam_ts, None)


def test_roam_quality_fires_on_bad_roams(repo: Repository) -> None:
    seed_cov(repo)
    cid = mk_client(repo, "cli-1")
    _roam_pair(repo, cid, NOW - 1000, before=-55.0, after=-75.0)
    _roam_pair(repo, cid, NOW - 500, before=-55.0, after=-75.0)

    findings = RoamQualityDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_ROAM_QUALITY
    assert f.severity is Severity.P3
    assert f.evidence["bad_roams"] == 2
    assert "transient_dip_excluded" in f.confounders_checked


def test_roam_quality_suppressed_when_signal_holds(repo: Repository) -> None:
    seed_cov(repo)
    cid = mk_client(repo, "cli-1")
    _roam_pair(repo, cid, NOW - 1000, before=-55.0, after=-56.0)
    _roam_pair(repo, cid, NOW - 500, before=-55.0, after=-57.0)
    assert RoamQualityDetector().evaluate(_ctx(repo)) == []


def test_roam_quality_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    cid = mk_client(repo, "cli-1")
    _roam_pair(repo, cid, NOW - 1000, before=-55.0, after=-75.0)
    _roam_pair(repo, cid, NOW - 500, before=-55.0, after=-75.0)
    assert RoamQualityDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.min_rssi_misconfig
# ====================================================================== #
def test_min_rssi_fires_stricter_than_floor(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_ap(repo, "ap-2")  # multi-AP site, not mesh
    mk_radio(
        repo, "ap-1:na", ap1, band="na", meta_extra={"min_rssi_enabled": True, "min_rssi": -65}
    )

    findings = MinRssiMisconfigDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.P3
    assert f.evidence["reason"] == "stricter_than_floor"


def test_min_rssi_fires_p2_on_mesh_ap(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1", meta={"mesh_enabled": True})
    mk_ap(repo, "ap-2")
    mk_radio(
        repo, "ap-1:na", ap1, band="na", meta_extra={"min_rssi_enabled": True, "min_rssi": -80}
    )

    findings = MinRssiMisconfigDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["reason"] == "mesh_uplink_ap"


def test_min_rssi_fires_p2_on_single_ap_site(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(
        repo, "ap-1:na", ap1, band="na", meta_extra={"min_rssi_enabled": True, "min_rssi": -80}
    )

    findings = MinRssiMisconfigDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["reason"] == "single_ap_site"


def test_min_rssi_suppressed_when_safe(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_ap(repo, "ap-2")  # multi-AP, roam targets exist
    # Enabled but lenient (-80), not mesh, not single-AP -> no misconfig.
    mk_radio(
        repo, "ap-1:na", ap1, band="na", meta_extra={"min_rssi_enabled": True, "min_rssi": -80}
    )
    assert MinRssiMisconfigDetector().evaluate(_ctx(repo)) == []


def test_min_rssi_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(
        repo, "ap-1:na", ap1, band="na", meta_extra={"min_rssi_enabled": True, "min_rssi": -65}
    )
    assert MinRssiMisconfigDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.channel_plan
# ====================================================================== #
def test_channel_plan_fires_off_grid_24(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:ng", ap1, band="ng", ht=20, channel=3)  # off 1/6/11

    findings = ChannelPlanDetector().evaluate(_ctx(repo))
    subtypes = {f.dims["subtype"] for f in findings}
    assert "channel_off_grid" in subtypes
    assert all(f.severity is Severity.P3 for f in findings)


def _chan_radios(repo: Repository, band: str, channels: list[int], *, ht: int = 20) -> None:
    """One AP per radio, each radio on the given channel of ``band``."""
    for i, channel in enumerate(channels):
        ap = mk_ap(repo, f"ap-{i}")
        mk_radio(repo, f"ap-{i}:{band}", ap, band=band, ht=ht, channel=channel)


def test_channel_plan_fires_co_channel_reuse(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    ap2 = mk_ap(repo, "ap-2")
    mk_radio(repo, "ap-1:na", ap1, band="na", ht=40, channel=36)
    mk_radio(repo, "ap-2:na", ap2, band="na", ht=40, channel=36)  # same channel

    findings = ChannelPlanDetector().evaluate(_ctx(repo))
    assert any(f.dims["subtype"] == "co_channel_reuse" for f in findings)


def test_channel_plan_co_channel_is_one_site_scoped_issue_per_band(repo: Repository) -> None:
    """Four radios piled on one 2.4 GHz channel is ONE issue, not four."""
    seed_cov(repo)
    _chan_radios(repo, "ng", [1, 1, 1, 1])

    findings = ChannelPlanDetector().evaluate(_ctx(repo))
    conflicts = [f for f in findings if f.dims["subtype"] == "co_channel_reuse"]
    assert len(conflicts) == 1
    finding = conflicts[0]
    assert finding.entity.native_id == "rf:2.4"
    assert finding.entity.entity_id is None  # site-scoped: no stored entity row
    assert finding.dims == {"subtype": "co_channel_reuse", "band": "2.4"}
    assert finding.evidence["per_channel"] == {"1": 4, "6": 0, "11": 0}
    assert finding.evidence["unused_candidates"] == [6, 11]
    assert [g["channel"] for g in finding.evidence["conflict_groups"]] == [1]
    assert len(finding.evidence["conflict_groups"][0]["radios"]) == 4
    assert "unavoidable_reuse_excluded" in finding.confounders_checked


def test_channel_plan_co_channel_fires_once_per_band(repo: Repository) -> None:
    """A site contended on both bands gets one issue per band, never per radio."""
    seed_cov(repo)
    for i, (chan_24, chan_5) in enumerate([(1, 36), (1, 36), (1, 36)]):
        ap = mk_ap(repo, f"ap-{i}")
        mk_radio(repo, f"ap-{i}:ng", ap, band="ng", ht=20, channel=chan_24)
        mk_radio(repo, f"ap-{i}:na", ap, band="na", ht=40, channel=chan_5)

    conflicts = [
        f
        for f in ChannelPlanDetector().evaluate(_ctx(repo))
        if f.dims["subtype"] == "co_channel_reuse"
    ]
    assert sorted(f.entity.native_id for f in conflicts) == ["rf:2.4", "rf:5"]


def test_channel_plan_pigeonhole_spread_does_not_fire(repo: Repository) -> None:
    """Four radios over 1/6/11 must reuse one channel; that is optimal, not a defect."""
    seed_cov(repo)
    _chan_radios(repo, "ng", [1, 1, 6, 11])

    findings = ChannelPlanDetector().evaluate(_ctx(repo))
    assert [f for f in findings if f.dims["subtype"] == "co_channel_reuse"] == []


def test_channel_plan_balanced_maximal_spread_does_not_fire(repo: Repository) -> None:
    """Six radios, two per channel: the best any plan can do on three channels."""
    seed_cov(repo)
    _chan_radios(repo, "ng", [1, 1, 6, 6, 11, 11])

    findings = ChannelPlanDetector().evaluate(_ctx(repo))
    assert [f for f in findings if f.dims["subtype"] == "co_channel_reuse"] == []


def test_channel_plan_fires_when_a_quieter_candidate_is_free(repo: Repository) -> None:
    """Two on 1 and two on 6 with 11 empty: one group could move. Avoidable."""
    seed_cov(repo)
    _chan_radios(repo, "ng", [1, 1, 6, 6])

    conflicts = [
        f
        for f in ChannelPlanDetector().evaluate(_ctx(repo))
        if f.dims["subtype"] == "co_channel_reuse"
    ]
    assert len(conflicts) == 1
    assert conflicts[0].evidence["unused_candidates"] == [11]


def test_channel_plan_fingerprint_survives_membership_and_channel_change(
    repo: Repository,
) -> None:
    """The band issue is one row for as long as the band is contended."""
    seed_cov(repo)
    _chan_radios(repo, "ng", [1, 1, 1])
    before = [
        f
        for f in ChannelPlanDetector().evaluate(_ctx(repo))
        if f.dims["subtype"] == "co_channel_reuse"
    ][0]

    # A fourth radio joins and the pile moves to another channel: same fingerprint.
    ap = mk_ap(repo, "ap-late")
    mk_radio(repo, "ap-late:ng", ap, band="ng", ht=20, channel=6)
    for i in range(3):
        repo.record_state_change(
            repo.find_entity(EntityType.RADIO, f"ap-{i}:ng")["entity_id"],
            "channel",
            "6",
            ts=NOW,
        )
    after = [
        f
        for f in ChannelPlanDetector().evaluate(_ctx(repo))
        if f.dims["subtype"] == "co_channel_reuse"
    ][0]
    assert fingerprint(after) == fingerprint(before)


def test_channel_plan_escalates_when_a_conflicted_radio_is_congested(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    ap2 = mk_ap(repo, "ap-2")
    r1 = mk_radio(repo, "ap-1:ng", ap1, band="ng", ht=20, channel=1)
    mk_radio(repo, "ap-2:ng", ap2, band="ng", ht=20, channel=1)
    gauge(repo, r1, "cu_total", [70.0] * 6)  # median well over the 50% floor

    conflict = [
        f
        for f in ChannelPlanDetector().evaluate(_ctx(repo))
        if f.dims["subtype"] == "co_channel_reuse"
    ][0]
    assert conflict.severity is Severity.P2
    assert conflict.evidence["congested_radios"] == ["ap-1:ng"]
    assert "conflicted_radio_congestion_checked" in conflict.confounders_checked


def test_channel_plan_wide_5ghz_is_one_issue_for_the_band(repo: Repository) -> None:
    """The 80 MHz width policy is one decision, not one issue per wide radio."""
    seed_cov(repo)
    for i in range(4):
        ap = mk_ap(repo, f"ap-{i}")
        mk_radio(repo, f"ap-{i}:na", ap, band="na", ht=80, channel=36 + i * 8)

    wide = [
        f
        for f in ChannelPlanDetector().evaluate(_ctx(repo))
        if f.dims["subtype"] == "wide_channel_dense_5ghz"
    ]
    assert len(wide) == 1
    assert wide[0].entity.native_id == "rf:5"
    assert wide[0].evidence["ap_count"] == 4
    assert len(wide[0].evidence["radios"]) == 4
    assert "single_ap_site_checked" in wide[0].confounders_checked


def test_channel_plan_per_radio_subtypes_stay_per_radio(repo: Repository) -> None:
    """Off-grid and 40 MHz on 2.4 GHz keep one issue (and one fix) per radio."""
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    ap2 = mk_ap(repo, "ap-2")
    mk_radio(repo, "ap-1:ng", ap1, band="ng", ht=40, channel=3)
    mk_radio(repo, "ap-2:ng", ap2, band="ng", ht=40, channel=9)

    findings = ChannelPlanDetector().evaluate(_ctx(repo))
    per_radio = [
        f for f in findings if f.dims["subtype"] in ("channel_off_grid", "wide_channel_24ghz")
    ]
    assert len(per_radio) == 4  # two radios x two independent defects
    assert all(f.entity.entity_type is EntityType.RADIO for f in per_radio)
    # An off-grid radio is not counted into the band's load vector, so two radios
    # off the grid do not also raise a band conflict.
    assert [f for f in findings if f.dims["subtype"] == "co_channel_reuse"] == []


def test_channel_plan_candidate_channels_are_tunable(repo: Repository) -> None:
    seed_cov(repo)
    _chan_radios(repo, "na", [36, 36])
    settings = _settings(KEY_CHANNEL_PLAN, candidate_channels_5=[100, 104])

    # Neither radio sits on a configured candidate, so the band has no load to
    # re-plan and nothing is claimed about it.
    findings = ChannelPlanDetector().evaluate(_ctx(repo, settings=settings))
    assert [f for f in findings if f.dims["subtype"] == "co_channel_reuse"] == []


def test_channel_plan_suppressed_when_clean(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:ng", ap1, band="ng", ht=20, channel=6)  # on-grid, 20 MHz
    mk_radio(repo, "ap-1:na", ap1, band="na", ht=40, channel=36)  # 40 MHz on 5 GHz is fine
    assert ChannelPlanDetector().evaluate(_ctx(repo)) == []


def test_channel_plan_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:ng", ap1, band="ng", ht=20, channel=3)
    assert ChannelPlanDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.dfs_recurring
# ====================================================================== #
def _radar(repo: Repository, ap_id: int, ts: int) -> None:
    repo.record_event(ts=ts, key="EVT_AP_RadarDetected", entity_id=ap_id)


def test_dfs_fires_on_recurring_radar(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    # 7 hits within the last 7 days, at distinct hours-of-day (no clustering).
    for j in range(1, 8):
        _radar(repo, ap1, NOW - j * DAY + j * 3600)

    findings = DfsRecurringDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_DFS_RECURRING
    assert f.severity is Severity.P3
    assert f.evidence["radar_events"] == 7
    assert "recurrence_over_days" in f.confounders_checked


def test_dfs_fires_p2_on_same_hour_clustering(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    # Seven hits, all at the same hour-of-day -> predictable radar -> P2.
    base_hour = (NOW % DAY) - (NOW % 3600)
    for j in range(1, 8):
        _radar(repo, ap1, NOW - j * DAY - (NOW % 3600) + 1800)
    assert base_hour or True  # readability anchor
    findings = DfsRecurringDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert "same_hour_clustering" in findings[0].confounders_checked


def test_dfs_suppressed_on_single_hit(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    _radar(repo, ap1, NOW - 2 * DAY)  # one-off, well under 1/day
    assert DfsRecurringDetector().evaluate(_ctx(repo)) == []


def test_dfs_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    for j in range(1, 9):
        _radar(repo, ap1, NOW - j * DAY)
    assert DfsRecurringDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.airtime_saturation
# ====================================================================== #
def test_airtime_fires_critical(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    rid = mk_radio(repo, "ap-1:na", ap1, band="na")
    gauge(repo, rid, "cu_total", [85.0] * 8)
    gauge(repo, rid, "cu_self_rx", [10.0] * 8)
    gauge(repo, rid, "cu_self_tx", [10.0] * 8)

    findings = AirtimeSaturationDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_AIRTIME_SATURATION
    assert f.severity is Severity.P1
    assert f.evidence["level"] == "critical"
    assert f.evidence["dominant_source"] == "non_self"
    assert "self_vs_non_self_split" in f.confounders_checked


def test_airtime_fires_degraded_p2(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    rid = mk_radio(repo, "ap-1:na", ap1, band="na")
    gauge(repo, rid, "cu_total", [60.0] * 8)

    findings = AirtimeSaturationDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["level"] == "degraded"


def test_airtime_suppressed_on_burst(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    rid = mk_radio(repo, "ap-1:na", ap1, band="na")
    # Mostly quiet with a single spike -> not sustained.
    gauge(repo, rid, "cu_total", [20.0, 20.0, 20.0, 90.0, 20.0, 20.0, 20.0, 20.0])
    assert AirtimeSaturationDetector().evaluate(_ctx(repo)) == []


def test_airtime_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    rid = mk_radio(repo, "ap-1:na", ap1, band="na")
    gauge(repo, rid, "cu_total", [85.0] * 8)
    assert AirtimeSaturationDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.tx_power_loud
# ====================================================================== #
def test_tx_power_fires_p3_multi_ap_high(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_ap(repo, "ap-2")
    mk_radio(repo, "ap-1:na", ap1, band="na", meta_extra={"tx_power_mode": "high"})

    findings = TxPowerLoudDetector().evaluate(_ctx(repo))
    loud = [f for f in findings if f.dims.get("subtype") == "loud_power"]
    assert len(loud) == 1
    assert loud[0].severity is Severity.P3
    assert "multi_ap_site" in loud[0].confounders_checked


def test_tx_power_escalates_p2_with_sticky_cluster(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_ap(repo, "ap-2")
    mk_radio(repo, "ap-1:na", ap1, band="na", meta_extra={"tx_power_mode": "high"})
    # Three weak clients parked on ap-1 -> sticky concentration corroborates.
    for i in range(3):
        c = mk_client(repo, f"cli-{i}", parent_id=ap1, ap_mac="ap-1")
        gauge(repo, c, "rssi", [-78.0] * 8)

    findings = TxPowerLoudDetector().evaluate(_ctx(repo))
    loud = [f for f in findings if f.dims.get("subtype") == "loud_power"]
    assert loud and loud[0].severity is Severity.P2
    assert loud[0].evidence["sticky_clients_on_ap"] == 3


def test_tx_power_imbalance_subcase(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_ap(repo, "ap-2")
    mk_radio(repo, "ap-1:ng", ap1, band="ng", meta_extra={"tx_power": 20})
    mk_radio(repo, "ap-1:na", ap1, band="na", meta_extra={"tx_power": 20})  # 2.4 not below 5

    findings = TxPowerLoudDetector().evaluate(_ctx(repo))
    assert any(f.dims.get("subtype") == "band_imbalance" for f in findings)


def test_tx_power_suppressed_single_ap(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:na", ap1, band="na", meta_extra={"tx_power_mode": "high"})
    assert TxPowerLoudDetector().evaluate(_ctx(repo)) == []


def test_tx_power_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_ap(repo, "ap-2")
    mk_radio(repo, "ap-1:na", ap1, band="na", meta_extra={"tx_power_mode": "high"})
    assert TxPowerLoudDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.legacy_rates
# ====================================================================== #
def test_legacy_rates_fires_on_11b_client(repo: Repository) -> None:
    seed_cov(repo)
    cid = mk_client(repo, "cli-1")
    # 11_000 kbps = 11 Mbps, the top 802.11b rate, as the store holds it.
    gauge(repo, cid, "tx_rate", [11_000.0] * 8)

    findings = LegacyRatesDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_LEGACY_RATES
    assert f.severity is Severity.P3
    assert f.evidence["median_tx_rate_mbps"] == 11.0
    assert f.evidence["matches_11b_rate"] is True
    assert "rate_sustained_not_momentary" in f.confounders_checked


def test_legacy_rates_suppressed_for_fast_client(repo: Repository) -> None:
    seed_cov(repo)
    cid = mk_client(repo, "cli-1")
    gauge(repo, cid, "tx_rate", [300_000.0] * 8)  # 300 Mbps
    assert LegacyRatesDetector().evaluate(_ctx(repo)) == []


def test_legacy_rates_converts_kbps_exactly_once(repo: Repository) -> None:
    """A modern client must not be mistaken for 802.11b by a double conversion.

    866_700 kbps is 866.7 Mbps; divide by 1000 twice and it reads 0.87 Mbps,
    under the 11 Mbps ceiling, and every fast client on the site becomes a
    legacy-rate finding.
    """
    seed_cov(repo)
    cid = mk_client(repo, "cli-1")
    gauge(repo, cid, "tx_rate", [866_700.0] * 8)
    assert LegacyRatesDetector().evaluate(_ctx(repo)) == []


def test_legacy_rates_excludes_wired(repo: Repository) -> None:
    seed_cov(repo)
    cid = mk_client(repo, "cli-1", is_wired=True)
    gauge(repo, cid, "tx_rate", [11_000.0] * 8)
    assert LegacyRatesDetector().evaluate(_ctx(repo)) == []


def test_legacy_rates_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    cid = mk_client(repo, "cli-1")
    gauge(repo, cid, "tx_rate", [11_000.0] * 8)
    assert LegacyRatesDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.band_steering
# ====================================================================== #
def test_band_steering_fires_steer_up(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:na", ap1, band="na")  # idle 5 GHz radio, no cu samples
    cid = mk_client(repo, "cli-1", parent_id=ap1, band_history=["na", "ng"])
    gauge(repo, cid, "rssi", [-60.0] * 8)  # strong 2.4

    findings = BandSteeringDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.dims["subtype"] == "parked_on_24"
    assert f.severity is Severity.P3
    assert "dual_band_confirmed" in f.confounders_checked


def test_band_steering_fires_steer_down(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    cid = mk_client(repo, "cli-1", parent_id=ap1, band_history=["na"])
    gauge(repo, cid, "rssi", [-85.0] * 8)  # held on 5 GHz, too weak

    findings = BandSteeringDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].dims["subtype"] == "held_on_5"
    assert "weak_rssi_sustained" in findings[0].confounders_checked


def test_band_steering_suppressed_single_band(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:na", ap1, band="na")  # idle 5 GHz available
    # Strong on 2.4 but never seen on 5 GHz -> cannot prove dual-band -> no nag.
    cid = mk_client(repo, "cli-1", parent_id=ap1, band_history=["ng"])
    gauge(repo, cid, "rssi", [-60.0] * 8)
    assert BandSteeringDetector().evaluate(_ctx(repo)) == []


def test_band_steering_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:na", ap1, band="na")
    cid = mk_client(repo, "cli-1", parent_id=ap1, band_history=["na", "ng"])
    gauge(repo, cid, "rssi", [-60.0] * 8)
    assert BandSteeringDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.mesh_uplink
# ====================================================================== #
def test_mesh_uplink_fires_on_bad_rssi(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1", uplink_type="wireless")
    gauge(repo, ap1, "uplink_rssi", [-75.0] * 8)

    findings = MeshUplinkDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_MESH_UPLINK
    assert f.severity is Severity.P2
    assert "sustained_poor_rssi" in f.confounders_checked


def test_mesh_uplink_warn_escalates_with_reconnects(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1", uplink_type="wireless")
    gauge(repo, ap1, "uplink_rssi", [-67.0] * 8)  # in warn band (-65..-70)
    for ts in (NOW - 400, NOW - 200):
        repo.record_event(ts=ts, key="EVT_AP_Lost_Contact", entity_id=ap1)

    findings = MeshUplinkDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["reconnect_cycles"] == 2
    assert "reconnect_corroboration_checked" in findings[0].confounders_checked


def test_mesh_uplink_warn_only_is_p3(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1", uplink_type="wireless", uplink_hops=1)
    gauge(repo, ap1, "uplink_rssi", [-67.0] * 8)  # warn band, no corroboration
    findings = MeshUplinkDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P3


def test_mesh_uplink_latent_wired_with_mesh_enabled(repo: Repository) -> None:
    seed_cov(repo)
    mk_ap(repo, "ap-1", uplink_type="wire", meta={"mesh_enabled": True})
    findings = MeshUplinkDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].dims["subtype"] == "wired_with_mesh_enabled"
    assert findings[0].severity is Severity.P3


def test_mesh_uplink_suppressed_on_strong_uplink(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1", uplink_type="wireless")
    gauge(repo, ap1, "uplink_rssi", [-55.0] * 8)  # healthy backhaul
    assert MeshUplinkDetector().evaluate(_ctx(repo)) == []


def test_mesh_uplink_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    ap1 = mk_ap(repo, "ap-1", uplink_type="wireless")
    gauge(repo, ap1, "uplink_rssi", [-75.0] * 8)
    assert MeshUplinkDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# threshold override wiring (spot-check the settings seam)
# ====================================================================== #
def test_airtime_threshold_override(repo: Repository) -> None:
    seed_cov(repo)
    ap1 = mk_ap(repo, "ap-1")
    rid = mk_radio(repo, "ap-1:na", ap1, band="na")
    gauge(repo, rid, "cu_total", [40.0] * 8)
    # Default degraded is 50%; lower it to 30% so 40% now fires.
    settings = _settings(KEY_AIRTIME_SATURATION, degraded_pct=30)
    findings = AirtimeSaturationDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1
    assert findings[0].evidence["level"] == "degraded"


def test_pingpong_threshold_override(repo: Repository) -> None:
    seed_cov(repo)
    ap_a = mk_ap(repo, "ap-a")
    ap_b = mk_ap(repo, "ap-b")
    cid = mk_client(repo, "cli-1")
    for i in range(4):
        roam(repo, cid, NOW - 300 + i * 60, ap_a if i % 2 == 0 else ap_b)
    # 4 roams/h: below default suspicious (5) but a lowered tier catches it.
    settings = _settings(KEY_PINGPONG_ROAMER, suspicious_rate_per_h=3)
    findings = PingpongRoamerDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1
    assert findings[0].evidence["reason"] == "rate_suspicious"
    assert findings[0].severity is Severity.P3


# ====================================================================== #
# Neighbour-scan fixtures (wifi.neighbor_density + wifi.rogue_ap)
# ====================================================================== #
def mk_rogue(
    repo: Repository,
    bssid: str,
    *,
    channel: int,
    rssi: int,
    signal: Optional[int] = None,
    band: Optional[str] = None,
    essid: str = "Neighbor",
    first_seen: Optional[int] = None,
    last_seen: int = NOW,
    is_rogue: bool = True,
    is_ubnt: Optional[bool] = None,
    scan_ts: Optional[list] = None,
    security: Optional[str] = None,
    channels: Optional[list] = None,
    seen_by_ap: str = "ap-1",
) -> int:
    """Seed a ``rogue_bss`` inventory entity as the daily rogueap poll would.

    ``first_seen``/``last_seen`` control the sighting span the detectors read for
    their (legacy) span-based persistence fallback; ``scan_ts`` supplies the
    per-scan sighting log they now prefer. ``is_ubnt`` mirrors the controller's
    own-hardware flag, ``channels`` the distinct-channel log the poll keeps for a
    hopping neighbour.
    """
    meta: dict = {"channel": channel, "rssi": rssi, "is_rogue": is_rogue, "seen_by_ap": seen_by_ap}
    if signal is not None:
        meta["signal"] = signal
    if band is not None:
        meta["band"] = band
    if is_ubnt is not None:
        meta["is_ubnt"] = is_ubnt
    if scan_ts is not None:
        meta["scan_ts"] = scan_ts
    if security is not None:
        meta["security"] = security
    if channels is not None:
        meta["channels"] = channels
    return repo.upsert_entity(
        Entity(
            entity_type=ROGUE_BSS_TYPE,  # type: ignore[arg-type]
            native_id=bssid,
            name=essid,
            meta=meta,
            first_seen_ts=first_seen if first_seen is not None else last_seen,
        ),
        ts=last_seen,
    )


def mk_wlan(
    repo: Repository,
    ssid: str,
    *,
    wlan_id: Optional[str] = None,
    enabled: bool = True,
    security: str = "wpapsk",
    now: int = NOW,
) -> int:
    """Seed a WLAN entity as the ``rest/wlanconf`` read would."""
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.WLAN,
            native_id=wlan_id or f"wlan-{ssid}",
            name=ssid,
            meta={"enabled": enabled, "security": security},
        ),
        ts=now,
    )


def _our_5ghz_radio(repo: Repository, *, channel: int = 36) -> int:
    ap = mk_ap(repo, "ap-1")
    return mk_radio(repo, "ap-1:na", ap, band="na", ht=20, channel=channel)


def _neighbors_on_36(repo: Repository, count: int, *, rssi: int = -60) -> None:
    """``count`` strong, persistent neighbours co-channel with our 5 GHz radio."""
    for i in range(count):
        mk_rogue(
            repo,
            f"de:ad:be:ef:10:{i:02x}",
            channel=36,
            rssi=rssi,
            band="na",
            essid=f"Neighbor-{i}",
            is_rogue=False,
            first_seen=NOW - 2 * DAY,
        )


# ====================================================================== #
# wifi.neighbor_density
# ====================================================================== #
def test_neighbor_density_fires_once_per_band(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 4)

    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert len(findings) == 1  # four neighbours, ONE issue
    f = findings[0]
    assert f.detector_key == KEY_NEIGHBOR_DENSITY
    assert f.severity is Severity.P3  # density alone is context, never P2
    assert f.entity.native_id == "rf:5"
    assert f.entity.entity_id is None  # a site-scoped pseudo-entity, not a stored row
    assert f.dims == {"band": "5"}  # channel is NOT in the fingerprint
    assert f.evidence["qualifying_count"] == 4
    assert f.evidence["per_channel"] == {"36": 4}
    assert f.evidence["overlapping_radios"] == ["ap-1:na"]
    assert [o["bssid"] for o in f.evidence["top_offenders"]] == [
        "de:ad:be:ef:10:00",
        "de:ad:be:ef:10:01",
        "de:ad:be:ef:10:02",
        "de:ad:be:ef:10:03",
    ]


def test_neighbor_density_one_issue_per_band(repo: Repository) -> None:
    """A crowded 2.4 GHz and a crowded 5 GHz are two issues, not two hundred."""
    seed_cov(repo)
    ap = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:na", ap, band="na", ht=20, channel=36)
    mk_radio(repo, "ap-1:ng", ap, band="ng", ht=20, channel=6)
    _neighbors_on_36(repo, 3)
    for i in range(3):
        mk_rogue(
            repo,
            f"de:ad:be:ef:24:{i:02x}",
            channel=6,
            rssi=-62,
            band="ng",
            is_rogue=False,
            first_seen=NOW - 2 * DAY,
        )

    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert sorted(f.dims["band"] for f in findings) == ["2.4", "5"]
    assert sorted(f.entity.native_id for f in findings) == ["rf:2.4", "rf:5"]


def test_neighbor_density_below_floor_is_a_clear(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 2)  # under the default density_min_count of 3
    assert NeighborDensityDetector().evaluate(_ctx(repo)) == []


def test_neighbor_density_floor_is_tunable(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 2)
    settings = _settings(KEY_NEIGHBOR_DENSITY, density_min_count=2)
    findings = NeighborDensityDetector().evaluate(_ctx(repo, settings=settings))
    assert len(findings) == 1
    assert findings[0].evidence["qualifying_count"] == 2


@pytest.mark.parametrize(
    "meta,expected",
    [
        ({"rssi": 9}, -86),  # bare quality index: 9 above a -95 dBm floor
        ({"rssi": 49}, -46),  # the loudest index seen on a real store
        ({"rssi": 0}, -95),  # a real reading AT the floor, not a missing one
        ({"rssi": -72}, -72),  # already dBm: passed through, re-run safe
        ({"signal": -88, "rssi": 40}, -88),  # signal is dBm and wins
        ({"signal": -88}, -88),  # signal alone
        ({"signal": 40, "rssi": 9}, -86),  # malformed positive signal: ignored
        ({"signal": 0, "rssi": 9}, -86),  # 0 is not a plausible dBm: ignored
        ({}, None),  # neither field: unplaceable
        ({"rssi": None, "signal": None}, None),
        ({"rssi": "notanumber"}, None),
        # A small-negative sentinel is not a -1 dBm neighbour standing in the
        # room. Reject it, or it clears every floor and inflates the count.
        ({"signal": -1, "rssi": 9}, -86),  # sentinel ignored, index used
        ({"signal": -1}, None),  # sentinel with nothing to fall back to
        ({"rssi": -2}, None),  # corrupted index, not a -2 dBm sighting
        ({"signal": -400, "rssi": 9}, -86),  # below any real noise floor
    ],
)
def test_neighbor_rssi_dbm_normalizes_every_shape_the_scan_can_report(meta, expected) -> None:
    """The scan's two strength fields, and the junk either can arrive as."""
    assert _neighbor_rssi_dbm(meta) == expected


def test_neighbor_rssi_dbm_honours_a_tuned_noise_floor() -> None:
    """A driver referenced to a different floor is correctable, not hardcoded."""
    assert _neighbor_rssi_dbm({"rssi": 9}, -90) == -81
    assert _neighbor_rssi_dbm({"signal": -88}, -90) == -88  # dBm ignores the floor


def test_neighbor_density_noise_floor_is_tunable_end_to_end(repo: Repository) -> None:
    """The threshold reaches the decoder, not just the helper's default arg."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 5, rssi=22)  # -73 dBm by default: over the -75 floor
    assert len(NeighborDensityDetector().evaluate(_ctx(repo))) == 1

    # Same scan, a floor 10 dB lower: -83 dBm, now under the -75 line.
    settings = _settings(KEY_NEIGHBOR_DENSITY, noise_floor_dbm=-105)
    assert NeighborDensityDetector().evaluate(_ctx(repo, settings=settings)) == []


def test_neighbor_density_reads_the_quality_index_as_dbm_not_as_a_raw_number(
    repo: Repository,
) -> None:
    """A scan that reports the 0-based index must not sail past the dBm floor.

    ``stat/rogueap`` reports strength as a positive quality index above the noise
    floor. Compared raw against ``rssi_floor_dbm`` (-75) every neighbour passed,
    because a positive number is never <= -75, and the filter became a no-op:
    real sites reported hundreds of "neighbouring networks" that were in fact
    barely audible. Index 9 is -86 dBm, well under the floor, so this is a clear.
    """
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 6, rssi=9)
    assert NeighborDensityDetector().evaluate(_ctx(repo)) == []


def test_neighbor_density_counts_a_quality_index_that_is_genuinely_strong(
    repo: Repository,
) -> None:
    """The conversion must not swing the other way and mute real neighbours."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 4, rssi=40)  # -55 dBm, comfortably over the floor
    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["qualifying_count"] == 4
    assert findings[0].evidence["top_offenders"][0]["rssi_dbm"] == -55


def test_neighbor_density_prefers_the_signal_field_when_the_poll_captured_it(
    repo: Repository,
) -> None:
    """``signal`` is already dBm, so it wins over the index it sits beside."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    for i in range(4):
        mk_rogue(
            repo,
            f"de:ad:be:ef:20:{i:02x}",
            channel=36,
            rssi=40,  # index alone would read as -55 dBm
            signal=-88,  # the controller's truth: far too weak to matter
            band="na",
            essid=f"Far-{i}",
            scan_ts=[NOW - 86_400, NOW],
        )
    assert NeighborDensityDetector().evaluate(_ctx(repo)) == []


def test_neighbor_density_p2_only_when_an_overlapped_radio_is_congested(repo: Repository) -> None:
    seed_cov(repo)
    radio = _our_5ghz_radio(repo, channel=36)
    gauge(repo, radio, "cu_total", [72.0] * 8)  # sustained congestion on the overlapped radio
    _neighbors_on_36(repo, 3)

    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].severity is Severity.P2
    assert findings[0].evidence["congested_overlap_radios"] == ["ap-1:na"]
    assert "overlapped_radio_congestion_checked" in findings[0].confounders_checked


def test_neighbor_density_counts_only_qualifying_neighbours(repo: Repository) -> None:
    """Weak, transient, allowlisted and own-hardware BSSes are seen, not counted."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 3)
    mk_rogue(repo, "de:ad:be:ef:20:01", channel=36, rssi=-88, band="na", first_seen=NOW - 2 * DAY)
    mk_rogue(repo, "de:ad:be:ef:20:02", channel=36, rssi=-55, band="na")  # single scan
    mk_rogue(repo, "de:ad:be:ef:20:03", channel=149, rssi=-55, band="na", first_seen=NOW - 2 * DAY)

    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["qualifying_count"] == 3
    assert findings[0].evidence["total_seen"] == 6  # the scan saw all six on 5 GHz


def test_neighbor_density_excludes_own_ubnt_hardware(repo: Repository) -> None:
    seed_cov(repo)
    ap = mk_ap(repo, "f0:9f:c2:11:22:33")
    mk_radio(repo, "f0:9f:c2:11:22:33:na", ap, band="na", ht=20, channel=36)
    _neighbors_on_36(repo, 3)
    # Our own virtual BSSID (same vendor+device prefix, is_ubnt) must not count.
    mk_rogue(
        repo,
        "f0:9f:c2:11:22:44",
        channel=36,
        rssi=-55,
        band="na",
        is_ubnt=True,
        first_seen=NOW - 2 * DAY,
    )
    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["qualifying_count"] == 3


def test_neighbor_density_excludes_allowlisted_bssid(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 4)
    settings = _settings(KEY_NEIGHBOR_DENSITY, known_bssids=["DE:AD:BE:EF:10:00"])
    findings = NeighborDensityDetector().evaluate(_ctx(repo, settings=settings))
    assert findings[0].evidence["qualifying_count"] == 3
    assert findings[0].evidence["total_seen"] == 4


def test_neighbor_density_counts_24ghz_adjacent_overlap(repo: Repository) -> None:
    seed_cov(repo)
    ap = mk_ap(repo, "ap-1")
    mk_radio(repo, "ap-1:ng", ap, band="ng", ht=20, channel=6)
    for i, channel in enumerate((4, 6, 8)):  # all within the 2.4 GHz overlap distance
        mk_rogue(
            repo,
            f"de:ad:be:ef:30:{i:02x}",
            channel=channel,
            rssi=-64,
            band="ng",
            first_seen=NOW - 2 * DAY,
        )
    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["per_channel"] == {"4": 1, "6": 1, "8": 1}


def test_neighbor_density_ignores_stale_sightings(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 3)
    for i in range(3):  # last seen five days ago -> gone from the air
        mk_rogue(
            repo,
            f"de:ad:be:ef:40:{i:02x}",
            channel=36,
            rssi=-55,
            band="na",
            first_seen=NOW - 7 * DAY,
            last_seen=NOW - 5 * DAY,
        )
    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["qualifying_count"] == 3
    assert findings[0].evidence["total_seen"] == 3


def test_neighbor_density_caps_the_offender_list(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 14)
    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["qualifying_count"] == 14
    assert len(findings[0].evidence["top_offenders"]) == 10  # row size stays bounded


def test_neighbor_density_unknown_on_no_data(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    # A fresh/unscanned store is not a clean "quiet neighbourhood".
    assert NeighborDensityDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_neighbor_density_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    _neighbors_on_36(repo, 4)
    assert NeighborDensityDetector().evaluate(_ctx(repo)) is UNKNOWN


# ====================================================================== #
# wifi.rogue_ap
# ====================================================================== #
def test_rogue_ap_ignores_ordinary_neighbours(repo: Repository) -> None:
    """The flood is gone: crowded air is not a rogue, however strong or close."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    _neighbors_on_36(repo, 12)
    assert RogueApDetector().evaluate(_ctx(repo)) == []


def test_rogue_ap_fires_p1_on_ssid_spoof(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet", security="wpapsk")
    mk_rogue(
        repo,
        "de:ad:be:ef:50:01",
        channel=149,  # a different channel: an evil twin need not share ours
        rssi=-70,
        band="na",
        essid="homenet",  # case-folded match against our SSID
        is_rogue=False,
        security="open",
    )
    findings = RogueApDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.detector_key == KEY_ROGUE_AP
    assert f.severity is Severity.P1
    assert f.dims == {"subtype": "ssid_spoof"}  # channel is NOT in the fingerprint
    assert f.evidence["matched_our_ssid"] == "HomeNet"
    assert f.evidence["our_ssid_source"] == "wlanconf"
    assert f.evidence["security_mismatch"] is True  # open twin of our secured SSID
    assert "our_ssid_set_from_wlanconf" in f.confounders_checked


def test_rogue_ap_spoof_falls_back_to_client_essids(repo: Repository) -> None:
    """No wlanconf on this console -> our clients' own ESSIDs are the SSID set."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_client(repo, "cc:cc:cc:00:00:01", essid="HomeNet")
    mk_rogue(repo, "de:ad:be:ef:50:02", channel=36, rssi=-65, band="na", essid="HomeNet")
    findings = RogueApDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].severity is Severity.P1
    assert findings[0].evidence["our_ssid_source"] == "client_essid"
    assert findings[0].evidence["security_mismatch"] is None  # neither side readable


def test_rogue_ap_spoof_ignores_a_deleted_wlan(repo: Repository) -> None:
    """A WLAN no longer in the controller's config is not one of our SSIDs."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "OldNet", now=NOW - 30 * DAY)  # not refreshed by the daily read
    mk_wlan(repo, "HomeNet")  # the SSID set is resolvable, so this is a real clear
    mk_rogue(
        repo, "de:ad:be:ef:50:03", channel=36, rssi=-65, band="na", essid="OldNet", is_rogue=False
    )
    assert RogueApDetector().evaluate(_ctx(repo)) == []


def test_rogue_ap_spoof_subsumes_the_controller_flag(repo: Repository) -> None:
    """One box, one issue: the P1 spoof carries the controller's flag as evidence."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(
        repo, "de:ad:be:ef:50:04", channel=36, rssi=-60, band="na", essid="HomeNet", is_rogue=True
    )
    findings = RogueApDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].dims == {"subtype": "ssid_spoof"}
    assert findings[0].evidence["controller_flagged_rogue"] is True


def test_rogue_ap_unknown_when_the_ssid_set_cannot_be_resolved(repo: Repository) -> None:
    """No wlanconf and no client ESSIDs: freeze, never guess an evil twin."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    flagged = mk_rogue(
        repo,
        "de:ad:be:ef:50:05",
        channel=36,
        rssi=-60,
        band="na",
        is_rogue=True,
        scan_ts=[NOW - 86_400, NOW - 600],  # persistent: the floors require it
    )
    result = RogueApDetector().evaluate(_ctx(repo))
    assert isinstance(result, DetectorResult)
    # The controller's verdict still fires; the spoof subtype is frozen, not cleared.
    assert [f.dims["subtype"] for f in result.findings] == ["controller_flagged"]
    assert flagged in result.unknown_entities


def test_rogue_ap_controller_flagged_is_p2(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(
        repo,
        "de:ad:be:ef:60:01",
        channel=36,
        rssi=-60,
        band="na",
        essid="SomeoneElse",
        is_rogue=True,
        channels=[36, 40],
        scan_ts=[NOW - 86_400, NOW - 600],  # persistent: the floors require it
    )
    findings = RogueApDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.P2
    assert f.dims == {"subtype": "controller_flagged"}
    assert f.evidence["verdict_source"] == "controller"
    assert f.evidence["channels_seen"] == [36, 40]  # hopping history, not new issues
    assert f.evidence["wired_mac_prefix_match"] is False


def test_rogue_ap_controller_flagged_lifts_to_p1_on_a_wired_mac_match(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_client(repo, "aa:bb:cc:dd:ee:01", is_wired=True)  # same vendor+device prefix
    mk_rogue(
        repo,
        "aa:bb:cc:dd:ee:ff",
        channel=36,
        rssi=-60,
        band="na",
        essid="SomeoneElse",
        is_rogue=True,
        scan_ts=[NOW - 86_400, NOW - 600],  # persistent: the floors require it
    )
    findings = RogueApDetector().evaluate(_ctx(repo))
    assert findings[0].severity is Severity.P1
    assert findings[0].evidence["matching_wired_clients"] == ["aa:bb:cc:dd:ee:01"]
    assert "wired_mac_prefix_corroboration_checked" in findings[0].confounders_checked


def test_rogue_ap_excludes_own_ubnt_hardware(repo: Repository) -> None:
    """Our own virtual BSSID, flagged by the controller, is never a rogue."""
    seed_cov(repo)
    ap = mk_ap(repo, "f0:9f:c2:11:22:33")
    mk_radio(repo, "f0:9f:c2:11:22:33:na", ap, band="na", ht=20, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(
        repo,
        "f0:9f:c2:11:22:44",
        channel=36,
        rssi=-55,
        band="na",
        essid="HomeNet",
        is_ubnt=True,
        is_rogue=True,
    )
    assert RogueApDetector().evaluate(_ctx(repo)) == []


def test_rogue_ap_excludes_allowlisted_bssid(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(repo, "de:ad:be:ef:70:01", channel=36, rssi=-55, band="na", essid="HomeNet")
    settings = _settings(KEY_ROGUE_AP, known_bssids=["DE:AD:BE:EF:70:01"])
    assert RogueApDetector().evaluate(_ctx(repo, settings=settings)) == []


def test_rogue_ap_ignores_stale_sightings(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(
        repo,
        "de:ad:be:ef:70:02",
        channel=36,
        rssi=-55,
        band="na",
        essid="HomeNet",
        first_seen=NOW - 7 * DAY,
        last_seen=NOW - 5 * DAY,
    )
    assert RogueApDetector().evaluate(_ctx(repo)) == []


def test_rogue_ap_unknown_on_no_data(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    # No rogue_bss rows at all: a fresh/unscanned store is not a clean clear.
    assert RogueApDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_rogue_ap_unknown_on_low_coverage(repo: Repository) -> None:
    seed_low_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(repo, "de:ad:be:ef:70:03", channel=36, rssi=-55, band="na", essid="HomeNet")
    assert RogueApDetector().evaluate(_ctx(repo)) is UNKNOWN


def test_neighbor_density_infers_band_from_channel(repo: Repository) -> None:
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    for i in range(3):  # band field absent from the scan row -> inferred from channel 36
        mk_rogue(
            repo,
            f"de:ad:be:ef:80:{i:02x}",
            channel=36,
            rssi=-60,
            band=None,
            first_seen=NOW - 2 * DAY,
        )
    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert findings[0].evidence["band"] == "5"


# --------------------------------------------------------------------------- #
# Neighbour persistence: the path production actually takes, plus the floors
# that keep a controller flag from recreating the flood (Gitea #17 review).
# --------------------------------------------------------------------------- #
def test_neighbor_density_uses_the_scan_log_not_just_the_span(repo: Repository) -> None:
    """The collector writes ``scan_ts`` every poll, so this is the live path.

    Every other density test omits it and therefore only exercises the legacy
    span fallback, which let the recency filter be deleted without failing.
    """
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    for i in range(4):
        mk_rogue(
            repo,
            f"de:ad:be:ef:20:0{i}",
            channel=36,
            rssi=-60,
            band="5",
            scan_ts=[NOW - 172_800, NOW - 86_400, NOW - 600],
        )

    findings = NeighborDensityDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].evidence["qualifying_count"] == 4


def test_neighbor_density_ignores_a_bss_whose_scans_are_all_stale(repo: Repository) -> None:
    """Seen twice, but long ago: a wide span is not persistence."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    for i in range(4):
        mk_rogue(
            repo,
            f"de:ad:be:ef:21:0{i}",
            channel=36,
            rssi=-60,
            band="5",
            first_seen=NOW - 2_000_000,  # a wide span the fallback would accept
            scan_ts=[NOW - 2_000_000, NOW - 1_900_000],  # but nothing recent
        )

    assert NeighborDensityDetector().evaluate(_ctx(repo)) == []


def test_controller_flagged_rogue_needs_proximity_and_persistence(repo: Repository) -> None:
    """A far, once-seen BSS the controller flagged is not a security finding.

    Regression for the review finding: the flag is unbounded evidence, so leaving
    it ungated recreated the exact flood migration 0005 exists to erase (80 weak,
    single-sighting neighbours each becoming their own P2 with M=1).
    """
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    for i in range(20):
        mk_rogue(
            repo,
            f"de:ad:be:ef:22:{i:02x}",
            channel=149,  # off our channel
            rssi=-92,  # far away
            band="5",
            essid="SomeNeighbor",
            is_rogue=True,
            scan_ts=[NOW - 600],  # seen exactly once
        )

    assert RogueApDetector().evaluate(_ctx(repo)) == []


def test_controller_flagged_rogue_still_fires_when_it_is_close_and_persistent(
    repo: Repository,
) -> None:
    """The floors bound the noise; they must not silence a real one."""
    seed_cov(repo)
    _our_5ghz_radio(repo, channel=36)
    mk_wlan(repo, "HomeNet")
    mk_rogue(
        repo,
        "de:ad:be:ef:23:01",
        channel=149,
        rssi=-52,
        band="5",
        essid="SomeNeighbor",
        is_rogue=True,
        scan_ts=[NOW - 86_400, NOW - 600],
    )

    findings = RogueApDetector().evaluate(_ctx(repo))
    assert len(findings) == 1
    assert findings[0].dims["subtype"] == "controller_flagged"


# ====================================================================== #
# title qualification (Gitea #55)
# ====================================================================== #
def _named_radio(
    repo: Repository, *, ap_name: Optional[str], radio_name: str, band: str = "na"
) -> int:
    """A radio named the way a real controller names it (``wifi0``/``wifi1``),
    under an AP with (or without) a resolvable name. Returns the radio id."""
    if ap_name is not None:
        parent = mk_ap(repo, "ap-named", name=ap_name)
    else:
        parent = None
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id="ap-named:na",
            site_id="default",
            name=radio_name,
            parent_id=parent,
            meta={"band": band},
        ),
        ts=NOW,
    )


def test_airtime_title_qualifies_radio_with_ap(repo: Repository) -> None:
    """The radio title names its AP ("Loft / wifi0"), not the bare "wifi0" that
    repeats identically across every AP on the site (Gitea #44/#55)."""
    seed_cov(repo)
    rid = _named_radio(repo, ap_name="Loft", radio_name="wifi0")
    gauge(repo, rid, "cu_total", [60.0] * 8)
    f = AirtimeSaturationDetector().evaluate(_ctx(repo))[0]
    assert f.title == "Airtime saturation (degraded) on Loft / wifi0"


def test_airtime_title_degrades_to_bare_radio_when_ap_unresolved(repo: Repository) -> None:
    """A radio whose parent AP is unresolved degrades to the bare name, never
    "None / wifi0" (Gitea #55, the degraded case)."""
    seed_cov(repo)
    rid = _named_radio(repo, ap_name=None, radio_name="wifi0")
    gauge(repo, rid, "cu_total", [60.0] * 8)
    f = AirtimeSaturationDetector().evaluate(_ctx(repo))[0]
    assert f.title == "Airtime saturation (degraded) on wifi0"
    assert "None" not in f.title
