"""SLE router: ``GET /api/sle`` — health score + classifier breakdown (section 8).

The one read surface over the SLE model: the headline blend, each SLE's score, its
classifier breakdown, and the infrastructure entities the failed minutes pin on.
Section 8's claim is that the score and its explanation are the *same* GROUP BY over
``sle_minutes`` — this endpoint returns exactly that
(:func:`netadmin.sle.scores.sle_scores`), nothing recomputed from raw metrics.

Read-only by construction: it calls only :func:`sle_scores` (which reads through
:meth:`Repository.query_sle_minutes`) and serialises the returned report. No SQL
here (section 4); no writes.

The handler is ``async`` deliberately: the store's SQLite connection is bound to the
event-loop thread (one process, shared loop — section 3), so it is read on that
thread rather than a threadpool worker.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from netadmin.server.serialize import entity_ref_map, get_store
from netadmin.sle.classifiers import OK, SLE_CONNECT, SLE_COVERAGE, SLE_ROAMING
from netadmin.sle.scores import MIN_EXPOSURE_FRACTION, ScoreReport, SleScore, sle_scores
from netadmin.store.repository import Repository

router = APIRouter(prefix="/api", tags=["sle"])

# Guard rails on the caller-supplied look-back so a stray query cannot ask for an
# unbounded scan of sle_minutes.
_MAX_WINDOW_S = 400 * 86_400  # ~13 months (daily rollups are kept forever)
_MAX_BUCKETS = 1_000
_DEFAULT_BUCKETS = 96

# SLEs that only ever write a row when something *happened* (a roam, a connect) --
# never a per-bucket "ok, nothing occurred" row the way coverage/capacity do. Zero
# exposed minutes for one of these is not automatically a gap: it can equally be a
# quiet, fully-observed window (docs: the three empty states). ``connect`` additionally
# gets a harder "not measurable" state when the event pipeline itself looks dead
# (see ``_events_stale``) -- that ISN'T offered for roaming, which has a live
# fallback (``roam_count`` counter deltas) independent of the event stream.
_OCCURRENCE_SLES = (SLE_ROAMING, SLE_CONNECT)

# How stale the newest stored event can be before connect (whose exposure gate is
# event-driven -- see SleMinutesJob._connect) is reported "not measurable on this
# controller" rather than "no connects happened". An hour is well past the cadence
# any live network produces SOME event (lifecycle or anomaly) at, so a longer gap
# is good evidence the WS listener (or its catch-up) has stopped delivering,
# never a guess dressed up as a fact.
_EVENTS_STALE_S = 3600


def _sle_timeseries(
    store: Repository, start_ts: int, end_ts: int, buckets: int
) -> dict[str, list[dict[str, Any]]]:
    """Per-SLE score-over-time, downsampled to at most ``buckets`` points.

    Reads the same ``sle_minutes`` GROUP BY one level finer (by ``bucket_ts``) and
    folds the 5-minute cells into equal-width coarse buckets: each coarse bucket's
    score is ``sum(ok) / sum(total)`` over the fine buckets inside it. A coarse
    bucket with no exposed minutes is **omitted** (a gap is a gap — the SVG draws
    the discontinuity, never an interpolated line). ``ts`` anchors to the earliest
    real fine-bucket in the coarse bucket.
    """
    rows = store.query_sle_minutes(start_ts, end_ts, group_by=("sle", "classifier", "bucket_ts"))
    span = max(1, end_ts - start_ts)
    buckets = max(1, min(int(buckets), _MAX_BUCKETS))
    width = span / buckets

    # sle -> coarse_idx -> {"ok", "total", "ts"}
    folded: dict[str, dict[int, dict[str, float]]] = {}
    for r in rows:
        bts = int(r["bucket_ts"])
        minutes = float(r["minutes"] or 0.0)
        idx = int((bts - start_ts) / width)
        idx = max(0, min(idx, buckets - 1))
        cell = folded.setdefault(r["sle"], {}).setdefault(idx, {"ok": 0.0, "total": 0.0, "ts": bts})
        cell["total"] += minutes
        if r["classifier"] == OK:
            cell["ok"] += minutes
        cell["ts"] = min(cell["ts"], bts)

    series: dict[str, list[dict[str, Any]]] = {}
    for sle, by_idx in folded.items():
        points: list[dict[str, Any]] = []
        for idx in sorted(by_idx):
            cell = by_idx[idx]
            total = cell["total"]
            if total <= 0:
                continue
            points.append(
                {
                    "ts": int(cell["ts"]),
                    "score": cell["ok"] / total,
                    "ok_minutes": cell["ok"],
                    "total_minutes": total,
                }
            )
        series[sle] = points
    return series


def _connect_events_stale(store: Repository, now: int) -> bool:
    """True when no event of ANY kind has landed recently enough to trust the
    event pipeline is alive (section 8's connect SLE rides entirely on events --
    see ``SleMinutesJob._connect``). Deliberately generic (not "a *Connected*
    event"): the diagnosed failure mode is the WS listener dying outright, after
    which even unrelated event keys (anomalies, etc.) stop arriving too -- that
    silence is the honest, cheaply-checkable signal, not a guess about *why*
    connect has no data this window.
    """
    newest = store.max_event_ts()
    return newest is None or (now - int(newest)) > _EVENTS_STALE_S


def _measurability(
    report: ScoreReport, store: Repository, now: int
) -> tuple[dict[str, str], set[str]]:
    """Resolve the three-empty-states distinction (docs) the raw ``sle_minutes``
    GROUP BY cannot make by itself: ``(not_measurable, quiet_pass)``.

    ``not_measurable`` maps an SLE name to a human reason -- today only ever
    ``connect``, and only when it has no data in this window *and* the event
    pipeline itself looks dead (:func:`_connect_events_stale`). An SLE that DID
    score something (even a link-local-only connect score) is real evidence, not
    a broken pipeline, so it is never overridden here.

    ``quiet_pass`` is the set of per-occurrence SLEs (:data:`_OCCURRENCE_SLES`)
    with no data in this window that are positively confirmed as "nothing
    happened" rather than "couldn't measure": coverage -- written for every
    active wireless client whenever RSSI is present, riding the same activity
    gate roaming/connect do -- cleared the confidence floor in this window, so
    the absence of roams/connects is real signal, not a measurement gap.
    """
    not_measurable: dict[str, str] = {}
    if SLE_CONNECT in report.excluded_no_data and _connect_events_stale(store, now):
        not_measurable[SLE_CONNECT] = "connection events unavailable"

    coverage: Optional[SleScore] = report.sles.get(SLE_COVERAGE)
    clients_observable = (
        coverage is not None
        and report.window_buckets > 0
        and (coverage.evaluated_buckets / report.window_buckets) >= MIN_EXPOSURE_FRACTION
    )
    quiet_pass = {
        sle
        for sle in _OCCURRENCE_SLES
        if sle in report.excluded_no_data and sle not in not_measurable and clients_observable
    }
    return not_measurable, quiet_pass


def _serialize(
    report: ScoreReport,
    timeseries: dict[str, list[dict[str, Any]]],
    names: dict[int, Any],
    *,
    not_measurable: dict[str, str],
    quiet_pass: set[str],
) -> dict[str, Any]:
    """A JSON view of a :class:`ScoreReport`: score + breakdown + offenders + trend.

    The score and its explanation are the same ``sle_minutes`` GROUP BY (section
    8); this attaches the UI extras the contract adds — each offender resolved to
    a name ref, a per-SLE score-over-time series, the exposure that grounds the
    confidence floor, and the ``not_measurable``/``quiet_pass`` overlay
    (:func:`_measurability`) — without recomputing anything from raw metrics.
    """
    excluded_no_data = [s for s in report.excluded_no_data if s not in not_measurable]
    return {
        "start_ts": report.start_ts,
        "end_ts": report.end_ts,
        "headline": report.headline,
        "weights": report.weights,
        "window_buckets": report.window_buckets,
        # Headline provenance for the UI's "Weighted across N/M service levels
        # (X below data floor, Y not measurable)" caption -- every SLE lands in
        # exactly one of these four buckets (or is silently opted out via a
        # zero-weight config, which is a product decision, not a data gap).
        "included_sles": list(report.included_sles),
        "excluded_below_floor": list(report.excluded_below_floor),
        "excluded_no_data": excluded_no_data,
        "excluded_not_measurable": sorted(not_measurable),
        "sles": {
            name: {
                "sle": s.sle,
                "score": s.score,
                "total_minutes": s.total_minutes,
                "ok_minutes": s.ok_minutes,
                "fail_minutes": s.fail_minutes,
                "evaluated_buckets": s.evaluated_buckets,
                "window_buckets": s.window_buckets,
                "below_floor": s.below_floor,
                "measurable": name not in not_measurable,
                "unmeasurable_reason": not_measurable.get(name),
                "quiet_pass": name in quiet_pass,
                "classifiers": s.classifiers,
                "top_offenders": [
                    {
                        **off,
                        "entity": (
                            names.get(int(off["attributed_entity_id"]))
                            if off.get("attributed_entity_id") is not None
                            else None
                        ),
                    }
                    for off in s.top_offenders
                ],
                "timeseries": timeseries.get(name, []),
            }
            for name, s in report.sles.items()
        },
    }


@router.get("/sle")
async def get_sle(
    request: Request,
    window_s: Optional[int] = Query(default=None, ge=1, le=_MAX_WINDOW_S),
    start: Optional[int] = Query(default=None, ge=0),
    end: Optional[int] = Query(default=None, ge=0),
    top_n: int = Query(default=5, ge=0, le=50),
    buckets: int = Query(default=_DEFAULT_BUCKETS, ge=1, le=_MAX_BUCKETS),
) -> dict[str, Any]:
    """SLE scores over a window, with offender names + a per-SLE score trend.

    Window resolution: an explicit ``start``/``end`` pair wins; otherwise the
    window is ``[now - window_s, now]`` with ``window_s`` defaulting to
    ``settings.sle.score_window_s`` (24 h). ``top_n`` caps each SLE's offender
    list; ``buckets`` caps the score-over-time resolution. An SLE with no exposed
    minutes reports ``score: null`` (no data — not a perfect score), and the
    headline blends only the SLEs that both had data and cleared the confidence
    floor (``below_floor: false`` — see :mod:`netadmin.sle.scores`). ``connect``
    additionally reports ``measurable: false`` with an explicit reason when it
    has no data and the event pipeline itself looks dead, rather than the
    generic (and, in that case, misleading) "no exposed minutes" silence.
    """
    store = get_store(request)
    settings = request.app.state.settings
    now = int(time.time())

    default_window = int(getattr(getattr(settings, "sle", None), "score_window_s", 86_400))
    if start is not None and end is not None:
        if end <= start:
            raise HTTPException(status_code=422, detail="end must be greater than start")
        start_ts, end_ts = int(start), int(end)
    else:
        end_ts = int(end) if end is not None else now
        span = int(window_s) if window_s is not None else default_window
        start_ts = int(start) if start is not None else end_ts - span

    report = sle_scores(store, start_ts, end_ts, top_n=top_n, settings=settings)
    not_measurable, quiet_pass = _measurability(report, store, now)

    offender_ids = [
        off["attributed_entity_id"]
        for s in report.sles.values()
        for off in s.top_offenders
        if off.get("attributed_entity_id") is not None
    ]
    names = entity_ref_map(store, offender_ids)
    timeseries = _sle_timeseries(store, start_ts, end_ts, buckets)
    return _serialize(
        report, timeseries, names, not_measurable=not_measurable, quiet_pass=quiet_pass
    )


__all__ = ["router"]
