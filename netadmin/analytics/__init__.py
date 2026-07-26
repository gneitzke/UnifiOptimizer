"""Analytics: read-only rollups over the store that rank and summarise.

Section 17's "seasoned expert" layer includes a *problem-device offender
ranking* — a composite problem-burden leaderboard ("who causes most of my
grief") computed as pure ``GROUP BY``\\s over ``sle_minutes``, ``issues``, and
``events``. No new storage; every figure is derived from tables the ingest,
detection, issue, and SLE layers already write.

Nothing here touches SQL — the joins and aggregates live in
:class:`netadmin.store.repository.Repository` (section 4); this package only
folds the aggregates into a documented, reproducible score.
"""

from __future__ import annotations

from netadmin.analytics.offenders import (
    DEFAULT_OFFENDER_WEIGHTS,
    OFFENDER_EVENT_KEYS,
    OffenderScore,
    load_offender_weights,
    rank_offenders,
)

__all__ = [
    "DEFAULT_OFFENDER_WEIGHTS",
    "OFFENDER_EVENT_KEYS",
    "OffenderScore",
    "load_offender_weights",
    "rank_offenders",
]
