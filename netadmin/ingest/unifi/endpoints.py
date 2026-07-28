"""Typed read-endpoint wrappers for the section 5.1 read set.

Only the endpoints the daemon actually consumes are wrapped here; the write set
(Phase 4) and the unofficial v2 endpoints are deliberately absent. Every method
returns parsed pydantic models from :mod:`netadmin.ingest.unifi.models`.

UniFi API conventions encoded here:

* ``stat/device``, ``stat/sta``, ``stat/health`` are GETs.
* ``stat/report/{interval}.{scope}`` is a POST with ``{attrs, start, end}`` in
  **milliseconds**.
* ``stat/event`` is a POST paged with ``_start`` / ``_limit`` (3000/page cap).
* ``stat/session`` is a POST with **seconds** timestamps.
* ``stat/rogueap`` uses ``within`` (hours); ``stat/anomalies`` uses a ms window.
* ``rest/wlanconf`` is a GET. It is a *read* of the WLAN config, not a write --
  the read-only posture holds (a write would be a PUT/POST to the same route).
"""

from __future__ import annotations

from typing import Any, Optional

from netadmin.logging import get_logger

from .auth import UnifiError
from .client import UnifiClient
from .models import (
    Alarm,
    Anomaly,
    Client,
    Device,
    Event,
    HealthSubsystem,
    ReportRow,
    RogueAp,
    Session,
    Wlan,
)

logger = get_logger("ingest.unifi.endpoints")

# stat/event hard page cap enforced by the controller.
EVENT_PAGE_CAP = 3000

# Candidate event-log endpoints, tried in order (LIVE-VALIDATED QUIRK).
#
# The classic ``stat/event`` route has been REMOVED on this UniFi OS console
# (Network 9.x): it answers EVERY method and body form -- GET, POST, with or
# without ``_start`` / ``_limit`` / ``within`` -- with ``404 api.err.NotFound``.
# The surviving route on such consoles is ``list/event`` (confirmed present: it
# returns ``400 api.err.InvalidObject`` to an empty body rather than a 404, i.e.
# the route exists but validates its body). Legacy / self-hosted controllers
# still serve ``stat/event``. We probe in order, stick to the first that answers,
# and -- when NONE is usable -- log once and return ``[]`` so catch-up degrades to
# the live WebSocket instead of throwing every poll cycle (the daemon bug this
# fixes). ``list/alarm`` and ``stat/anomalies`` remain the separate alarm/anomaly
# feeds (ARCHITECTURE.md 5.1); they are not substitutes for the EVT_* stream.
_EVENT_ENDPOINTS: tuple[str, ...] = ("stat/event", "list/event")

# ``meta.msg`` markers meaning "this route/body is not usable on this console" ->
# fall through to the next candidate (or degrade), never crash the poll cycle.
# Matched case-insensitively against the raised ``UnifiError`` message.
_ROUTE_ABSENT_MARKERS: tuple[str, ...] = ("api.err.notfound", "api.err.invalidobject")


def _route_absent(exc: UnifiError) -> bool:
    """True when a ``UnifiError`` means the route/body is unusable on this console.

    Distinguishes "this console does not serve this endpoint" (a 404 ``NotFound``
    or a 400 ``InvalidObject``) -- which should fall through to the next candidate
    or degrade to no data -- from a genuine server/transport error, which must
    surface. Shared by :meth:`Endpoints.stat_event` (fall through),
    :meth:`Endpoints.rest_wlanconf` (degrade per call) and
    :meth:`Endpoints.list_alarm` (degrade, sticky for the session).
    """
    text = str(exc).lower()
    return any(marker in text for marker in _ROUTE_ABSENT_MARKERS)


REPORT_INTERVALS = frozenset({"5minutes", "hourly", "daily"})
REPORT_SCOPES = frozenset({"ap", "user", "gw", "site"})

# Sensible default attr sets per report scope. Callers may override.
DEFAULT_REPORT_ATTRS: dict[str, list[str]] = {
    "ap": ["bytes", "rx_bytes", "tx_bytes", "num_sta", "satisfaction", "time"],
    "user": ["rx_bytes", "tx_bytes", "signal", "satisfaction", "time"],
    "gw": ["lan-rx_bytes", "lan-tx_bytes", "wan-rx_bytes", "wan-tx_bytes", "time"],
    "site": ["bytes", "num_sta", "wan-rx_bytes", "wan-tx_bytes", "time"],
}


class Endpoints:
    """Read-endpoint facade over a connected :class:`UnifiClient`."""

    def __init__(self, client: UnifiClient) -> None:
        self._c = client
        # Discovered working event endpoint (sticky). None = not yet probed;
        # once an endpoint answers we reuse it, and once every candidate proves
        # absent we set ``_event_disabled`` and short-circuit to [] (see
        # :meth:`stat_event`). Reset on process restart, which re-probes.
        self._event_endpoint: Optional[str] = None
        self._event_disabled: bool = False
        # Sticky "this console has no usable alarm read route" latch; see
        # :meth:`list_alarm`. Reset on process restart, which re-probes.
        self._alarm_disabled: bool = False

    # --------------------------------------------------------------- #
    # 60 s cadence
    # --------------------------------------------------------------- #
    async def stat_device(self) -> list[Device]:
        rows = await self._c.get_data("stat/device")
        return [Device.model_validate(r) for r in rows]

    async def stat_sta(self) -> list[Client]:
        rows = await self._c.get_data("stat/sta")
        return [Client.model_validate(r) for r in rows]

    async def stat_health(self) -> list[HealthSubsystem]:
        rows = await self._c.get_data("stat/health")
        return [HealthSubsystem.model_validate(r) for r in rows]

    # --------------------------------------------------------------- #
    # events (paged, 3000/page cap)
    # --------------------------------------------------------------- #
    async def stat_event(
        self,
        *,
        within_hours: Optional[int] = None,
        max_events: Optional[int] = None,
    ) -> list[Event]:
        """Page the event log, drained or until ``max_events``, with fallback.

        ``within_hours`` limits to the last N hours (controller-side). Each page
        returns at most :data:`EVENT_PAGE_CAP` rows.

        Endpoint selection is a LIVE-VALIDATED QUIRK (see :data:`_EVENT_ENDPOINTS`
        for the full note): this console removed ``stat/event`` (hard 404
        ``api.err.NotFound`` for every method/body), so the method probes the
        candidates in order, GET-then-POST per page, sticks to the first that
        answers, and if none is usable logs once and returns ``[]`` -- catch-up
        then rides on the live WebSocket rather than raising every poll cycle.
        Both verbs are read-only (section 5.1 read set).
        """
        if self._event_disabled:
            return []
        if self._event_endpoint is not None:
            return await self._page_events(self._event_endpoint, within_hours, max_events)

        for endpoint in _EVENT_ENDPOINTS:
            try:
                events = await self._page_events(endpoint, within_hours, max_events)
            except UnifiError as exc:
                if _route_absent(exc):
                    logger.debug(
                        "event endpoint %r not usable on this console (%s); trying next",
                        endpoint,
                        exc,
                    )
                    continue
                raise
            self._event_endpoint = endpoint
            if endpoint != _EVENT_ENDPOINTS[0]:
                logger.info(
                    "Event catch-up bound to %r (classic stat/event absent on this console).",
                    endpoint,
                )
            return events

        self._event_disabled = True
        logger.warning(
            "No event-log endpoint available on this console (tried %s, all "
            "api.err.NotFound/InvalidObject); stat/event catch-up disabled for "
            "this session -- the live WebSocket remains the event source.",
            ", ".join(repr(e) for e in _EVENT_ENDPOINTS),
        )
        return []

    async def _page_events(
        self,
        endpoint: str,
        within_hours: Optional[int],
        max_events: Optional[int],
    ) -> list[Event]:
        """Page one event endpoint with ``_start`` (GET first, POST fallback).

        Once GET fails on a page the method sticks to POST for the remaining
        pages rather than re-eating the same failure each page. A terminal
        ``UnifiError`` (both verbs failed) propagates to :meth:`stat_event`,
        which decides fall-through vs. surface via :func:`_route_absent`.
        """
        events: list[Event] = []
        start = 0
        use_get = True
        while True:
            body: dict[str, Any] = {"_start": start, "_limit": EVENT_PAGE_CAP}
            if within_hours is not None:
                body["within"] = within_hours
            if use_get:
                try:
                    rows = await self._c.get_data(endpoint, body)
                except UnifiError:
                    use_get = False
                    rows = await self._c.post_data(endpoint, body)
            else:
                rows = await self._c.post_data(endpoint, body)
            events.extend(Event.model_validate(r) for r in rows)
            if len(rows) < EVENT_PAGE_CAP:
                break
            if max_events is not None and len(events) >= max_events:
                break
            start += EVENT_PAGE_CAP
        if max_events is not None:
            return events[:max_events]
        return events

    # --------------------------------------------------------------- #
    # reports (POST, attrs + start + end in ms)
    # --------------------------------------------------------------- #
    async def stat_report(
        self,
        interval: str,
        scope: str,
        *,
        start_ms: int,
        end_ms: int,
        attrs: Optional[list[str]] = None,
    ) -> list[ReportRow]:
        """``stat/report/{interval}.{scope}`` over ``[start_ms, end_ms]``."""
        if interval not in REPORT_INTERVALS:
            raise ValueError(
                f"interval must be one of {sorted(REPORT_INTERVALS)}, got {interval!r}"
            )
        if scope not in REPORT_SCOPES:
            raise ValueError(f"scope must be one of {sorted(REPORT_SCOPES)}, got {scope!r}")
        selected = attrs if attrs is not None else DEFAULT_REPORT_ATTRS[scope]
        if "time" not in selected:
            selected = [*selected, "time"]
        body = {"attrs": selected, "start": start_ms, "end": end_ms}
        rows = await self._c.post_data(f"stat/report/{interval}.{scope}", body)
        return [ReportRow.model_validate(r) for r in rows]

    async def stat_report_5min(
        self, scope: str, *, start_ms: int, end_ms: int, attrs: Optional[list[str]] = None
    ) -> list[ReportRow]:
        return await self.stat_report(
            "5minutes", scope, start_ms=start_ms, end_ms=end_ms, attrs=attrs
        )

    async def stat_report_hourly(
        self, scope: str, *, start_ms: int, end_ms: int, attrs: Optional[list[str]] = None
    ) -> list[ReportRow]:
        return await self.stat_report(
            "hourly", scope, start_ms=start_ms, end_ms=end_ms, attrs=attrs
        )

    async def stat_report_daily(
        self, scope: str, *, start_ms: int, end_ms: int, attrs: Optional[list[str]] = None
    ) -> list[ReportRow]:
        return await self.stat_report("daily", scope, start_ms=start_ms, end_ms=end_ms, attrs=attrs)

    # --------------------------------------------------------------- #
    # per-client forensics (seconds timestamps)
    # --------------------------------------------------------------- #
    async def stat_session(
        self,
        mac: str,
        *,
        start_s: int,
        end_s: int,
        type_: str = "all",
    ) -> list[Session]:
        """``stat/session`` for one client. ``start_s``/``end_s`` are seconds."""
        body = {"mac": mac, "type": type_, "start": start_s, "end": end_s}
        rows = await self._c.post_data("stat/session", body)
        return [Session.model_validate(r) for r in rows]

    # --------------------------------------------------------------- #
    # neighbor / anomaly signals
    # --------------------------------------------------------------- #
    async def stat_rogueap(self, *, within_hours: int = 24) -> list[RogueAp]:
        rows = await self._c.post_data("stat/rogueap", {"within": within_hours})
        return [RogueAp.model_validate(r) for r in rows]

    async def rest_wlanconf(self) -> list[Wlan]:
        """``rest/wlanconf`` -- our configured SSIDs (GET, read-only).

        Returns ``[]`` when the console does not serve the route (some older or
        locked-down consoles answer ``api.err.NotFound``): an absent route is
        data, not a failure, and the caller degrades to the client-ESSID fallback
        rather than crashing the daily inventory read.
        """
        try:
            rows = await self._c.get_data("rest/wlanconf")
        except UnifiError as exc:
            if not _route_absent(exc):
                raise
            logger.info("rest/wlanconf is not served by this console; WLAN config unread")
            return []
        return [Wlan.model_validate(r) for r in rows]

    async def list_alarm(self, *, archived: bool = False) -> list[Alarm]:
        """``list/alarm`` -- controller alarms (LIVE-VALIDATED QUIRK).

        Some UniFi OS consoles no longer serve any classic alarm read route: this
        one answers ``400 api.err.InvalidObject`` to our POST *and* to a bare GET
        carrying no payload at all, so no body shape can satisfy it -- the same
        removal already documented for ``stat/event`` (see :data:`_EVENT_ENDPOINTS`).
        When that happens we log once, latch off for the session and return ``[]``:
        alarms are a supplementary feed (the live WebSocket carries the events), and
        a permanently failing job would hold ``/api/health`` at ``degraded`` forever,
        which costs the health banner its credibility. Genuine 5xx / transport
        errors still raise, so a real outage still fails the job loudly, and the
        latch resets on restart so firmware that restores the route recovers itself.
        """
        if self._alarm_disabled:
            return []
        try:
            rows = await self._c.post_data("list/alarm", {"archived": archived})
        except UnifiError as exc:
            if not _route_absent(exc):
                raise
            self._alarm_disabled = True
            logger.warning(
                "No alarm route available on this console (list/alarm -> %s); alarm "
                "ingest disabled for this session -- the live WebSocket remains the "
                "event source.",
                exc,
            )
            return []
        return [Alarm.model_validate(r) for r in rows]

    async def stat_anomalies(
        self, *, start_ms: Optional[int] = None, end_ms: Optional[int] = None
    ) -> list[Anomaly]:
        params: dict[str, Any] = {}
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms
        rows = await self._c.get_data("stat/anomalies", params or None)
        return [Anomaly.model_validate(r) for r in rows]


__all__ = [
    "Endpoints",
    "EVENT_PAGE_CAP",
    "REPORT_INTERVALS",
    "REPORT_SCOPES",
    "DEFAULT_REPORT_ATTRS",
]
