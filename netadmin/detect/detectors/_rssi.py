"""Per-AP RSSI attribution shared by ``wifi.sticky_client`` and ``net.coverage_hole``.

Both detectors have to answer the same question honestly: *which AP measured this
reading?* A client's RSSI means nothing except against the AP it was attached to
when the sample was taken, so crediting a whole window to the client's current AP
mis-attributes every reading it took before its last roam -- the dishonesty Gitea
#42 removed from sticky and #46 removed from coverage_hole.

The join lives here, imported by both, so there is one notion of "measured on"
rather than two that can drift apart. ``wifi`` re-exports the names it already
published (the demo seed imports :func:`sticky_per_ap_rssi` straight off it), so
relocating the code is invisible to every existing caller.
"""

from __future__ import annotations

from typing import Any, Optional

#: Row cap for the ``ap_mac`` trail read. The repository default is 200 rows,
#: newest first, which a ping-pong client changing AP every 10 s exceeds in about
#: 35 minutes; the truncation would drop the *oldest* rows and corrupt the
#: opening interval. 10_000 mirrors ``SleMinutesJob._state_at``'s read of the
#: same table.
_STATE_TRAIL_LIMIT = 10_000


def _norm_mac(value: Any) -> Optional[str]:
    """Lower-cased, whitespace-stripped MAC/BSSID, or ``None`` if unusable."""
    if value is None:
        return None
    mac = str(value).strip().lower()
    return mac or None


def _samples(window: Any) -> list[tuple[int, float]]:
    """A :class:`WindowResult` as ``(ts, value)`` pairs, oldest first, or ``[]``.

    :func:`_values` throws the timestamp away, which is right for every
    threshold-over-a-window question. Anything that has to *attribute* a reading
    to what else was true at that instant needs the instant too.
    """
    if window is None:
        return []
    out: list[tuple[int, float]] = []
    for row in window.rows:
        v = row.get("value")
        if v is not None:
            out.append((int(row["ts"]), float(v)))
    return out


def _attachment_intervals(
    ctx: Any, entity_id: int, start_ts: int, end_ts: int
) -> list[tuple[int, int, str]]:
    """A client's ``ap_mac`` trail over ``[start_ts, end_ts)`` as attachment intervals.

    Two reads: the newest change *before* the window (the value the window opens
    on -- the same seeding ``SleMinutesJob._state_at`` does, without which the
    whole span before the first roam is unattributed), then the changes inside
    it. Both are time-bounded and read with an explicit row cap; the repository
    default of 200 is not enough for a churny roamer (see
    :data:`_STATE_TRAIL_LIMIT`).
    """
    opening = ctx.repo.list_state_changes(0, start_ts, entity_id=entity_id, attr="ap_mac", limit=1)
    changes = ctx.repo.list_state_changes(
        start_ts, end_ts, entity_id=entity_id, attr="ap_mac", limit=_STATE_TRAIL_LIMIT
    )
    intervals: list[tuple[int, int, str]] = []
    current = _norm_mac(opening[0]["new_value"]) if opening else None
    since = start_ts
    for row in reversed(changes):  # list_state_changes is newest-first
        ts = int(row["ts"])
        if current and ts > since:
            intervals.append((since, ts, current))
        current = _norm_mac(row["new_value"])
        since = ts
    if current and end_ts > since:
        intervals.append((since, end_ts, current))
    return intervals


def sticky_per_ap_rssi(
    intervals: list[tuple[int, int, str]], samples: list[tuple[int, float]]
) -> dict[str, list[float]]:
    """Credit each RSSI reading to the AP the client was attached to at that instant.

    ``intervals`` are half-open ``(from_ts, to_ts, ap_mac)`` attachments, oldest
    first and non-overlapping; ``samples`` are ``(ts, value)`` pairs, oldest
    first. A reading outside every interval (the client's attachment was never
    recorded then) is credited to nobody rather than to a guess.

    This is the join that makes "AP X measured -57 dBm for this client" a single
    fact instead of two unrelated ones. The demo seed runs its own fabricated
    trail through it for the same reason it runs rates through
    :func:`sticky_rate_evidence`: so a broken attribution fails the demo instead
    of hiding in it.
    """
    per_ap: dict[str, list[float]] = {}
    i = 0
    for ts, value in samples:
        while i < len(intervals) and intervals[i][1] <= ts:
            i += 1
        if i >= len(intervals):
            break
        start, end, ap_mac = intervals[i]
        if start <= ts < end:
            per_ap.setdefault(ap_mac, []).append(value)
    return per_ap


__all__ = [
    "_STATE_TRAIL_LIMIT",
    "_attachment_intervals",
    "_norm_mac",
    "_samples",
    "sticky_per_ap_rssi",
]
