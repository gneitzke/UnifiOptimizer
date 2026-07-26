"""SLE scoring: the health number and its explanation are the same GROUP BY.

Section 8's central claim is that the score and the reason for it are not two data
paths but one: every figure here is a ``GROUP BY`` over ``sle_minutes``, the exact
table :mod:`netadmin.sle.minutes` wrote. A per-SLE score is simply
``ok_minutes / total_minutes``; the classifier breakdown is the same rows grouped
one level finer; the top offenders are those rows grouped by
``attributed_entity_id`` with ``ok`` excluded. Nothing is recomputed from raw
metrics — if the score says coverage is 92 %, the 8 % is right there as
``weak_signal`` / ``asymmetry_suspected`` minutes pinned on specific APs.

The headline is a **weighted blend** of the available per-SLE scores. Weights are a
product decision that will move to config; the documented defaults below reflect
the section-8 priorities (client-facing air quality first, infra availability last,
since infra failures already surface as coverage/capacity/connect impact). An SLE
with no exposed minutes in the window contributes neither a score nor weight, so a
gateway-less site whose WAN SLE is silent still gets an honest headline over the
SLEs that did have data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from netadmin.sle.classifiers import ALL_SLES, OK

__all__ = ["DEFAULT_WEIGHTS", "SleScore", "ScoreReport", "sle_scores", "load_weights"]

# Documented default blend weights (sum to 1.0). Client-facing air quality is
# weighted highest; infra lowest because a downed AP already shows up as the
# coverage/capacity/connect minutes it caused. Move to config later (section 8).
DEFAULT_WEIGHTS: dict[str, float] = {
    "coverage": 0.25,
    "capacity": 0.20,
    "connect": 0.20,
    "roaming": 0.15,
    "wan": 0.15,
    "infra": 0.05,
}


@dataclass
class SleScore:
    """One SLE's score plus the breakdown that explains it.

    ``score`` is ``ok_minutes / total_minutes`` in ``[0, 1]``, or ``None`` when the
    SLE had no exposed minutes in the window (no data — not a perfect score). The
    ``classifiers`` map carries every classifier's minutes (including ``ok``);
    ``top_offenders`` ranks the infrastructure entities that own the failed minutes.
    """

    sle: str
    score: Optional[float]
    total_minutes: float
    ok_minutes: float
    fail_minutes: float
    classifiers: dict[str, float] = field(default_factory=dict)
    top_offenders: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ScoreReport:
    """The full SLE report over a window: per-SLE scores plus the headline blend."""

    start_ts: int
    end_ts: int
    headline: Optional[float]
    weights: dict[str, float]
    sles: dict[str, SleScore]


def load_weights(settings: Any = None) -> dict[str, float]:
    """Blend weights, overriding :data:`DEFAULT_WEIGHTS` from
    ``settings.thresholds["sle"]["weights"]`` when present. Unknown SLE keys are
    ignored; missing ones keep their default. Never raises.
    """
    weights = dict(DEFAULT_WEIGHTS)
    thresholds = getattr(settings, "thresholds", None)
    section = thresholds.get("sle") if isinstance(thresholds, dict) else None
    override = section.get("weights") if isinstance(section, dict) else None
    if isinstance(override, dict):
        for sle, w in override.items():
            if sle in weights:
                try:
                    weights[sle] = float(w)
                except (TypeError, ValueError):
                    continue
    return weights


def sle_scores(
    repo: Any,
    start_ts: int,
    end_ts: int,
    *,
    weights: Optional[dict[str, float]] = None,
    top_n: int = 5,
    settings: Any = None,
) -> ScoreReport:
    """Score every SLE over ``[start_ts, end_ts)`` and blend the headline.

    One repository GROUP BY (``sle``, ``classifier``, ``attributed_entity_id``)
    supplies every figure: the per-SLE score, the classifier breakdown, and the
    top offenders. ``weights`` overrides the blend (else :func:`load_weights`);
    ``top_n`` caps each SLE's offender list.
    """
    weights = weights if weights is not None else load_weights(settings)
    rows = repo.query_sle_minutes(
        start_ts, end_ts, group_by=("sle", "classifier", "attributed_entity_id")
    )

    # Fold the GROUP BY into per-SLE accumulators.
    per_classifier: dict[str, dict[str, float]] = {}
    per_offender: dict[str, dict[Optional[int], float]] = {}
    for row in rows:
        sle = row["sle"]
        classifier = row["classifier"]
        minutes = float(row["minutes"] or 0.0)
        per_classifier.setdefault(sle, {}).setdefault(classifier, 0.0)
        per_classifier[sle][classifier] += minutes
        if classifier != OK:
            attr = row["attributed_entity_id"]
            per_offender.setdefault(sle, {}).setdefault(attr, 0.0)
            per_offender[sle][attr] += minutes

    sles: dict[str, SleScore] = {}
    # Iterate the canonical SLE set so absent SLEs report as no-data, not missing.
    for sle in ALL_SLES:
        breakdown = per_classifier.get(sle, {})
        total = sum(breakdown.values())
        ok_minutes = breakdown.get(OK, 0.0)
        fail_minutes = total - ok_minutes
        score = (ok_minutes / total) if total > 0 else None
        offenders = _rank_offenders(per_offender.get(sle, {}), top_n)
        sles[sle] = SleScore(
            sle=sle,
            score=score,
            total_minutes=total,
            ok_minutes=ok_minutes,
            fail_minutes=fail_minutes,
            classifiers=dict(sorted(breakdown.items())),
            top_offenders=offenders,
        )

    headline = _blend(sles, weights)
    return ScoreReport(
        start_ts=int(start_ts),
        end_ts=int(end_ts),
        headline=headline,
        weights=weights,
        sles=sles,
    )


def _rank_offenders(offenders: dict[Optional[int], float], top_n: int) -> list[dict[str, Any]]:
    """Top ``top_n`` infrastructure entities by failed minutes, most first.

    Deterministic ordering: most fail minutes, then lowest ``attributed_entity_id``
    (a ``None`` attribution — a failure the model could not pin on an entity —
    sorts last).
    """
    ranked = sorted(
        offenders.items(),
        key=lambda kv: (-kv[1], kv[0] if kv[0] is not None else 1 << 62),
    )
    return [
        {"attributed_entity_id": attr, "fail_minutes": minutes}
        for attr, minutes in ranked[: max(0, top_n)]
    ]


def _blend(sles: dict[str, SleScore], weights: dict[str, float]) -> Optional[float]:
    """Weighted mean of the SLEs that had data, weights renormalised over them.

    An SLE with no exposed minutes (``score is None``) contributes neither score
    nor weight, so the headline is honest on a site where some SLEs are silent.
    Returns ``None`` when no SLE had any data.
    """
    num = 0.0
    den = 0.0
    for sle, sc in sles.items():
        if sc.score is None:
            continue
        w = weights.get(sle, 0.0)
        if w <= 0:
            continue
        num += w * sc.score
        den += w
    return (num / den) if den > 0 else None
