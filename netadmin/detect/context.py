"""``DetectorContext`` — the read-only surface every detector evaluates against.

A detector never touches SQL and never constructs issues (``docs/ARCHITECTURE.md``
section 6). It reads through this context, which wraps the
:class:`~netadmin.store.repository.Repository` and the
:class:`~netadmin.detect.baseline.Baselines` behind a small, stable API:

* :meth:`window` — a metric's recent samples for an entity (tier-stitched);
* :meth:`events` — the normalized event log, filtered by entity / key / time;
* :meth:`entities` — inventory, as decoded :class:`~netadmin.domain.entities.Entity`
  objects a detector can drop straight onto a :class:`Finding`;
* :meth:`coverage` — the measured fraction of a collector job that actually ran
  in a window (the honest gap signal a detector gates UNKNOWN on);
* :meth:`threshold` — a per-detector tunable, overridable from
  ``settings.thresholds[detector_key]`` with a hard-coded default fallback.

``.repo`` and ``.baselines`` are exposed directly for the reads that do not yet
have a dedicated helper (``repo.current_state``, ``baselines.band``); they are the
same repository API, not raw SQL, so section 6's rule holds.

The context is a per-cycle value object: the engine builds a fresh one for each
pass with the evaluation ``now_ts`` injected, exactly like the issue engine's
injected clock — logic never reads the wall clock itself.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Iterable, Optional, Union

from netadmin.domain.entities import Entity, Timestamp
from netadmin.domain.types import EntityType
from netadmin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from netadmin.detect.baseline import Baselines
    from netadmin.store.repository import Repository, WindowResult

_log = get_logger("detect.context")

# Canonical collector/probe job id -> nominal cadence (seconds). Mirrors the
# ``poll_runs.job`` values emitted by netadmin.ingest (collector.py / probes.py),
# kept here as plain strings so ``detect`` never imports ``ingest``. Per-install
# cadences from ``settings.poll`` override these where present.
_DEFAULT_JOB_INTERVALS: dict[str, int] = {
    "fast_device": 60,
    "fast_sta": 60,
    "fast_health": 60,
    "events_catchup": 300,
    "reports_5min": 21_600,
    "probe.dns": 60,
    "probe.dns.anchor": 60,
    "probe.gw_rtt": 60,
}
# Fallback cadence for an unrecognised job id: the primary fast cadence. Detectors
# only ever pass known job ids, so this exists to keep :meth:`coverage` total
# rather than to be relied on.
_FALLBACK_INTERVAL_S = 60


def _row_get(row: Any, key: str) -> Any:
    """Read ``key`` from a ``sqlite3.Row`` or a plain mapping, or ``None``.

    ``sqlite3.Row`` raises ``IndexError`` for an unknown column; ``dict`` raises
    ``KeyError``. Detectors run against the real store (rows) but the context is
    unit-tested with dict-shaped fakes, so both are tolerated.
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _entity_from_row(row: Any) -> Entity:
    """Decode an ``entities`` row into an :class:`Entity` (``meta`` JSON parsed)."""
    raw_meta = _row_get(row, "meta")
    meta: dict[str, Any] = {}
    if raw_meta:
        try:
            decoded = json.loads(raw_meta)
            if isinstance(decoded, dict):
                meta = decoded
        except (TypeError, ValueError):
            meta = {}
    return Entity(
        entity_type=EntityType(_row_get(row, "entity_type")),
        native_id=_row_get(row, "native_id"),
        site_id=_row_get(row, "site_id") or "default",
        entity_id=_row_get(row, "entity_id"),
        parent_id=_row_get(row, "parent_id"),
        name=_row_get(row, "name"),
        model=_row_get(row, "model"),
        first_seen_ts=_row_get(row, "first_seen_ts"),
        last_seen_ts=_row_get(row, "last_seen_ts"),
        meta=meta,
    )


def _coerce_thresholds(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _build_job_intervals(poll: Any) -> dict[str, int]:
    """Job-id -> cadence map, seeded from defaults and overridden by ``settings.poll``.

    ``poll`` is a settings ``PollIntervals`` (or any object exposing the ``*_s``
    attributes). Missing attributes fall back to the architecture defaults via
    ``getattr`` so the map is robust to a partially-populated settings object.
    """
    intervals = dict(_DEFAULT_JOB_INTERVALS)
    if poll is None:
        return intervals
    intervals["fast_device"] = int(getattr(poll, "device_s", intervals["fast_device"]))
    intervals["fast_sta"] = int(getattr(poll, "sta_s", intervals["fast_sta"]))
    intervals["fast_health"] = int(getattr(poll, "health_s", intervals["fast_health"]))
    intervals["events_catchup"] = int(getattr(poll, "event_catchup_s", intervals["events_catchup"]))
    intervals["reports_5min"] = int(getattr(poll, "report_5min_s", intervals["reports_5min"]))
    probe_s = int(getattr(poll, "probe_s", 60))
    intervals["probe.dns"] = probe_s
    intervals["probe.dns.anchor"] = probe_s
    intervals["probe.gw_rtt"] = probe_s
    return intervals


class DetectorContext:
    """Everything a detector may read for one evaluation cycle.

    Constructed fresh per pass by :class:`~netadmin.detect.engine.DetectorEngine`
    with the evaluation timestamp injected. ``baselines`` is injected too so unit
    tests can supply a fake; :meth:`for_repository` is the composition-root factory
    that builds the real :class:`~netadmin.detect.baseline.Baselines` from a store.
    """

    def __init__(
        self,
        *,
        repo: "Repository",
        baselines: "Baselines",
        now_ts: Timestamp,
        site_id: str,
        settings: Any = None,
    ) -> None:
        self.repo = repo
        self.baselines = baselines
        self.now_ts = int(now_ts)
        self.site_id = site_id
        self._settings = settings
        self._thresholds = _coerce_thresholds(getattr(settings, "thresholds", None))
        self._job_intervals = _build_job_intervals(getattr(settings, "poll", None))

    @classmethod
    def for_repository(
        cls,
        repo: "Repository",
        now_ts: Timestamp,
        *,
        site_id: Optional[str] = None,
        settings: Any = None,
        baselines: Optional["Baselines"] = None,
    ) -> "DetectorContext":
        """Build a context against a live store, wiring real baselines.

        The :class:`~netadmin.detect.baseline.Baselines` import is deferred to call
        time so importing this module never pulls the baseline engine in; that keeps
        the context unit-testable with an injected fake and the two sides of the
        pinned seam independently loadable.
        """
        if baselines is None:
            from netadmin.detect.baseline import Baselines

            baselines = Baselines.for_repository(repo)
        resolved_site = site_id if site_id is not None else getattr(repo, "site_id", "default")
        return cls(
            repo=repo,
            baselines=baselines,
            now_ts=now_ts,
            site_id=resolved_site,
            settings=settings,
        )

    # ------------------------------------------------------------------ #
    # Series windows
    # ------------------------------------------------------------------ #
    def window(self, entity_id: int, metric: str, seconds: int) -> Optional["WindowResult"]:
        """Recent samples for ``(entity_id, metric)`` over the last ``seconds``.

        Returns the tier-stitched :class:`~netadmin.store.repository.WindowResult`,
        or ``None`` when the series has never been recorded or the window is
        non-positive. The window ends at :attr:`now_ts`; the store decides which
        retention tiers serve it.
        """
        if seconds <= 0:
            return None
        series_id = self.repo.get_series(entity_id, metric)
        if series_id is None:
            return None
        start_ts = self.now_ts - int(seconds)
        return self.repo.read_window(series_id, start_ts, self.now_ts, now=self.now_ts)

    # ------------------------------------------------------------------ #
    # Event log
    # ------------------------------------------------------------------ #
    def events(
        self,
        entity_id: Optional[int] = None,
        keys: Optional[Iterable[str]] = None,
        since_ts: Optional[Timestamp] = None,
    ) -> list:
        """Normalized events, oldest-first, filtered by entity / keys / time.

        ``since_ts`` is an inclusive floor (``None`` = from the beginning of
        retained history); the window is closed at :attr:`now_ts` inclusive.
        ``keys`` restricts to a set of event keys (e.g. the ``*_Lost_Contact``
        family); ``None`` returns every key. Rows are ``sqlite3.Row`` with the
        ``events`` columns (``ts``, ``key``, ``entity_id``, ``related_entity_id``,
        ``native_id``, ``msg``, ``data``).
        """
        start_ts = 0 if since_ts is None else int(since_ts)
        end_ts = self.now_ts + 1  # read_events is [start, end); include now_ts
        rows = self.repo.read_events(start_ts, end_ts, entity_id=entity_id)
        if keys is None:
            return list(rows)
        wanted = set(keys)
        return [row for row in rows if _row_get(row, "key") in wanted]

    # ------------------------------------------------------------------ #
    # Inventory
    # ------------------------------------------------------------------ #
    def entities(self, entity_type: Optional[Union[EntityType, str]] = None) -> list[Entity]:
        """All entities of ``entity_type`` (or every entity) for this site.

        Returned as decoded :class:`Entity` objects (``meta`` JSON parsed) so a
        detector can attach one directly to a :class:`Finding` without re-reading
        the store.
        """
        rows = self.repo.list_entities(entity_type, site_id=self.site_id)
        return [_entity_from_row(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Coverage (the honest gap signal)
    # ------------------------------------------------------------------ #
    def coverage(self, window_seconds: int, job: str) -> float:
        """Fraction in ``[0, 1]`` of ``job``'s expected polls that succeeded live.

        The denominator is ``window_seconds / cadence(job)``; the numerator is the
        count of successful live ``poll_runs`` for ``job`` in the window. A gap is
        measured here, never inferred from missing samples. A detector whose window
        reads under 0.5 must return the engine's ``UNKNOWN`` sentinel rather than a
        false OK (``docs/ARCHITECTURE.md`` sections 4 & 6).
        """
        if window_seconds <= 0:
            return 0.0
        interval_s = self._job_intervals.get(job, _FALLBACK_INTERVAL_S)
        start_ts = self.now_ts - int(window_seconds)
        return self.repo.expected_coverage(job, start_ts, self.now_ts, interval_s, source="live")

    # ------------------------------------------------------------------ #
    # Tunables
    # ------------------------------------------------------------------ #
    def threshold(self, detector_key: str, name: str, default: Any) -> Any:
        """A per-detector tunable: ``settings.thresholds[detector_key][name]``.

        Detectors ship their own defaults as dataclass fields / literals and read
        overrides through here; when nothing is configured the ``default`` passed
        by the detector wins. Never raises — a missing section or key yields
        ``default`` (section 6).
        """
        section = self._thresholds.get(detector_key)
        if isinstance(section, dict) and name in section:
            return section[name]
        return default


__all__ = ["DetectorContext"]
