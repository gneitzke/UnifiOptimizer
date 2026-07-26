"""Problem-device offender ranking (ARCHITECTURE.md section 17).

The "who causes most of my grief" leaderboard. A seasoned admin knows which two
or three boxes (or clients) are behind most of the network's pain; this ranks
entities by a **composite problem burden** over a window, computed entirely as
``GROUP BY``\\s over ``sle_minutes``, ``issues``, and ``events`` — no new storage
(section 17). Surfaced on the dashboard as "Top offenders" and as a sortable
page; the API layer resolves the ranked ids to names.

The weighting formula
----------------------

Each candidate entity's score is a linear blend of three independently-measured
burden channels, each already computed by an earlier layer:

    score = w_sle_minute * failed_attributed_client_minutes
          + w_p1 * open_p1_issues
          + w_p2 * open_p2_issues
          + w_p3 * open_p3_issues
          + w_event * disconnect_and_roam_events

* **Failed SLE client-minutes** (``sle_minutes``, ``classifier != 'ok'``, grouped
  by ``attributed_entity_id``) is the *impact-weighted* term: one point per real
  minute a real client had a degraded experience that the SLE engine pinned on
  this entity. It is impact-weighted by construction (section 8: an idle client
  with bad RSSI contributes zero failed minutes), so it dominates the score for
  infrastructure that is actually hurting users — which is the point.
* **Open-issue count, weighted by severity** (``issues``, ``state != 'resolved'``)
  encodes ``p1 > p2 > p3``: a single P1 outranks several P3s. These are the
  detector-confirmed, independently-tracked problems *on* the entity.
* **Disconnect / roam event volume** (``events`` with keys in
  :data:`OFFENDER_EVENT_KEYS`, grouped by the event's own subject ``entity_id``)
  is the churn term — high for a flapping client, near-zero for a device, since a
  disconnect's subject is the client, not the AP. Weighted below a failed minute
  because raw event volume is noisier evidence than attributed impact.

The default weights (:data:`DEFAULT_OFFENDER_WEIGHTS`) are a documented product
choice, deliberately scaled so failed-client-minutes drive the ranking, severe
issues lift an entity sharply, and event churn only breaks near-ties. Every
weight is overridable from ``settings.thresholds["offenders"]["weights"]`` (see
:func:`load_offender_weights`) so an operator can retune without a code change.

Conservatism (section 17): the score never *guesses* blame. The only channel
that attributes a symptom to an infrastructure device is ``sle_minutes``, whose
``attributed_entity_id`` was pinned by rule in the SLE engine; unattributed
failed minutes are excluded entirely, and event volume counts an event only
against its own subject. A high score means measured, attributable burden — not a
statistical hunch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from netadmin.domain.types import EntityType

__all__ = [
    "DEFAULT_OFFENDER_WEIGHTS",
    "OFFENDER_EVENT_KEYS",
    "DEVICE_ENTITY_TYPES",
    "CLIENT_ENTITY_TYPES",
    "OffenderScore",
    "load_offender_weights",
    "rank_offenders",
]

# Documented default blend weights. Scaled so a failed client-minute is the base
# unit (1.0); a P1 issue is worth ~half an hour of failed minutes, a P2 ten, a P3
# three; and a disconnect/roam event is worth half a failed minute (churn is
# noisier evidence than attributed impact). Overridable via
# settings.thresholds["offenders"]["weights"].
DEFAULT_OFFENDER_WEIGHTS: dict[str, float] = {
    "sle_minute": 1.0,  # per failed attributed client-minute
    "issue_p1": 30.0,  # per open P1 issue
    "issue_p2": 10.0,  # per open P2 issue
    "issue_p3": 3.0,  # per open P3 issue
    "event": 0.5,  # per disconnect / roam event
}

# Disconnect + roam event keys the churn term counts (wireless + wired user).
# Kept here, next to the weight that scores them, so the "grief" definition is
# one edit away.
OFFENDER_EVENT_KEYS: tuple[str, ...] = (
    "EVT_WU_Disconnected",
    "EVT_LU_Disconnected",
    "EVT_WU_Roam",
    "EVT_WU_RoamRadio",
)

# Which entity types each surface ranks. Devices mirror the inventory "devices"
# surface (ap / switch / gateway); clients are their own surface.
DEVICE_ENTITY_TYPES: tuple[str, ...] = (
    EntityType.AP.value,
    EntityType.SWITCH.value,
    EntityType.GATEWAY.value,
)
CLIENT_ENTITY_TYPES: tuple[str, ...] = (EntityType.CLIENT.value,)


@dataclass
class OffenderScore:
    """One entity's composite problem burden plus the channels that explain it.

    ``score`` is the weighted blend; the component fields are the raw,
    pre-weight measurements so a caller can show *why* an entity ranks where it
    does (the score and its explanation travel together, like the SLE model).
    """

    entity_id: int
    score: float
    fail_minutes: float
    issue_counts: dict[str, int]
    event_count: int
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable view for the API layer (name resolution added there)."""
        return {
            "entity_id": self.entity_id,
            "score": self.score,
            "fail_minutes": self.fail_minutes,
            "issue_counts": dict(self.issue_counts),
            "event_count": self.event_count,
            "components": dict(self.components),
        }


def load_offender_weights(settings: Any = None) -> dict[str, float]:
    """Blend weights, overriding :data:`DEFAULT_OFFENDER_WEIGHTS` from
    ``settings.thresholds["offenders"]["weights"]`` when present.

    Unknown weight keys are ignored; missing ones keep their default; a
    non-numeric override is skipped. Never raises (mirrors
    :func:`netadmin.sle.scores.load_weights`), so a malformed config degrades to
    the documented defaults instead of breaking the endpoint.
    """
    weights = dict(DEFAULT_OFFENDER_WEIGHTS)
    thresholds = getattr(settings, "thresholds", None)
    section = thresholds.get("offenders") if isinstance(thresholds, dict) else None
    override = section.get("weights") if isinstance(section, dict) else None
    if isinstance(override, dict):
        for key, value in override.items():
            if key in weights:
                try:
                    weights[key] = float(value)
                except (TypeError, ValueError):
                    continue
    return weights


def _empty_counts() -> dict[str, int]:
    return {"p1": 0, "p2": 0, "p3": 0, "total": 0}


def rank_offenders(
    repo: Any,
    entity_types: Sequence[str],
    start_ts: int,
    end_ts: int,
    *,
    top_n: int = 10,
    weights: Optional[dict[str, float]] = None,
    settings: Any = None,
    event_keys: Iterable[str] = OFFENDER_EVENT_KEYS,
) -> list[OffenderScore]:
    """Rank entities of ``entity_types`` by composite problem burden.

    Three repository ``GROUP BY``\\s (failed attributed SLE minutes, open-issue
    counts by severity, disconnect/roam event volume) over ``[start_ts, end_ts)``
    are folded into one score per candidate entity via the documented weighting
    (module docstring). Only entities whose ``entity_type`` is in ``entity_types``
    are ranked; the candidate set is the union of ids appearing in any of the
    three aggregates, so an entity with grief in *any* channel is considered.

    Results are sorted by ``score`` descending, ties broken by ``entity_id``
    ascending (a stable, reproducible order — no statistical jitter), and capped
    at ``top_n`` (``top_n <= 0`` returns every ranked entity). An empty window, or
    one with no failed minutes / issues / events, yields ``[]``.
    """
    weights = weights if weights is not None else load_offender_weights(settings)
    wanted_types = set(entity_types)

    fail_minutes = repo.sle_fail_minutes_by_attributed(start_ts, end_ts)
    issue_counts = repo.open_issue_counts()  # window-independent: open issues now
    event_counts = repo.event_counts_by_entity(start_ts, end_ts, list(event_keys))

    # Candidate set: every entity that appears in any channel. open_issue_counts
    # spans all entity types, so it is filtered to the requested types below.
    candidate_ids: set[int] = set()
    candidate_ids.update(fail_minutes)
    candidate_ids.update(issue_counts)
    candidate_ids.update(event_counts)
    if not candidate_ids:
        return []

    # Resolve types in one query, then keep only the requested surface's entities.
    entities = repo.entities_by_ids(candidate_ids)

    scored: list[OffenderScore] = []
    for eid in candidate_ids:
        row = entities.get(eid)
        if row is None or row["entity_type"] not in wanted_types:
            continue

        fm = float(fail_minutes.get(eid, 0.0))
        counts = issue_counts.get(eid, _empty_counts())
        n_p1 = int(counts.get("p1", 0))
        n_p2 = int(counts.get("p2", 0))
        n_p3 = int(counts.get("p3", 0))
        n_events = int(event_counts.get(eid, 0))

        sle_pts = weights["sle_minute"] * fm
        issue_pts = (
            weights["issue_p1"] * n_p1 + weights["issue_p2"] * n_p2 + weights["issue_p3"] * n_p3
        )
        event_pts = weights["event"] * n_events
        score = sle_pts + issue_pts + event_pts
        if score <= 0:
            continue  # no measurable burden in any channel — not an offender

        scored.append(
            OffenderScore(
                entity_id=eid,
                score=score,
                fail_minutes=fm,
                issue_counts={"p1": n_p1, "p2": n_p2, "p3": n_p3, "total": n_p1 + n_p2 + n_p3},
                event_count=n_events,
                components={
                    "sle_minutes": sle_pts,
                    "issues": issue_pts,
                    "events": event_pts,
                },
            )
        )

    scored.sort(key=lambda s: (-s.score, s.entity_id))
    if top_n and top_n > 0:
        return scored[:top_n]
    return scored
