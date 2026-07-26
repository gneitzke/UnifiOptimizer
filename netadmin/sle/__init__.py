"""netadmin.sle: Mist-style SLE user-minute accounting and classifiers.

Three pieces (ARCHITECTURE.md section 8):

* :mod:`netadmin.sle.classifiers` — deterministic, pure classifier rules per SLE
  (coverage, roaming, capacity, connect, wan, infra) plus the tunable
  :class:`~netadmin.sle.classifiers.SleConfig`.
* :mod:`netadmin.sle.minutes` — the 5-minute-bucket job
  (:class:`~netadmin.sle.minutes.SleMinutesJob`) that writes pass/fail user-minutes
  with exactly-one-classifier attribution, gated on real client activity.
* :mod:`netadmin.sle.scores` — :func:`~netadmin.sle.scores.sle_scores`, the health
  number and its explanation as one GROUP BY over ``sle_minutes``.
"""

from netadmin.sle.classifiers import ALL_SLES, CLASSIFIERS_BY_SLE, OK, SleConfig
from netadmin.sle.minutes import BucketResult, SleMinutesJob, bucket_of
from netadmin.sle.scores import DEFAULT_WEIGHTS, ScoreReport, SleScore, sle_scores

__all__ = [
    "ALL_SLES",
    "CLASSIFIERS_BY_SLE",
    "OK",
    "SleConfig",
    "SleMinutesJob",
    "BucketResult",
    "bucket_of",
    "sle_scores",
    "ScoreReport",
    "SleScore",
    "DEFAULT_WEIGHTS",
]
