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
from netadmin.sle.scores import DEFAULT_WEIGHTS, load_weights, sle_scores
from netadmin.store.repository import Repository

WIN = (0, 10_000)


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


def test_load_weights_override(repo: Repository) -> None:
    settings = SimpleNamespace(thresholds={"sle": {"weights": {"coverage": 0.5}}})
    weights = load_weights(settings)
    assert weights[SLE_COVERAGE] == 0.5
    # untouched weights keep their defaults
    assert weights[SLE_CAPACITY] == DEFAULT_WEIGHTS[SLE_CAPACITY]
