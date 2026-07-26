"""Metrics router: ``GET /api/metrics/window`` — downsampled series for charts.

The charts in the UI are hand-rolled SVG (DESIGN_FOUNDATION chart rules) and must
never receive 100k raw points. This endpoint reads a tier-aware window through
:meth:`Repository.read_window` (raw for recent, hourly/daily rollups for older,
stitched across a retention boundary) and downsamples it **server-side** to at
most ``points`` buckets of ``{ts, min, max, avg, n}`` before it ever leaves the
process.

Downsampling preserves the min/max envelope, not just the mean, so a chart can
draw the spread (Health-app range bars) and a transient spike is never averaged
away. **Data gaps stay gaps** (rule 8 / rule 62): a time bucket with no samples is
omitted, never interpolated — the SVG renderer draws the discontinuity.

Read-only, ``async`` (the SQLite connection is loop-bound; section 3). No SQL
here (section 4) — every row comes back through the repository.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from netadmin.server.serialize import get_store

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# Bounds so a stray query cannot ask for an unbounded scan or a pathological
# bucket count. ~13 months matches the SLE window ceiling (daily rollups persist).
_MAX_SECONDS = 400 * 86_400
_MAX_POINTS = 2_000
_DEFAULT_POINTS = 300


def _normalise_row(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Coerce a raw or rollup window row to ``{ts, min, max, avg, n}``.

    Raw rows carry a single ``value`` (min=max=avg, n=1); rollup rows already
    carry ``min/max/avg/n``. A row with no usable numeric value is dropped so a
    null never poisons the aggregate.
    """
    ts = row.get("ts")
    if ts is None:
        return None
    if "n" in row and row.get("avg") is not None:
        lo = row.get("min")
        hi = row.get("max")
        avg = row.get("avg")
        n = int(row.get("n") or 1)
        if avg is None:
            return None
        return {
            "ts": int(ts),
            "min": lo if lo is not None else avg,
            "max": hi if hi is not None else avg,
            "avg": float(avg),
            "n": n,
        }
    value = row.get("value")
    if value is None:
        return None
    v = float(value)
    return {"ts": int(ts), "min": v, "max": v, "avg": v, "n": 1}


def downsample(
    rows: list[dict[str, Any]], points: int, start_ts: int, end_ts: int
) -> list[dict[str, Any]]:
    """Reduce window rows to at most ``points`` equal-width time buckets.

    Each output bucket is ``{ts, min, max, avg, n}`` where ``avg`` is the
    sample-count-weighted mean of the rows folded into it (so aggregating already
    aggregated rollup rows stays correct), ``min``/``max`` bound the envelope, and
    ``ts`` anchors to the **earliest real sample** in the bucket — never a
    synthetic boundary — so a point is only drawn where data exists. Empty buckets
    are omitted (gaps render as gaps). When the series already has ``<= points``
    rows it is returned one-bucket-per-row, unchanged.
    """
    normalised = [n for n in (_normalise_row(r) for r in rows) if n is not None]
    if not normalised:
        return []
    points = max(1, min(int(points), _MAX_POINTS))
    if len(normalised) <= points:
        return normalised

    span = end_ts - start_ts
    if span <= 0:
        # Degenerate window: fold everything into a single bucket.
        return [_fold(normalised)]

    width = span / points
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in normalised:
        idx = int((row["ts"] - start_ts) / width)
        if idx >= points:  # the exact end_ts edge
            idx = points - 1
        if idx < 0:
            idx = 0
        buckets.setdefault(idx, []).append(row)

    out: list[dict[str, Any]] = []
    for idx in sorted(buckets):
        out.append(_fold(buckets[idx]))
    return out


def _fold(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold same-bucket rows into one ``{ts, min, max, avg, n}`` point."""
    total_n = sum(r["n"] for r in rows)
    weighted = sum(r["avg"] * r["n"] for r in rows)
    return {
        "ts": min(r["ts"] for r in rows),
        "min": min(r["min"] for r in rows),
        "max": max(r["max"] for r in rows),
        "avg": (weighted / total_n) if total_n else rows[0]["avg"],
        "n": total_n,
    }


@router.get("/window")
async def metrics_window(
    request: Request,
    entity_id: int = Query(..., ge=1),
    metric: str = Query(..., min_length=1),
    seconds: int = Query(default=3600, ge=1, le=_MAX_SECONDS),
    points: int = Query(default=_DEFAULT_POINTS, ge=1, le=_MAX_POINTS),
    end: Optional[int] = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Downsampled ``[end-seconds, end]`` series for one ``(entity_id, metric)``.

    404 when the series does not exist (the entity never reported that metric) —
    honest, not an empty 200 that a chart would render as a flat line. The
    ``tier`` field tells the client whether it is seeing raw or rolled-up data so
    the axis can be labelled accordingly.
    """
    store = get_store(request)
    series_id = store.get_series(entity_id, metric)
    if series_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"no series for entity {entity_id} metric {metric!r}",
        )

    end_ts = int(end) if end is not None else int(time.time())
    start_ts = end_ts - int(seconds)
    window = store.read_window(series_id, start_ts, end_ts, now=end_ts)
    buckets = downsample(window.rows, points, start_ts, end_ts)
    return {
        "entity_id": int(entity_id),
        "metric": metric,
        "series_id": int(series_id),
        "tier": window.tier,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "seconds": int(seconds),
        "points": int(points),
        "raw_count": len(window.rows),
        "buckets": buckets,
    }


__all__ = ["router", "downsample"]
