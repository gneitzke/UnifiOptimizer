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
from netadmin.sle.classifiers import OK
from netadmin.sle.scores import ScoreReport, sle_scores
from netadmin.store.repository import Repository

router = APIRouter(prefix="/api", tags=["sle"])

# Guard rails on the caller-supplied look-back so a stray query cannot ask for an
# unbounded scan of sle_minutes.
_MAX_WINDOW_S = 400 * 86_400  # ~13 months (daily rollups are kept forever)
_MAX_BUCKETS = 1_000
_DEFAULT_BUCKETS = 96


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


def _serialize(
    report: ScoreReport, timeseries: dict[str, list[dict[str, Any]]], names: dict[int, Any]
) -> dict[str, Any]:
    """A JSON view of a :class:`ScoreReport`: score + breakdown + offenders + trend.

    The score and its explanation are the same ``sle_minutes`` GROUP BY (section
    8); this attaches the two UI extras the contract adds — each offender resolved
    to a name ref, and a per-SLE score-over-time series — without recomputing
    anything from raw metrics.
    """
    return {
        "start_ts": report.start_ts,
        "end_ts": report.end_ts,
        "headline": report.headline,
        "weights": report.weights,
        "sles": {
            name: {
                "sle": s.sle,
                "score": s.score,
                "total_minutes": s.total_minutes,
                "ok_minutes": s.ok_minutes,
                "fail_minutes": s.fail_minutes,
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
    headline blends only the SLEs that had data.
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

    offender_ids = [
        off["attributed_entity_id"]
        for s in report.sles.values()
        for off in s.top_offenders
        if off.get("attributed_entity_id") is not None
    ]
    names = entity_ref_map(store, offender_ids)
    timeseries = _sle_timeseries(store, start_ts, end_ts, buckets)
    return _serialize(report, timeseries, names)


__all__ = ["router"]
