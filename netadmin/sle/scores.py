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

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from netadmin.sle.classifiers import ALL_SLES, OK

__all__ = [
    "DEFAULT_WEIGHTS",
    "MIN_EXPOSURE_FRACTION",
    "MIN_EXPOSURE_MINUTES",
    "SleScore",
    "ScoreReport",
    "sle_scores",
    "load_weights",
]

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

# The confidence floor (docs: "no data must never look like scoring badly"). An
# SLE can score a real number from a handful of judged minutes -- that number is
# never hidden or fabricated, but it also must never carry the same weight in
# the headline blend as an SLE judged across the whole window, and the UI must
# not paint it Good/Fair/Poor with the same confidence. An SLE is below the
# confidence floor only when it is thin BOTH ways: few of the window's buckets
# judged AND few judged minutes in absolute terms.
#
# The conjunction matters. On a real deployment, coverage, capacity and WAN each
# carried over 4,000 judged client-minutes but landed in only 12 of 288 buckets,
# because byte counters arrived in a six-hourly sweep. Treating either condition
# alone as disqualifying dropped all three from the headline, leaving infra (a
# 0.05 weight) to renormalise to 1.0: the score jumped from 72% to 100% and a
# genuine roaming failure vanished from the number. Thousands of judged minutes
# are real evidence even when they cluster; only data that is thin on both axes
# is too thin to blend.
MIN_EXPOSURE_FRACTION = 0.20  # < 20% of the window's buckets judged
MIN_EXPOSURE_MINUTES = 30.0  # or < 30 judged minutes total


@dataclass
class SleScore:
    """One SLE's score plus the breakdown that explains it.

    ``score`` is ``ok_minutes / total_minutes`` in ``[0, 1]``, or ``None`` when the
    SLE had no exposed minutes in the window (no data — not a perfect score). The
    ``classifiers`` map carries every classifier's minutes (including ``ok``);
    ``top_offenders`` ranks the infrastructure entities that own the failed minutes.

    ``evaluated_buckets`` is the count of distinct 5-minute buckets in the window
    that produced *any* ``sle_minutes`` row for this SLE (a judgment, pass or
    fail); ``window_buckets`` is how many buckets the window holds in total. For a
    dense, every-active-client SLE (coverage, capacity, wan-when-evaluable, infra)
    this is a direct exposure fraction. For a per-occurrence SLE (roaming,
    connect) — one that only ever writes a row when something happened — a low
    ``evaluated_buckets`` does NOT by itself mean "couldn't measure": it can
    equally mean "measured continuously, nothing occurred" (docs: the three
    empty states). Distinguishing those is the caller's job (the SLE router
    cross-references a dense SLE's exposure as a "clients were observable" proxy).
    """

    sle: str
    score: Optional[float]
    total_minutes: float
    ok_minutes: float
    fail_minutes: float
    evaluated_buckets: int = 0
    window_buckets: int = 0
    classifiers: dict[str, float] = field(default_factory=dict)
    top_offenders: list[dict[str, Any]] = field(default_factory=list)

    @property
    def below_floor(self) -> bool:
        """True when this SLE scored but on too little evidence to headline with
        full confidence (see :data:`MIN_EXPOSURE_FRACTION` / :data:`MIN_EXPOSURE_MINUTES`).

        A ``None`` score is never "below floor" in this sense — it is simply
        absent (see :func:`_blend`); this flags the distinct case of a real
        number computed from thin exposure, which the headline blend excludes
        and the UI must render without the confident Good/Fair/Poor band.
        """
        if self.score is None:
            return False
        frac = (self.evaluated_buckets / self.window_buckets) if self.window_buckets > 0 else 0.0
        # The absolute quantity of judged evidence decides. The bucket fraction is
        # reported for display ("measured 12 of 288 intervals") but must NOT
        # disqualify on its own: real data clusters, because byte counters arrive
        # in a six-hourly sweep, and 4,000 judged client-minutes in 12 buckets is
        # strong evidence that happens to be bunched. Excluding it dropped three
        # SLEs from the headline and let a 0.05-weight infra score renormalise to
        # 100%, hiding a live roaming failure.
        del frac  # kept above for readability; intentionally not a disqualifier
        return self.total_minutes < MIN_EXPOSURE_MINUTES


@dataclass
class ScoreReport:
    """The full SLE report over a window: per-SLE scores plus the headline blend.

    ``included_sles`` headlined with full weight; ``excluded_below_floor`` scored
    but too thin on evidence to headline (see :attr:`SleScore.below_floor`);
    ``excluded_no_data`` had no exposed minutes in the window at all (``score is
    None``) — which, for a per-occurrence SLE, may be a legitimate quiet pass
    rather than a gap (the router resolves that distinction for the UI).
    """

    start_ts: int
    end_ts: int
    headline: Optional[float]
    weights: dict[str, float]
    window_buckets: int
    sles: dict[str, SleScore]
    included_sles: list[str] = field(default_factory=list)
    excluded_below_floor: list[str] = field(default_factory=list)
    excluded_no_data: list[str] = field(default_factory=list)


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
    bucket_seconds: Optional[int] = None,
) -> ScoreReport:
    """Score every SLE over ``[start_ts, end_ts)`` and blend the headline.

    Two repository GROUP BYs over the same ``sle_minutes`` rows: (``sle``,
    ``classifier``, ``attributed_entity_id``) supplies the per-SLE score, the
    classifier breakdown, and the top offenders; (``sle``, ``bucket_ts``) supplies
    each SLE's exposure (:attr:`SleScore.evaluated_buckets` — how many of the
    window's buckets it actually judged). ``weights`` overrides the blend (else
    :func:`load_weights`); ``top_n`` caps each SLE's offender list;
    ``bucket_seconds`` is the SLE bucket width (:attr:`SleConfig.bucket_seconds`,
    300s by default) the exposure fraction is measured against.

    The headline blend (:func:`_blend`) excludes any SLE with no data (``score is
    None``) *or* below the confidence floor (:attr:`SleScore.below_floor`) — a
    number computed from a handful of judged minutes never carries full weight
    (docs: "no data must never look like scoring badly").
    """
    # Fall back to the configured SLE bucket width rather than a hardcoded 300.
    # The exposure maths divides the window by this, so a site that overrides
    # thresholds.sle.bucket_seconds would otherwise get a silently wrong
    # window_buckets and therefore a wrong confidence floor.
    if bucket_seconds is None:
        cfg = getattr(getattr(settings, "sle", None), "bucket_seconds", None)
        if cfg is None:
            cfg = ((getattr(settings, "thresholds", None) or {}).get("sle") or {}).get(
                "bucket_seconds"
            )
        bucket_seconds = int(cfg) if cfg else 300
    weights = weights if weights is not None else load_weights(settings)
    rows = repo.query_sle_minutes(
        start_ts, end_ts, group_by=("sle", "classifier", "attributed_entity_id")
    )
    bucket_rows = repo.query_sle_minutes(start_ts, end_ts, group_by=("sle", "bucket_ts"))

    window_buckets = max(1, math.ceil((end_ts - start_ts) / max(1, bucket_seconds)))

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

    evaluated_buckets: dict[str, set[int]] = {}
    for row in bucket_rows:
        evaluated_buckets.setdefault(row["sle"], set()).add(int(row["bucket_ts"]))

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
            evaluated_buckets=len(evaluated_buckets.get(sle, ())),
            window_buckets=window_buckets,
            classifiers=dict(sorted(breakdown.items())),
            top_offenders=offenders,
        )

    headline, included, excluded_floor, excluded_no_data = _blend(sles, weights)
    return ScoreReport(
        start_ts=int(start_ts),
        end_ts=int(end_ts),
        headline=headline,
        weights=weights,
        window_buckets=window_buckets,
        sles=sles,
        included_sles=included,
        excluded_below_floor=excluded_floor,
        excluded_no_data=excluded_no_data,
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


def _blend(
    sles: dict[str, SleScore], weights: dict[str, float]
) -> tuple[Optional[float], list[str], list[str], list[str]]:
    """Weighted mean of the SLEs that had data AND met the confidence floor,
    weights renormalised over them. Returns ``(headline, included, excluded_below_floor,
    excluded_no_data)``:

    * ``included`` — contributed to the headline (real score, floor met, weight > 0).
    * ``excluded_below_floor`` — scored, but too thin on evidence to headline
      (see :attr:`SleScore.below_floor`); still a real number, just not blended.
    * ``excluded_no_data`` — no exposed minutes in the window (``score is None``).

    An SLE configured with zero weight is silently skipped either way (a product
    decision, not a data-quality one) and appears in neither list. The headline is
    ``None`` only when nothing qualified at all.
    """
    num = 0.0
    den = 0.0
    included: list[str] = []
    excluded_below_floor: list[str] = []
    excluded_no_data: list[str] = []
    for sle in ALL_SLES:
        sc = sles.get(sle)
        if sc is None:
            continue
        if sc.score is None:
            excluded_no_data.append(sle)
            continue
        w = weights.get(sle, 0.0)
        if w <= 0:
            continue
        if sc.below_floor:
            excluded_below_floor.append(sle)
            continue
        num += w * sc.score
        den += w
        included.append(sle)
    headline = (num / den) if den > 0 else None
    return headline, included, excluded_below_floor, excluded_no_data
