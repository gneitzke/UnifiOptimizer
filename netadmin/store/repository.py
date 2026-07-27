"""The repository: the only module in netadmin that speaks SQL.

Every other layer -- ingest, detectors, issue engine, SLE, fixes, server --
calls :class:`Repository` methods and never touches the database directly. This
is the seam the architecture doc (section 4) reserves for a future
VictoriaMetrics swap: change this file, nothing else.

Responsibilities:

- **Inventory**: :meth:`upsert_entity`, discrete :meth:`record_state_change`
  history (a row is written only when a tracked attribute actually changes).
- **Series interning**: :meth:`intern_series`, backed by an in-memory cache so a
  hot poll cycle does no dimension lookups.
- **Samples**: :meth:`record_samples` computes counter deltas (with counter-reset
  handling) and upserts the hourly *and* daily rollups in the **same
  transaction** as the raw rows.
- **Reads**: :meth:`read_window` serves raw rows for recent windows and falls
  back to hourly/daily rollups for older ones.
- **Events / poll_runs / coverage**: deduped event insert, collector accounting,
  and an expected-coverage helper computed from ``poll_runs``.
- **Retention**: a single :meth:`prune` the nightly scheduler calls.
- **CRUD** for issues, issue_events, changes, baselines, sle_minutes,
  investigations, and incidents/incident_members, used by the
  issue/SLE/fix/LLM/correlation modules.

All timestamps are epoch seconds, UTC. All writes go through
:func:`netadmin.store.db.begin_immediate`; helpers that may run inside a larger
transaction ride it rather than nesting.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence, Union

from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store import db as _db
from netadmin.store.metrics import MetricKind, metric_kind

__all__ = [
    "SampleReading",
    "WindowResult",
    "Repository",
    "HOUR_SECONDS",
    "DAY_SECONDS",
]

HOUR_SECONDS = 3600
DAY_SECONDS = 86400


def _now() -> int:
    return int(time.time())


def _hour_bucket(ts: int) -> int:
    """Start of the UTC hour containing ``ts`` (epoch seconds)."""
    return ts - (ts % HOUR_SECONDS)


def _day_bucket(ts: int) -> int:
    """Start of the UTC day containing ``ts`` (epoch 0 == UTC midnight)."""
    return ts - (ts % DAY_SECONDS)


@dataclass
class SampleReading:
    """One metric reading handed to :meth:`Repository.record_samples`.

    ``value`` is the raw controller reading -- cumulative for counters,
    instantaneous for gauges. The repository consults the metric registry (or an
    explicit ``kind`` override) to decide whether to diff it into a delta.
    """

    entity_id: int
    metric: str
    ts: int
    value: float
    unit: Optional[str] = None
    kind: Optional[MetricKind] = None


@dataclass
class WindowResult:
    """Result of a windowed read: the tier served plus its rows.

    ``tier`` is ``"raw"``, ``"hourly"``, ``"daily"``, or ``"stitched"`` (a window
    that straddles a retention boundary and is served from more than one tier;
    see :meth:`Repository.read_window`). For raw, each row is ``{"ts", "value"}``.
    For rollups, each row is
    ``{"ts", "n", "min", "max", "avg", "sum", "last", "value"}`` where ``value``
    aliases ``sum`` for counter series and ``avg`` for gauges (a counter's
    per-interval deltas sum to the honest bucket total; averaging them is
    meaningless) so callers can treat every tier uniformly. A ``"stitched"``
    result concatenates rows oldest-first, so ``ts`` is still monotonic even
    though the row shapes differ between the rollup and raw segments.
    """

    tier: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def rate(self) -> list[dict[str, Any]]:
        """Per-second rate between consecutive samples, for **counter** series.

        A counter sample (raw ``value`` or a rollup bucket ``sum``) is a raw
        per-interval delta, not a rate (``netadmin.store.metrics`` documents the
        contract). This divides each row's delta by the *actual* elapsed seconds
        since the previous row -- not by an assumed cadence -- so a coalesced or
        gap-widened interval yields the correct rate instead of an inflated one.

        Returns ``[{"ts", "rate"}, ...]``. The first row has no predecessor and
        is omitted; rows with a non-positive elapsed time (duplicate or
        out-of-order ts) are skipped. Calling this on a gauge window is
        meaningless -- gauges are instantaneous, not accumulated -- and the caller
        is responsible for only invoking it on counter series.
        """
        out: list[dict[str, Any]] = []
        prev_ts: Optional[int] = None
        for row in self.rows:
            ts = int(row["ts"])
            # Rollup rows carry the bucket total in "sum"; raw rows carry the
            # per-interval delta in "value".
            delta = row["sum"] if "sum" in row else row.get("value")
            if prev_ts is not None and delta is not None:
                elapsed = ts - prev_ts
                if elapsed > 0:
                    out.append({"ts": ts, "rate": delta / elapsed})
            prev_ts = ts
        return out


class Repository:
    """SQL-facing data access for the whole netadmin core."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        site_id: str = "default",
        retention_raw_days: int = 30,
        retention_hourly_days: int = 548,  # ~18 months
        counter_max_gap_s: int = 150,
    ) -> None:
        self._conn = conn
        self.site_id = site_id
        self.retention_raw_days = retention_raw_days
        self.retention_hourly_days = retention_hourly_days
        # Largest spacing (seconds) between two consecutive counter readings that
        # still yields a real per-interval delta. Beyond it a poll was missed, so
        # emitting ``value - prev`` would fold the whole gap's accumulation into
        # one oversized sample; instead the series re-seeds (see record_samples).
        # Default ~2.5x the 60 s primary device/client cadence; collectors with a
        # coarser counter cadence pass ``max_gap_s`` to record_samples.
        self.counter_max_gap_s = counter_max_gap_s
        # (entity_id, metric) -> series_id
        self._series_cache: dict[tuple[int, str], int] = {}
        # series_id -> metric name, for kind-aware rollup reads (counter vs gauge)
        self._series_metric_cache: dict[int, str] = {}
        # series_id -> (last cumulative counter reading, its ts) for delta
        # computation. In-memory by design: on process restart the first reading
        # per counter series re-seeds the baseline and emits no delta. One
        # interval of loss on restart is acceptable and recovered by backfill.
        self._counter_last: dict[int, tuple[float, int]] = {}

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def open(
        cls,
        db_path: Union[str, Path],
        *,
        site_id: str = "default",
        busy_timeout_ms: int = 5000,
        migrate: bool = True,
        retention_raw_days: int = 30,
        retention_hourly_days: int = 548,
        counter_max_gap_s: int = 150,
        read_only: bool = False,
    ) -> "Repository":
        """Open (and by default migrate) a database, returning a repository.

        ``read_only=True`` opens the file through SQLite's ``mode=ro`` URI with
        ``PRAGMA query_only=ON`` and **forces** ``migrate=False`` -- a read-only
        connection cannot apply a migration, and silently trying would fail
        mid-startup. This is the mode the MCP server uses to read a store the
        daemon owns (``docs/MCP_SERVER.md`` section 1); every write method on the
        returned repository raises ``sqlite3.OperationalError`` rather than
        relying on callers to behave.
        """
        conn = _db.connect(db_path, busy_timeout_ms=busy_timeout_ms, read_only=read_only)
        if migrate and not read_only:
            _db.apply_migrations(conn)
        return cls(
            conn,
            site_id=site_id,
            retention_raw_days=retention_raw_days,
            retention_hourly_days=retention_hourly_days,
            counter_max_gap_s=counter_max_gap_s,
        )

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection (for tests and advanced callers)."""
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Transaction plumbing
    # ------------------------------------------------------------------ #

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Open a write transaction, or ride the one already open.

        Lets a coarse method (``record_samples``) wrap several finer writes in a
        single ``BEGIN IMMEDIATE`` without those writes trying to nest their own.
        """
        if self._conn.in_transaction:
            yield self._conn
        else:
            with _db.begin_immediate(self._conn):
                yield self._conn

    @contextmanager
    def transaction(self) -> Iterator["Repository"]:
        """Public cycle-spanning write transaction (ARCHITECTURE.md section 4).

        "One poll cycle = one transaction." Wrapping several repository calls in
        this context opens a single ``BEGIN IMMEDIATE`` up front; every inner
        write (``upsert_entity``, ``sync_entity_state``, ``record_samples`` and
        its rollups, ...) rides that transaction via :meth:`_write` instead of
        committing on its own, so a cycle commits its inventory, state changes,
        samples and rollups atomically or not at all.

        Rides an already-open transaction (idempotent nesting). Callers must do
        their controller I/O *before* entering this block -- holding the write
        lock across a network round-trip would stall every other writer -- and
        should keep ``record_samples`` the last write in the block: its own
        in-memory counter/series-cache rollback only fires when it raises, so a
        rollback triggered by a later statement would leave those caches ahead of
        the committed rows.
        """
        if self._conn.in_transaction:
            yield self
        else:
            with _db.begin_immediate(self._conn):
                yield self

    # ------------------------------------------------------------------ #
    # Inventory: entities + discrete state history
    # ------------------------------------------------------------------ #

    def upsert_entity(self, entity: Entity, *, ts: Optional[int] = None) -> int:
        """Insert or update an entity by (site_id, entity_type, native_id).

        Sets ``entity.entity_id`` and returns it. ``first_seen_ts`` is preserved
        across updates; ``last_seen_ts`` advances to ``ts``.
        """
        ts = _now() if ts is None else ts
        site = entity.site_id or self.site_id
        etype = (
            entity.entity_type.value
            if isinstance(entity.entity_type, EntityType)
            else str(entity.entity_type)
        )
        meta_json = json.dumps(entity.meta or {}, sort_keys=True)

        with self._write() as conn:
            row = conn.execute(
                "SELECT entity_id FROM entities "
                "WHERE site_id=? AND entity_type=? AND native_id=?",
                (site, etype, entity.native_id),
            ).fetchone()
            if row is not None:
                entity_id = int(row["entity_id"])
                conn.execute(
                    "UPDATE entities SET parent_id=?, name=?, model=?, meta=?, last_seen_ts=? "
                    "WHERE entity_id=?",
                    (entity.parent_id, entity.name, entity.model, meta_json, ts, entity_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO entities "
                    "(site_id, entity_type, native_id, parent_id, name, model, "
                    " first_seen_ts, last_seen_ts, meta) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        site,
                        etype,
                        entity.native_id,
                        entity.parent_id,
                        entity.name,
                        entity.model,
                        entity.first_seen_ts or ts,
                        ts,
                        meta_json,
                    ),
                )
                entity_id = int(cur.lastrowid)

        entity.entity_id = entity_id
        entity.site_id = site
        return entity_id

    def get_entity(self, entity_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM entities WHERE entity_id=?", (entity_id,)
        ).fetchone()

    def find_entity(
        self, entity_type: Union[EntityType, str], native_id: str, *, site_id: Optional[str] = None
    ) -> Optional[sqlite3.Row]:
        site = site_id or self.site_id
        etype = entity_type.value if isinstance(entity_type, EntityType) else str(entity_type)
        return self._conn.execute(
            "SELECT * FROM entities WHERE site_id=? AND entity_type=? AND native_id=?",
            (site, etype, native_id),
        ).fetchone()

    def list_entities(
        self, entity_type: Optional[Union[EntityType, str]] = None, *, site_id: Optional[str] = None
    ) -> list[sqlite3.Row]:
        site = site_id or self.site_id
        if entity_type is None:
            return self._conn.execute(
                "SELECT * FROM entities WHERE site_id=? ORDER BY entity_id", (site,)
            ).fetchall()
        etype = entity_type.value if isinstance(entity_type, EntityType) else str(entity_type)
        return self._conn.execute(
            "SELECT * FROM entities WHERE site_id=? AND entity_type=? ORDER BY entity_id",
            (site, etype),
        ).fetchall()

    def current_state(self, entity_id: int, attr: str) -> Optional[str]:
        """Latest recorded value of a tracked attribute, or None if never seen."""
        row = self._conn.execute(
            "SELECT new_value FROM state_changes WHERE entity_id=? AND attr=? "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (entity_id, attr),
        ).fetchone()
        return None if row is None else row["new_value"]

    def record_state_change(
        self, entity_id: int, attr: str, new_value: Optional[str], ts: Optional[int] = None
    ) -> bool:
        """Record a discrete state change **only if the value actually changed**.

        Compares ``new_value`` against the last recorded value for
        ``(entity_id, attr)``. Returns True when a row was written, False when
        the value was unchanged (no-op). ``new_value`` is coerced to ``str`` so
        callers can pass ints/bools (link speed, up/down) directly.
        """
        ts = _now() if ts is None else ts
        normalized = None if new_value is None else str(new_value)
        with self._write() as conn:
            prev = conn.execute(
                "SELECT new_value FROM state_changes WHERE entity_id=? AND attr=? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (entity_id, attr),
            ).fetchone()
            prev_value = None if prev is None else prev["new_value"]
            if prev is not None and prev_value == normalized:
                return False
            conn.execute(
                "INSERT INTO state_changes (entity_id, attr, old_value, new_value, ts) "
                "VALUES (?,?,?,?,?)",
                (entity_id, attr, prev_value, normalized, ts),
            )
            return True

    def sync_entity_state(
        self, entity_id: int, attrs: dict[str, Any], ts: Optional[int] = None
    ) -> list[str]:
        """Apply many tracked attributes at once; return the ones that changed."""
        ts = _now() if ts is None else ts
        changed: list[str] = []
        with self._write():
            for attr, value in attrs.items():
                if self.record_state_change(entity_id, attr, value, ts=ts):
                    changed.append(attr)
        return changed

    def state_history(
        self, entity_id: int, attr: Optional[str] = None, *, limit: int = 100
    ) -> list[sqlite3.Row]:
        if attr is None:
            return self._conn.execute(
                "SELECT * FROM state_changes WHERE entity_id=? ORDER BY ts DESC, id DESC LIMIT ?",
                (entity_id, limit),
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM state_changes WHERE entity_id=? AND attr=? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (entity_id, attr, limit),
        ).fetchall()

    def list_state_changes(
        self,
        start_ts: int,
        end_ts: int,
        *,
        entity_id: Optional[int] = None,
        attr: Optional[str] = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        """Every tracked-attribute change in ``[start_ts, end_ts)``, newest first.

        :meth:`state_history` answers "what did *this* entity do"; this answers
        "what changed on the network at all" -- the site-wide firmware / channel /
        link-speed / up-down timeline the MCP server's ``netadmin_what_changed``
        tool renders next to the fix ledger (``docs/MCP_SERVER.md`` section 2).
        Optional ``entity_id`` / ``attr`` narrow it; ``limit`` is a hard cap so a
        wide window on a churny site cannot ship an unbounded result.

        The site-wide form scans by ``ts`` rather than riding
        ``idx_state_entity_ts`` (which leads with ``entity_id``). That is
        deliberate and cheap: ``state_changes`` only gains a row when a value
        *actually* changes, and ``prune`` trims it on the raw-retention schedule,
        so the table stays small by construction.
        """
        clauses = ["ts>=?", "ts<?"]
        params: list[Any] = [int(start_ts), int(end_ts)]
        if entity_id is not None:
            clauses.append("entity_id=?")
            params.append(int(entity_id))
        if attr is not None:
            clauses.append("attr=?")
            params.append(attr)
        params.append(max(1, int(limit)))
        where = " AND ".join(clauses)
        return self._conn.execute(
            f"SELECT * FROM state_changes WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
            params,
        ).fetchall()

    # ------------------------------------------------------------------ #
    # Series interning
    # ------------------------------------------------------------------ #

    def intern_series(self, entity_id: int, metric: str, unit: Optional[str] = None) -> int:
        """Return the series_id for ``(entity_id, metric)``, creating it once.

        Cached in memory; a cache hit does zero SQL. First miss does a SELECT,
        then an INSERT if still absent. Runs in autocommit when called on its own
        and rides any open transaction otherwise.
        """
        key = (entity_id, metric)
        cached = self._series_cache.get(key)
        if cached is not None:
            return cached

        row = self._conn.execute(
            "SELECT series_id FROM series WHERE entity_id=? AND metric=?",
            (entity_id, metric),
        ).fetchone()
        if row is not None:
            series_id = int(row["series_id"])
        else:
            cur = self._conn.execute(
                "INSERT INTO series (entity_id, metric, unit) VALUES (?,?,?)",
                (entity_id, metric, unit),
            )
            series_id = int(cur.lastrowid)

        self._series_cache[key] = series_id
        return series_id

    def get_series(self, entity_id: int, metric: str) -> Optional[int]:
        """series_id for an existing series, or None. Does not create."""
        key = (entity_id, metric)
        if key in self._series_cache:
            return self._series_cache[key]
        row = self._conn.execute(
            "SELECT series_id FROM series WHERE entity_id=? AND metric=?",
            (entity_id, metric),
        ).fetchone()
        if row is None:
            return None
        series_id = int(row["series_id"])
        self._series_cache[key] = series_id
        return series_id

    def _series_metric(self, series_id: int) -> Optional[str]:
        """Metric name for a series_id (cached), or None if the series is unknown.

        Used by :meth:`read_rollup` to decide, from the metric registry, whether
        a bucket's headline ``value`` should be its ``sum`` (counters) or ``avg``
        (gauges).
        """
        cached = self._series_metric_cache.get(series_id)
        if cached is not None:
            return cached
        row = self._conn.execute(
            "SELECT metric FROM series WHERE series_id=?", (series_id,)
        ).fetchone()
        if row is None:
            return None
        metric = str(row["metric"])
        self._series_metric_cache[series_id] = metric
        return metric

    # ------------------------------------------------------------------ #
    # Samples: raw + counter deltas + rollups (all one transaction)
    # ------------------------------------------------------------------ #

    def record_samples(
        self, readings: Iterable[SampleReading], *, max_gap_s: Optional[int] = None
    ) -> int:
        """Ingest a batch of readings; return the count of raw rows written.

        For each reading:

        - **Gauge**: stored verbatim.
        - **Counter**: stored as the delta from the previous reading of the same
          series. A negative delta means the counter reset (device reboot); the
          new cumulative value is then stored as the delta (``treat as
          new_value``). The very first reading of a counter series only seeds the
          baseline and writes no row. If more than ``max_gap_s`` seconds elapsed
          since that series' previous reading (a missed poll), the series
          **re-seeds** just like a first reading and writes no row, rather than
          emitting one gap-spanning delta that would poison the hour/day rollups
          and the baselines built from them.

        ``max_gap_s`` defaults to :attr:`counter_max_gap_s`; a collector polling a
        counter on a coarser cadence passes its own (``~2x`` the poll interval).

        Every raw row written also upserts its hourly and daily rollup rows
        (n/min/max/avg/sum/last) **inside the same transaction**, so a poll cycle
        never leaves raw and rolled-up data inconsistent. Duplicate
        ``(series_id, ts)`` rows are ignored and do not double-count rollups.
        """
        gap_limit = self.counter_max_gap_s if max_gap_s is None else max_gap_s
        readings = list(readings)
        if not readings:
            return 0

        written = 0
        # In-memory state (series cache, counter baselines) is mutated inside the
        # write transaction below; if that transaction rolls back, the DB is
        # reverted but Python memory is not. Track what we touch so we can undo
        # it on failure and keep the cache/baselines consistent with the store.
        new_cache_keys: list[tuple[int, str]] = []
        counter_backup: dict[int, Optional[tuple[float, int]]] = {}
        try:
            # One poll cycle = one transaction (ARCHITECTURE.md section 4). Series
            # interning rides the *same* BEGIN IMMEDIATE as the sample + rollup
            # writes -- not separate autocommit INSERTs -- so a new series and the
            # samples that reference it commit or roll back atomically.
            with self._write() as conn:
                series_ids: list[int] = []
                for reading in readings:
                    key = (reading.entity_id, reading.metric)
                    if key not in self._series_cache:
                        new_cache_keys.append(key)
                    series_ids.append(
                        self.intern_series(reading.entity_id, reading.metric, reading.unit)
                    )

                for reading, series_id in zip(readings, series_ids):
                    kind = reading.kind or metric_kind(reading.metric)
                    if kind is MetricKind.COUNTER:
                        if series_id not in counter_backup:
                            counter_backup[series_id] = self._counter_last.get(series_id)
                        prev = self._counter_last.get(series_id)
                        self._counter_last[series_id] = (reading.value, reading.ts)
                        if prev is None:
                            continue  # seed baseline only
                        prev_value, prev_ts = prev
                        if gap_limit > 0 and (reading.ts - prev_ts) > gap_limit:
                            # Missed poll(s): re-seed instead of emitting a
                            # gap-spanning delta that would bake into the rollups.
                            continue
                        delta = reading.value - prev_value
                        if delta < 0:  # counter reset -> treat new cumulative as the delta
                            delta = reading.value
                        store_value = float(delta)
                    else:
                        store_value = float(reading.value)

                    if self._insert_raw(conn, series_id, reading.ts, store_value):
                        self._upsert_rollup(
                            conn, "samples_hourly", series_id, _hour_bucket(reading.ts), store_value
                        )
                        self._upsert_rollup(
                            conn, "samples_daily", series_id, _day_bucket(reading.ts), store_value
                        )
                        written += 1
        except BaseException:
            # The transaction rolled back: undo the in-memory mutations so the
            # cache never points at a series_id that no longer exists and a
            # counter baseline never reflects an un-stored reading.
            for key in new_cache_keys:
                self._series_cache.pop(key, None)
            for series_id, prev in counter_backup.items():
                if prev is None:
                    self._counter_last.pop(series_id, None)
                else:
                    self._counter_last[series_id] = prev
            raise
        return written

    @staticmethod
    def _insert_raw(conn: sqlite3.Connection, series_id: int, ts: int, value: float) -> bool:
        """Insert a raw sample; return True if inserted, False if a dup was ignored."""
        cur = conn.execute(
            "INSERT OR IGNORE INTO samples (series_id, ts, value) VALUES (?,?,?)",
            (series_id, ts, value),
        )
        return cur.rowcount > 0

    @staticmethod
    def _upsert_rollup(
        conn: sqlite3.Connection, table: str, series_id: int, bucket_ts: int, value: float
    ) -> None:
        """Fold one value into a rollup bucket (n/min/max/avg/sum/last).

        ``avg`` is recomputed as ``(sum + value) / (n + 1)`` from the pre-update
        row so it stays exact without a second pass. ``last`` is set to the new
        value: ingest is forward in time, so the most recently folded value is
        the latest.
        """
        conn.execute(
            f"INSERT INTO {table} (series_id, bucket_ts, n, min, max, avg, sum, last) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(series_id, bucket_ts) DO UPDATE SET "
            "  n = n + 1, "
            "  min = MIN(min, excluded.min), "
            "  max = MAX(max, excluded.max), "
            "  sum = sum + excluded.sum, "
            "  avg = (sum + excluded.sum) / (n + 1), "
            "  last = excluded.last",
            (series_id, bucket_ts, value, value, value, value, value),
        )

    # ------------------------------------------------------------------ #
    # Windowed reads
    # ------------------------------------------------------------------ #

    def read_raw(self, series_id: int, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ts, value FROM samples WHERE series_id=? AND ts>=? AND ts<? ORDER BY ts",
            (series_id, start_ts, end_ts),
        ).fetchall()
        return [{"ts": int(r["ts"]), "value": r["value"]} for r in rows]

    def max_sample_ts(
        self, entity_type: Union[EntityType, str], *, site_id: Optional[str] = None
    ) -> Optional[int]:
        """Newest raw-sample ts across all series of one entity type, or None.

        The startup/incremental backfill (ARCHITECTURE.md 5.3) needs the last
        stored sample ts per report *scope* to compute the gap it must refill.
        A report scope maps to an entity type (ap->AP, user->CLIENT, gw->GATEWAY),
        so this collapses ``entities -> series -> samples`` to the single MAX(ts)
        the backfiller keys off. Returns None when the type has no samples yet
        (a fresh install -> pull the full retained history).
        """
        site = site_id or self.site_id
        etype = entity_type.value if isinstance(entity_type, EntityType) else str(entity_type)
        row = self._conn.execute(
            "SELECT MAX(s.ts) AS m FROM samples s "
            "JOIN series se ON se.series_id = s.series_id "
            "JOIN entities e ON e.entity_id = se.entity_id "
            "WHERE e.site_id=? AND e.entity_type=?",
            (site, etype),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def max_sample_ts_for_metrics(
        self,
        entity_type: Union[EntityType, str],
        metrics: Sequence[str],
        *,
        site_id: Optional[str] = None,
    ) -> Optional[int]:
        """Newest raw-sample ts across the given metrics of one entity type.

        Unlike :meth:`max_sample_ts` (which collapses *every* series of a type to
        one MAX), this restricts to a named metric set, so the report backfill can
        anchor its gap on the report-only series that have no live source
        (``rx_bytes``/``tx_bytes`` on the ap/user entity, ``wan_*``/``lan_*`` bytes
        on the gateway). Anchoring on the whole entity type instead would read the
        live 60 s cpu/rssi series and see the report gap as permanently closed
        (ARCHITECTURE.md 5.3). Returns None when none of the metrics has a sample
        yet (a fresh install -> pull the full retained history).
        """
        metric_list = list(metrics)
        if not metric_list:
            return None
        site = site_id or self.site_id
        etype = entity_type.value if isinstance(entity_type, EntityType) else str(entity_type)
        placeholders = ",".join("?" for _ in metric_list)
        row = self._conn.execute(
            "SELECT MAX(s.ts) AS m FROM samples s "
            "JOIN series se ON se.series_id = s.series_id "
            "JOIN entities e ON e.entity_id = se.entity_id "
            f"WHERE e.site_id=? AND e.entity_type=? AND se.metric IN ({placeholders})",
            (site, etype, *metric_list),
        ).fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    def read_rollup(
        self, series_id: int, tier: str, start_ts: int, end_ts: int
    ) -> list[dict[str, Any]]:
        """Read hourly or daily rollup buckets in ``[start_ts, end_ts)``.

        The headline ``value`` alias is **metric-kind-aware** (ARCHITECTURE.md
        section 4): a counter series stores per-interval deltas, whose honest
        bucket aggregate is the total (``sum``) -- averaging deltas is
        meaningless. Gauge series alias ``value`` to ``avg`` as before. The full
        ``n/min/max/avg/sum/last`` columns are always present so a caller that
        wants a different aggregate has it.
        """
        table = {"hourly": "samples_hourly", "daily": "samples_daily"}[tier]
        metric = self._series_metric(series_id)
        is_counter = metric is not None and metric_kind(metric) is MetricKind.COUNTER
        value_col = "sum" if is_counter else "avg"
        rows = self._conn.execute(
            f"SELECT bucket_ts, n, min, max, avg, sum, last FROM {table} "
            "WHERE series_id=? AND bucket_ts>=? AND bucket_ts<? ORDER BY bucket_ts",
            (series_id, start_ts, end_ts),
        ).fetchall()
        return [
            {
                "ts": int(r["bucket_ts"]),
                "n": int(r["n"]),
                "min": r["min"],
                "max": r["max"],
                "avg": r["avg"],
                "sum": r["sum"],
                "last": r["last"],
                "value": r[value_col],
            }
            for r in rows
        ]

    def read_window(
        self, series_id: int, start_ts: int, end_ts: int, *, now: Optional[int] = None
    ) -> WindowResult:
        """Serve a window from the finest tier whose data still exists, stitching
        across a retention boundary the window straddles.

        Each portion of ``[start_ts, end_ts)`` is served from the finest tier
        that still retains it: raw for the last 30 days, hourly for the last 18
        months, daily forever. A window entirely inside one tier returns that
        tier verbatim. A window that *straddles* a boundary -- e.g. it starts 45
        days ago (raw already pruned there) and ends now -- is **stitched**: the
        old portion comes from the coarser rollup and the recent portion from raw,
        rather than silently serving the whole span as coarse rollup and throwing
        away the fine detail that still exists for the recent part. Rows are
        concatenated oldest-first so ``ts`` stays monotonic; ``tier`` is the
        single tier when only one was used, else ``"stitched"``.
        """
        now = _now() if now is None else now
        raw_floor = now - self.retention_raw_days * DAY_SECONDS
        hourly_floor = now - self.retention_hourly_days * DAY_SECONDS

        rows: list[dict[str, Any]] = []
        tiers_used: list[str] = []

        # Oldest first: daily (pre-hourly-retention), then hourly, then raw.
        daily_hi = min(end_ts, hourly_floor)
        if start_ts < daily_hi:
            daily_rows = self.read_rollup(series_id, "daily", start_ts, daily_hi)
            if daily_rows:
                tiers_used.append("daily")
            rows.extend(daily_rows)

        hourly_lo = max(start_ts, hourly_floor)
        hourly_hi = min(end_ts, raw_floor)
        if hourly_lo < hourly_hi:
            hourly_rows = self.read_rollup(series_id, "hourly", hourly_lo, hourly_hi)
            if hourly_rows:
                tiers_used.append("hourly")
            rows.extend(hourly_rows)

        raw_lo = max(start_ts, raw_floor)
        if raw_lo < end_ts:
            raw_rows = self.read_raw(series_id, raw_lo, end_ts)
            if raw_rows:
                tiers_used.append("raw")
            rows.extend(raw_rows)

        # Which tiers the window *spans*, independent of whether each held rows,
        # so an empty stitched window still reports the boundary it crossed and a
        # single-tier window keeps its plain tier label for callers that branch on
        # it. Boundaries touched, not rows returned, define the tier.
        spanned: list[str] = []
        if start_ts < min(end_ts, hourly_floor):
            spanned.append("daily")
        if max(start_ts, hourly_floor) < min(end_ts, raw_floor):
            spanned.append("hourly")
        if max(start_ts, raw_floor) < end_ts:
            spanned.append("raw")

        if len(spanned) == 1:
            return WindowResult(spanned[0], rows)
        if not spanned:
            # Degenerate/empty window: fall back to the finest tier by start.
            tier = (
                "raw"
                if start_ts >= raw_floor
                else "hourly"
                if start_ts >= hourly_floor
                else "daily"
            )
            return WindowResult(tier, rows)
        return WindowResult("stitched", rows)

    # ------------------------------------------------------------------ #
    # Events (deduped on native controller event id)
    # ------------------------------------------------------------------ #

    def record_event(
        self,
        *,
        ts: int,
        key: str,
        entity_id: Optional[int] = None,
        related_entity_id: Optional[int] = None,
        native_id: Optional[str] = None,
        msg: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        """Insert one event, deduping on ``native_id`` when present.

        Returns the new row id, or None if an event with the same ``native_id``
        already exists. Events with no ``native_id`` are always inserted (the WS
        stream sometimes lacks one; those cannot be deduped by id).
        """
        data_json = json.dumps(data or {}, sort_keys=True)
        with self._write() as conn:
            if native_id is not None:
                exists = conn.execute(
                    "SELECT 1 FROM events WHERE native_id=? LIMIT 1", (native_id,)
                ).fetchone()
                if exists is not None:
                    return None
            cur = conn.execute(
                "INSERT INTO events (ts, key, entity_id, related_entity_id, native_id, msg, data) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, key, entity_id, related_entity_id, native_id, msg, data_json),
            )
            return int(cur.lastrowid)

    def record_events(self, events: Sequence[dict[str, Any]]) -> int:
        """Insert a batch of event dicts (same keys as :meth:`record_event`).

        Returns the number actually inserted (deduped ones are skipped). The
        whole batch is one transaction.
        """
        inserted = 0
        with self._write():
            for ev in events:
                if self.record_event(**ev) is not None:
                    inserted += 1
        return inserted

    def read_events(
        self,
        start_ts: int,
        end_ts: int,
        *,
        entity_id: Optional[int] = None,
        key: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        clauses = ["ts>=?", "ts<?"]
        params: list[Any] = [start_ts, end_ts]
        if entity_id is not None:
            clauses.append("entity_id=?")
            params.append(entity_id)
        if key is not None:
            clauses.append("key=?")
            params.append(key)
        where = " AND ".join(clauses)
        return self._conn.execute(
            f"SELECT * FROM events WHERE {where} ORDER BY ts", params
        ).fetchall()

    def max_event_ts(self) -> Optional[int]:
        """Timestamp (epoch s) of the most recent stored event, or None if empty.

        The catch-up cursor (``events.catchup_events``) reads this instead of
        loading the whole events table just to inspect the last ts. ``MAX(ts)``
        is answered from ``idx_events_ts`` without materializing any rows.
        """
        row = self._conn.execute("SELECT MAX(ts) AS m FROM events").fetchone()
        return None if row is None or row["m"] is None else int(row["m"])

    # ------------------------------------------------------------------ #
    # Collector accounting + coverage
    # ------------------------------------------------------------------ #

    def record_poll_run(
        self,
        *,
        job: str,
        ok: bool,
        ts: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
        source: str = "live",
    ) -> None:
        """Record one collector cycle outcome (the source of truth for gaps)."""
        ts = _now() if ts is None else ts
        with self._write() as conn:
            conn.execute(
                "INSERT INTO poll_runs (ts, job, ok, duration_ms, error, source) "
                "VALUES (?,?,?,?,?,?)",
                (ts, job, 1 if ok else 0, duration_ms, error, source),
            )

    def read_poll_runs(
        self, job: str, start_ts: int, end_ts: int, *, ok_only: bool = False
    ) -> list[sqlite3.Row]:
        clause = "AND ok=1" if ok_only else ""
        return self._conn.execute(
            f"SELECT * FROM poll_runs WHERE job=? AND ts>=? AND ts<? {clause} ORDER BY ts",
            (job, start_ts, end_ts),
        ).fetchall()

    def expected_coverage(
        self,
        job: str,
        start_ts: int,
        end_ts: int,
        interval_s: int,
        *,
        source: Optional[str] = "live",
    ) -> float:
        """Fraction of expected polls that actually succeeded in a window.

        Expected count is ``(end - start) / interval_s`` for a job that should
        run every ``interval_s`` seconds; observed is the number of *successful*
        ``poll_runs`` rows for that job in the window. Returns a value in
        ``[0.0, 1.0]`` (clamped). Detectors treat a window under 0.5 as UNKNOWN
        rather than OK -- a gap is measured here, never inferred from missing
        samples. Returns ``0.0`` for a non-positive window or interval.

        ``source`` defaults to ``"live"``: backfilled polls (``source='backfill'``)
        are coarser evidence than live collection and must **not** be counted as
        live coverage (ARCHITECTURE.md section 4: "detectors treat backfilled
        intervals as partial evidence"). Counting them as live would let a gap
        that backfill only partially reconstructed read as fully covered. Pass
        ``source=None`` to count every successful poll regardless of source, or
        an explicit source string to measure one tier; see
        :meth:`coverage_breakdown` for both at once.
        """
        if interval_s <= 0 or end_ts <= start_ts:
            return 0.0
        expected = (end_ts - start_ts) / interval_s
        if expected <= 0:
            return 0.0
        sql = "SELECT COUNT(*) AS c FROM poll_runs WHERE job=? AND ok=1 AND ts>=? AND ts<?"
        params: list[Any] = [job, start_ts, end_ts]
        if source is not None:
            sql += " AND source=?"
            params.append(source)
        row = self._conn.execute(sql, params).fetchone()
        observed = int(row["c"])
        return min(1.0, observed / expected)

    def coverage_breakdown(
        self, job: str, start_ts: int, end_ts: int, interval_s: int
    ) -> dict[str, float]:
        """Live, backfill, and total coverage fractions for a window, at once.

        Returns ``{"live", "backfill", "total"}``, each clamped to ``[0, 1]``.
        The live fraction is the one detectors gate on; ``backfill`` and ``total``
        let a caller see how much of a gap was reconstructed from controller
        history versus collected live, so backfilled evidence can be weighted
        down rather than trusted as if it were live.
        """
        return {
            "live": self.expected_coverage(job, start_ts, end_ts, interval_s, source="live"),
            "backfill": self.expected_coverage(
                job, start_ts, end_ts, interval_s, source="backfill"
            ),
            "total": self.expected_coverage(job, start_ts, end_ts, interval_s, source=None),
        }

    # ------------------------------------------------------------------ #
    # App metadata (generic key/value cache; migration 0007)
    # ------------------------------------------------------------------ #

    def get_app_meta(self, key: str) -> Optional[str]:
        """A cached value by key, or ``None`` if never set."""
        row = self._conn.execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_app_meta(self, key: str, value: str) -> None:
        """Upsert one key/value pair (e.g. the self-update version-check cache)."""
        with self._write() as conn:
            conn.execute(
                "INSERT INTO app_meta (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ #
    # Retention
    # ------------------------------------------------------------------ #

    def prune(
        self,
        *,
        now: Optional[int] = None,
        raw_before: Optional[int] = None,
        hourly_before: Optional[int] = None,
    ) -> dict[str, int]:
        """Nightly retention pass: drop everything past its tier's window.

        - ``samples`` (raw metrics), ``poll_runs``, ``events`` and
          ``state_changes`` are all **raw-tier** append-only logs: they share the
          30-day raw retention window. Without pruning them they grow without
          bound on a long-running install (``poll_runs`` alone gets one row per
          job per cadence, forever) -- the raw-tier evidence they hold is only
          needed while raw samples exist to correlate against.
        - ``samples_hourly`` keeps 18 months.
        - Daily rollups are kept forever (year-over-year comparisons).

        Cutoffs default to ``now`` minus the configured retention, but can be
        passed explicitly for tests. Returns a dict of deleted row counts per
        table. One transaction.
        """
        now = _now() if now is None else now
        raw_before = (
            now - self.retention_raw_days * DAY_SECONDS if raw_before is None else raw_before
        )
        hourly_before = (
            now - self.retention_hourly_days * DAY_SECONDS
            if hourly_before is None
            else hourly_before
        )
        with self._write() as conn:
            raw_deleted = conn.execute("DELETE FROM samples WHERE ts < ?", (raw_before,)).rowcount
            hourly_deleted = conn.execute(
                "DELETE FROM samples_hourly WHERE bucket_ts < ?", (hourly_before,)
            ).rowcount
            poll_runs_deleted = conn.execute(
                "DELETE FROM poll_runs WHERE ts < ?", (raw_before,)
            ).rowcount
            events_deleted = conn.execute("DELETE FROM events WHERE ts < ?", (raw_before,)).rowcount
            # state_changes is pruned past the raw window too, but the *latest*
            # row per (entity, attr) is always kept even when it is older than the
            # window: it is the current value current_state() reads back, so
            # dropping it would erase the entity's present state (e.g. a firmware
            # set once a year ago and never changed since). Only superseded
            # history past the window is discarded.
            state_changes_deleted = conn.execute(
                "DELETE FROM state_changes WHERE ts < ? AND id NOT IN ("
                "  SELECT MAX(id) FROM state_changes GROUP BY entity_id, attr"
                ")",
                (raw_before,),
            ).rowcount
        return {
            "raw": int(raw_deleted),
            "hourly": int(hourly_deleted),
            "poll_runs": int(poll_runs_deleted),
            "events": int(events_deleted),
            "state_changes": int(state_changes_deleted),
        }

    # ------------------------------------------------------------------ #
    # Issues + issue_events
    # ------------------------------------------------------------------ #

    def get_issue(self, issue_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()

    def get_open_issue(self, fingerprint: str) -> Optional[sqlite3.Row]:
        """The single non-resolved issue for a fingerprint, if any.

        The partial unique index ``idx_issues_open_fp`` guarantees at most one.
        """
        return self._conn.execute(
            "SELECT * FROM issues WHERE fingerprint=? AND state != 'resolved' LIMIT 1",
            (fingerprint,),
        ).fetchone()

    def get_recent_resolved_issue(
        self, fingerprint: str, resolved_since_ts: int
    ) -> Optional[sqlite3.Row]:
        """Most recently resolved issue for a fingerprint, resolved at or after
        ``resolved_since_ts`` (the reopen-window floor), or ``None``.

        Filtered and ordered in SQL against ``idx_issues_fp_resolved``
        (``fingerprint, resolved_ts``); the issue engine calls this on every
        new-fingerprint fire, so it must not load and scan the whole resolved
        history (which is never pruned).
        """
        return self._conn.execute(
            "SELECT * FROM issues "
            "WHERE fingerprint=? AND state='resolved' "
            "AND resolved_ts IS NOT NULL AND resolved_ts>=? "
            "ORDER BY resolved_ts DESC, id DESC LIMIT 1",
            (fingerprint, resolved_since_ts),
        ).fetchone()

    def insert_issue(
        self,
        *,
        fingerprint: str,
        detector_key: str,
        severity: str,
        state: str,
        first_seen_ts: int,
        last_seen_ts: int,
        title: str,
        entity_id: Optional[int] = None,
        evidence: Optional[dict[str, Any]] = None,
        clear_streak: int = 0,
        occurrences: int = 1,
        resolved_ts: Optional[int] = None,
        ack_ts: Optional[int] = None,
        snooze_until_ts: Optional[int] = None,
        fix_state: Optional[str] = None,
        reopened_from: Optional[int] = None,
    ) -> int:
        # NOT sort_keys: each detector writes its evidence dict in narrative order
        # (headline measurement, then its comparison, then supporting facts) and
        # the issue detail page renders it in that same order — alphabetising here
        # would silently throw that ordering away on every write.
        evidence_json = json.dumps(evidence or {})
        with self._write() as conn:
            cur = conn.execute(
                "INSERT INTO issues "
                "(fingerprint, detector_key, entity_id, severity, state, first_seen_ts, "
                " last_seen_ts, resolved_ts, clear_streak, occurrences, ack_ts, "
                " snooze_until_ts, title, evidence, fix_state, reopened_from) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fingerprint,
                    detector_key,
                    entity_id,
                    severity,
                    state,
                    first_seen_ts,
                    last_seen_ts,
                    resolved_ts,
                    clear_streak,
                    occurrences,
                    ack_ts,
                    snooze_until_ts,
                    title,
                    evidence_json,
                    fix_state,
                    reopened_from,
                ),
            )
            return int(cur.lastrowid)

    _ISSUE_COLUMNS = frozenset(
        {
            "fingerprint",
            "detector_key",
            "entity_id",
            "severity",
            "state",
            "first_seen_ts",
            "last_seen_ts",
            "resolved_ts",
            "clear_streak",
            "occurrences",
            "ack_ts",
            "snooze_until_ts",
            "title",
            "evidence",
            "fix_state",
            "reopened_from",
        }
    )

    def update_issue(self, issue_id: int, **fields: Any) -> None:
        """Update named columns on an issue. ``evidence`` is JSON-encoded if a dict."""
        if not fields:
            return
        unknown = set(fields) - self._ISSUE_COLUMNS
        if unknown:
            raise ValueError(f"unknown issue column(s): {sorted(unknown)}")
        if isinstance(fields.get("evidence"), (dict, list)):
            # Preserve the detector's narrative key order (see insert_issue).
            fields["evidence"] = json.dumps(fields["evidence"])
        assignments = ", ".join(f"{col}=?" for col in fields)
        params = list(fields.values()) + [issue_id]
        with self._write() as conn:
            conn.execute(f"UPDATE issues SET {assignments} WHERE id=?", params)

    def delete_issue(self, issue_id: int) -> None:
        """Delete an issue and its ``issue_events`` trail, in one transaction.

        Used by the issue engine to discard an unconfirmed ``pending`` issue that
        clears before reaching M (section 7): deleting the row -- rather than
        resolving it -- keeps a later refire from reopening straight to active and
        skipping the M gate. SQL lives here, in the repository, by design (section
        4): callers reach this only through the ``delete_issue`` seam.
        """
        with self._write() as conn:
            conn.execute("DELETE FROM issue_events WHERE issue_id=?", (issue_id,))
            conn.execute("DELETE FROM issues WHERE id=?", (issue_id,))

    def list_issues(
        self,
        *,
        state: Optional[str] = None,
        severity: Optional[str] = None,
        entity_id: Optional[int] = None,
        open_only: bool = False,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if open_only:
            clauses.append("state != 'resolved'")
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        if severity is not None:
            clauses.append("severity=?")
            params.append(severity)
        if entity_id is not None:
            clauses.append("entity_id=?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._conn.execute(
            f"SELECT * FROM issues {where} ORDER BY last_seen_ts DESC, id DESC", params
        ).fetchall()

    def list_issue_history(self, fingerprint: str, *, limit: int = 50) -> list[sqlite3.Row]:
        """Every issue ever recorded for a fingerprint, most recent onset first.

        :meth:`get_open_issue` finds the one live instance and
        :meth:`get_recent_resolved_issue` the one reopen candidate; this returns
        the whole recurrence chain, resolved instances included, which is what
        "has this happened before?" actually means (``docs/MCP_SERVER.md``
        section 2, tool 3). Each row carries its own ``first_seen_ts`` /
        ``resolved_ts``, so the caller derives per-occurrence durations without a
        second query. Ordered by onset (not ``last_seen_ts``) because a
        recurrence chain reads as a sequence of *starts*; ``limit`` caps a
        pathologically flappy fingerprint.
        """
        return self._conn.execute(
            "SELECT * FROM issues WHERE fingerprint=? "
            "ORDER BY first_seen_ts DESC, id DESC LIMIT ?",
            (fingerprint, max(1, int(limit))),
        ).fetchall()

    def list_issues_for_entities(self, entity_ids: Iterable[int]) -> dict[int, list[sqlite3.Row]]:
        """Group every issue for a set of entities by ``entity_id`` in one query.

        The dossier's related-issues section correlates across an entity's children;
        doing that with one ``list_issues`` call per child is an N+1. This resolves
        the whole set in a single ``IN`` query and buckets the rows, preserving the
        same newest-first order as :meth:`list_issues`. Entities with no issues map
        to an empty list so callers can index without a membership check.
        """
        ids = [int(e) for e in dict.fromkeys(entity_ids) if e is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT * FROM issues WHERE entity_id IN ({placeholders}) "
            "ORDER BY last_seen_ts DESC, id DESC",
            ids,
        ).fetchall()
        out: dict[int, list[sqlite3.Row]] = {i: [] for i in ids}
        for row in rows:
            out[int(row["entity_id"])].append(row)
        return out

    def record_issue_event(
        self,
        issue_id: int,
        kind: str,
        *,
        ts: Optional[int] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> int:
        """Append one entry to an issue's lifecycle trail (section 7)."""
        ts = _now() if ts is None else ts
        # NOT sort_keys: the lifecycle trail reads this back in the order the
        # engine wrote it (e.g. escalated's "reason" before its "occurrences").
        detail_json = json.dumps(detail or {})
        with self._write() as conn:
            cur = conn.execute(
                "INSERT INTO issue_events (issue_id, ts, kind, detail) VALUES (?,?,?,?)",
                (issue_id, ts, kind, detail_json),
            )
            return int(cur.lastrowid)

    def list_issue_events(self, issue_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM issue_events WHERE issue_id=? ORDER BY ts, id", (issue_id,)
        ).fetchall()

    # ------------------------------------------------------------------ #
    # Server-surface read helpers (name resolution, inventory rollups, events)
    #
    # The API layer (netadmin.server) resolves entity names, rolls up per-device
    # state/metrics, and queries events for the UI. Section 4 keeps SQL in this
    # module, so those joins live here rather than in the routers. All read-only.
    # ------------------------------------------------------------------ #

    def entities_by_ids(self, ids: Iterable[int]) -> dict[int, sqlite3.Row]:
        """Resolve a set of entity_ids to their rows, for name/type resolution.

        Returns ``{entity_id: row}`` for the ids that exist (missing ids are just
        absent). One ``IN`` query so the router can batch-resolve the entities an
        issue list, event feed, or offender ranking references without N lookups.
        """
        id_list = [int(i) for i in dict.fromkeys(ids) if i is not None]
        if not id_list:
            return {}
        placeholders = ",".join("?" for _ in id_list)
        rows = self._conn.execute(
            f"SELECT * FROM entities WHERE entity_id IN ({placeholders})", id_list
        ).fetchall()
        return {int(r["entity_id"]): r for r in rows}

    def children(self, parent_id: int) -> list[sqlite3.Row]:
        """Direct children of an entity (a switch's ports, an AP's radios)."""
        return self._conn.execute(
            "SELECT * FROM entities WHERE parent_id=? ORDER BY entity_type, entity_id",
            (parent_id,),
        ).fetchall()

    def current_states(self, entity_id: int) -> dict[str, Optional[str]]:
        """Latest recorded value of every tracked attribute for an entity.

        The discrete state history (firmware, up/down, link speed, channel, ip,
        uplink type...) collapsed to its present value per attr -- the current
        row for each ``attr`` is the one with the greatest ``id``. Read-only;
        used by the device/client detail rollups.
        """
        rows = self._conn.execute(
            "SELECT attr, new_value FROM state_changes "
            "WHERE entity_id=? AND id IN ("
            "  SELECT MAX(id) FROM state_changes WHERE entity_id=? GROUP BY attr"
            ") ORDER BY attr",
            (entity_id, entity_id),
        ).fetchall()
        return {str(r["attr"]): r["new_value"] for r in rows}

    def latest_samples(self, entity_id: int) -> list[dict[str, Any]]:
        """Most-recent raw sample per series for an entity (current metric values).

        Returns ``[{"metric", "unit", "ts", "value"}, ...]`` -- the latest reading
        of each series the entity owns, so a device page can show "current" gauge
        values (cpu, rssi, temperature) without pulling a window. The correlated
        ``MAX(ts)`` rides the ``samples`` primary key ``(series_id, ts)``.
        """
        rows = self._conn.execute(
            "SELECT se.metric AS metric, se.unit AS unit, s.ts AS ts, s.value AS value "
            "FROM series se JOIN samples s ON s.series_id = se.series_id "
            "WHERE se.entity_id=? AND s.ts = ("
            "  SELECT MAX(ts) FROM samples WHERE series_id = se.series_id"
            ") ORDER BY se.metric",
            (entity_id,),
        ).fetchall()
        return [
            {"metric": str(r["metric"]), "unit": r["unit"], "ts": int(r["ts"]), "value": r["value"]}
            for r in rows
        ]

    def current_states_bulk(self, entity_ids: Sequence[int]) -> dict[int, dict[str, Optional[str]]]:
        """Current value of every tracked attribute, for many entities at once.

        The batched form of :meth:`current_states`: the same collapse-to-latest
        per ``attr`` (greatest ``id`` wins) but over an id set in one query, so an
        inventory list resolves N entities' states without N correlated reads on
        the event loop. Entities with no state history are simply absent from the
        map (callers default to ``{}``).
        """
        ids = [int(e) for e in entity_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            "SELECT entity_id, attr, new_value FROM state_changes WHERE id IN ("
            "  SELECT MAX(id) FROM state_changes "
            f"  WHERE entity_id IN ({placeholders}) GROUP BY entity_id, attr"
            ") ORDER BY entity_id, attr",
            ids,
        ).fetchall()
        out: dict[int, dict[str, Optional[str]]] = {}
        for r in rows:
            out.setdefault(int(r["entity_id"]), {})[str(r["attr"])] = r["new_value"]
        return out

    def latest_samples_bulk(self, entity_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
        """Latest raw sample per series, for many entities at once.

        The batched form of :meth:`latest_samples`: one statement over an id set
        instead of a query per entity. A grouped ``MAX(ts)`` per series (riding the
        ``samples`` PK ``(series_id, ts)``) picks each series' current reading.
        Entities with no series are absent from the map (callers default to ``[]``).
        """
        ids = [int(e) for e in entity_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            "SELECT se.entity_id AS entity_id, se.metric AS metric, se.unit AS unit, "
            "       s.ts AS ts, s.value AS value "
            "FROM series se "
            "JOIN samples s ON s.series_id = se.series_id "
            "JOIN ("
            "  SELECT s2.series_id AS series_id, MAX(s2.ts) AS mts "
            "  FROM samples s2 JOIN series se2 ON se2.series_id = s2.series_id "
            f"  WHERE se2.entity_id IN ({placeholders}) "
            "  GROUP BY s2.series_id"
            ") m ON m.series_id = se.series_id AND s.ts = m.mts "
            f"WHERE se.entity_id IN ({placeholders}) "
            "ORDER BY se.entity_id, se.metric",
            ids + ids,
        ).fetchall()
        out: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(int(r["entity_id"]), []).append(
                {
                    "metric": str(r["metric"]),
                    "unit": r["unit"],
                    "ts": int(r["ts"]),
                    "value": r["value"],
                }
            )
        return out

    def open_issue_counts(self) -> dict[int, dict[str, int]]:
        """Open (non-resolved) issue counts per entity, split by severity.

        Returns ``{entity_id: {"p1", "p2", "p3", "total"}}`` for every entity that
        owns at least one open issue. One GROUP BY feeds the whole inventory list's
        issue badges instead of a per-device query.
        """
        rows = self._conn.execute(
            "SELECT entity_id, severity, COUNT(*) AS c FROM issues "
            "WHERE state != 'resolved' AND entity_id IS NOT NULL "
            "GROUP BY entity_id, severity"
        ).fetchall()
        out: dict[int, dict[str, int]] = {}
        for r in rows:
            eid = int(r["entity_id"])
            sev = str(r["severity"])
            count = int(r["c"])
            bucket = out.setdefault(eid, {"p1": 0, "p2": 0, "p3": 0, "total": 0})
            if sev in bucket:
                bucket[sev] += count
            bucket["total"] += count
        return out

    def sle_fail_minutes_by_attributed(self, start_ts: int, end_ts: int) -> dict[int, float]:
        """Failed SLE client-minutes attributed to each infrastructure entity.

        One ``GROUP BY attributed_entity_id`` over ``sle_minutes`` in
        ``[start_ts, end_ts)``, excluding the ``ok`` classifier (only *failed*
        minutes are grief) and unattributed rows (``attributed_entity_id IS
        NULL`` — a failure the SLE engine could not pin on a device is not that
        device's fault, and blaming NULL would be exactly the mis-attribution the
        offender ranking must avoid). Returns ``{entity_id: failed_minutes}`` for
        every entity the failed minutes pin on; entities with none are absent.

        This is the impact-weighted term of the offender score
        (``netadmin.analytics.offenders``): a failed client-minute is a real
        minute a real client had a degraded experience because of this entity.
        """
        rows = self._conn.execute(
            "SELECT attributed_entity_id AS eid, SUM(minutes) AS m FROM sle_minutes "
            "WHERE bucket_ts>=? AND bucket_ts<? "
            "AND classifier != 'ok' AND attributed_entity_id IS NOT NULL "
            "GROUP BY attributed_entity_id",
            (start_ts, end_ts),
        ).fetchall()
        return {int(r["eid"]): float(r["m"] or 0.0) for r in rows}

    def event_counts_by_entity(
        self, start_ts: int, end_ts: int, keys: Sequence[str]
    ) -> dict[int, int]:
        """Count events per subject entity in ``[start_ts, end_ts)`` for ``keys``.

        One ``GROUP BY entity_id`` over ``events``, filtered to the given event
        ``key`` set (disconnect / roam keys for the offender ranking) and a
        half-open time window, excluding rows with no ``entity_id``. Returns
        ``{entity_id: count}``; an empty ``keys`` yields ``{}`` (no key set, no
        volume — never "count everything").

        Deliberately keyed on ``entity_id`` (the event's own subject — the client
        that roamed or disconnected), **not** ``related_entity_id``: pinning a
        client's disconnect on the AP it left is a causal guess, and the offender
        ranking pins device blame only through the rigorously-attributed
        ``sle_minutes`` term (section 17: rules, never guessing).
        """
        key_list = [k for k in keys if k]
        if not key_list:
            return {}
        placeholders = ",".join("?" for _ in key_list)
        rows = self._conn.execute(
            f"SELECT entity_id AS eid, COUNT(*) AS c FROM events "
            "WHERE ts>=? AND ts<? AND entity_id IS NOT NULL "
            f"AND key IN ({placeholders}) "
            "GROUP BY entity_id",
            (start_ts, end_ts, *key_list),
        ).fetchall()
        return {int(r["eid"]): int(r["c"]) for r in rows}

    def event_count_for_entity(
        self, entity_id: int, start_ts: int, end_ts: int, keys: Sequence[str]
    ) -> int:
        """Count one entity's events in ``[start_ts, end_ts)`` for ``keys``.

        The single-entity form of :meth:`event_counts_by_entity` (a client detail
        page needs one count, not the whole site's), riding the same
        ``idx_events_entity_ts`` index. An empty ``keys`` yields ``0``.
        """
        key_list = [k for k in keys if k]
        if not key_list:
            return 0
        placeholders = ",".join("?" for _ in key_list)
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM events "
            "WHERE entity_id=? AND ts>=? AND ts<? "
            f"AND key IN ({placeholders})",
            (entity_id, start_ts, end_ts, *key_list),
        ).fetchone()
        return int(row["c"]) if row is not None else 0

    def query_events(
        self,
        *,
        since_ts: Optional[int] = None,
        until_ts: Optional[int] = None,
        keys: Optional[Sequence[str]] = None,
        entity_id: Optional[int] = None,
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        """Recent events for the UI feed, newest first, capped at ``limit``.

        Unlike :meth:`read_events` (a bounded ascending window used by the catch-up
        machinery), this powers the dashboard ticker and entity journeys: an open
        ``since_ts`` lower bound, an optional ``keys`` set (``key IN (...)``), an
        optional entity filter, and a hard ``LIMIT`` so a busy site cannot ship an
        unbounded feed. Rows come back most-recent-first.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if since_ts is not None:
            clauses.append("ts>=?")
            params.append(int(since_ts))
        if until_ts is not None:
            clauses.append("ts<?")
            params.append(int(until_ts))
        if entity_id is not None:
            clauses.append("entity_id=?")
            params.append(int(entity_id))
        key_list = [k for k in (keys or []) if k]
        if key_list:
            placeholders = ",".join("?" for _ in key_list)
            clauses.append(f"key IN ({placeholders})")
            params.extend(key_list)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit)))
        return self._conn.execute(
            f"SELECT * FROM events {where} ORDER BY ts DESC, id DESC LIMIT ?", params
        ).fetchall()

    # ------------------------------------------------------------------ #
    # Baselines
    # ------------------------------------------------------------------ #

    def upsert_baseline(
        self, series_id: int, bucket: str, stat: str, value: float, ts: Optional[int] = None
    ) -> None:
        ts = _now() if ts is None else ts
        with self._write() as conn:
            conn.execute(
                "INSERT INTO baselines (series_id, bucket, stat, value, updated_ts) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(series_id, bucket, stat) DO UPDATE SET "
                "  value=excluded.value, updated_ts=excluded.updated_ts",
                (series_id, bucket, stat, value, ts),
            )

    def get_baseline(self, series_id: int, bucket: str, stat: str) -> Optional[float]:
        row = self._conn.execute(
            "SELECT value FROM baselines WHERE series_id=? AND bucket=? AND stat=?",
            (series_id, bucket, stat),
        ).fetchone()
        return None if row is None else float(row["value"])

    def get_baselines(self, series_id: int, bucket: Optional[str] = None) -> list[sqlite3.Row]:
        if bucket is None:
            return self._conn.execute(
                "SELECT * FROM baselines WHERE series_id=? ORDER BY bucket, stat", (series_id,)
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM baselines WHERE series_id=? AND bucket=? ORDER BY stat",
            (series_id, bucket),
        ).fetchall()

    # ------------------------------------------------------------------ #
    # Changes (config change ledger, with revert)
    # ------------------------------------------------------------------ #

    def insert_change(
        self,
        *,
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        status: str,
        ts: Optional[int] = None,
        issue_id: Optional[int] = None,
        entity_id: Optional[int] = None,
    ) -> int:
        ts = _now() if ts is None else ts
        with self._write() as conn:
            cur = conn.execute(
                "INSERT INTO changes "
                "(ts, issue_id, entity_id, action, before_json, after_json, status, reverted_ts) "
                "VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    ts,
                    issue_id,
                    entity_id,
                    action,
                    json.dumps(before, sort_keys=True),
                    json.dumps(after, sort_keys=True),
                    status,
                ),
            )
            return int(cur.lastrowid)

    def update_change_status(
        self, change_id: int, status: str, *, reverted_ts: Optional[int] = None
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE changes SET status=?, reverted_ts=? WHERE id=?",
                (status, reverted_ts, change_id),
            )

    def get_change(self, change_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM changes WHERE id=?", (change_id,)).fetchone()

    def list_changes(
        self, *, issue_id: Optional[int] = None, entity_id: Optional[int] = None
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if issue_id is not None:
            clauses.append("issue_id=?")
            params.append(issue_id)
        if entity_id is not None:
            clauses.append("entity_id=?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._conn.execute(
            f"SELECT * FROM changes {where} ORDER BY ts DESC, id DESC", params
        ).fetchall()

    # ------------------------------------------------------------------ #
    # SLE minutes
    # ------------------------------------------------------------------ #

    def upsert_sle_minute(
        self,
        *,
        bucket_ts: int,
        sle: str,
        classifier: str,
        entity_id: int,
        minutes: float,
        attributed_entity_id: Optional[int] = None,
    ) -> None:
        """Set the minutes for one (bucket, sle, classifier, client) cell.

        Replaces rather than accumulates: the SLE engine computes a bucket's
        minutes and writes them idempotently, so a recompute of the same bucket
        overwrites cleanly.
        """
        with self._write() as conn:
            conn.execute(
                "INSERT INTO sle_minutes "
                "(bucket_ts, sle, classifier, entity_id, attributed_entity_id, minutes) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(bucket_ts, sle, classifier, entity_id) DO UPDATE SET "
                "  minutes=excluded.minutes, attributed_entity_id=excluded.attributed_entity_id",
                (bucket_ts, sle, classifier, entity_id, attributed_entity_id, minutes),
            )

    def add_sle_minutes(
        self,
        *,
        bucket_ts: int,
        sle: str,
        classifier: str,
        entity_id: int,
        minutes: float,
        attributed_entity_id: Optional[int] = None,
    ) -> None:
        """Accumulate minutes into a cell (for incremental attribution)."""
        with self._write() as conn:
            conn.execute(
                "INSERT INTO sle_minutes "
                "(bucket_ts, sle, classifier, entity_id, attributed_entity_id, minutes) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(bucket_ts, sle, classifier, entity_id) DO UPDATE SET "
                "  minutes = minutes + excluded.minutes, "
                "  attributed_entity_id = COALESCE(excluded.attributed_entity_id, attributed_entity_id)",
                (bucket_ts, sle, classifier, entity_id, attributed_entity_id, minutes),
            )

    def delete_sle_minutes(self, bucket_ts: int) -> int:
        """Delete every ``sle_minutes`` row for one bucket; returns rows removed.

        Used only by a bucket **recompute** (:meth:`SleMinutesJob.run_bucket` /
        ``run_range`` with ``clear_existing=True``, e.g. after a report backfill
        refines a bucket's activity data): the plain upsert in
        :meth:`upsert_sle_minute` replaces a cell it writes, but it never touches
        a cell the *previous* computation wrote that the new one no longer
        produces (a classifier the byte-accurate pass drops, or a client the
        byte-accurate gate now excludes). Delete-then-rewrite is the only way a
        recompute cannot strand a stale row.
        """
        with self._write() as conn:
            cur = conn.execute("DELETE FROM sle_minutes WHERE bucket_ts=?", (bucket_ts,))
            return cur.rowcount

    def query_sle_minutes(
        self,
        start_ts: int,
        end_ts: int,
        *,
        group_by: Sequence[str] = ("sle", "classifier"),
    ) -> list[dict[str, Any]]:
        """Aggregate SLE minutes over a window, grouped by the given dimensions.

        The health score and its explanation are the same GROUP BY (section 8):
        pass ``("sle",)`` for the headline blend, ``("sle", "classifier")`` for
        the breakdown, add ``"attributed_entity_id"`` to pin blame.
        """
        allowed = {"sle", "classifier", "entity_id", "attributed_entity_id", "bucket_ts"}
        cols = list(group_by)
        bad = set(cols) - allowed
        if bad:
            raise ValueError(f"cannot group SLE minutes by {sorted(bad)}")

        sql = "SELECT "
        if cols:
            sql += ", ".join(cols) + ", "
        sql += "SUM(minutes) AS minutes FROM sle_minutes WHERE bucket_ts>=? AND bucket_ts<?"
        if cols:
            sql += " GROUP BY " + ", ".join(cols) + " ORDER BY " + ", ".join(cols)

        rows = self._conn.execute(sql, (start_ts, end_ts)).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            entry: dict[str, Any] = {col: r[col] for col in cols}
            entry["minutes"] = r["minutes"]
            result.append(entry)
        return result

    # ------------------------------------------------------------------ #
    # Investigations (LLM)
    # ------------------------------------------------------------------ #

    def insert_investigation(
        self,
        *,
        issue_id: int,
        provider: str,
        dossier_md: str,
        status: str = "pending",
        ts: Optional[int] = None,
        response_md: Optional[str] = None,
    ) -> int:
        ts = _now() if ts is None else ts
        with self._write() as conn:
            cur = conn.execute(
                "INSERT INTO investigations "
                "(issue_id, ts, provider, dossier_md, response_md, status) "
                "VALUES (?,?,?,?,?,?)",
                (issue_id, ts, provider, dossier_md, response_md, status),
            )
            return int(cur.lastrowid)

    def attach_investigation_response(
        self, investigation_id: int, response_md: str, *, status: str = "answered"
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE investigations SET response_md=?, status=? WHERE id=?",
                (response_md, status, investigation_id),
            )

    def get_investigation(self, investigation_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM investigations WHERE id=?", (investigation_id,)
        ).fetchone()

    def list_investigations(self, issue_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM investigations WHERE issue_id=? ORDER BY ts, id", (issue_id,)
        ).fetchall()

    # ------------------------------------------------------------------ #
    # Correlation: incidents + incident_members + the reads the engine needs
    #
    # The correlation engine (docs/ARCHITECTURE.md section 17) is pure logic over
    # the open-issue set + entity topology; its only I/O is this repository. SQL
    # stays here (section 4). The engine reasons only over *confirmed* open issues
    # (active/resolving; pending excluded) and preserves incident identity by the
    # partial unique index on the open fingerprint.
    # ------------------------------------------------------------------ #

    def list_correlatable_issues(self) -> list[sqlite3.Row]:
        """Confirmed open issues eligible for correlation.

        ``active`` and ``resolving`` only: ``pending`` is unconfirmed (excluded per
        section 17, step 1) and ``resolved`` is closed. Ordered by id for a
        deterministic pass.
        """
        return self._conn.execute(
            "SELECT * FROM issues WHERE state IN ('active','resolving') ORDER BY id"
        ).fetchall()

    def entity_topology(self, *, site_id: Optional[str] = None) -> list[sqlite3.Row]:
        """The parent/child tree the engine correlates over.

        Just the columns correlation needs (id, type, parent, site, name,
        native_id) for every entity in the site, so the engine can resolve
        ancestry between two issues' entities — and match a detector's recorded
        attributed-AP hint (name or MAC) — without pulling full rows.
        """
        site = site_id or self.site_id
        return self._conn.execute(
            "SELECT entity_id, entity_type, parent_id, site_id, name, native_id "
            "FROM entities WHERE site_id=? ORDER BY entity_id",
            (site,),
        ).fetchall()

    def get_incident(self, incident_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()

    def get_open_incident(self, fingerprint: str) -> Optional[sqlite3.Row]:
        """The single non-resolved incident for a fingerprint, if any.

        The partial unique index ``idx_incidents_open_fp`` guarantees at most one.
        """
        return self._conn.execute(
            "SELECT * FROM incidents WHERE fingerprint=? AND state != 'resolved' LIMIT 1",
            (fingerprint,),
        ).fetchone()

    # An incident earns the presentation-tier word "incident" only once it
    # actually groups something: the root plus at least one symptom. Below that
    # it is the engine's own incident-of-one bookkeeping (correlate/engine.py --
    # real and load-bearing there, so it is never deleted), but everywhere this
    # repository is asked "is this a genuine incident", the answer is this one
    # predicate. The incidents router, the issues read-model join and MCP all
    # call through it (directly or via `genuine_only=`) so "genuine" cannot
    # drift between them (Gitea #21).
    GENUINE_INCIDENT_MIN_MEMBERS = 2

    @classmethod
    def is_genuine_incident(cls, member_count: int) -> bool:
        return member_count >= cls.GENUINE_INCIDENT_MIN_MEMBERS

    def list_incidents(
        self, *, open_only: bool = False, genuine_only: bool = False
    ) -> list[sqlite3.Row]:
        """Incident rows, most-recently-seen first.

        ``genuine_only`` applies :meth:`is_genuine_incident` -- only rows with
        2+ members survive. This filters in Python against a batched member
        count rather than a SQL ``HAVING``, so the one genuineness predicate
        lives in exactly one place instead of being re-expressed as SQL here.
        """
        where = "WHERE state != 'resolved' " if open_only else ""
        rows = self._conn.execute(
            f"SELECT * FROM incidents {where}ORDER BY last_seen_ts DESC, id DESC"
        ).fetchall()
        if not genuine_only:
            return rows
        counts = self.incident_member_counts([int(r["id"]) for r in rows])
        return [r for r in rows if self.is_genuine_incident(counts.get(int(r["id"]), 0))]

    def insert_incident(
        self,
        *,
        fingerprint: str,
        root_issue_id: int,
        severity: str,
        state: str,
        first_seen_ts: int,
        last_seen_ts: int,
        title: str,
        summary: str = "",
        resolved_ts: Optional[int] = None,
    ) -> int:
        with self._write() as conn:
            cur = conn.execute(
                "INSERT INTO incidents "
                "(fingerprint, root_issue_id, severity, state, first_seen_ts, "
                " last_seen_ts, resolved_ts, title, summary) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    fingerprint,
                    root_issue_id,
                    severity,
                    state,
                    first_seen_ts,
                    last_seen_ts,
                    resolved_ts,
                    title,
                    summary,
                ),
            )
            return int(cur.lastrowid)

    _INCIDENT_COLUMNS = frozenset(
        {
            "fingerprint",
            "root_issue_id",
            "severity",
            "state",
            "first_seen_ts",
            "last_seen_ts",
            "resolved_ts",
            "title",
            "summary",
        }
    )

    def update_incident(self, incident_id: int, **fields: Any) -> None:
        """Update named columns on an incident."""
        if not fields:
            return
        unknown = set(fields) - self._INCIDENT_COLUMNS
        if unknown:
            raise ValueError(f"unknown incident column(s): {sorted(unknown)}")
        assignments = ", ".join(f"{col}=?" for col in fields)
        params = list(fields.values()) + [incident_id]
        with self._write() as conn:
            conn.execute(f"UPDATE incidents SET {assignments} WHERE id=?", params)

    def replace_incident_members(self, incident_id: int, members: Sequence[dict[str, Any]]) -> None:
        """Atomically replace an incident's member set (delete-then-insert).

        Correlation recomputes membership every pass, so the stored set is
        authoritative each run. Each member dict carries ``issue_id``, ``role``,
        ``rule`` and ``rationale``. Both statements ride one transaction.
        """
        with self._write() as conn:
            conn.execute("DELETE FROM incident_members WHERE incident_id=?", (incident_id,))
            for m in members:
                conn.execute(
                    "INSERT INTO incident_members "
                    "(incident_id, issue_id, role, rule, rationale) VALUES (?,?,?,?,?)",
                    (incident_id, m["issue_id"], m["role"], m["rule"], m["rationale"]),
                )

    def list_incident_members(self, incident_id: int) -> list[sqlite3.Row]:
        """Members of an incident, root first (role ordering), then by issue id."""
        return self._conn.execute(
            "SELECT * FROM incident_members WHERE incident_id=? "
            "ORDER BY CASE role WHEN 'root' THEN 0 ELSE 1 END, issue_id",
            (incident_id,),
        ).fetchall()

    def incident_id_for_issue(self, issue_id: int) -> Optional[int]:
        """The open incident an issue currently belongs to, if any.

        Powers the issue read model's ``incident_id`` join (section 17): a member
        row scoped to the one non-resolved incident.
        """
        row = self._conn.execute(
            "SELECT im.incident_id AS incident_id FROM incident_members im "
            "JOIN incidents i ON i.id = im.incident_id "
            "WHERE im.issue_id=? AND i.state != 'resolved' LIMIT 1",
            (issue_id,),
        ).fetchone()
        return None if row is None else int(row["incident_id"])

    def incident_brief_for_issues(self, issue_ids: Iterable[int]) -> dict[int, sqlite3.Row]:
        """Batch the ``incident_id`` + ``incident_role`` + incident label join for
        a set of issues (section 17: the issue read model gains these via a join,
        never a stored column, so issue lifecycle stays untouched).

        Returns ``{issue_id: row}`` where each row carries ``incident_id``,
        ``incident_role``, ``incident_title``, ``incident_summary``,
        ``incident_severity`` and ``incident_member_count`` for the one *open*
        incident that issue belongs to. ``incident_member_count`` is what callers
        pass to :meth:`is_genuine_incident` to decide whether to surface a
        presentation-tier "incident" (Gitea #21) -- it is a correlated subquery
        here rather than a second batched call so the join stays one query.
        Issues in no open incident are simply absent from the map (callers
        default them to ``None``). One ``IN`` query, so a list endpoint never
        fans into N per-row lookups.
        """
        ids = [int(i) for i in dict.fromkeys(issue_ids) if i is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            "SELECT im.issue_id AS issue_id, im.incident_id AS incident_id, "
            "im.role AS incident_role, i.title AS incident_title, "
            "i.summary AS incident_summary, i.severity AS incident_severity, "
            "(SELECT COUNT(*) FROM incident_members im2 WHERE im2.incident_id = i.id) "
            "  AS incident_member_count "
            "FROM incident_members im JOIN incidents i ON i.id = im.incident_id "
            f"WHERE i.state != 'resolved' AND im.issue_id IN ({placeholders})",
            ids,
        ).fetchall()
        return {int(r["issue_id"]): r for r in rows}

    def incident_member_counts(self, incident_ids: Iterable[int]) -> dict[int, int]:
        """Member count per incident, batched in one ``GROUP BY`` (section 17).

        Powers the ``GET /api/incidents`` list card ("+N related"): the number of
        issues grouped under each incident, root included. Incidents with no rows
        (should not happen — the root is always a member) map to ``0``.
        """
        ids = [int(i) for i in dict.fromkeys(incident_ids) if i is not None]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            "SELECT incident_id, COUNT(*) AS n FROM incident_members "
            f"WHERE incident_id IN ({placeholders}) GROUP BY incident_id",
            ids,
        ).fetchall()
        counts = {i: 0 for i in ids}
        for r in rows:
            counts[int(r["incident_id"])] = int(r["n"])
        return counts
