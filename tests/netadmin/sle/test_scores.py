"""SLE scoring: the score and its explanation are the same GROUP BY.

Seeds sle_minutes rows directly (the scorer is a pure read over the table the
minute job writes) and asserts per-SLE score = ok/total, the classifier breakdown,
the top-offender ranking, and the renormalised headline blend.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from netadmin.sle.classifiers import (
    CLS_NON_WIFI_UTIL,
    CLS_WEAK_SIGNAL,
    OK,
    SLE_CAPACITY,
    SLE_COVERAGE,
    SLE_INFRA,
    SLE_WAN,
)
from netadmin.sle.scores import DEFAULT_WEIGHTS, MIN_EXPOSURE_MINUTES, load_weights, sle_scores
from netadmin.store.repository import Repository

# One bucket wide: these tests seed every row at bucket_ts=0 and check the pure
# score/offender/blend math, so the window matches exactly what was measured
# (full exposure, one bucket) rather than tripping the confidence floor
# (test_scores.py's dedicated exposure tests cover thin-window behaviour).
WIN = (0, 300)


def _put(repo: Repository, sle, classifier, entity_id, minutes, attributed=None) -> None:
    repo.upsert_sle_minute(
        bucket_ts=0,
        sle=sle,
        classifier=classifier,
        entity_id=entity_id,
        minutes=minutes,
        attributed_entity_id=attributed,
    )


def test_per_sle_score_is_ok_over_total(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 90.0)
    _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, 1, 10.0, attributed=100)

    report = sle_scores(repo, *WIN)
    cov = report.sles[SLE_COVERAGE]
    assert cov.total_minutes == 100.0
    assert cov.ok_minutes == 90.0
    assert cov.fail_minutes == 10.0
    assert math.isclose(cov.score, 0.9)


def test_classifier_breakdown_includes_ok(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 90.0)
    _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, 1, 10.0, attributed=100)
    cov = sle_scores(repo, *WIN).sles[SLE_COVERAGE]
    assert cov.classifiers == {CLS_WEAK_SIGNAL: 10.0, OK: 90.0}


def test_no_data_sle_scores_none_and_excluded_from_headline(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 100.0)
    report = sle_scores(repo, *WIN)
    assert report.sles[SLE_INFRA].score is None
    assert report.sles[SLE_WAN].score is None
    # headline is driven only by the SLE that had data
    assert math.isclose(report.headline, 1.0)


def test_top_offenders_ranked_by_fail_minutes(repo: Repository) -> None:
    # two APs own weak minutes; the worse one ranks first, ok is never an offender
    _put(repo, SLE_COVERAGE, OK, 1, 50.0, attributed=None)
    _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, 1, 5.0, attributed=100)
    _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, 2, 20.0, attributed=200)

    cov = sle_scores(repo, *WIN).sles[SLE_COVERAGE]
    offenders = cov.top_offenders
    assert offenders[0] == {"attributed_entity_id": 200, "fail_minutes": 20.0}
    assert offenders[1] == {"attributed_entity_id": 100, "fail_minutes": 5.0}
    # ok minutes (attributed None) never appear as an offender
    assert all(o["attributed_entity_id"] is not None for o in offenders)


def test_top_offenders_capped_by_top_n(repo: Repository) -> None:
    for i in range(6):
        _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, i + 1, float(i + 1), attributed=i + 10)
    cov = sle_scores(repo, *WIN, top_n=3).sles[SLE_COVERAGE]
    assert len(cov.top_offenders) == 3
    # the three biggest offenders, most first
    assert [o["fail_minutes"] for o in cov.top_offenders] == [6.0, 5.0, 4.0]


def test_headline_is_renormalised_weighted_blend(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 90.0)
    _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, 1, 10.0, attributed=100)  # score 0.9
    _put(repo, SLE_CAPACITY, OK, 1, 80.0)
    _put(repo, SLE_CAPACITY, CLS_NON_WIFI_UTIL, 1, 20.0, attributed=200)  # score 0.8

    report = sle_scores(repo, *WIN)
    wc = DEFAULT_WEIGHTS[SLE_COVERAGE]
    wk = DEFAULT_WEIGHTS[SLE_CAPACITY]
    expected = (wc * 0.9 + wk * 0.8) / (wc + wk)
    assert math.isclose(report.headline, expected)


def test_headline_none_when_no_data(repo: Repository) -> None:
    report = sle_scores(repo, *WIN)
    assert report.headline is None
    assert all(sc.score is None for sc in report.sles.values())


def test_window_bounds_are_respected(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 100.0)  # bucket_ts = 0
    # a window that starts after the only row sees no data
    report = sle_scores(repo, 1, 10_000)
    assert report.sles[SLE_COVERAGE].score is None


# --------------------------------------------------------------------------- #
# Exposure: evaluated_buckets / window_buckets / the confidence floor
# --------------------------------------------------------------------------- #
def test_evaluated_and_window_buckets_reported(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 100.0)  # a single row at bucket_ts=0
    report = sle_scores(repo, 0, 3_000)  # 10 buckets of 300s
    cov = report.sles[SLE_COVERAGE]
    assert report.window_buckets == 10
    assert cov.window_buckets == 10
    assert cov.evaluated_buckets == 1


def test_plentiful_minutes_in_few_buckets_still_counts(repo: Repository) -> None:
    """Clustered evidence is still evidence, so a thin bucket fraction alone must
    not drop an SLE from the headline.

    Real data clusters: client byte counters arrive in a six-hourly sweep, so on
    the production network coverage, capacity and WAN each carried more than 4,000
    judged client-minutes inside just 12 of 288 buckets. Disqualifying on the
    fraction dropped all three, left infra (weight 0.05) to renormalise to 1.0,
    and swung the headline from 72% to 100% while hiding a live roaming failure.
    The absolute quantity of judged minutes is what decides; the fraction is
    reported so the UI can caption how bunched the sampling was.
    """
    assert 100.0 >= MIN_EXPOSURE_MINUTES
    _put(repo, SLE_COVERAGE, OK, 1, 100.0)  # 1 of 10 buckets, but 100 minutes
    report = sle_scores(repo, 0, 3_000)
    cov = report.sles[SLE_COVERAGE]

    assert cov.score is not None
    assert cov.below_floor is False
    assert SLE_COVERAGE in report.included_sles
    assert report.headline is not None
    # The sparse sampling is still reported, just not disqualifying.
    assert cov.evaluated_buckets < cov.window_buckets


def test_below_floor_by_thin_minutes_even_at_full_bucket_fraction(repo: Repository) -> None:
    # Full exposure (the window IS exactly the one seeded bucket) but well under
    # the minutes floor: too little evidence to paint Good/Fair/Poor, however
    # evenly it was sampled.
    minutes = MIN_EXPOSURE_MINUTES - 1
    _put(repo, SLE_COVERAGE, OK, 1, minutes)
    report = sle_scores(repo, *WIN)
    cov = report.sles[SLE_COVERAGE]

    assert cov.evaluated_buckets == cov.window_buckets == 1  # 100% exposure
    assert cov.below_floor is True


def test_at_or_above_both_floors_is_included_in_headline(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 90.0)
    _put(repo, SLE_COVERAGE, CLS_WEAK_SIGNAL, 1, 10.0, attributed=100)
    report = sle_scores(repo, *WIN)  # full exposure, 100 judged minutes
    cov = report.sles[SLE_COVERAGE]

    assert cov.below_floor is False
    assert SLE_COVERAGE in report.included_sles
    assert SLE_COVERAGE not in report.excluded_below_floor
    assert math.isclose(report.headline, 0.9)


def test_no_data_sles_land_in_excluded_no_data(repo: Repository) -> None:
    _put(repo, SLE_COVERAGE, OK, 1, 100.0)
    report = sle_scores(repo, *WIN)

    assert SLE_WAN in report.excluded_no_data
    assert SLE_INFRA in report.excluded_no_data
    assert SLE_WAN not in report.included_sles
    assert SLE_WAN not in report.excluded_below_floor


def test_load_weights_override(repo: Repository) -> None:
    settings = SimpleNamespace(thresholds={"sle": {"weights": {"coverage": 0.5}}})
    weights = load_weights(settings)
    assert weights[SLE_COVERAGE] == 0.5
    # untouched weights keep their defaults
    assert weights[SLE_CAPACITY] == DEFAULT_WEIGHTS[SLE_CAPACITY]
