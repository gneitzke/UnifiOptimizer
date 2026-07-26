"""The one real WebSocket: ``/ws`` pushes issue transitions + poll heartbeats.

ARCHITECTURE.md 12 kills the old 2-second polling loop in favour of a single
server-push socket. Two frame types cross it:

* ``issue_transition`` — emitted from the issue engine's ``on_transition``
  callbacks (section 7). The daemon registers :meth:`WsBroadcaster.on_transition`
  as a callback at startup, so every lifecycle change (detected / escalated /
  resolved / acked / fix_applied ...) fans out to connected UIs the instant the
  engine produces it, with no polling.
* ``heartbeat`` — a 30 s tick carrying each poll job's last-success age, so the
  UI can show "collector last ran 12 s ago" and grey out when the daemon goes
  quiet. It doubles as liveness: a heartbeat that fails to send reaps a dead
  connection.

Backpressure discipline (the whole point of a bounded queue per connection): a
slow or wedged consumer must never stall the event loop or the engine callback.
Each connection has its own bounded send queue; :meth:`WsBroadcaster.broadcast`
offers a frame with ``put_nowait`` and, on overflow, **drops that consumer**
(marks it dead and wakes its send loop to close) rather than blocking. One slow
browser tab cannot back up the detection pipeline.

The broadcaster's ``on_transition`` is synchronous and fire-and-forget by the
engine's contract; it runs on the event-loop thread (the detection passes and the
ack/snooze handlers all run there — section 3), so ``put_nowait`` is safe without
cross-thread hops.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from netadmin.issues.models import Transition
from netadmin.logging import get_logger
from netadmin.server.auth import WS_UNAUTHORIZED_CODE, token_matches

_log = get_logger("server.ws")

FRAME_ISSUE_TRANSITION = "issue_transition"
FRAME_HEARTBEAT = "heartbeat"

DEFAULT_HEARTBEAT_S = 30.0
DEFAULT_QUEUE_MAX = 100

# Sentinel pushed onto a connection's queue to wake its send loop for shutdown
# (clean close, or a slow-consumer drop). Distinct object, never a real frame.
_CLOSE = object()

HeartbeatProvider = Callable[[], dict[str, Any]]


def transition_frame(transition: Transition) -> dict[str, Any]:
    """Serialise an issue :class:`Transition` into a JSON push frame."""
    return {
        "type": FRAME_ISSUE_TRANSITION,
        "issue_id": transition.issue_id,
        "fingerprint": transition.fingerprint,
        "detector_key": transition.detector_key,
        # Real severity + human title so the UI ticker renders the transition
        # faithfully instead of defaulting a P1 to a neutral/P3 pill.
        "severity": transition.severity.value,
        "title": transition.title,
        "kind": transition.kind,
        "ts": transition.ts,
        "from_state": transition.from_state.value if transition.from_state else None,
        "to_state": transition.to_state.value if transition.to_state else None,
        "detail": dict(transition.detail),
    }


class _Connection:
    """One connected client's bounded send queue + liveness flag.

    ``offer`` never blocks: on a full queue it marks the connection dead and
    enqueues a close sentinel (evicting one stale frame to make room), so the send
    loop wakes promptly and the slow consumer is dropped rather than throttling
    the broadcaster.
    """

    __slots__ = ("queue", "alive")

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self.alive = True

    def offer(self, frame: dict[str, Any]) -> bool:
        """Try to enqueue a frame. Returns False (and drops the consumer) if full."""
        if not self.alive:
            return False
        try:
            self.queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            self.alive = False
            # Evict one stale frame so the close sentinel fits and the send loop
            # unblocks immediately to tear the slow consumer down.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait(_CLOSE)
            except asyncio.QueueFull:  # pragma: no cover - just-freed a slot
                pass
            return False

    def close(self) -> None:
        """Signal the send loop to finish (best-effort, never raises)."""
        self.alive = False
        try:
            self.queue.put_nowait(_CLOSE)
        except asyncio.QueueFull:
            pass


class WsBroadcaster:
    """Fans issue transitions + heartbeats out to every connected WebSocket.

    Constructed once and stored on ``app.state.ws_broadcaster``. The lifespan
    registers :meth:`on_transition` on the issue engine and starts
    :meth:`start_heartbeat`; the ``/ws`` endpoint registers/unregisters a
    connection around its send loop.
    """

    def __init__(
        self,
        *,
        heartbeat_provider: Optional[HeartbeatProvider] = None,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
        queue_max: int = DEFAULT_QUEUE_MAX,
    ) -> None:
        self._conns: set[_Connection] = set()
        self._heartbeat_provider = heartbeat_provider
        self._heartbeat_s = heartbeat_s
        self._queue_max = queue_max
        self._heartbeat_task: Optional[asyncio.Task[None]] = None

    # -- registration -------------------------------------------------- #
    def set_heartbeat_provider(self, provider: HeartbeatProvider) -> None:
        self._heartbeat_provider = provider

    def register(self) -> _Connection:
        conn = _Connection(self._queue_max)
        self._conns.add(conn)
        return conn

    def unregister(self, conn: _Connection) -> None:
        self._conns.discard(conn)

    @property
    def connection_count(self) -> int:
        return len(self._conns)

    # -- fan-out ------------------------------------------------------- #
    def broadcast(self, frame: dict[str, Any]) -> None:
        """Offer a frame to every connection; drop any whose queue is full."""
        for conn in list(self._conns):
            if not conn.offer(frame):
                self._conns.discard(conn)

    def on_transition(self, transition: Transition) -> None:
        """Issue-engine ``on_transition`` callback: broadcast the transition.

        Fire-and-forget by the engine's contract; it swallows exceptions so a
        broadcast fault can never corrupt engine state. Kept trivially cheap
        (build frame + non-blocking enqueue) so it adds no latency to a cycle.
        """
        self.broadcast(transition_frame(transition))

    # -- heartbeat ----------------------------------------------------- #
    def heartbeat_frame(self) -> dict[str, Any]:
        """Build a heartbeat frame from the injected provider (empty if none)."""
        payload: dict[str, Any] = {}
        if self._heartbeat_provider is not None:
            try:
                payload = self._heartbeat_provider() or {}
            except Exception:  # noqa: BLE001 - a heartbeat must never crash the loop
                _log.warning("heartbeat provider raised; sending bare frame", exc_info=True)
                payload = {}
        return {"type": FRAME_HEARTBEAT, **payload}

    async def start_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        """Stop the heartbeat and signal every connection to close."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best effort
                pass
            self._heartbeat_task = None
        for conn in list(self._conns):
            conn.close()
        self._conns.clear()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._heartbeat_s)
                self.broadcast(self.heartbeat_frame())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let a tick kill the loop
                _log.warning("heartbeat tick failed", exc_info=True)


async def _send_loop(websocket: WebSocket, conn: _Connection) -> None:
    """Drain a connection's queue to the socket until closed or the peer leaves."""
    while True:
        frame = await conn.queue.get()
        if frame is _CLOSE:
            return
        try:
            await websocket.send_json(frame)
        except (WebSocketDisconnect, RuntimeError):
            return


async def _recv_loop(websocket: WebSocket) -> None:
    """Consume (and ignore) inbound frames so a peer disconnect is noticed promptly.

    The client sends nothing meaningful; this exists only to surface a close so
    the endpoint can reap the connection without waiting for the next heartbeat.
    """
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        return


async def websocket_endpoint(websocket: WebSocket) -> None:
    """The ``/ws`` handler: accept, stream frames, reap on disconnect.

    Registers a bounded-queue connection with the broadcaster, sends an immediate
    heartbeat so a fresh client has poll ages without waiting 30 s, then races the
    send and receive loops — whichever finishes first (peer close, slow-consumer
    drop, or shutdown) tears the other down and unregisters the connection.
    """
    broadcaster: Optional[WsBroadcaster] = getattr(websocket.app.state, "ws_broadcaster", None)
    if broadcaster is None:
        await websocket.close(code=1011)
        return

    # Static-token auth (section 12): when a token is configured the socket takes it
    # as ``?token=`` (browsers cannot set WebSocket headers, so the query param is the
    # documented channel), constant-time compared. Unconfigured -> open, like the API.
    settings = getattr(websocket.app.state, "settings", None)
    expected = getattr(settings, "api_token", None) if settings is not None else None
    if expected:
        supplied: Optional[str] = None
        try:
            supplied = websocket.query_params.get("token")
        except Exception:  # noqa: BLE001 - a malformed query string is just "no token"
            supplied = None
        if not token_matches(supplied, expected):
            await websocket.close(code=WS_UNAUTHORIZED_CODE)
            return

    await websocket.accept()
    conn = broadcaster.register()
    try:
        await websocket.send_json(broadcaster.heartbeat_frame())
    except (WebSocketDisconnect, RuntimeError):
        broadcaster.unregister(conn)
        return

    send_task = asyncio.create_task(_send_loop(websocket, conn))
    recv_task = asyncio.create_task(_recv_loop(websocket))
    try:
        _done, pending = await asyncio.wait(
            {send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best effort
                pass
    finally:
        broadcaster.unregister(conn)
        if websocket.application_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except RuntimeError:
                pass


__all__ = [
    "WsBroadcaster",
    "websocket_endpoint",
    "transition_frame",
    "FRAME_ISSUE_TRANSITION",
    "FRAME_HEARTBEAT",
]
