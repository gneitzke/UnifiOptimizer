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

* **Failed SLE client-minutes** (``sle_minutes`` on the *client* axis only —
  :data:`~netadmin.store.repository.SLE_CLIENT_AXIS_SLES` — with
  ``classifier != 'ok'``, grouped by ``attributed_entity_id``) is the
  *impact-weighted* term: one point per real minute a real client had a degraded
  experience that the SLE engine pinned on this entity. It is impact-weighted by
  construction (section 8: an idle client with bad RSSI contributes zero failed
  minutes), so it dominates the score for infrastructure that is actually
  hurting clients — which is the point.
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

Why device downtime is NOT a fourth term (Gitea #38)
-----------------------------------------------------

The ``infra`` SLE writes *device down-minutes* — the AP's or switch's own state
timeline — while every other SLE writes *client-minutes*. Summing them (which
this ranking used to do, by reading both axes at once) is wrong in a way that no
exchange rate can repair, so :attr:`OffenderScore.down_minutes` is carried beside
the score and never inside it:

* **A down AP's harm is already counted, on the client axis.** When an AP dies
  its clients do not vanish. They land on the next-best AP and burn
  coverage/roaming minutes *there*, attributed to *that* AP. Adding the dead
  AP's downtime to the same score does not capture harm the client axis missed;
  it counts the same outage twice.
* **There is no defensible exchange rate.** "A device-minute is worth N
  client-minutes" prices a measured quantity against an unobservable one: a dead
  AP has, by definition, zero clients associated with it, so the client-minutes
  it "would have" cost were never measured and cannot be. The second term is
  redundant, not merely awkward.
* **The property that has to hold.** A quiet AP hurting 28 clients must outrank
  a loud AP hurting 1. Downtime accumulates easily and says nothing about how
  many people noticed, so folding it into the score is precisely how that
  ordering inverts. ``tests/netadmin/analytics/test_offenders.py`` pins it.

Downtime is still shown — as its own column, in its own unit, next to a score it
did not contribute to. A device whose *only* measured burden is downtime is
still listed (with a zero score, which sorts it strictly below anything that
cost a client a minute) rather than dropped, because dropping it would lose the
outage from the surface entirely.

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
from netadmin.store.repository import (
    SLE_CLIENT_AXIS_SLES,
    SLE_DEVICE_AXIS_ENTITY_TYPES,
    SLE_DEVICE_AXIS_SLES,
)

__all__ = [
    "DEFAULT_OFFENDER_WEIGHTS",
    "OFFENDER_EVENT_KEYS",
    "ROAM_EVENT_KEYS",
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

# Roam-only subset of the above (no disconnects): the source of truth for a
# client's "Roams" count on the Clients list/detail (Gitea #23). The raw
# `roam_count` metric the controller reports per client is a COUNTER (see
# store/metrics.py's COUNTER_METRICS) -- the delta since the last poll, not a
# meaningful lifetime total -- so counting the discrete roam events already
# recorded for Timeline/Journey is the honest number, not a second-guessed one.
ROAM_EVENT_KEYS: tuple[str, ...] = (
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

    ``fail_minutes`` and ``down_minutes`` are **different units over different
    populations** and there is no field that holds their sum, by design (Gitea
    #36, #38):

    * ``fail_minutes`` — client-axis minutes attributed to this entity. Time real
      clients spent below a service level because of it. This is what the score
      is built from.
    * ``down_minutes`` — device-axis minutes: how long this AP, switch or gateway
      was itself offline. Nobody spent those as a client, so they are reported
      beside the score and never folded into it (module docstring). ``None``
      means *not measured* — the entity has no downtime axis at all (a client, a
      radio: see :data:`~netadmin.store.repository.SLE_DEVICE_AXIS_ENTITY_TYPES`)
      or the SLE engine judged that axis nowhere in the window. ``0.0`` is the
      different, stronger claim that it was watched and never went down.
    """

    entity_id: int
    score: float
    fail_minutes: float
    issue_counts: dict[str, int]
    event_count: int
    down_minutes: Optional[float] = None
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable view for the API layer (name resolution added there)."""
        return {
            "entity_id": self.entity_id,
            "score": self.score,
            "fail_minutes": self.fail_minutes,
            "down_minutes": self.down_minutes,
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

    Three repository ``GROUP BY``\\s (failed attributed **client-axis** SLE
    minutes, open-issue counts by severity, disconnect/roam event volume) over
    ``[start_ts, end_ts)`` are folded into one score per candidate entity via the
    documented weighting (module docstring). Only entities whose ``entity_type``
    is in ``entity_types`` are ranked; the candidate set is the union of ids
    appearing in any channel, so an entity with grief anywhere is considered.

    A fourth query reads the **device axis** (``infra`` down-minutes) and hangs
    it off :attr:`OffenderScore.down_minutes` *without* touching the score. A
    down AP's harm is already on the client axis — its clients moved to the next
    AP and burned coverage minutes there — so scoring downtime as well would
    double-count one outage and let a loud, harmless device outrank a quiet,
    costly one. The reasoning in full is in the module docstring.

    Results are sorted by ``score`` descending, ties broken by ``entity_id``
    ascending (a stable, reproducible order — no statistical jitter), and capped
    at ``top_n`` (``top_n <= 0`` returns every ranked entity). An empty window, or
    one with no failed minutes / issues / events / downtime, yields ``[]``.
    """
    weights = weights if weights is not None else load_offender_weights(settings)
    wanted_types = set(entity_types)

    fail_minutes = repo.sle_fail_minutes_by_attributed(start_ts, end_ts, sles=SLE_CLIENT_AXIS_SLES)
    # The device axis, read separately and kept separate. `infra` rows carry the
    # device in both entity_id and attributed_entity_id, so grouping by the
    # attributed id is the same GROUP BY the client axis uses -- one helper, two
    # axes, never one figure.
    down_minutes = repo.sle_fail_minutes_by_attributed(start_ts, end_ts, sles=SLE_DEVICE_AXIS_SLES)
    issue_counts = repo.open_issue_counts()  # window-independent: open issues now
    event_counts = repo.event_counts_by_entity(start_ts, end_ts, list(event_keys))

    # "Not measured" is not "zero" (Gitea #36): the infra axis carries a figure
    # only where the SLE engine actually judged it. The same exposure probe the
    # issues list reads, asked of the whole window rather than one issue's life.
    infra_judged = repo.sle_minutes_axis_spans(start_ts, end_ts).get("infra") is not None

    # Candidate set: every entity that appears in any channel. open_issue_counts
    # spans all entity types, so it is filtered to the requested types below.
    candidate_ids: set[int] = set()
    candidate_ids.update(fail_minutes)
    candidate_ids.update(down_minutes)
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
        # A radio can be *attributed* client minutes but the infra SLE never
        # walks a radio's state timeline, and a client has no such timeline at
        # all -- so for those "down 0 min" would be a claim nobody measured.
        has_down_axis = infra_judged and row["entity_type"] in SLE_DEVICE_AXIS_ENTITY_TYPES
        dm = float(down_minutes.get(eid, 0.0)) if has_down_axis else None

        sle_pts = weights["sle_minute"] * fm
        issue_pts = (
            weights["issue_p1"] * n_p1 + weights["issue_p2"] * n_p2 + weights["issue_p3"] * n_p3
        )
        event_pts = weights["event"] * n_events
        score = sle_pts + issue_pts + event_pts
        # Downtime never earns a place on the leaderboard by *rank*, but it does
        # earn a row: a device that went down and cost no measurable client
        # minute is listed at score 0, strictly below anything that cost a
        # client a minute. Dropping it would erase the outage from the surface,
        # and adding it to the score is the double-count this ranking exists to
        # avoid.
        if score <= 0 and not dm:
            continue  # no measurable burden in any channel — not an offender

        scored.append(
            OffenderScore(
                entity_id=eid,
                score=score,
                fail_minutes=fm,
                issue_counts={"p1": n_p1, "p2": n_p2, "p3": n_p3, "total": n_p1 + n_p2 + n_p3},
                event_count=n_events,
                down_minutes=dm,
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
