"""Job health must not report a running job as UNKNOWN (``/api/health``).

Regression for a live bug: the collector's status snapshot only covers the jobs the
*collector* owns. The detector passes, the analysis jobs (baseline, sle_minutes,
correlate) and the probes record their runs in ``poll_runs`` and never appear in it.
Treating "absent from the snapshot" as "never ran" pinned all of them to UNKNOWN
forever. On a real 37-hour deployment that showed 10 of 18 jobs greyed out while
``poll_runs`` held 5,558 detect_fast runs and the newest was 24 seconds old.
"""

from __future__ import annotations

from netadmin.server.runtime import UNKNOWN, _job_health


class _Store:
    """Minimal store returning one fresh successful poll_run for every job."""

    def __init__(self, now: int) -> None:
        self._now = now

    def read_poll_runs(self, job: str, start: int, end: int) -> list[dict]:
        return [{"ts": self._now - 30, "ok": 1, "job": job}]


def _snapshot_owning_only_the_collector() -> dict:
    """What a live CollectorStatus looks like: collector jobs only."""
    return {
        "last_ok_ts": {"fast_device": 1_000_000},
        "last_run_ts": {"fast_device": 1_000_000},
        "consecutive_failures": {"fast_device": 0},
    }


def test_job_not_owned_by_the_collector_falls_back_to_poll_runs() -> None:
    now = 1_000_100
    store = _Store(now)
    snapshot = _snapshot_owning_only_the_collector()

    for job in ("detect_fast", "baseline", "sle_minutes", "correlate", "probe.dns"):
        health = _job_health(store, None, job, now, snapshot)
        assert health["status"] == "ok", f"{job} reported {health['status']}, expected ok"
        assert health["last_success_age_s"] == 30


def test_collector_owned_job_still_uses_the_snapshot() -> None:
    """The snapshot stays authoritative for the jobs it does own."""
    now = 1_000_100
    health = _job_health(
        _Store(now), None, "fast_device", now, _snapshot_owning_only_the_collector()
    )
    assert health["last_success_ts"] == 1_000_000
    assert health["last_success_age_s"] == 100


def test_owned_job_that_has_never_run_is_unknown() -> None:
    """A job the collector tracks but has never run is honestly UNKNOWN."""
    snapshot = {"last_ok_ts": {"fast_sta": None}, "last_run_ts": {}, "consecutive_failures": {}}
    health = _job_health(None, None, "fast_sta", 1_000_100, snapshot)
    assert health["status"] == UNKNOWN
