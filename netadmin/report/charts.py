"""Pure chart-series and bin computation for the report.

Every number a report chart draws is computed here, in Python, from data the
assembler already pulled through the repository -- the UI renders these series
and bins, it never derives them (``docs/ARCHITECTURE.md`` 19: "No number is
computed in the UI"). The functions are pure (lists in, series out) so the
histogram-sums-to-count and neighbour-aggregation invariants are unit-testable in
isolation from the store.

The dataviz rules (``docs/DESIGN_FOUNDATION.md``) shape the *shape* of the output,
not just its styling: a gap is emitted as an omitted point (never interpolated),
a distribution is returned in full (never collapsed to a mean), and neighbour
noise is aggregated to a per-channel count (never one row per BSSID).
"""

from __future__ import annotations

from typing import Any, Optional

from netadmin.sle.classifiers import OK

__all__ = [
    "rssi_histogram",
    "neighbor_density",
    "clients_per_ap",
    "health_trend",
    "median",
]

# Interior RSSI bin edges (dBm), weak-tail first. The coverage weak threshold is
# injected as an edge at build time so no bin straddles it and the weak flag lines
# up exactly with the coverage classifier (rssi < weak_threshold).
_BASE_RSSI_EDGES: tuple[int, ...] = (-85, -80, -75, -67, -60, -50)


def median(values: list[float]) -> Optional[float]:
    """Median of ``values`` (the distribution's centre), or None if empty.

    Reported *alongside* a histogram, never instead of it -- the honesty rule is
    "never an average without its distribution" (``docs/REPORT_SPEC.md``).
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def rssi_histogram(values: list[float], weak_threshold_dbm: float) -> dict[str, Any]:
    """Bin client RSSI values into a distribution, colouring the weak tail.

    Bins are half-open ``[floor, ceil)`` with the first ``floor`` and last ``ceil``
    open (``None`` in the output for the infinite edges). ``weak_threshold_dbm`` is
    injected as an edge, so a bin is flagged ``weak`` exactly when its ``ceil`` is
    at or below the threshold -- i.e. every value in it is below the coverage floor,
    matching :func:`netadmin.sle.classifiers.classify_coverage`.

    The bin counts sum to ``total`` (the number of values), the invariant the tests
    assert. ``median`` and ``min``/``max`` accompany the distribution; a value is
    never dropped or double-counted.
    """
    interior = sorted({*_BASE_RSSI_EDGES, int(round(weak_threshold_dbm))})
    # Build [floor, ceil) bins: (-inf, e0), [e0, e1), ..., [e_last, +inf).
    edges: list[Optional[int]] = [None, *interior, None]
    bins: list[dict[str, Any]] = []
    for i in range(len(edges) - 1):
        floor = edges[i]
        ceil = edges[i + 1]
        weak = ceil is not None and ceil <= weak_threshold_dbm
        bins.append({"floor": floor, "ceil": ceil, "count": 0, "weak": weak})

    for v in values:
        placed = False
        for b in bins:
            ceil = b["ceil"]
            if ceil is None or v < ceil:
                b["count"] += 1
                placed = True
                break
        if not placed:  # pragma: no cover - the last bin has ceil None, always matches
            bins[-1]["count"] += 1

    weak_count = sum(b["count"] for b in bins if b["weak"])
    return {
        "bins": bins,
        "total": len(values),
        "weak_count": weak_count,
        "weak_threshold_dbm": weak_threshold_dbm,
        "median_dbm": median(values),
        "min_dbm": min(values) if values else None,
        "max_dbm": max(values) if values else None,
    }


def neighbor_density(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate neighbour/rogue BSS rows to per-channel counts (never per-BSSID).

    ``rows`` are parsed neighbour records carrying ``band`` and ``channel``. The
    anti-slop rule (``docs/REPORT_SPEC.md``: "neighbour-AP noise is environmental
    context, aggregated -- never 80 individual issues") is enforced here: the
    output is a count per ``(band, channel)`` and a count per band, plus the total.
    A row with no channel is counted into the total and its band only (it cannot be
    placed on a channel bar).
    """
    by_channel: dict[tuple[Optional[str], Optional[int]], int] = {}
    by_band: dict[Optional[str], int] = {}
    total = 0
    for r in rows:
        band = r.get("band")
        channel = r.get("channel")
        total += 1
        by_band[band] = by_band.get(band, 0) + 1
        if channel is not None:
            key = (band, channel)
            by_channel[key] = by_channel.get(key, 0) + 1

    channel_bars = [
        {"band": band, "channel": channel, "count": count}
        for (band, channel), count in sorted(
            by_channel.items(),
            key=lambda kv: (str(kv[0][0]), kv[0][1] if kv[0][1] is not None else 0),
        )
    ]
    band_bars = [
        {"band": band, "count": count}
        for band, count in sorted(by_band.items(), key=lambda kv: str(kv[0]))
    ]
    return {"total": total, "by_channel": channel_bars, "by_band": band_bars}


def clients_per_ap(
    aps: list[dict[str, Any]], parent_counts: dict[int, int]
) -> list[dict[str, Any]]:
    """Client-count bar per AP, from a pre-computed ``{ap_entity_id: count}`` map.

    ``aps`` are the AP identity dicts (``entity_id`` + ``name``); ``parent_counts``
    is the number of clients currently parented to each AP. APs with no clients are
    kept (a zero bar is honest load information), sorted by count descending then
    name so the busiest AP reads first.
    """
    bars = [
        {
            "entity_id": ap["entity_id"],
            "name": ap["name"],
            "client_count": int(parent_counts.get(int(ap["entity_id"]), 0)),
        }
        for ap in aps
    ]
    bars.sort(key=lambda b: (-b["client_count"], str(b["name"])))
    return bars


def health_trend(
    rows: list[dict[str, Any]],
    start_ts: int,
    end_ts: int,
    buckets: int,
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Overall health score over the window, one point per coarse bucket.

    Folds the 5-minute ``sle_minutes`` cells (``rows`` grouped by
    ``sle``/``classifier``/``bucket_ts``) into ``buckets`` equal-width coarse
    buckets, computes each SLE's ``ok/total`` per coarse bucket, and blends them
    with ``weights`` -- the same weighted-mean the headline uses
    (:func:`netadmin.sle.scores.sle_scores`), computed per bucket over time.

    A coarse bucket with no exposed minutes is **omitted** (a gap is a gap; the
    SVG draws the discontinuity, never an interpolated line). Scores are integer
    0-100. ``ts`` anchors to the earliest real fine-bucket inside the coarse one.
    """
    span = max(1, end_ts - start_ts)
    buckets = max(1, int(buckets))
    width = span / buckets

    # coarse_idx -> sle -> {"ok", "total"} ; plus the earliest ts seen in the idx.
    folded: dict[int, dict[str, dict[str, float]]] = {}
    idx_ts: dict[int, int] = {}
    for r in rows:
        bts = int(r["bucket_ts"])
        minutes = float(r["minutes"] or 0.0)
        idx = int((bts - start_ts) / width)
        idx = max(0, min(idx, buckets - 1))
        cell = folded.setdefault(idx, {}).setdefault(r["sle"], {"ok": 0.0, "total": 0.0})
        cell["total"] += minutes
        if r["classifier"] == OK:
            cell["ok"] += minutes
        idx_ts[idx] = bts if idx not in idx_ts else min(idx_ts[idx], bts)

    points: list[dict[str, Any]] = []
    for idx in sorted(folded):
        num = 0.0
        den = 0.0
        for sle, cell in folded[idx].items():
            total = cell["total"]
            if total <= 0:
                continue
            w = weights.get(sle, 0.0)
            if w <= 0:
                continue
            num += w * (cell["ok"] / total)
            den += w
        if den <= 0:
            continue  # no weighted SLE had data this bucket -> a gap, omit the point
        points.append({"ts": int(idx_ts[idx]), "score": int(round((num / den) * 100))})
    return points
