"""Metric registry: gauge vs counter, explicit per metric.

The storage layer must know, for every metric name it ingests, whether the
controller reports it as a **gauge** (an instantaneous reading: RSSI, airtime
utilization, PoE watts, temperature) or a **counter** (a monotonically
increasing total since boot: rx_bytes, rx_errors, tx_retries). Counters are
stored in ``samples`` as per-interval deltas, not cumulative values
(``docs/ARCHITECTURE.md`` section 4), so the repository consults this registry
to decide whether to diff a reading against the previous one.

Unknown metrics default to :data:`MetricKind.GAUGE`. Registering a metric as a
counter is a deliberate act -- getting it wrong turns a rate into an
ever-climbing line, so the counter set below is explicit and reviewed.

Delta semantics (the storage contract every consumer relies on):

- A **counter** sample stored in ``samples`` is the *raw per-interval delta*
  (the accumulation since the previous reading of that series), **not** a
  per-second rate. The interval is whatever elapsed between the two polls that
  produced the readings -- nominally the collector cadence, but wider after a
  coalesced poll and re-seeded (no row) after a gap or a counter reset. So a
  raw counter value of ``500`` means "500 more since last time", over an
  interval that is usually but not always the nominal cadence.
- Rollups fold those deltas: a bucket's ``sum`` is the total accumulation over
  the bucket, which is the honest counter aggregate (``avg`` of deltas is
  meaningless for counters -- see :meth:`Repository.read_rollup`, which aliases
  ``value`` to ``sum`` for counters and ``avg`` for gauges).
- To turn deltas into a time-normalized **rate** (per second), divide each
  delta by the *actual* elapsed time between its sample and the prior one --
  never by the assumed cadence. :meth:`WindowResult.rate` does exactly this so
  detectors compare like with like across variable intervals.
- A **gauge** sample is the instantaneous reading, stored verbatim; ``avg`` is
  its natural rollup aggregate and no rate conversion applies.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "MetricKind",
    "metric_kind",
    "is_counter",
    "register_metric",
    "COUNTER_METRICS",
]


class MetricKind(str, Enum):
    """How a metric accumulates. String values are stable for logging/debug."""

    GAUGE = "gauge"  # instantaneous reading; stored verbatim
    COUNTER = "counter"  # cumulative total; stored as per-interval delta


# Metrics the controller reports as cumulative counters. Everything else is a
# gauge. Names match the fields wrappers in netadmin/ingest emit; add here
# (never guess at ingest time) when a new counter field is wired in.
COUNTER_METRICS: frozenset[str] = frozenset(
    {
        # port / interface byte & packet totals (stat/device port_table)
        "rx_bytes",
        "tx_bytes",
        "rx_packets",
        "tx_packets",
        "rx_errors",
        "tx_errors",
        "rx_dropped",
        "tx_dropped",
        "rx_crypts",
        "rx_frags",
        "rx_nwids",
        "rx_multicast",
        "tx_multicast",
        "rx_broadcast",
        "tx_broadcast",
        # radio / wifi totals (radio_table_stats, stat/sta)
        "tx_retries",
        "tx_packets_retried",
        "wifi_tx_attempts",
        "wifi_tx_dropped",
        # per-client cumulative
        "roam_count",
    }
)


# Mutable working registry, seeded from the reviewed sets above. Ingest code may
# extend it at import time via register_metric() for endpoint-specific fields.
_REGISTRY: dict[str, MetricKind] = {name: MetricKind.COUNTER for name in COUNTER_METRICS}


def register_metric(metric: str, kind: MetricKind) -> None:
    """Declare a metric's kind explicitly (overrides the default gauge)."""
    _REGISTRY[metric] = kind


def metric_kind(metric: str) -> MetricKind:
    """Return the kind for ``metric`` (unknown metrics are gauges)."""
    return _REGISTRY.get(metric, MetricKind.GAUGE)


def is_counter(metric: str) -> bool:
    """True when ``metric`` is a cumulative counter (stored as deltas)."""
    return metric_kind(metric) is MetricKind.COUNTER
