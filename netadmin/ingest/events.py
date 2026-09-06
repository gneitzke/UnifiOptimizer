"""Event pipeline: normalize + persist controller events (ARCHITECTURE.md 5.1-5.2).

Two sources feed one store table:

* the live WebSocket (``netadmin.ingest.unifi.ws.EventListener``), and
* ``stat/event`` catch-up pages for anything the socket missed.

Both are normalized identically -- controller-ms timestamps folded to epoch
seconds, device/client MACs resolved to ``entities.entity_id`` via repository
lookups, and a dedupe key derived from the controller event ``_id`` (or, when a
frame lacks one, a stable ``(ts, key, mac)`` hash) so a WS event and its
catch-up twin land as one row. Unknown MACs are tolerated: the event is stored
with a null entity rather than dropped.

Three moving parts:

* :class:`EventNormalizer` -- pure per-event transform (Event model -> the kwargs
  :meth:`Repository.record_event` expects), with an entity-id resolution cache.
* :class:`EventListener` -- consumes the WS generator and writes batches; plus
  :func:`catchup_events`, the ``stat/event`` gap filler.
* :class:`WsSupervisor` -- restarts a dead/disconnected listener with capped
  exponential backoff, recording each state transition to ``poll_runs`` (``ws``).

Network safety: this module never issues controller writes. The only controller
traffic it drives is the read-only WebSocket and the documented ``stat/event``
read-query, both via already-built clients.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Awaitable, Callable, Optional


from netadmin.domain.types import EntityType
from netadmin.ingest.unifi.endpoints import Endpoints
from netadmin.ingest.unifi.models import Event
from netadmin.ingest.unifi.ws import EventListener as WsEventListener
from netadmin.logging import get_logger
from netadmin.store.repository import Repository

logger = get_logger("ingest.events")

# Anything at or above this magnitude is milliseconds, not seconds: epoch
# seconds are ~1.7e9 today and stay below 1e11 until the year 5138, while epoch
# milliseconds are already ~1.7e12. The split is unambiguous for any realistic
# controller timestamp.
_MS_THRESHOLD = 100_000_000_000

# Catch-up fetch bounding (ARCHITECTURE.md 5.1, section 16 "keep queries narrow").
# When the caller does not pin ``within_hours``, it is derived from the stored
# cursor so the controller query spans only the gap since the last catch-up plus
# a small safety margin, and never more than the local event-retention window.
_CATCHUP_MARGIN_HOURS = 1
_CATCHUP_MAX_WITHIN_HOURS = 30 * 24  # events are pruned at ~30 days locally

# Switch events whose semantic scope is a single PORT, not the whole switch, and
# whose consuming detector compares against PORT entity ids (wired.stp_loop reads
# ``EVT_SW_StpPortBlocking`` at port scope). These must be attributed to the PORT
# entity (native_id ``"<switch_mac>:<port_idx>"`` -- how ingest keys ports, see
# netadmin/ingest/mapping.py), or the detector's port-id comparison never matches
# a switch-attributed event and the rule silently never fires (#4). NOT every
# port-bearing switch event belongs here: EVT_SW_PoeOverload names a port in its
# message but is a switch-budget event that ``wired.poe_budget`` queries at SWITCH
# scope, so it stays switch-attributed. Membership tracks the consuming detector's
# scope, not the mere presence of a ``port`` field.
_PORT_SCOPED_SW_EVENT_KEYS = frozenset({"EVT_SW_StpPortBlocking"})

# #w16a-5 (out-of-range int port): a port index is a PORT native_id segment only
# when it is a small, sane switch-port ordinal. Real UniFi switches top out well
# under this; the ceiling exists to reject a garbage/huge integer that would
# otherwise DESYNC the two native_ids. json_extract renders an integer outside
# SQLite's signed-64-bit range as a FLOAT (e.g. 9223372036854775808 ->
# 9.223372036854776e+18), so the SQL resolvability predicate builds
# "<sw>:9.22...e+18" while this normalizer builds the full-decimal
# "<sw>:9223372036854775808": the native_ids DISAGREE, the row reads
# forever-"resolvable" against an entity that never exists, and starves newer
# repairable rows. Bounding an accepted port to [0, _MAX_PORT_INDEX] in BOTH the
# normalizer and the SQL means an out-of-range value is never accepted-then-
# mismatched: it is rejected identically on both sides and routes to the SWITCH.
_MAX_PORT_INDEX = 4095

# P1: a SYSTEM-WIDE ceiling on the events the supervisor holds in memory while
# storage is down. The per-listener ``_max_pending`` bounds ONE listener's batch,
# but the supervisor rescues each dead listener's batch onto ``_pending`` and then
# starts another -- so under SUSTAINED total storage failure with a live producer
# that aggregate grew without bound (1001, 2002, 3003, ... -> OOM, losing
# EVERYTHING). This cap turns that into a bounded, counted, observable loss: once
# ``_pending`` would exceed it we evict (drop-oldest) and count the drop, so the
# process survives and can still flush every retained event the instant storage
# returns. It sits well above the per-listener bound so a single blip + restart
# (the confirmed 1000-blip and 1001-overflow cases) recovers with ZERO loss.
_SYSTEM_PENDING_MAX = 50_000


def _masks_cancel(exc: BaseException) -> bool:
    """True if ``exc`` is (or masks) a propagating ``asyncio.CancelledError``.

    When a ``finally`` (or other cleanup) raises an exception while a
    ``CancelledError`` is propagating, Python links that CancelledError into the
    new exception's ``__context__`` (implicit chaining) or ``__cause__`` (explicit
    ``raise ... from``). Walking that chain detects a masked cancel WITHOUT
    consulting ``current_task().cancelling()`` -- whose count is 0 when the cancel
    arrived by awaiting an already-cancelled future, the case that defeated the
    old count-based check (FINDING#5). A bounded walk guards against a cyclic
    chain.
    """
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, asyncio.CancelledError):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _to_epoch_s(event: Event) -> Optional[int]:
    """Fold a controller event timestamp to epoch **seconds**.

    Prefers the numeric ``time`` field (ms on every observed controller);
    values already in seconds pass through unscaled. Falls back to parsing the
    ISO ``datetime`` string. Returns None when neither yields a timestamp -- the
    event cannot be stored (``events.ts`` is NOT NULL).
    """
    raw = event.time
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = int(raw)
        return value // 1000 if value >= _MS_THRESHOLD else value
    text = event.datetime
    if isinstance(text, str) and text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    return None


def _field(event: Event, name: str) -> Any:
    """Read a field whether it is declared on :class:`Event` or an extra.

    ``Event`` sets ``extra="allow"``, so version-specific keys (``ap_from``,
    ``port``, ...) arrive in ``model_extra`` rather than as declared attributes.
    """
    value = getattr(event, name, None)
    if value is not None:
        return value
    extra = event.model_extra or {}
    return extra.get(name)


class EventNormalizer:
    """Turn a parsed :class:`Event` into ``Repository.record_event`` kwargs.

    Successful entity resolutions are cached per ``(entity_type, native_id)`` for
    the life of the normalizer: a busy roam stream re-references the same handful
    of APs and clients thousands of times, and each lookup is a SQL round-trip
    otherwise. Misses are deliberately **not** cached (see :meth:`_resolve`).
    """

    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._cache: dict[tuple[str, str], int] = {}

    def _resolve(self, entity_type: EntityType, mac: Optional[str]) -> Optional[int]:
        if not mac:
            return None
        key = (entity_type.value, mac)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        row = self._repo.find_entity(entity_type, mac)
        if row is None:
            # Do NOT negative-cache: a client's first frame (an assoc/connect
            # event) routinely arrives before the stat/sta poll that creates its
            # entity. Caching the miss would strand every later event for that
            # client with a NULL entity_id for the whole WS session, breaking its
            # journey timeline (idx_events_entity_ts). Re-resolve on each miss so
            # the link forms as soon as inventory catches up.
            return None
        entity_id = int(row["entity_id"])
        self._cache[key] = entity_id
        return entity_id

    def _entities(
        self, event: Event, key: str
    ) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """Resolve (entity_id, related_entity_id, primary native MAC) for a key.

        Routing follows ARCHITECTURE.md section 4:

        * roam events -> entity is the client, related is the *from*-AP;
        * any other client-scoped event -> entity is the client, related is the
          AP or switch it was on;
        * device events -> entity is the AP / switch / gateway.

        The returned MAC is the primary entity's, used to salt the dedupe hash
        for frames that carry no controller ``_id``.
        """
        user_mac = _field(event, "user") or _field(event, "client")
        ap_mac = _field(event, "ap")
        sw_mac = _field(event, "sw")
        gw_mac = _field(event, "gw")
        ap_from = _field(event, "ap_from")

        if "Roam" in key and user_mac:
            entity_id = self._resolve(EntityType.CLIENT, user_mac)
            related_mac = ap_from or ap_mac
            related_id = self._resolve(EntityType.AP, related_mac)
            return entity_id, related_id, user_mac
        if user_mac:
            entity_id = self._resolve(EntityType.CLIENT, user_mac)
            related_id: Optional[int] = None
            if ap_mac:
                related_id = self._resolve(EntityType.AP, ap_mac)
            elif sw_mac:
                related_id = self._resolve(EntityType.SWITCH, sw_mac)
            return entity_id, related_id, user_mac
        if ap_mac:
            return self._resolve(EntityType.AP, ap_mac), None, ap_mac
        if sw_mac:
            # A PORT-scoped switch event (e.g. EVT_SW_StpPortBlocking) is attributed
            # to the PORT entity, not the switch, so a port-scoped detector's
            # port-id comparison matches. The port's native_id is "<sw_mac>:<idx>"
            # -- exactly how ingest/mapping.py keys ports -- so the resolved id is
            # the real port entity. Related is the parent switch. When the port
            # entity is not in inventory yet, entity_id resolves None and the event
            # persists with a null entity; reconcile_unresolved re-links it once the
            # device poll creates the port (the same late-link path clients use).
            # #4/#w15a-4: a port index is a PORT ONLY when it is a genuine integer.
            # Follow the normalizer's source precedence (``port`` then ``port_idx``),
            # then accept the chosen value only when it is a real ``int`` and NOT a
            # ``bool``. A ``bool`` (``port: true``) would otherwise stringify to
            # ``"<sw>:True"`` while the repository's resolvability SQL renders the
            # same JSON boolean as ``"<sw>:1"`` (SQLite coerces bool -> 1): the two
            # native_ids DISAGREE, so with a real integer port 1 present the row reads
            # as forever-"resolvable" against the wrong entity, never actually fills,
            # floats to the head of the reconcile window and STARVES newer repairable
            # events. A non-integer/bool/garbage port is NOT a port-scoped event -- it
            # is attributed to the SWITCH, exactly and consistently with the SQL, so
            # normalizer-stored and SQL-derived port native_ids are byte-identical for
            # every ACCEPTED (integer) port value.
            port_raw = _field(event, "port")
            if port_raw is None:
                port_raw = _field(event, "port_idx")
            # #w16a-5: accept only a genuine int (never a bool) AND within the sane
            # port range, so an out-of-range integer -- which the SQL renders as a
            # float, desyncing the native_id -- is rejected here too and routes to
            # the switch on BOTH sides.
            port_idx = (
                port_raw
                if isinstance(port_raw, int)
                and not isinstance(port_raw, bool)
                and 0 <= port_raw <= _MAX_PORT_INDEX
                else None
            )
            if key in _PORT_SCOPED_SW_EVENT_KEYS and port_idx is not None:
                port_nid = f"{sw_mac}:{port_idx}"
                return (
                    self._resolve(EntityType.PORT, port_nid),
                    self._resolve(EntityType.SWITCH, sw_mac),
                    port_nid,
                )
            return self._resolve(EntityType.SWITCH, sw_mac), None, sw_mac
        if gw_mac:
            return self._resolve(EntityType.GATEWAY, gw_mac), None, gw_mac
        return None, None, None

    @staticmethod
    def _dedupe_key(event: Event, ts: int, key: str, mac: Optional[str]) -> str:
        """Native dedupe id: the controller ``_id`` if present, else a hash.

        The hash makes a WS frame and its ``stat/event`` twin collapse to one row
        even when neither carries an ``_id``. ``msg`` is folded into the hash: a
        bare ``(ts, key, mac)`` key collides for two *distinct* events that share
        a second, a key and an entity -- e.g. two ``EVT_WU_Disconnected`` for the
        same client in the same second with different reasons -- silently dropping
        the second one. ``msg`` is the controller's rendered, per-event detail and
        is identical across a WS/catch-up twin, so it disambiguates real
        collisions without breaking the twin-collapse it exists to preserve.
        """
        if event.id:
            return str(event.id)
        digest = hashlib.sha1(f"{ts}|{key}|{mac or ''}|{event.msg or ''}".encode()).hexdigest()
        return f"h:{digest}"

    def normalize(self, event: Event) -> Optional[dict[str, Any]]:
        """Return record_event kwargs, or None if the event cannot be stored.

        An event with no ``key`` or no resolvable timestamp is unstorable (both
        columns are NOT NULL) and is skipped rather than raising.
        """
        key = event.key
        if not key:
            return None
        ts = _to_epoch_s(event)
        if ts is None:
            return None
        entity_id, related_id, mac = self._entities(event, key)
        native_id = self._dedupe_key(event, ts, key, mac)
        data = event.model_dump(exclude_none=True)
        return {
            "ts": ts,
            "key": key,
            "entity_id": entity_id,
            "related_entity_id": related_id,
            "native_id": native_id,
            "msg": event.msg,
            "data": data,
        }

    def reconcile_unresolved(self, *, limit: int = 500) -> int:
        """Fill links for old events after inventory has caught up (C7).

        This is intentionally a replay of the original controller payload,
        rather than guessing from a denormalized MAC column.  It also repairs
        WS-only deployments, where there may never be a stat/event replay.
        """
        repaired = 0
        unfilled: list[int] = []
        for row in self._repo.unresolved_events(limit=limit):
            try:
                payload = json.loads(row["data"])
                event = Event.model_validate(payload)
            except Exception:  # malformed retained payload is not recoverable
                logger.warning("Cannot reconcile malformed event payload id=%s", row["id"])
                unfilled.append(int(row["id"]))
                continue
            record = self.normalize(event)
            if record is not None and self._repo.fill_event_entity_refs(
                int(row["id"]),
                entity_id=record["entity_id"],
                related_entity_id=record["related_entity_id"],
            ):
                repaired += 1
            else:
                unfilled.append(int(row["id"]))
        # Bump the attempt counter on rows we selected but could not fill, so a
        # candidate whose AP never appears is eventually parked out of the LIMIT
        # window (repository fair-progress mechanism 2) and cannot starve newer
        # repairable rows. A row that later becomes resolvable is un-parked by the
        # resolvable-first ordering regardless of its attempt count.
        self._repo.bump_event_reconcile_attempts(unfilled)
        return repaired


def newest_stored_event_ts(repo: Repository) -> Optional[int]:
    """Timestamp (epoch s) of the most recent stored event, or None if empty.

    Used as the catch-up cursor. Reads through the repository's ``max_event_ts``
    (a ``MAX(ts)`` answered from the index) rather than loading up to ~30 days of
    event rows into Python just to read the last one.
    """
    return repo.max_event_ts()


async def catchup_events(
    repo: Repository,
    endpoints: Endpoints,
    *,
    normalizer: Optional[EventNormalizer] = None,
    within_hours: Optional[int] = None,
    max_events: Optional[int] = None,
    since_ts: Optional[int] = None,
    now: Optional[int] = None,
) -> int:
    """Pull ``stat/event`` and persist its retained history overlap.

    ``endpoints.stat_event`` already pages with ``_start`` (3000/page cap).
    A default cursor comes only from a completed history-read coverage interval,
    never from the newest stored event (which may be a live WS arrival).  All
    fetched overlap is offered to identity dedupe; ``since_ts`` filters only
    when an explicit caller supplied it as a volume constraint. Returns the
    number of rows actually inserted.

    The controller fetch is **bounded**: when ``within_hours`` is not pinned by
    the caller, it is derived from completed history coverage so ``stat/event``
    spans only the outstanding history gap (plus :data:`_CATCHUP_MARGIN_HOURS`), capped at
    :data:`_CATCHUP_MAX_WITHIN_HOURS`. Without this the periodic sweep would page
    the controller's entire retained event backlog every cycle to insert a
    handful of new rows -- ``since_ts`` only trims what is *inserted*, never what
    is *fetched* (ARCHITECTURE.md section 16: keep controller queries narrow).
    On a fresh store with no completed coverage yet the first sweep is unbounded
    by design, then self-bounds once history has actually been read.
    """
    normalizer = normalizer or EventNormalizer(repo)
    now_s = int(time.time()) if now is None else int(now)
    explicit_cursor = since_ts is not None
    # C3: only a completed *history read* can advance this cursor.  A newer WS
    # arrival says nothing about whether the event log's older interval was
    # recovered, so max_event_ts must never participate here.
    coverage_cursor = repo.latest_ingest_coverage_end(kind="event_history", scope="site")
    if since_ts is None:
        since_ts = coverage_cursor
    coverage_start = (
        max(0, now_s - _CATCHUP_MAX_WITHIN_HOURS * 3600)
        if coverage_cursor is None
        else coverage_cursor
    )
    if within_hours is None and since_ts is not None:
        gap_hours = max(0, now_s - since_ts) // 3600
        within_hours = min(_CATCHUP_MAX_WITHIN_HOURS, gap_hours + 1 + _CATCHUP_MARGIN_HOURS)
    # C3: coverage may only be recorded for the span we actually read. Clamp the
    # start forward to the tightest of every bound that limited the fetch --
    # ``within_hours`` (how far back stat/event returned), an explicit ``since_ts``
    # floor, and the retention cap -- so a bounded 1 h fetch never books coverage
    # for the untouched day behind it. ``max()`` picks the *latest* (narrowest)
    # floor; ``coverage_start`` already carries the cursor / retention baseline.
    coverage_start = max(coverage_start, now_s - _CATCHUP_MAX_WITHIN_HOURS * 3600)
    if within_hours is not None:
        coverage_start = max(coverage_start, now_s - within_hours * 3600)
    if explicit_cursor and since_ts is not None:
        coverage_start = max(coverage_start, since_ts)
    try:
        events = await endpoints.stat_event(within_hours=within_hours, max_events=max_events)
    except Exception as exc:  # noqa: BLE001 - record the hole for ANY read/parse failure
        # BUG#6 / #w14a-3 / #w15a-3: the event read/parse FAILED. The window was NOT
        # read, so coverage must NOT be credited for it -- record a first-class FAILED
        # hole (queryable, retried on the next sweep -- never counted by
        # ``observed_event_coverage``, which unions only 'complete' spans) instead of
        # the bogus 'complete' the empty-default fabricated, then RE-RAISE so the
        # collector's per-job firewall marks this poll failed rather than clean.
        #
        # #w15a-3: the except MUST catch ANY exception, not an enumerated subset. It
        # previously caught only ``(UnifiError, httpx.HTTPError)`` -- but the read/parse
        # can raise exceptions in NEITHER class and leave the coverage ledger EMPTY
        # (neither complete NOR failed, silently never retried nor observed):
        #   * ``httpx.CookieConflict`` -- a ``RequestError``, NOT an ``HTTPError``
        #     subclass, so it slipped through entirely; and
        #   * ``pydantic.ValidationError`` -- raised when a malformed ``stat/event``
        #     row fails :class:`Event` validation during response parsing
        #     (endpoints.py), likewise not an ``HTTPError``.
        # Whatever the exception, the requested window was not read; a durable FAILED
        # hole is the correct, uniform outcome. This never swallows -- it always
        # re-raises after recording so the firewall still sees the poll as failed.
        repo.record_ingest_coverage(
            kind="event_history", scope="site", interval="retained",
            start_ts=coverage_start, end_ts=now_s, status="failed",
            detail=f"event read failed: {type(exc).__name__}: {exc}"[:200],
        )
        logger.warning("Catch-up event read failed; window recorded failed: %s", exc)
        raise
    records: list[dict[str, Any]] = []
    # #w16a-4: count events that were READ but could NOT be stored because they
    # are structurally unusable (normalize -> None: no key, or no usable
    # timestamp). These are real history we failed to persist, so a window that
    # dropped any of them must NOT later be credited fully-'complete' with an
    # observed-empty (or under-filled) history -- that fabrication lets an
    # event-based detector false-clear. A genuinely EMPTY read drops nothing and
    # still records complete.
    unusable_dropped = 0
    try:
        for event in events:
            record = normalizer.normalize(event)
            if record is None:
                unusable_dropped += 1
                continue
            # An explicit cursor is an API caller's volume constraint.  The normal
            # coverage cursor is deliberately not an insertion filter: overlap is
            # deduped by event identity, and filtering it recreates the C3 loss.
            if explicit_cursor and since_ts is not None and record["ts"] < since_ts:
                continue
            records.append(record)
        inserted = repo.record_events_enriching_entities(records)
        normalizer.reconcile_unresolved()
    except Exception as exc:  # noqa: BLE001 - record the hole for a normalize/store failure
        # #w16a-3: normalization (entity-resolution DB READS) and the persist step
        # run HERE, OUTSIDE the HTTP-read guard above. A storage error -- e.g. a
        # ``sqlite3.OperationalError`` from an entity lookup during normalize, or a
        # failed insert -- would otherwise re-raise leaving ``ingest_coverage``
        # EMPTY for this window (neither complete NOR failed: silently never
        # retried nor observed, the exact hole the read-guard closes for HTTP). The
        # window is 'complete' only if its events were successfully read AND
        # normalized AND persisted; a normalize/persist failure means it was NOT,
        # so record a durable FAILED hole (queryable, retried next sweep, never
        # counted by ``observed_event_coverage``) then RE-RAISE so the collector's
        # per-job firewall marks this poll failed rather than clean.
        repo.record_ingest_coverage(
            kind="event_history", scope="site", interval="retained",
            start_ts=coverage_start, end_ts=now_s, status="failed",
            detail=f"event normalize/store failed: {type(exc).__name__}: {exc}"[:200],
        )
        logger.warning(
            "Catch-up event normalize/store failed; window recorded failed: %s", exc
        )
        raise
    if getattr(endpoints, "_event_disabled", False):
        # C3/R3: absence of the permitted history read is a durable,
        # queryable unrecoverable gap, not a successful empty collection.
        repo.record_ingest_coverage(
            kind="event_history", scope="site", interval="retained",
            start_ts=coverage_start, end_ts=now_s, status="unrecoverable",
            detail="controller event-history endpoint unsupported",
        )
    elif unusable_dropped:
        # #w16a-4: the window was read (HTTP ok) but at least one event in it was
        # structurally unusable and could not be stored. Crediting 'complete' here
        # would fabricate observed history with ZERO (or fewer) stored events over
        # a real span and let event-based detectors false-clear. Record it NOT
        # complete -- a 'partial' hole that is queryable, never credited as
        # observed coverage, and never advances the history cursor -- so the honest
        # gap stands. It self-heals once the undecodable event ages out of the read
        # window and a later fully-usable read of the span records complete.
        repo.record_ingest_coverage(
            kind="event_history", scope="site", interval="retained",
            start_ts=coverage_start, end_ts=now_s, status="partial",
            detail=f"{unusable_dropped} unusable event(s) dropped; window not fully observed",
        )
    elif max_events is None:
        # A successful unbounded/fully-paged GET establishes coverage.  A caller
        # capped response cannot prove the tail complete and must not move it.
        repo.record_ingest_coverage(
            kind="event_history", scope="site", interval="retained",
            start_ts=coverage_start, end_ts=now_s, status="complete",
        )
    logger.info(
        "Catch-up: %d stat/event rows fetched, %d new (cursor=%s).",
        len(events),
        inserted,
        since_ts,
    )
    return inserted


class EventListener:
    """Consume the WS event generator and persist events in batches.

    Wraps the low-level socket listener (``netadmin.ingest.unifi.ws``); this
    layer owns normalization, deduped batch writes, and a size/interval flush
    policy. It does not own reconnect supervision -- :class:`WsSupervisor` does.
    """

    def __init__(
        self,
        ws_listener: WsEventListener,
        repo: Repository,
        *,
        normalizer: Optional[EventNormalizer] = None,
        batch_size: int = 50,
        flush_interval: Optional[float] = 2.0,
        heartbeat_interval: float = 30.0,
    ) -> None:
        self._ws = ws_listener
        self._repo = repo
        self._normalizer = normalizer or EventNormalizer(repo)
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        # B4 (positive liveness): while CONNECTED and successfully draining, the
        # flusher writes a periodic ``poll_runs`` heartbeat (rate-limited to this
        # cadence) so event-source coverage is credited only across spans with
        # positive evidence the feed was observing -- never through end_ts on a
        # still-open ``connected`` row. Correctness never depends on a close row.
        self._heartbeat_interval = max(0.0, heartbeat_interval)
        self._last_heartbeat_ts: Optional[int] = None
        self._batch: list[dict[str, Any]] = []
        # P1 (normalize-read blip): events already CONSUMED off the socket whose
        # normalization hit a *transient* storage READ error (entity-resolution
        # lookup). A WS event has no ``stat/event`` recovery source, so it must NOT
        # be dropped when a storage blip makes ``normalize`` raise before the event
        # ever reaches ``_batch``. It is retained RAW here and re-normalized once
        # the read recovers (see :meth:`_drain_pending_raw`). A genuinely malformed
        # payload (a *non*-transient normalize error) is dropped-with-logging
        # instead -- it can never be stored regardless of storage health.
        self._pending_raw: list[Event] = []
        self._max_pending = max(1_000, self._batch_size * 4)
        self._storage_error: Optional[BaseException] = None
        self.terminal_state: Optional[str] = None
        self.written = 0
        # R3: surface the low-level socket's real connection state. The ws listener
        # calls back on connect/drop; we cache it and relay it to the supervisor
        # (``on_connection_state``) so health reflects the handshake, not the mere
        # existence of a running task.
        self.connection_state: str = "reconnecting"
        self.on_connection_state: Optional[Callable[[str], None]] = None
        # D5: fired whenever this listener proves storage is healthy (a heartbeat =
        # connected AND draining successfully). The supervisor points this at its
        # rescued-buffer drain so events rescued from a prior dead listener are
        # persisted promptly DURING this replacement listener's life -- not left
        # queued until this listener itself dies. Best-effort, never on the data
        # path: a raising hook is swallowed.
        self.on_healthy_drain: Optional[Callable[[], None]] = None
        # BUG#4: a predicate the SUPERVISOR points at its own rescued buffers so a
        # heartbeat (= "the event pipeline is fully drained") is suppressed while
        # ANY rescued event is still stuck upstream -- normalized OR raw -- or a
        # storage error is active anywhere in the pipeline, not merely in THIS
        # listener's local buffer. A fresh, empty replacement listener has empty
        # local buffers and would otherwise credit coverage over a span where the
        # supervisor still holds an undrained rescued event (e.g. one blocked by a
        # failing entity-resolution read). Returns True while the pipeline is NOT
        # fully drained. Best-effort: a raising/absent hook is treated
        # conservatively (no positive liveness) rather than as "drained".
        self.pipeline_blocked: Optional[Callable[[], bool]] = None
        # The socket listener reports state through this hook; setattr is safe on
        # both the real ws listener and the test doubles.
        setattr(self._ws, "on_state", self._relay_ws_state)

    def _relay_ws_state(self, state: str) -> None:
        self.connection_state = state
        callback = self.on_connection_state
        if callback is not None:
            callback(state)

    def pending_records(self) -> list[dict[str, Any]]:
        """Events buffered but NOT yet committed to storage (R2).

        The supervisor rescues these when it replaces a listener that died with an
        unflushed batch: WS events have no ``stat/event`` recovery source, so a
        storage blip that outlives the listener must not silently drop them.
        """
        return list(self._batch)

    def pending_raw_records(self) -> list[Event]:
        """Consumed events retained RAW because a *transient* storage READ error
        blocked their normalization (P1).

        These have been pulled off the socket but never normalized -- their
        entity-resolution lookup raised while storage was down. The supervisor
        rescues them alongside :meth:`pending_records` when the listener dies, and
        re-normalizes them once storage recovers, so a normalize-read blip that
        outlives the listener does not silently drop an unrecoverable WS event.
        """
        return list(self._pending_raw)

    @staticmethod
    def _is_transient_storage_error(exc: BaseException) -> bool:
        """A storage READ blip (recoverable) vs. a permanent malformed payload.

        A transient ``sqlite3.OperationalError`` (locked/busy/IO) raised by an
        entity-resolution lookup means the event can be normalized once storage
        recovers, so it must be RETAINED. Any other exception is treated as an
        unstorable/malformed payload and dropped-with-logging -- retaining it would
        just wedge the retry buffer on an event that can never be normalized.
        """
        return isinstance(exc, sqlite3.OperationalError)

    def _drain_pending_raw(self) -> None:
        """Re-normalize events retained after a transient normalize-read blip (P1).

        Called on each consumer-loop turn and before every flush, since storage
        may have recovered since the read failed. A raw event that now normalizes
        moves into ``_batch``; one that normalizes to ``None`` is permanently
        unstorable and dropped; one whose lookup still raises a transient storage
        error is kept (in order) for the next attempt, and draining stops there --
        if this read failed, later ones will too this tick.
        """
        if not self._pending_raw:
            return
        raws = self._pending_raw
        self._pending_raw = []
        for i, event in enumerate(raws):
            try:
                record = self._normalizer.normalize(event)
            except Exception as exc:  # noqa: BLE001 - classified below
                if self._is_transient_storage_error(exc):
                    # Storage still down: keep this event and the rest, in order.
                    self._storage_error = exc
                    self._pending_raw.extend(raws[i:])
                    return
                logger.exception(
                    "Dropping unstorable retained WS event (malformed payload)"
                )
                continue
            if record is not None:
                self._batch.append(record)
        return

    def _total_pending(self) -> int:
        """Every consumed-but-uncommitted event: normalized batch + retained raw.

        Both are held in memory and both have no ``stat/event`` recovery source, so
        the bounded-queue guard must count them together.
        """
        return len(self._batch) + len(self._pending_raw)

    def _flush(self) -> int:
        """Write and clear the pending batch. Synchronous and atomic.

        ``record_events`` runs to completion with no ``await`` between reading
        and clearing ``self._batch``, so the periodic flusher and the consumer
        loop never race on the buffer in a single-threaded event loop.
        """
        # First fold in any raw events whose normalize-read has since recovered,
        # so a blip'd event is flushed the moment storage returns. This may leave
        # events STUCK in ``_pending_raw`` (and (re)set ``_storage_error``) if the
        # entity-resolution read is still failing.
        self._drain_pending_raw()
        inserted = 0
        if self._batch:
            batch = list(self._batch)
            # C7's enrich-aware writer dedupes replayed events while filling links.
            # Crucially, no buffer entry is removed until SQLite committed. A write
            # failure raises out here; the caller records it as ``_storage_error``.
            inserted = self._repo.record_events_enriching_entities(batch)
            del self._batch[: len(batch)]
            self.written += inserted
        # D2 (normalization-read outage earns no false coverage): draining is
        # "successful" only when NOTHING is stuck in the raw retry buffer. Clear a
        # stale storage error -- so heartbeats (gated on an empty raw buffer AND no
        # active error) can resume -- ONLY once ``_pending_raw`` is truly empty.
        # While a raw event is still stuck, ``_drain_pending_raw`` has kept
        # ``_storage_error`` set, so the heartbeat stays suppressed and the
        # unprocessed span is never credited as observed.
        if not self._pending_raw:
            self._storage_error = None
        return inserted

    def _maybe_heartbeat(self, *, now: Optional[float] = None) -> None:
        """Emit a WS liveness heartbeat when CONNECTED and draining healthily.

        B4 (positive liveness): a heartbeat is credited toward event-source
        coverage, so it is written ONLY with positive evidence the feed is
        observing -- the socket is connected AND this flush tick committed (or
        found an empty queue) without error. Rate-limited to
        ``heartbeat_interval`` so a 2 s flush cadence does not flood ``poll_runs``.
        Best-effort: a failed heartbeat write is not a data-path error (coverage
        merely gets no positive evidence for this tick), it never raises out.

        D2 (normalization-read outage must earn no coverage): a heartbeat is
        positive proof the feed was CONNECTED *and draining successfully*. Draining
        is successful only when the raw retry buffer is empty (nothing consumed off
        the socket is stuck awaiting re-normalization) and no storage error is
        active. While a raw event is retained after a normalize-read blip, the feed
        has UNPROCESSED history: crediting coverage over that span would let an
        event-based detector treat unprocessed events as observed and false-clear a
        live issue (the B4 harm). So no beat fires until the raw buffer drains and
        the read recovers -- coverage correctly ends at the last truly-drained beat
        and detectors freeze to UNKNOWN over the unprocessed span. Heartbeats
        resume the moment reads recover and the raw buffer empties.
        """
        if self.connection_state != "connected":
            return
        if self._pending_raw or self._storage_error is not None:
            return
        ts = int(time.time()) if now is None else int(now)
        if (
            self._last_heartbeat_ts is not None
            and ts - self._last_heartbeat_ts < self._heartbeat_interval
        ):
            return
        # D5: this beat proves storage is healthy right now. Give the supervisor a
        # chance to hand off any events rescued from a prior listener while THIS
        # listener is still alive, instead of stranding them until it dies. BUG#4:
        # this drain must run BEFORE the heartbeat is committed, not after -- the
        # beat asserts the pipeline is fully drained, and committing it first would
        # credit coverage over a span the drain then reveals is still blocked.
        hook = self.on_healthy_drain
        if hook is not None:
            try:
                hook()
            except Exception:  # noqa: BLE001 - rescue drain must never break draining
                logger.exception("Rescued-event drain hook failed")
        # BUG#4: suppress the beat while the SUPERVISOR still holds an undrained
        # rescued event (normalized OR raw) or a storage error is active anywhere
        # upstream. A heartbeat = "the event pipeline is fully drained"; crediting
        # coverage while any rescued event is stuck (e.g. one still blocked by a
        # failing entity-resolution read the drain just retried) would let an
        # event-based detector treat unprocessed events as observed and false-clear
        # a live issue (the B4 harm), even though THIS listener's local buffers are
        # empty. A raising check is treated conservatively as "blocked".
        blocked = self.pipeline_blocked
        if blocked is not None:
            try:
                if blocked():
                    return
            except Exception:  # noqa: BLE001 - be conservative: no false liveness
                logger.exception("Pipeline-drain check failed; suppressing heartbeat")
                return
        try:
            self._repo.record_ws_heartbeat(ts=ts)
        except Exception:  # noqa: BLE001 - liveness accounting must never break draining
            logger.exception("Could not record WS liveness heartbeat")
            return
        self._last_heartbeat_ts = ts

    async def _periodic_flush(self) -> None:
        assert self._flush_interval is not None
        while True:
            await asyncio.sleep(self._flush_interval)
            try:
                self._flush()
                self._normalizer.reconcile_unresolved()
            except Exception as exc:  # keep the flusher alive; the batch remains queued
                self._storage_error = exc
                logger.exception("WS event storage flush failed; retaining %d events", len(self._batch))
                try:
                    self._repo.record_poll_run(
                        job="ws", ok=False, error=f"storage-failed: {type(exc).__name__}: {exc}"[:200],
                        source="live",
                    )
                except Exception:
                    logger.exception("Could not surface WS storage failure in poll_runs")
                # No heartbeat on a failed tick: the feed has no positive evidence
                # it is observing right now. When flushing RECOVERS, the next tick
                # resumes heartbeats -- reopening coverage and clearing the health
                # failure -- so a transient stall never closes coverage forever.
                continue
            # Positive liveness: connected AND just drained (or empty) with no error.
            self._maybe_heartbeat()

    async def run(self) -> int:
        """Drain the WS generator into the store until it ends.

        Returns when the underlying generator stops (its ``stop()`` was called,
        or a fatal re-auth failure propagated). Flushes any partial batch on the
        way out. Returns the total number of events written this run.
        """
        flusher: Optional[asyncio.Task[None]] = None
        completed = False
        cancelled = False
        if self._flush_interval:
            flusher = asyncio.create_task(self._periodic_flush())
        try:
            async for event in self._ws.events():
                # Retry any event whose normalization previously hit a transient
                # storage read blip; storage may have recovered since. A recovered
                # raw event moves into the batch here so it flushes with the rest.
                self._drain_pending_raw()
                try:
                    record = self._normalizer.normalize(event)
                except Exception as exc:  # noqa: BLE001 - classified below
                    # normalize() does entity-resolution DB READS. A transient
                    # storage error there must NOT drop the just-consumed event on
                    # the floor: it has no stat/event recovery source. Retain the
                    # RAW event for retry once the read recovers; only a genuinely
                    # malformed/undecodable payload (a non-transient error) is
                    # dropped-with-logging, matching normalize() returning None.
                    if not self._is_transient_storage_error(exc):
                        logger.exception(
                            "Dropping unstorable WS event (malformed payload)"
                        )
                        continue
                    self._storage_error = exc
                    self._pending_raw.append(event)
                    if self._total_pending() > self._max_pending:
                        # Sustained read outage: bound memory. The retained raw
                        # event stays in the buffer the supervisor rescues, so a
                        # raise here loses nothing.
                        raise RuntimeError("WS event storage queue is full")
                    continue
                if record is not None:
                    # Buffer the consumed event BEFORE any flush. A WS event has
                    # no stat/event recovery source, so once it is pulled off the
                    # generator it must live in the retained buffer before a flush
                    # that could raise (storage down) can strand it. Previously the
                    # capacity-triggered flush ran while this event was still only
                    # a local ``record``: at the exact overflow boundary that flush
                    # raised and the just-consumed event was lost, never reaching
                    # the batch the supervisor rescues. Appending first guarantees a
                    # capacity raise carries THIS event out with the rest of the
                    # batch, so nothing is dropped across the storage blip.
                    self._batch.append(record)
                    if self._total_pending() > self._max_pending:
                        # A bounded queue makes sustained storage loss visible
                        # instead of consuming unbounded RAM. Attempt to drain; the
                        # just-appended event is already retained, so a raise here
                        # loses nothing -- the whole batch (including it) is kept
                        # for rescue by the supervisor.
                        try:
                            self._flush()
                        except Exception as exc:
                            self._storage_error = exc
                            logger.exception("WS event storage flush failed; retaining batch")
                        if self._total_pending() > self._max_pending:
                            raise RuntimeError("WS event storage queue is full")
                    elif len(self._batch) >= self._batch_size:
                        try:
                            self._flush()
                        except Exception as exc:
                            self._storage_error = exc
                            logger.exception("WS event storage flush failed; retaining batch")
            completed = True
        except asyncio.CancelledError:
            # FINDING#5 root cause: a cancellation is propagating through this
            # teardown, and it MUST win over any storage error the final flush
            # raises. The old teardown decided "am I cancelling?" from
            # ``current_task().cancelling() > 0`` -- but a CancelledError raised by
            # awaiting an ALREADY-CANCELLED future carries cancelling()==0, so that
            # count-based check missed it, the teardown flush's OperationalError
            # REPLACED the CancelledError, and the supervisor saw an ordinary death
            # (returned normally / restarted; task.cancelled()==False). Catching the
            # ``asyncio.CancelledError`` ITSELF -- a separate clause from
            # ``except Exception`` (it is a BaseException in 3.11, so an
            # ``except Exception`` can never see it) -- is the signal, independent of
            # HOW the cancel arose: a direct ``.cancel()``, an inherited cancel, or
            # an already-cancelled awaited future all land here. Flag it so the
            # shared teardown below swallows a flush storage error and re-raise so
            # the cancel always propagates (task.cancelled()==True).
            cancelled = True
            raise
        finally:
            if flusher is not None:
                flusher.cancel()
                await asyncio.gather(flusher, return_exceptions=True)
            # A storage failure in the final ``_flush()`` must NEVER replace an
            # in-flight CancelledError. A raw exception raised from a ``finally``
            # silently REPLACES the exception propagating through it -- so an
            # ``OperationalError`` from a teardown flush would turn a shutdown into a
            # spurious listener DEATH. ``cancelled`` is set only by the explicit
            # ``except asyncio.CancelledError`` clause above, so it is true for EVERY
            # cancel regardless of ``cancelling()`` count. On a flush failure during
            # cancellation, retain the batch (the supervisor rescues it) and swallow
            # the storage error so the pending CancelledError resumes propagating
            # out of ``run`` untouched. The batch is preserved either way --
            # ``_flush`` removes nothing until SQLite commits -- so a normal final
            # flush still commits and clears exactly once, and a non-cancel flush
            # failure still RAISES so supervisor/health report it.
            try:
                self._flush()
                self._normalizer.reconcile_unresolved()
            except Exception as exc:  # noqa: BLE001 - classified by cancel state
                self._storage_error = exc
                if not cancelled:
                    # Normal (non-shutdown) death: surface the failure as before.
                    raise
                # Shutdown: keep the batch for the supervisor to rescue and let the
                # cancel win. Swallowing here (not returning) lets the original
                # CancelledError resume propagating out of ``run``.
                logger.exception(
                    "WS event storage flush failed during cancellation; retaining "
                    "%d events for rescue and propagating the cancel",
                    len(self._batch),
                )
            # The production low-level listener returns normally only when its
            # subscription is unavailable (a requested stop is not a failure).
            ws_stop = getattr(self._ws, "_stop", None)
            stopped = bool(getattr(ws_stop, "is_set", lambda: False)())
            if completed and not stopped:
                self.terminal_state = "unsupported"
        return self.written


class WsSupervisor:
    """Keep a WS :class:`EventListener` alive across drops and deaths.

    Each attempt builds a fresh listener from ``factory`` and runs it to
    completion. A clean end resets the backoff; an exception grows it, capped at
    ``backoff_max``. Every transition (``started`` / ``stopped`` / error) is
    written to ``poll_runs`` under ``job='ws'`` so listener health is queryable,
    never inferred. ``stop()`` ends the loop after the current attempt.
    """

    def __init__(
        self,
        factory: Callable[[], EventListener],
        repo: Repository,
        *,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        max_restarts: Optional[int] = None,
        pending_max: int = _SYSTEM_PENDING_MAX,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._factory = factory
        self._repo = repo
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._max_restarts = max_restarts
        # P1: system-wide bound on rescued+pending events (see _SYSTEM_PENDING_MAX).
        self._pending_max = max(1, pending_max)
        self._sleep = sleep
        self._stop = asyncio.Event()
        self.state = "reconnecting"
        # R2: events a dying listener buffered but could not commit live HERE, on
        # the supervisor, not on the listener it is about to discard. A storage
        # blip that kills the listener leaves the batch stranded otherwise; the
        # supervisor retries it on the next attempt once storage recovers.
        self._pending: list[dict[str, Any]] = []
        # P1 (normalize-read blip): raw events a dying listener consumed but could
        # not normalize because a transient storage READ error blocked their
        # entity lookup. They are rescued here and re-normalized on the next drain
        # (storage may have recovered), then join ``_pending``. A raw WS event has
        # no stat/event recovery source, so it cannot be discarded on the death of
        # the listener that held it.
        self._pending_raw: list[Event] = []
        # Re-normalizer for rescued raw events. Its own entity-resolution cache is
        # independent of any listener's; correctness does not depend on the cache.
        self._normalizer = EventNormalizer(repo)
        # P1: count of events evicted under sustained storage failure once the
        # aggregate pending buffer hit its system-wide cap. Exposed (not just
        # logged) so the loss is observable rather than silent.
        self.dropped = 0

    def stop(self) -> None:
        self._stop.set()

    def _on_listener_state(self, state: str) -> None:
        """R3: the supervisor's health follows the listener's ACTUAL socket state.

        Called by the listener when its handshake succeeds ("connected") or it
        drops/backs off ("reconnecting"). Never inferred from the task existing.

        These ``connected``/``disconnected`` rows drive the health-state STRING
        only. B4 (positive-liveness redesign): event-source coverage no longer
        derives from these transitions -- it is credited solely from the periodic
        liveness HEARTBEATS the listener writes while connected and draining (see
        :meth:`EventListener._maybe_heartbeat` and
        :meth:`Repository._ws_observed_intervals`). So correctness does NOT depend
        on any close row: if this ``disconnected`` accounting write raises (it is
        swallowed by ``_record``) or is skipped entirely on a shutdown/cancel/crash
        path, coverage still ends at the last heartbeat and cannot over-credit
        through end_ts. The row is kept for observability on clean transitions.
        """
        if state == self.state:
            return
        self.state = state
        self._record("connected" if state == "connected" else "disconnected", ok=True)

    def _pending_blocked(self) -> bool:
        """BUG#4: is any rescued event still undrained anywhere on the supervisor?

        A replacement listener consults this before crediting a liveness heartbeat.
        While the supervisor still holds a rescued normalized batch (``_pending``)
        or a rescued raw event awaiting re-normalization (``_pending_raw`` -- e.g.
        one blocked by a failing entity-resolution read), the event pipeline is NOT
        fully drained, so no positive-liveness beat may fire over that span. Both
        buffers empty is the only "fully drained" state; a storage error that keeps
        the drain from clearing them leaves at least one non-empty, so it is
        captured here too.
        """
        return bool(self._pending or self._pending_raw)

    def _drain_pending(self) -> None:
        """R2: retry events rescued from a replaced listener.

        Called at each restart (storage may have recovered) and once more on
        teardown. Kept-or-cleared atomically: on a still-failing write the batch
        stays queued for the next attempt rather than being lost.
        """
        # First re-normalize any RAW events rescued from a listener that died while
        # a transient storage READ error blocked their normalization -- the read
        # may have recovered by now. Successes join ``_pending`` and are written
        # below; a still-failing read keeps them raw for the next attempt.
        self._renormalize_pending_raw()
        if not self._pending:
            return
        try:
            self._repo.record_events_enriching_entities(self._pending)
        except Exception:  # storage still down; keep the batch for the next try
            logger.exception(
                "Rescued WS batch still cannot be stored; retaining %d events",
                len(self._pending),
            )
            return
        self._pending = []

    def _renormalize_pending_raw(self) -> None:
        """Re-normalize rescued raw events once storage may have recovered (P1).

        A raw event that now normalizes moves onto ``_pending`` for the drain to
        persist; one that normalizes to ``None`` is permanently unstorable and
        dropped; one whose entity lookup still raises a transient storage error is
        kept (in order) for the next attempt, and processing stops there.
        """
        if not self._pending_raw:
            return
        raws = self._pending_raw
        self._pending_raw = []
        for i, event in enumerate(raws):
            try:
                record = self._normalizer.normalize(event)
            except Exception as exc:  # noqa: BLE001 - classified below
                if EventListener._is_transient_storage_error(exc):
                    self._pending_raw.extend(raws[i:])
                    break
                logger.exception(
                    "Dropping unstorable rescued WS event (malformed payload)"
                )
                continue
            if record is not None:
                self._pending.append(record)
        self._enforce_pending_bound()

    def _rescue_pending(self, listener: EventListener) -> None:
        """Move a dead listener's uncommitted batch onto the supervisor (R2),
        under a SYSTEM-WIDE memory bound (P1).

        The per-listener ``_max_pending`` bounds ONE batch; nothing bounded the
        supervisor's aggregate. Under sustained total storage failure with a live
        producer, each dead listener's rescued batch was appended and another
        started, so ``_pending`` grew without limit (1001, 2002, 3003, ...) until
        the process OOM'd and lost EVERYTHING. We rescue, then clamp the aggregate
        to :attr:`_pending_max`: a bounded, counted, observable loss is correct
        under a real outage; unbounded growth is not.
        """
        leftover = getattr(listener, "pending_records", lambda: [])()
        if leftover:
            self._pending.extend(leftover)
        # Also take custody of events the listener consumed but could not normalize
        # because a transient storage READ blip blocked their entity lookup (P1).
        # These have no stat/event recovery source either; they are re-normalized
        # on the next drain once the read recovers.
        raw_leftover = getattr(listener, "pending_raw_records", lambda: [])()
        if raw_leftover:
            self._pending_raw.extend(raw_leftover)
        self._enforce_pending_bound()

    def _enforce_pending_bound(self) -> None:
        """Clamp the aggregate rescued buffer to the system-wide cap (P1).

        DROP-OLDEST: the just-rescued (newest) events are the likeliest to still
        matter for a live incident, so when the aggregate overflows we evict the
        oldest retained events, count the loss, and surface it. Everything left in
        ``_pending`` is still flushed the instant storage returns, so recovery of
        the bounded survivors is preserved.
        """
        # BUG#6: the cap is SYSTEM-WIDE and must bound the AGGREGATE of both
        # buffers combined, never each independently -- capping them separately let
        # cap=3 retain SIX (3 raw + 3 normalized), defeating the memory bound. And
        # eviction must drop the genuinely OLDEST event across BOTH buffers by event
        # time, not blindly delete a queue prefix: recovery re-normalizes rescued
        # RAW events and appends them (older, by arrival) BEHIND newer normalized
        # ones, so a prefix delete discarded the NEWER survivors while the old ones
        # lived. Rank every retained event by timestamp across both buffers and
        # evict only the oldest overflow-many, keeping the newest -- the likeliest
        # to still matter for a live incident -- regardless of buffer or position.
        overflow = (len(self._pending) + len(self._pending_raw)) - self._pending_max
        if overflow <= 0:
            return
        ranked: list[tuple[int, int, int]] = []  # (event_ts, buffer, index)
        for i, rec in enumerate(self._pending):
            ts = rec.get("ts")
            ranked.append((ts if isinstance(ts, int) else 0, 0, i))
        for i, ev in enumerate(self._pending_raw):
            ts = _to_epoch_s(ev)
            ranked.append((ts if ts is not None else 0, 1, i))
        # Oldest first; ties break by (buffer, index) so eviction is deterministic
        # and, within a buffer, removes the earliest-arrived first.
        ranked.sort()
        evict = ranked[:overflow]
        drop_norm = {idx for _ts, buf, idx in evict if buf == 0}
        drop_raw = {idx for _ts, buf, idx in evict if buf == 1}
        if drop_norm:
            self._pending = [r for i, r in enumerate(self._pending) if i not in drop_norm]
        if drop_raw:
            self._pending_raw = [e for i, e in enumerate(self._pending_raw) if i not in drop_raw]
        dropped_now = len(evict)
        self.dropped += dropped_now
        logger.error(
            "WS pending buffer hit system-wide cap of %d under sustained storage "
            "failure; dropped %d oldest event(s) (cumulative dropped=%d) to bound "
            "memory. Retained %d normalized + %d raw event(s) for recovery once "
            "storage returns.",
            self._pending_max,
            dropped_now,
            self.dropped,
            len(self._pending),
            len(self._pending_raw),
        )
        # Surface the loss to poll_runs too (best-effort; _record is guarded, so a
        # concurrent storage outage that fails this write never breaks the rescue).
        self._record(f"pending-overflow-dropped:{dropped_now}", ok=False)

    def _record(self, label: str, *, ok: bool, duration_ms: Optional[int] = None) -> None:
        """R2: health accounting is best-effort observability, never the data path.

        A failure writing this ``poll_runs`` liveness row (e.g. the same storage
        outage that killed the listener) must never propagate out of the
        supervisor loop -- doing so would skip the pending-record rescue/drain and
        strand buffered events that have no ``stat/event`` recovery source. Swallow
        and log so the event hand-off always runs.
        """
        try:
            self._repo.record_poll_run(
                job="ws", ok=ok, error=label, duration_ms=duration_ms, source="live"
            )
        except Exception:  # noqa: BLE001 - accounting must never break the data path
            logger.exception("Could not record WS poll_run (%s); continuing", label)

    async def run(self) -> None:
        backoff = self._backoff_base
        restarts = 0
        # BUG#5: wrap the WHOLE supervise loop so a CancelledError that lands
        # ANYWHERE -- during ``listener.run()`` (handled inline for observability)
        # OR during the backoff ``self._sleep`` BETWEEN listeners (outside that
        # inline handler) -- always triggers one best-effort FINAL drain before it
        # propagates. Production ``SupervisorTask.stop()`` cancels this task, and
        # that cancel can arrive mid-backoff after storage has recovered; without
        # this finally the loop unwinds straight past the post-loop drain and a
        # rescued batch is stranded despite healthy storage at shutdown. The finally
        # also covers the normal stop / give-up exits (the old post-loop drain).
        try:
            while not self._stop.is_set():
                self.state = "reconnecting"
                # R2: retry any batch rescued from a prior listener before starting a
                # fresh one -- storage may have recovered during the backoff.
                self._drain_pending()
                listener = self._factory()
                # R3: report the listener's REAL connection state, not an assumption.
                listener.on_connection_state = self._on_listener_state
                # D5: drain rescued events DURING this listener's life. The listener
                # fires this whenever it proves storage healthy (a heartbeat), so
                # recovered storage persists rescued events promptly rather than
                # leaving them queued until this replacement listener itself dies.
                listener.on_healthy_drain = self._drain_pending
                # BUG#4: let the listener check the supervisor's rescued buffers
                # before it credits a heartbeat, so an empty replacement listener
                # cannot book coverage while a rescued event is still stuck upstream.
                listener.pipeline_blocked = self._pending_blocked
                self._record("started", ok=True)
                start = monotonic()
                clean = True
                error: Optional[str] = None
                try:
                    # Do NOT pre-declare "connected": the handshake has not happened
                    # yet. The listener flips us to "connected" once its socket is up
                    # (a quiet-but-connected socket still reports connected), and back
                    # to "reconnecting" on a drop -- state tracks the socket, not the
                    # mere existence of this task.
                    await listener.run()
                except asyncio.CancelledError:
                    self.state = "stopped"
                    # Clean-path observability only: record the close so the health
                    # STRING reflects the stop. B4: coverage does not depend on this
                    # -- the liveness heartbeats already stopped, so coverage ends at
                    # the last beat whether or not this row is written.
                    try:
                        self._record("disconnected", ok=True)
                        self._rescue_pending(listener)
                    finally:
                        # D5: a FINAL drain BEFORE propagating the cancel. Shutdown
                        # cancels this loop mid-attempt, bypassing the post-loop
                        # ``_drain_pending``; without a drain here the rescued events
                        # (and this dying listener's just-rescued batch) would be
                        # stranded even when storage is healthy at shutdown. Guarded
                        # (``_drain_pending`` swallows its own storage errors), so the
                        # cancel always propagates. (The outer finally is belt-and-
                        # braces and covers the backoff-cancel path too.)
                        self._drain_pending()
                    raise
                except Exception as exc:  # noqa: BLE001 - firewall: any death is recoverable
                    # FINDING#5 (belt-and-braces): a storage failure in listener
                    # teardown can surface HERE as an ordinary exception even while
                    # this task is being cancelled -- if any cleanup path still lets
                    # a non-CancelledError replace the cancel. When a cancel is
                    # actually masked this is a SHUTDOWN, not a listener death:
                    # rescue + final-drain the dying listener's buffers and re-raise
                    # CancelledError so ``stop()`` reliably stops instead of
                    # returning normally (task.cancelled()==False) or restarting.
                    #
                    # Do NOT rely solely on ``current_task().cancelling() > 0``: a
                    # CancelledError raised by awaiting an ALREADY-CANCELLED future
                    # carries cancelling()==0, so a count-based test alone can be
                    # defeated (round-12 verifier). When a ``finally``-raised
                    # exception replaces a propagating CancelledError, Python links
                    # that CancelledError into the replacement's ``__context__`` /
                    # ``__cause__`` chain -- so a masked cancel is detectable from the
                    # exception itself, independent of the cancelling() count and of
                    # HOW the cancel arose. (In practice the listener now re-raises
                    # the CancelledError directly and this branch never fires for
                    # FINDING#5; it remains as a robust net for any residual path.)
                    current = asyncio.current_task()
                    count_cancelling = (
                        current is not None
                        and getattr(current, "cancelling", lambda: 0)() > 0
                    )
                    if count_cancelling or _masks_cancel(exc):
                        self.state = "stopped"
                        try:
                            self._rescue_pending(listener)
                        finally:
                            self._drain_pending()
                        raise asyncio.CancelledError() from exc
                    clean = False
                    error = f"{type(exc).__name__}: {exc}"
                    lowered = error.lower()
                    if "storage" in lowered or "sqlite" in lowered:
                        self.state = "storage-failed"
                    elif "auth" in lowered or "401" in lowered or "403" in lowered:
                        self.state = "auth-failed"
                    else:
                        self.state = "reconnecting"
                    logger.warning("WS listener died: %s", error)
                else:
                    if getattr(listener, "terminal_state", None) == "unsupported":
                        # The low-level listener returns cleanly only when the
                        # controller cannot offer a usable events subscription.
                        self.state = "unsupported"
                        clean = False
                        error = "unsupported"
                duration_ms = int((monotonic() - start) * 1000)
                # R2: take custody of anything this dying listener buffered but could
                # not commit BEFORE writing any health accounting. The rescue must
                # never sit behind the terminal ``_record`` -- if both the event
                # store and the accounting write are failing, an accounting raise
                # ahead of the rescue would strand the buffered batch. Rescue first,
                # then account (and ``_record`` is itself guarded, belt and braces).
                self._rescue_pending(listener)
                self._record(error or "stopped", ok=clean, duration_ms=duration_ms)

                if self._stop.is_set():
                    break
                restarts += 1
                if self._max_restarts is not None and restarts > self._max_restarts:
                    logger.error("WS supervisor gave up after %d restarts.", restarts - 1)
                    break
                # A cancel HERE -- during the between-listeners backoff -- lands
                # outside the inline run()-cancel handler above. The outer finally is
                # what guarantees the final drain on that path (BUG#5).
                await self._sleep(backoff)
                backoff = self._backoff_base if clean else min(backoff * 2, self._backoff_max)
        finally:
            # R2 + BUG#5: a final attempt to persist rescued events (storage may have
            # come back by the time the loop ends OR is cancelled) so a blip +
            # shutdown -- wherever the cancel lands -- does not drop them.
            self._drain_pending()
        if self._stop.is_set():
            self.state = "stopped"


__all__ = [
    "EventNormalizer",
    "EventListener",
    "WsSupervisor",
    "catchup_events",
    "newest_stored_event_ts",
]
