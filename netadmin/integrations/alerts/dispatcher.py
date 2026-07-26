"""The outbound alert dispatcher (``docs/ARCHITECTURE.md`` 20).

Turns issue-lifecycle transitions into Discord / Slack / ntfy / raw-webhook
deliveries. Three hard properties, the same three the Home Assistant bridge holds:

* **Off by default.** ``alerts.enabled`` defaults False and every delivery URL comes
  from ``data/secrets.env``. Disabled, or with no channel configured, ``start`` is a
  total no-op: no engine callback, no tasks, no HTTP client.
* **The daemon never notices a broken webhook.** The engine feeds this through one
  sync, fire-and-forget callback that only ``put_nowait``s onto a bounded queue --
  it never blocks and never raises. All HTTP happens in isolated worker tasks.
* **One dead channel cannot stall another.** A router task fans accepted events onto
  a *per-channel* bounded queue, each drained by its own worker. A webhook that
  hangs for its full timeout on every attempt backs up only its own queue.

Ordering matters as much as isolation: retries run in-line in the channel worker, so
per-channel delivery stays FIFO and a ``resolved`` can never overtake the ``opened``
it resolves.

Nothing is silently lost. Rate-limited events coalesce into a digest, overflow is
counted as ``dropped``, and both counters surface at ``/api/health``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from netadmin.config import AlertChannelConfig, AlertsConfig, AlertSecrets, Settings
from netadmin.integrations.alerts import formats
from netadmin.integrations.alerts.models import (
    STATUS_FAILING,
    STATUS_INERT,
    STATUS_OK,
    AlertEvent,
    ChannelStatus,
    DigestSummary,
    Payload,
)
from netadmin.integrations.alerts.policy import DIGEST, SKIP, ChannelPolicy, classify
from netadmin.integrations.alerts.transport import AlertTransport, HttpxTransport, TransportError
from netadmin.issues.engine import IssueEngine
from netadmin.issues.models import Transition
from netadmin.logging import get_logger

_log = get_logger("integrations.alerts")

__all__ = ["AlertDispatcher", "build_alert_dispatcher"]

# --- tunables (section 20) ------------------------------------------------- #

# The engine-facing intake queue. Bounded so a wedged dispatcher cannot grow
# memory without limit; overflow is counted, never blocking.
_INTAKE_MAX = 512
# How often an intake drop is logged. The first drop always logs; after that only
# every Nth, so a burst larger than the queue leaves a breadcrumb without flooding
# the log. ``/api/health`` still reports the exact drop count.
_DROP_LOG_EVERY = 500
# Per-channel queue. Smaller: a channel this far behind is already failing.
_CHANNEL_QUEUE_MAX = 128

_MAX_ATTEMPTS = 5
_BACKOFF_INITIAL_S = 2.0
_BACKOFF_CAP_S = 60.0
# A server may ask for an absurd Retry-After; honour it, but not past this.
_RETRY_AFTER_CAP_S = 300.0
# Consecutive failed deliveries before a channel reports ``failing``. It keeps
# attempting future events -- no silent self-disable, the operator decides.
_FAILING_THRESHOLD = 5
# Graceful-stop budget: drain queued deliveries, then cancel.
_STOP_GRACE_S = 5.0

Sleeper = Callable[[float], Awaitable[None]]


class _Sentinel:
    """A queue marker (stop). Identity-compared, never delivered."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self.name}>"


_STOP = _Sentinel("alert-stop")


def _drain(queue: "asyncio.Queue[Any]") -> None:
    """Discard everything left on a queue. Used at stop, never while running."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


@dataclass(frozen=True)
class _Item:
    """One unit of channel work: deliver this event, or fold it into the digest."""

    event: AlertEvent
    digest: bool = False


class _Channel:
    """Runtime state for one configured channel: config, secret, policy, counters."""

    def __init__(
        self,
        cfg: AlertChannelConfig,
        url: Optional[str],
        token: Optional[str],
        *,
        clock: Callable[[], float],
    ) -> None:
        self.cfg = cfg
        self.url = url
        self.token = token
        self.policy = ChannelPolicy(cfg, clock=clock)
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_CHANNEL_QUEUE_MAX)
        self.task: Optional[asyncio.Task[None]] = None
        self.status = ChannelStatus(
            name=cfg.name,
            type=cfg.type,
            configured=bool(url),
            status=STATUS_OK if url else STATUS_INERT,
        )

    @property
    def live(self) -> bool:
        """True when this channel has a delivery URL and can actually send."""
        return bool(self.url)

    def auth_headers(self) -> dict[str, str]:
        """The optional bearer header (ntfy access token, authenticated webhook)."""
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


class AlertDispatcher:
    """Fans issue transitions out to the configured outbound channels.

    Built by :func:`build_alert_dispatcher` and driven by the daemon lifespan:
    ``start`` registers the engine callback and spins up the router plus one worker
    per live channel; ``stop`` drains and tears them down.
    """

    def __init__(
        self,
        settings: Settings,
        engine: IssueEngine,
        *,
        transport: Optional[AlertTransport] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Optional[Sleeper] = None,
        wall_clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._engine = engine
        self._cfg: AlertsConfig = settings.alerts
        self._site_id = str(getattr(settings, "site_id", "default"))
        self._secrets: AlertSecrets = settings.alert_secrets
        self._transport = transport if transport is not None else HttpxTransport()
        self._owns_transport = transport is None
        self._clock = clock
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._wall_clock = wall_clock
        self._intake: asyncio.Queue[Any] = asyncio.Queue(maxsize=_INTAKE_MAX)
        self._intake_dropped = 0
        self._running = False
        self._router: Optional[asyncio.Task[None]] = None
        self._channels: list[_Channel] = [
            _Channel(
                cfg,
                self._secrets.url_for(cfg.name),
                self._secrets.token_for(cfg.name),
                clock=clock,
            )
            for cfg in self._cfg.channels
        ]

    # -- introspection -------------------------------------------------- #
    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def channels(self) -> list[_Channel]:
        return list(self._channels)

    def _live_channels(self) -> list[_Channel]:
        return [c for c in self._channels if c.live]

    # -- lifecycle ------------------------------------------------------ #
    async def start(self) -> None:
        """Register the engine callback and start the tasks -- or no-op.

        Disabled, with no channels, or with no channel carrying a URL, this returns
        having done nothing at all. Each configured-but-URL-less channel gets
        exactly one warning here and then stays quiet: the honest no-op contract,
        not a log line per transition forever.
        """
        if not self._cfg.enabled:
            _log.info("outbound alerts disabled (alerts.enabled=false); not starting")
            return
        if self._router is not None:
            return
        for channel in self._channels:
            if not channel.live:
                _log.warning(
                    "alert channel %r (%s) has no URL configured "
                    "(set ALERT_URLS__%s in data/secrets.env); staying inert",
                    channel.cfg.name,
                    channel.cfg.type,
                    channel.cfg.name.upper(),
                )
        live = self._live_channels()
        if not live:
            _log.warning("outbound alerts enabled but no channel is configured; staying inert")
            return

        self._running = True
        # Fire-and-forget by the engine's contract: on_transition only enqueues.
        # Remove-then-add makes the subscription exactly-once no matter what came
        # before: the engine appends unconditionally, so a restart (or a start that
        # aborted after subscribing) would otherwise leave two copies registered and
        # every alert would go out twice.
        self._engine.remove_callback(self.on_transition)
        self._engine.add_callback(self.on_transition)
        self._router = asyncio.create_task(self._route_loop())
        for channel in live:
            channel.task = asyncio.create_task(self._worker_loop(channel))
        _log.info(
            "outbound alerts started (%d channel(s): %s)",
            len(live),
            ", ".join(f"{c.cfg.name}/{c.cfg.type}" for c in live),
        )

    async def stop(self) -> None:
        """Unsubscribe, drain queued deliveries within a grace window, then tear down.

        The engine callback is unregistered here, not merely made inert: the engine
        appends unconditionally, so leaving it attached means a later ``start`` in
        the same process delivers every alert twice. ``_running`` still guards the
        callback, which covers the transitions already in flight. A pending digest
        is force-flushed on the way out so a coalesced batch is not silently
        discarded, and the queues are emptied so a restart begins clean rather than
        on a leftover stop sentinel.
        """
        self._running = False
        self._engine.remove_callback(self.on_transition)
        tasks = [t for t in [self._router, *(c.task for c in self._channels)] if t is not None]
        if tasks:
            with contextlib.suppress(asyncio.QueueFull):
                self._intake.put_nowait(_STOP)
            gathered = asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.wait_for(gathered, timeout=_STOP_GRACE_S)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        self._router = None
        for channel in self._channels:
            channel.task = None
        # A cancelled worker can leave its stop sentinel (or undelivered work) on a
        # queue. Emptying them here is what makes a second start a real restart: a
        # stale sentinel would stop the new router on its first item.
        _drain(self._intake)
        for channel in self._channels:
            _drain(channel.queue)
        if self._owns_transport:
            await self._transport.aclose()

    # -- engine callback (sync, fire-and-forget) ------------------------ #
    def on_transition(self, transition: Transition) -> None:
        """Engine ``on_transition`` hook: drop the transition on the intake queue.

        Never blocks, never raises into the engine, does zero policy work. When
        stopped it is inert; on a full queue it counts a drop and moves on -- an
        alert backlog must never slow the detection pass that produced it.
        """
        if not self._running:
            return
        try:
            self._intake.put_nowait(transition)
        except asyncio.QueueFull:
            self._intake_dropped += 1
            # Throttled: the engine emits synchronously inside a detect pass, so a
            # pass larger than the queue (a startup backfill can produce thousands)
            # would otherwise write one line per dropped transition and flood the
            # log. The true total is always exact in /api/health via
            # ``intake_dropped``; this is only the human breadcrumb.
            if self._intake_dropped == 1 or self._intake_dropped % _DROP_LOG_EVERY == 0:
                _log.warning(
                    "alert intake queue full; dropping transition %s (%d dropped so far)",
                    transition.kind,
                    self._intake_dropped,
                )

    # -- router --------------------------------------------------------- #
    async def _route_loop(self) -> None:
        """Classify each transition once, then fan it out to the channel queues."""
        while True:
            item = await self._intake.get()
            if item is _STOP:
                for channel in self._live_channels():
                    self._enqueue_stop(channel)
                return
            try:
                self._route(item)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - routing must never kill the loop
                _log.warning("alert routing failed for a transition; skipped", exc_info=True)

    def _route(self, transition: Transition) -> None:
        event_class = classify(transition)
        if event_class is None:
            return
        event = AlertEvent(event=event_class, transition=transition)
        for channel in self._live_channels():
            decision = channel.policy.evaluate(event)
            if decision == SKIP:
                continue
            self._enqueue(channel, _Item(event=event, digest=(decision == DIGEST)))

    def _enqueue(self, channel: _Channel, item: _Item) -> None:
        """Queue one unit of channel work, counting (never blocking on) overflow.

        A dropped SEND has already spent its token and recorded its dedupe state, so
        a later resolve for the same fingerprint still goes out on its own. The drop
        is counted and surfaced rather than papered over.
        """
        try:
            channel.queue.put_nowait(item)
        except asyncio.QueueFull:
            channel.status.dropped += 1
            _log.warning(
                "alert channel %r queue full; dropped a %s notification",
                channel.cfg.name,
                item.event.event,
            )

    def _enqueue_stop(self, channel: _Channel) -> None:
        try:
            channel.queue.put_nowait(_STOP)
        except asyncio.QueueFull:
            # The worker is saturated; the grace timeout in ``stop`` cancels it.
            _log.debug("alert channel %r queue full at stop; will cancel", channel.cfg.name)

    # -- per-channel worker --------------------------------------------- #
    async def _worker_loop(self, channel: _Channel) -> None:
        """Drain one channel's queue in order, flushing its digest when due.

        When a digest is pending, the wait on the queue is bounded by the time until
        the next token frees, so the summary goes out on schedule even if no further
        event arrives.
        """
        while True:
            delay = channel.policy.next_flush_delay()
            try:
                if delay is None:
                    item = await channel.queue.get()
                else:
                    item = await asyncio.wait_for(channel.queue.get(), timeout=delay)
            except asyncio.TimeoutError:
                await self._flush_digest(channel)
                continue
            if item is _STOP:
                await self._flush_digest(channel, force=True)
                return
            if item.digest:
                channel.policy.buffer(item.event)
                channel.status.digested += 1
                continue
            await self._deliver(channel, self._build_payload(channel, item.event))

    async def _flush_digest(self, channel: _Channel, *, force: bool = False) -> None:
        summary = channel.policy.take_digest(force=force)
        if summary is None:
            return
        payload = self._build_digest_payload(channel, summary)
        if payload is not None:
            await self._deliver(channel, payload)

    # -- payload construction ------------------------------------------- #
    def _build_payload(self, channel: _Channel, event: AlertEvent) -> Optional[Payload]:
        builder = formats.PAYLOAD_BUILDERS.get(channel.cfg.type)
        if builder is None:  # pragma: no cover - config validation forbids this
            _log.error("alert channel %r has unknown type %r", channel.cfg.name, channel.cfg.type)
            return None
        return builder(event, site_id=self._site_id)

    def _build_digest_payload(self, channel: _Channel, summary: DigestSummary) -> Optional[Payload]:
        builder = formats.DIGEST_BUILDERS.get(channel.cfg.type)
        if builder is None:  # pragma: no cover - config validation forbids this
            return None
        return builder(summary, site_id=self._site_id)

    # -- delivery + retry ----------------------------------------------- #
    async def _deliver(self, channel: _Channel, payload: Optional[Payload]) -> None:
        """POST one payload, retrying transient failures in-line (FIFO preserved).

        Retry classification:

        * ``2xx`` -- delivered.
        * ``429`` -- honour ``Retry-After`` when the server sent one, capped.
        * ``5xx`` / timeout / network error -- exponential backoff from 2 s to a 60 s
          cap, up to five attempts total.
        * any other ``4xx`` -- **permanent**. A 401/403/404 means the URL is wrong or
          revoked; replaying it five times just burns the endpoint's rate limit.
        """
        if payload is None or channel.url is None:
            return
        headers = {**payload.headers, **channel.auth_headers()}
        attempt = 0
        backoff = _BACKOFF_INITIAL_S
        while True:
            retryable = True
            delay = backoff
            try:
                result = await self._transport.post(
                    channel.url,
                    json=payload.json,
                    content=payload.content,
                    headers=headers or None,
                    timeout_s=float(channel.cfg.timeout_s),
                )
            except asyncio.CancelledError:
                raise
            except TransportError as exc:
                error = str(exc) or "TransportError"
            except Exception as exc:  # noqa: BLE001 - a bad client must not kill the worker
                error = type(exc).__name__
            else:
                code = result.status_code
                if 200 <= code < 300:
                    self._record_success(channel)
                    return
                error = f"HTTP {code}"
                if code == 429:
                    if result.retry_after_s is not None:
                        delay = min(float(result.retry_after_s), _RETRY_AFTER_CAP_S)
                elif code < 500:
                    retryable = False

            attempt += 1
            if not retryable or attempt >= _MAX_ATTEMPTS:
                self._record_failure(channel, error, permanent=not retryable)
                return
            await self._sleep(delay)
            backoff = min(backoff * 2, _BACKOFF_CAP_S)

    def _record_success(self, channel: _Channel) -> None:
        status = channel.status
        status.delivered += 1
        status.consecutive_failures = 0
        status.last_success_ts = self._wall_clock()
        status.last_error = None
        status.status = STATUS_OK

    def _record_failure(self, channel: _Channel, error: str, *, permanent: bool) -> None:
        status = channel.status
        status.failed += 1
        status.consecutive_failures += 1
        status.last_error = error
        if status.consecutive_failures >= _FAILING_THRESHOLD:
            status.status = STATUS_FAILING
        _log.warning(
            "alert delivery to channel %r failed (%s%s); %d consecutive failure(s)",
            channel.cfg.name,
            error,
            ", not retried" if permanent else "",
            status.consecutive_failures,
        )

    # -- health --------------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        """The ``/api/health`` block (section 12). Carries no URL, ever."""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "intake_dropped": self._intake_dropped,
            "channels": [c.status.as_health() for c in self._channels],
        }


def build_alert_dispatcher(
    settings: Settings,
    engine: IssueEngine,
    *,
    transport: Optional[AlertTransport] = None,
    sleeper: Optional[Sleeper] = None,
) -> AlertDispatcher:
    """Construct the dispatcher for the daemon lifespan (section 20 wiring surface).

    Always returns an object; whether it does anything is decided at ``start`` by
    ``settings.alerts.enabled`` and whether any channel has a URL in
    ``data/secrets.env``. No store handle is needed -- a ``Transition`` already
    carries severity, title, detector, and fingerprint.
    """
    return AlertDispatcher(settings, engine, transport=transport, sleeper=sleeper)
