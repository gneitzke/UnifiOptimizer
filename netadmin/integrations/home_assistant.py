"""Home Assistant integration via MQTT discovery (``docs/ARCHITECTURE.md`` 11).

A read-only bridge. It publishes the daemon's health, its per-SLE scores, its
open-issue counts, and one dynamic ``binary_sensor`` per active P1/P2 issue onto
an MQTT broker using Home Assistant's `MQTT discovery
<https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery>`_ convention, so
HA picks them up with no custom component. It also mirrors issue transitions onto a
``netadmin/events`` topic for HA automations ("notify my phone on a new P1").

Three hard properties, all from section 11 and the global "firewall" rule:

* **Read-only.** It publishes; it subscribes to nothing. No HA-triggered action can
  reach the controller through this module. (The whole fix/apply path is out of
  scope this phase.)
* **Off by default.** ``ha.enabled`` defaults False and broker credentials come
  only from the environment / ``data/secrets.env`` (never yaml, never code). A
  disabled or unconfigured publisher is a total no-op: it registers no engine
  callback, opens no socket, and touches nothing.
* **The daemon never notices a broker problem.** The engine feeds this via a sync,
  fire-and-forget ``on_transition`` callback that only drops a transition onto a
  bounded in-memory queue — it never blocks and never raises into the engine. All
  actual MQTT I/O runs in one isolated supervisor task that reconnects with capped
  backoff; a dead or slow broker degrades HA visibility, nothing else.

The availability topic is wired as the MQTT LWT (last will), so if the daemon dies
the broker publishes ``offline`` on its behalf and HA greys every netadmin entity
out — the daemon itself shows offline, not stale-but-green.

``aiomqtt`` is imported lazily inside the default client factory so this module (and
its tests, which inject a fake client) import with no broker library present.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, Callable, Optional, Protocol, runtime_checkable

from netadmin.config import HaConfig, MqttCredentials, Settings
from netadmin.domain.types import IssueState, Severity
from netadmin.issues.engine import IssueEngine
from netadmin.issues.models import EventKind, Transition
from netadmin.logging import get_logger
from netadmin.sle.classifiers import ALL_SLES
from netadmin.sle.scores import sle_scores
from netadmin.store.repository import Repository

_log = get_logger("integrations.home_assistant")

__all__ = [
    "MqttClient",
    "HaTopics",
    "HaPublisher",
    "build_ha_publisher",
    "health_discovery",
    "sle_discovery",
    "issue_count_discovery",
    "issue_binary_sensor_discovery",
    "issue_attributes",
]

# --- constants ------------------------------------------------------------- #

# The manufacturer/model advertised on the shared HA device the entities group
# under. Static strings, no controller identity leaks here.
_DEVICE_MANUFACTURER = "UnifiOptimizer"
_DEVICE_MODEL = "netadmin daemon"

_AVAIL_ONLINE = "online"
_AVAIL_OFFLINE = "offline"
_STATE_ON = "ON"
_STATE_OFF = "OFF"
# HA's documented sentinel for "this sensor has no value right now" — distinct
# from a real ``0``. A ``%`` sensor fed a JSON null must render this instead.
_HA_UNKNOWN = "unknown"

# Only *confirmed and still-open* issues surface as HA binary_sensors / counts:
# a ``pending`` issue is unconfirmed noise, a ``resolved`` one is gone. ACTIVE and
# RESOLVING are the open, real states (a RESOLVING issue can still snap back).
_OPEN_VISIBLE_STATES = frozenset({IssueState.ACTIVE.value, IssueState.RESOLVING.value})

# Severities that earn their own dynamic binary_sensor (section 11: "per active
# P1/P2 issue"). P3 issues still contribute to the ``issues_p3`` count sensor.
_BINARY_SENSOR_SEVERITIES = frozenset({Severity.P1.value, Severity.P2.value})

# Transition kinds that can change the open-issue set (so a reconcile is worth
# running immediately rather than waiting for the periodic refresh). Ack/snooze/
# investigate/fix bookkeeping never add or remove an entity.
_ISSUE_SET_KINDS = frozenset(
    {
        EventKind.DETECTED,
        EventKind.ESCALATED,
        EventKind.RESOLVED,
        EventKind.REOPENED,
    }
)

# HA caps an attribute string at 255 chars; the evidence digest is truncated to fit.
_EVIDENCE_DIGEST_MAX = 255

_QUEUE_MAX = 512
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_CAP_S = 60.0
# Backpressure: the full SLE score pass (``sle_scores`` over a 24 h window) is the
# expensive part of a state publish. A burst of issue transitions must not trigger
# one score pass per transition, so the score portion of the state doc is recomputed
# at most once per this interval and cached; open-issue *counts* stay fresh on every
# publish (cheap). The periodic refresh loop and drain-coalescing keep it consistent.
_STATE_SLE_MIN_INTERVAL_S = 5.0
# How long a graceful stop waits for the supervisor to unwind cleanly (announce
# offline, disconnect) before falling back to cancelling it.
_STOP_GRACE_S = 5.0


# --- MQTT client seam ------------------------------------------------------ #


@runtime_checkable
class MqttClient(Protocol):
    """The slice of ``aiomqtt.Client`` the publisher uses.

    A ``publish``-only surface (this integration subscribes to nothing). Tests
    supply an in-memory fake satisfying exactly this; the real client is
    ``aiomqtt.Client``, which already matches this signature.
    """

    async def publish(
        self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False
    ) -> Any:
        """Publish ``payload`` to ``topic`` (the only broker call this bridge makes)."""


# A client factory returns an async context manager that connects on ``__aenter__``
# and disconnects on ``__aexit__``, yielding an :class:`MqttClient`. This is exactly
# ``aiomqtt.Client``'s shape, and the seam tests inject a fake through.
ClientFactory = Callable[[], "AsyncContextManager[MqttClient]"]


@dataclass(frozen=True)
class _Will:
    """The MQTT last-will the broker publishes if the daemon drops off."""

    topic: str
    payload: str = _AVAIL_OFFLINE
    qos: int = 1
    retain: bool = True


def _default_client_factory(creds: MqttCredentials, will: _Will) -> ClientFactory:
    """Build the production factory: a real ``aiomqtt.Client`` with the LWT armed.

    ``aiomqtt`` is imported here, lazily, so the module imports without it. The
    returned callable is what the supervisor loop enters once per connection
    attempt.
    """

    def factory() -> "AsyncContextManager[MqttClient]":
        import aiomqtt  # local import: optional dependency, only needed when enabled

        return aiomqtt.Client(
            hostname=creds.host,
            port=creds.port,
            username=creds.username,
            password=creds.password,
            will=aiomqtt.Will(
                topic=will.topic, payload=will.payload, qos=will.qos, retain=will.retain
            ),
        )

    return factory


# --- topic scheme ---------------------------------------------------------- #


@dataclass(frozen=True)
class HaTopics:
    """Every topic the integration uses, derived from :class:`HaConfig`.

    Discovery *config* topics live under HA's ``discovery_prefix`` (that is the
    only prefix HA listens on); our own *state*, *attribute*, *availability*, and
    *event* topics hang off ``base_topic``. Kept as one small value object so the
    payload builders and the publisher agree on every string.
    """

    discovery_prefix: str
    base: str
    node: str

    @classmethod
    def from_config(cls, cfg: HaConfig) -> "HaTopics":
        return cls(discovery_prefix=cfg.discovery_prefix, base=cfg.base_topic, node=cfg.node_id)

    # -- our topics --
    @property
    def availability(self) -> str:
        return f"{self.base}/status"

    @property
    def state(self) -> str:
        """The single retained JSON doc the health/SLE/count sensors template off."""
        return f"{self.base}/state"

    @property
    def events(self) -> str:
        return f"{self.base}/events"

    def issue_state(self, uid: str) -> str:
        return f"{self.base}/issue/{uid}/state"

    def issue_attributes(self, uid: str) -> str:
        return f"{self.base}/issue/{uid}/attributes"

    # -- HA discovery config topics --
    def sensor_config(self, object_id: str) -> str:
        return f"{self.discovery_prefix}/sensor/{self.node}/{object_id}/config"

    def binary_sensor_config(self, object_id: str) -> str:
        return f"{self.discovery_prefix}/binary_sensor/{self.node}/{object_id}/config"


def issue_object_id(node: str, uid: str) -> str:
    """The HA object_id / unique_id stem for a per-issue binary_sensor."""
    return f"{node}_issue_{uid}"


def issue_uid(fingerprint: str) -> str:
    """The stable short id for an issue's HA entity: the fingerprint hash prefix.

    The fingerprint is a sha1 hex digest (section 7); its 12-char prefix is
    collision-safe at this scale and keeps the HA entity_id readable.
    """
    return fingerprint[:12]


# --- discovery payload builders (pure) ------------------------------------- #


def _nullable_pct_template(path: str) -> str:
    """A value_template that maps a JSON ``null`` to HA's ``unknown`` sentinel.

    When an SLE has no data its score is ``None`` -> serialised as JSON ``null``.
    Feeding ``null`` straight into a ``%`` measurement sensor makes HA log an
    "invalid state" and can coerce it to a misleading ``0``. Emitting the literal
    ``unknown`` (HA's documented sentinel) instead marks the sensor honestly
    unknown — no data is not zero coverage. Data present renders the number as-is.
    """
    return f"{{{{ '{_HA_UNKNOWN}' if {path} is none else {path} }}}}"


def _device_block(cfg: HaConfig) -> dict[str, Any]:
    """The shared HA ``device`` every entity references, so they group as one."""
    return {
        "identifiers": [cfg.node_id],
        "name": cfg.device_name,
        "manufacturer": _DEVICE_MANUFACTURER,
        "model": _DEVICE_MODEL,
    }


def _availability_block(topics: HaTopics) -> dict[str, Any]:
    """The availability wiring shared by every entity — reads the LWT topic."""
    return {
        "availability_topic": topics.availability,
        "payload_available": _AVAIL_ONLINE,
        "payload_not_available": _AVAIL_OFFLINE,
    }


def health_discovery(cfg: HaConfig, topics: HaTopics) -> dict[str, Any]:
    """Discovery config for ``sensor.netadmin_health`` — the headline health %."""
    object_id = f"{cfg.node_id}_health"
    return {
        "name": "Network Health",
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": topics.state,
        "value_template": _nullable_pct_template("value_json.health"),
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "icon": "mdi:heart-pulse",
        "device": _device_block(cfg),
        **_availability_block(topics),
    }


def sle_discovery(cfg: HaConfig, topics: HaTopics, sle: str) -> dict[str, Any]:
    """Discovery config for one per-SLE score sensor (coverage, roaming, ...)."""
    object_id = f"{cfg.node_id}_sle_{sle}"
    return {
        "name": f"SLE {sle.capitalize()}",
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": topics.state,
        "value_template": _nullable_pct_template(f"value_json.sle.{sle}"),
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "icon": "mdi:speedometer",
        "device": _device_block(cfg),
        **_availability_block(topics),
    }


def issue_count_discovery(cfg: HaConfig, topics: HaTopics, sev: str) -> dict[str, Any]:
    """Discovery config for ``sensor.netadmin_issues_{p1,p2,p3}`` (open counts)."""
    object_id = f"{cfg.node_id}_issues_{sev}"
    return {
        "name": f"Open Issues {sev.upper()}",
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": topics.state,
        "value_template": f"{{{{ value_json.issues.{sev} }}}}",
        "state_class": "measurement",
        "icon": "mdi:alert-circle",
        "device": _device_block(cfg),
        **_availability_block(topics),
    }


def issue_binary_sensor_discovery(
    cfg: HaConfig, topics: HaTopics, view: "_IssueView"
) -> dict[str, Any]:
    """Discovery config for one active-issue ``binary_sensor`` (device_class problem).

    ``unique_id`` derives from the issue fingerprint prefix (section 11); the
    per-issue attributes (title, entity, detector, severity, duration, evidence
    digest) ride the separate ``json_attributes_topic``.
    """
    object_id = issue_object_id(cfg.node_id, view.uid)
    return {
        "name": view.title,
        "unique_id": object_id,
        "object_id": object_id,
        "state_topic": topics.issue_state(view.uid),
        "payload_on": _STATE_ON,
        "payload_off": _STATE_OFF,
        "device_class": "problem",
        "json_attributes_topic": topics.issue_attributes(view.uid),
        "device": _device_block(cfg),
        **_availability_block(topics),
    }


def _evidence_digest(evidence: dict[str, Any]) -> str:
    """A compact, ``<=255``-char one-line digest of an issue's evidence blob.

    Confounders travel as their own attribute, not in the digest. Deterministic
    (sorted keys) and always truncated so it can never overflow HA's attribute cap.
    """
    parts = []
    for key in sorted(evidence):
        if key == "confounders_checked":
            continue
        parts.append(f"{key}={evidence[key]}")
    digest = ", ".join(parts)
    if len(digest) > _EVIDENCE_DIGEST_MAX:
        digest = digest[: _EVIDENCE_DIGEST_MAX - 1] + "…"
    return digest


def issue_attributes(view: "_IssueView", *, now: int) -> dict[str, Any]:
    """The JSON attribute doc for an issue binary_sensor's ``json_attributes_topic``."""
    return {
        "title": view.title,
        "entity": view.entity_name,
        "detector": view.detector_key,
        "severity": view.severity,
        "duration_s": max(0, now - view.first_seen_ts),
        "first_seen_ts": view.first_seen_ts,
        "occurrences": view.occurrences,
        "evidence": _evidence_digest(view.evidence),
    }


# --- issue projection ------------------------------------------------------ #


@dataclass(frozen=True)
class _IssueView:
    """The read-only slice of an open issue the HA layer publishes."""

    uid: str
    fingerprint: str
    title: str
    detector_key: str
    severity: str
    first_seen_ts: int
    occurrences: int
    entity_name: Optional[str]
    evidence: dict[str, Any] = field(default_factory=dict)


# --- the publisher --------------------------------------------------------- #


@dataclass
class _StateDoc:
    """The health/SLE/count snapshot rendered into the retained state topic."""

    health: Optional[int]
    sle: dict[str, Optional[int]]
    issues: dict[str, int]

    def as_json(self) -> str:
        return json.dumps(
            {"health": self.health, "sle": self.sle, "issues": self.issues}, sort_keys=True
        )


class HaPublisher:
    """Bridges the issue engine + store to Home Assistant over MQTT (section 11).

    Constructed by :func:`build_ha_publisher` and driven by the daemon lifespan:
    ``start`` (a no-op when disabled/unconfigured) registers the engine callback
    and spins up one supervisor task; ``stop`` tears it down. Everything MQTT
    happens inside that task; the engine only ever enqueues.
    """

    def __init__(
        self,
        store: Repository,
        engine: IssueEngine,
        settings: Settings,
        *,
        client_factory: Optional[ClientFactory] = None,
        sle_min_interval_s: float = _STATE_SLE_MIN_INTERVAL_S,
    ) -> None:
        self._store = store
        self._engine = engine
        self._settings = settings
        self._cfg: HaConfig = settings.ha
        self._creds: MqttCredentials = settings.mqtt
        self._topics = HaTopics.from_config(self._cfg)
        self._will = _Will(topic=self._topics.availability)
        self._client_factory = client_factory or _default_client_factory(self._creds, self._will)
        self._queue: asyncio.Queue[Transition] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._connected = False
        # uid -> object_id of every issue binary_sensor currently discovered, so a
        # resolve can publish the discovery-removal to the exact config topic.
        self._published: dict[str, str] = {}
        # Backpressure state for the throttled SLE score pass (finding 7).
        self._sle_min_interval_s = float(sle_min_interval_s)
        self._sle_cache: Optional[tuple[Optional[int], dict[str, Optional[int]]]] = None
        self._sle_cache_mono: float = 0.0

    # -- lifecycle ----------------------------------------------------- #
    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled)

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Register the engine callback and start the supervisor — or no-op.

        Disabled or with no broker host configured, this returns having done
        nothing: no callback, no task, no socket. That is the "off by default"
        contract; the daemon boots identically whether or not HA is wired.
        """
        if not self._cfg.enabled:
            _log.info("home assistant integration disabled (ha.enabled=false); not starting")
            return
        if not self._creds.is_configured:
            _log.warning(
                "home assistant integration enabled but no broker host set "
                "(HA_MQTT_HOST); staying inert"
            )
            return
        if self._task is not None:
            return
        self._running = True
        self._stop.clear()
        # Fire-and-forget by the engine's contract: on_transition only enqueues.
        # Remove-then-add makes the subscription exactly-once no matter what came
        # before: the engine appends unconditionally, so a restart (or a start that
        # aborted after subscribing) would otherwise leave two copies registered and
        # every transition would be published twice.
        self._engine.remove_callback(self.on_transition)
        self._engine.add_callback(self.on_transition)
        self._task = asyncio.create_task(self._supervise())
        _log.info(
            "home assistant publisher started (broker %s:%s, base topic %r)",
            self._creds.host,
            self._creds.port,
            self._cfg.base_topic,
        )

    async def stop(self) -> None:
        """Stop publishing, announcing a clean ``offline`` first.

        The engine callback is unregistered here, not merely made inert: the engine
        appends unconditionally, so leaving it attached means a later ``start`` in
        the same process publishes every transition twice. ``_running`` still guards
        the callback for anything already in flight.

        Stop is signalled via the event and the supervisor is *awaited*
        (not cancelled outright) so :meth:`_session` can publish a retained
        ``offline`` to the availability topic while still connected — a graceful
        shutdown must grey netadmin out in HA, and the MQTT LWT only fires on an
        *ungraceful* drop, so a clean DISCONNECT would otherwise leave a stale
        retained ``online``. Cancellation is the fallback if the unwind stalls.
        """
        self._running = False
        self._engine.remove_callback(self.on_transition)
        self._stop.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=_STOP_GRACE_S)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            except Exception:  # noqa: BLE001 - a supervisor fault on shutdown is not fatal
                pass
            self._task = None
        self._connected = False

    # -- engine callback (sync, fire-and-forget) ----------------------- #
    def on_transition(self, transition: Transition) -> None:
        """Engine ``on_transition`` hook: drop the transition on the queue.

        Never blocks, never raises into the engine. When stopped it is inert; on
        a full queue it drops the transition (HA visibility is best-effort — the
        periodic refresh reconciles the truth regardless).
        """
        if not self._running:
            return
        try:
            self._queue.put_nowait(transition)
        except asyncio.QueueFull:
            _log.warning("HA publish queue full; dropping transition %s", transition.kind)

    # -- supervisor / reconnect ---------------------------------------- #
    async def _supervise(self) -> None:
        """Connect, run a session, reconnect with capped backoff on any fault.

        A missing ``aiomqtt`` (ImportError) is treated as terminal for this
        subsystem — it will not fix itself on retry — so we log once and stop
        rather than spin. Every other error backs off and reconnects.
        """
        backoff = _BACKOFF_INITIAL_S
        while not self._stop.is_set():
            try:
                async with self._client_factory() as client:
                    self._connected = True
                    backoff = _BACKOFF_INITIAL_S
                    _log.info("connected to MQTT broker %s:%s", self._creds.host, self._creds.port)
                    await self._session(client)
                # Clean session exit means stop was requested.
                self._connected = False
                return
            except asyncio.CancelledError:
                self._connected = False
                raise
            except ImportError:
                self._connected = False
                _log.error(
                    "aiomqtt is not installed; home assistant integration cannot run "
                    "(pip install aiomqtt). Disabling this subsystem."
                )
                return
            except Exception as exc:  # noqa: BLE001 - a broker fault must never escape
                self._connected = False
                # Log the fault type + host only — never credentials.
                _log.warning(
                    "MQTT session to %s:%s ended (%s); reconnecting in %.0fs",
                    self._creds.host,
                    self._creds.port,
                    type(exc).__name__,
                    backoff,
                )
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, _BACKOFF_CAP_S)

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep, but wake immediately if stop is signalled during the backoff."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _session(self, client: MqttClient) -> None:
        """One connected session: announce, then drain + refresh until stop/fault.

        A publish fault propagates out of here so the supervisor reconnects; stop
        unwinds it cleanly. The two worker loops and the stop-wait race; whichever
        finishes first tears the others down.
        """
        await self._on_connect(client)
        drain = asyncio.create_task(self._drain_loop(client))
        refresh = asyncio.create_task(self._refresh_loop(client))
        stopper = asyncio.create_task(self._stop.wait())
        workers = (drain, refresh)
        try:
            done, _pending = await asyncio.wait(
                {*workers, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (*workers, stopper):
                task.cancel()
            await asyncio.gather(*workers, stopper, return_exceptions=True)
        # A graceful stop: announce a retained ``offline`` while the client is still
        # connected, then exit cleanly. The broker discards the LWT on a clean
        # DISCONNECT, so without this an operator-triggered stop / SIGTERM / deploy
        # would leave HA showing netadmin (and every entity) stale-but-online.
        if self._stop.is_set():
            with contextlib.suppress(Exception):
                await self._announce_offline(client)
            return
        # Surface a worker fault (a broken publish) so the supervisor reconnects.
        for task in done:
            if task is stopper or task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                raise exc

    async def _announce_offline(self, client: MqttClient) -> None:
        """Publish a retained ``offline`` to the availability topic (clean shutdown)."""
        await client.publish(self._topics.availability, _AVAIL_OFFLINE, qos=1, retain=True)
        self._connected = False

    async def _on_connect(self, client: MqttClient) -> None:
        """Announce availability, publish all static discovery + initial state, and
        discover the currently-open P1/P2 issues. Idempotent across reconnects."""
        await client.publish(self._topics.availability, _AVAIL_ONLINE, qos=1, retain=True)
        await self._publish_static_discovery(client)
        await self._publish_state(client)
        await self._reconcile(client)

    async def _publish_static_discovery(self, client: MqttClient) -> None:
        """The fixed entity set: health, one sensor per SLE, three count sensors."""
        cfg, topics = self._cfg, self._topics
        await self._publish_config(
            client, topics.sensor_config(f"{cfg.node_id}_health"), health_discovery(cfg, topics)
        )
        for sle in ALL_SLES:
            await self._publish_config(
                client,
                topics.sensor_config(f"{cfg.node_id}_sle_{sle}"),
                sle_discovery(cfg, topics, sle),
            )
        for sev in (Severity.P1.value, Severity.P2.value, Severity.P3.value):
            await self._publish_config(
                client,
                topics.sensor_config(f"{cfg.node_id}_issues_{sev}"),
                issue_count_discovery(cfg, topics, sev),
            )

    async def _publish_config(
        self, client: MqttClient, topic: str, payload: dict[str, Any]
    ) -> None:
        """Publish one retained discovery config doc."""
        await client.publish(topic, json.dumps(payload, sort_keys=True), qos=1, retain=True)

    # -- worker loops -------------------------------------------------- #
    async def _drain_loop(self, client: MqttClient) -> None:
        """Publish queued transitions to the events topic; reconcile once per burst.

        A single transition can arrive with several more already queued behind it
        (an escalation cascade, a bulk resolve). Draining everything immediately
        available and reconciling / republishing state ONCE for the whole batch —
        rather than once per transition — coalesces the burst: every transition
        still reaches the events topic, but the expensive reconcile + score pass
        run a single time (finding 7)."""
        while True:
            batch = [await self._queue.get()]
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            issue_set_changed = False
            for transition in batch:
                await self._publish_event(client, transition)
                if transition.kind in _ISSUE_SET_KINDS:
                    issue_set_changed = True
            if issue_set_changed:
                await self._reconcile(client)
                await self._publish_state(client)

    async def _refresh_loop(self, client: MqttClient) -> None:
        """Periodically re-assert availability + republish state + reconcile the
        issue set (section 11 b).

        Re-asserting ``online`` every cycle (not only on connect) is a self-heal:
        the availability topic is retained, so any other actor that publishes
        ``offline`` to it — a second publisher sharing our identity, a stale LWT, a
        broker restart — would otherwise grey out every netadmin entity in HA until
        our next reconnect. Republishing ``online`` while we are demonstrably
        connected repairs that within one refresh interval.
        """
        interval = max(1, int(self._cfg.state_refresh_s))
        while True:
            await asyncio.sleep(interval)
            await client.publish(self._topics.availability, _AVAIL_ONLINE, qos=1, retain=True)
            await self._publish_state(client)
            await self._reconcile(client)

    # -- publishes ----------------------------------------------------- #
    async def _publish_event(self, client: MqttClient, transition: Transition) -> None:
        """Mirror an issue transition onto ``netadmin/events`` for HA automations."""
        payload = {
            "kind": transition.kind,
            "issue_id": transition.issue_id,
            "fingerprint": transition.fingerprint,
            "detector": transition.detector_key,
            "severity": transition.severity.value,
            "title": transition.title,
            "ts": transition.ts,
            "from_state": transition.from_state.value if transition.from_state else None,
            "to_state": transition.to_state.value if transition.to_state else None,
        }
        await client.publish(self._topics.events, json.dumps(payload, sort_keys=True), qos=1)

    async def _publish_state(self, client: MqttClient) -> None:
        """Publish the retained health/SLE/count JSON the templated sensors read."""
        doc = self._build_state_doc()
        await client.publish(self._topics.state, doc.as_json(), qos=1, retain=True)

    async def _reconcile(self, client: MqttClient) -> None:
        """Add discovery for newly-open P1/P2 issues, remove it for resolved ones.

        Publishing the per-issue attributes + ``ON`` state is idempotent, so it is
        safe to run every refresh; a discovery-removal is an empty retained payload
        to the entity's config topic, HA's documented way to drop an entity.
        """
        current = self._active_issue_views()
        now = int(time.time())
        for uid, view in current.items():
            if uid not in self._published:
                await self._publish_config(
                    client,
                    self._topics.binary_sensor_config(issue_object_id(self._cfg.node_id, uid)),
                    issue_binary_sensor_discovery(self._cfg, self._topics, view),
                )
                self._published[uid] = issue_object_id(self._cfg.node_id, uid)
            await client.publish(
                self._topics.issue_attributes(uid),
                json.dumps(issue_attributes(view, now=now), sort_keys=True),
                qos=1,
                retain=True,
            )
            await client.publish(self._topics.issue_state(uid), _STATE_ON, qos=1, retain=True)
        for uid in [u for u in self._published if u not in current]:
            await self._remove_issue(client, uid)

    async def _remove_issue(self, client: MqttClient, uid: str) -> None:
        """Discovery-remove one issue binary_sensor AND clear its retained topics.

        Three topics were published with ``retain=True`` for a live issue: the
        discovery *config*, the *state* (``ON``), and the *attributes* JSON. HA
        drops the entity when the config topic is cleared, but the retained state
        and attributes would otherwise linger on the broker forever — reappearing
        on any client that subscribes, and resurrecting a stale ``ON``/attributes
        pair if the same fingerprint's entity is ever rediscovered. Clear all three
        with empty retained payloads so a resolve leaves no trace on the broker.
        """
        object_id = self._published.pop(uid, issue_object_id(self._cfg.node_id, uid))
        await client.publish(self._topics.binary_sensor_config(object_id), "", qos=1, retain=True)
        await client.publish(self._topics.issue_state(uid), "", qos=1, retain=True)
        await client.publish(self._topics.issue_attributes(uid), "", qos=1, retain=True)

    # -- store projection (sync, loop-thread reads) -------------------- #
    def _open_issue_rows(self) -> list[Any]:
        """Open issues in a visible (active/resolving) state, newest first."""
        return [
            r for r in self._store.list_issues(open_only=True) if r["state"] in _OPEN_VISIBLE_STATES
        ]

    def _build_state_doc(self) -> _StateDoc:
        """Compose the health/SLE/count snapshot, throttling the SLE score pass.

        Open-issue *counts* are recomputed on every call (one cheap query). The
        *headline + per-SLE* scores come from ``sle_scores`` — the expensive pass —
        which is recomputed at most once per ``sle_min_interval_s`` and cached, so a
        burst of issue transitions coalesces to a single score pass rather than one
        per transition (finding 7). The periodic refresh loop still forces a fresh
        pass on its own cadence, so a throttled score is never stale for long.
        """
        health, sle_pct = self._headline_and_sle()
        counts = {"p1": 0, "p2": 0, "p3": 0}
        for row in self._open_issue_rows():
            sev = row["severity"]
            if sev in counts:
                counts[sev] += 1
        return _StateDoc(health=health, sle=sle_pct, issues=counts)

    def _headline_and_sle(self) -> tuple[Optional[int], dict[str, Optional[int]]]:
        """The (headline%, per-SLE%) pair, recomputed at most once per interval."""
        mono = time.monotonic()
        if self._sle_cache is not None and (mono - self._sle_cache_mono) < self._sle_min_interval_s:
            return self._sle_cache
        now = int(time.time())
        window = int(getattr(self._settings.sle, "score_window_s", 86_400))
        report = sle_scores(self._store, now - window, now, settings=self._settings)
        sle_pct = {sle: _pct(report.sles[sle].score) for sle in ALL_SLES if sle in report.sles}
        self._sle_cache = (_pct(report.headline), sle_pct)
        self._sle_cache_mono = mono
        return self._sle_cache

    def _active_issue_views(self) -> dict[str, _IssueView]:
        """The open P1/P2 issues that get their own binary_sensor, keyed by uid."""
        views: dict[str, _IssueView] = {}
        rows = [r for r in self._open_issue_rows() if r["severity"] in _BINARY_SENSOR_SEVERITIES]
        name_by_id = self._resolve_entity_names(
            [r["entity_id"] for r in rows if r["entity_id"] is not None]
        )
        for row in rows:
            fp = row["fingerprint"]
            uid = issue_uid(fp)
            eid = row["entity_id"]
            views[uid] = _IssueView(
                uid=uid,
                fingerprint=fp,
                title=row["title"],
                detector_key=row["detector_key"],
                severity=row["severity"],
                first_seen_ts=int(row["first_seen_ts"]),
                occurrences=int(row["occurrences"]),
                entity_name=name_by_id.get(int(eid)) if eid is not None else None,
                evidence=_decode_evidence(row["evidence"]),
            )
        return views

    def _resolve_entity_names(self, ids: list[int]) -> dict[int, Optional[str]]:
        """Best-effort {entity_id: name} resolution; never raises into a publish."""
        try:
            rows = self._store.entities_by_ids(ids)
        except Exception:  # noqa: BLE001 - name resolution is cosmetic, never fatal
            _log.debug("entity name resolution failed", exc_info=True)
            return {}
        return {int(eid): row["name"] for eid, row in rows.items()}


def _pct(score: Optional[float]) -> Optional[int]:
    """A 0..1 score as an integer percent, or ``None`` (no data — not a zero)."""
    if score is None:
        return None
    return int(round(max(0.0, min(1.0, score)) * 100))


def _decode_evidence(raw: Any) -> dict[str, Any]:
    """Decode the ``issues.evidence`` JSON blob defensively."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (TypeError, ValueError):
        return {}


def build_ha_publisher(
    settings: Settings,
    store: Repository,
    engine: IssueEngine,
    *,
    client_factory: Optional[ClientFactory] = None,
) -> HaPublisher:
    """Construct the HA publisher for the daemon lifespan (section 11 wiring surface).

    Always returns an object; whether it does anything is decided at ``start`` by
    ``settings.ha.enabled`` and whether a broker host is configured. ``client_factory``
    is injected by tests; production uses the lazy ``aiomqtt`` factory.
    """
    return HaPublisher(store, engine, settings, client_factory=client_factory)
