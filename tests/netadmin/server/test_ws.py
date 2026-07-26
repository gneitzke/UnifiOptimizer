"""Tests for the /ws WebSocket: frames, backpressure, endpoint, engine wiring.

Driven single-threaded with a fake WebSocket and the in-loop lifespan context,
so no uvicorn / no cross-thread SQLite (the store connection is loop-bound).
"""

from __future__ import annotations

import asyncio

import pytest

from netadmin.domain.types import IssueState, Severity
from netadmin.issues.models import Transition
from netadmin.server.ws import (
    _CLOSE,
    FRAME_HEARTBEAT,
    FRAME_ISSUE_TRANSITION,
    WsBroadcaster,
    _Connection,
    transition_frame,
    websocket_endpoint,
)

_DISCONNECT = object()


class FakeWebSocket:
    """A minimal in-loop stand-in for a Starlette WebSocket."""

    def __init__(self, app: object) -> None:
        self.app = app
        from starlette.websockets import WebSocketState

        self._State = WebSocketState
        self.application_state = WebSocketState.CONNECTING
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True
        self.application_state = self._State.CONNECTED

    async def send_json(self, data: dict) -> None:
        if self.closed:
            raise RuntimeError("send on closed socket")
        self.sent.append(data)

    async def receive_text(self) -> str:
        from starlette.websockets import WebSocketDisconnect

        item = await self.incoming.get()
        if item is _DISCONNECT:
            raise WebSocketDisconnect(1000)
        return item

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code
        self.application_state = self._State.DISCONNECTED


def _transition(kind: str = "escalated") -> Transition:
    return Transition(
        issue_id=7,
        fingerprint="fp-x",
        detector_key="wired.bad_cable",
        severity=Severity.P1,
        title="Bad cable on port 3",
        kind=kind,
        ts=1_700_000_000,
        from_state=IssueState.PENDING,
        to_state=IssueState.ACTIVE,
        detail={"m": 3},
    )


def test_transition_frame_serialises_states() -> None:
    frame = transition_frame(_transition())
    assert frame["type"] == FRAME_ISSUE_TRANSITION
    assert frame["issue_id"] == 7
    assert frame["from_state"] == "pending"
    assert frame["to_state"] == "active"
    assert frame["detail"] == {"m": 3}


def test_transition_frame_carries_severity_and_title() -> None:
    frame = transition_frame(_transition())
    # The frame must carry the issue's real severity + title so the UI never
    # misencodes a P1 as a neutral/P3 pill (never-do rules 1/2).
    assert frame["severity"] == "p1"
    assert frame["title"] == "Bad cable on port 3"


@pytest.mark.asyncio
async def test_connection_offer_drops_slow_consumer() -> None:
    conn = _Connection(maxsize=1)
    assert conn.offer({"a": 1}) is True
    # queue is full now -> the next offer drops the consumer
    assert conn.offer({"b": 2}) is False
    assert conn.alive is False
    # the close sentinel is queued so the send loop wakes and tears down
    drained = [conn.queue.get_nowait() for _ in range(conn.queue.qsize())]
    assert _CLOSE in drained


@pytest.mark.asyncio
async def test_broadcaster_on_transition_delivers_to_registered_conn() -> None:
    b = WsBroadcaster()
    conn = b.register()
    b.on_transition(_transition("resolved"))
    frame = conn.queue.get_nowait()
    assert frame["type"] == FRAME_ISSUE_TRANSITION
    assert frame["kind"] == "resolved"


@pytest.mark.asyncio
async def test_broadcast_drops_full_connection_from_set() -> None:
    b = WsBroadcaster(queue_max=1)
    conn = b.register()
    b.broadcast({"type": "x"})  # fills the single slot
    b.broadcast({"type": "y"})  # overflow -> dropped
    assert b.connection_count == 0
    assert conn.alive is False


@pytest.mark.asyncio
async def test_endpoint_streams_heartbeat_then_broadcast(rich_app) -> None:
    broadcaster: WsBroadcaster = rich_app.state.ws_broadcaster
    broadcaster.set_heartbeat_provider(lambda: {"ts": 1, "jobs": []})
    ws = FakeWebSocket(rich_app)

    task = asyncio.create_task(websocket_endpoint(ws))
    # let it accept + send the immediate heartbeat
    for _ in range(5):
        await asyncio.sleep(0)
        if ws.sent:
            break
    assert ws.accepted
    assert ws.sent[0]["type"] == FRAME_HEARTBEAT

    broadcaster.broadcast({"type": FRAME_ISSUE_TRANSITION, "kind": "acked"})
    for _ in range(50):
        await asyncio.sleep(0)
        if len(ws.sent) >= 2:
            break
    assert any(f.get("kind") == "acked" for f in ws.sent)

    # peer disconnects -> the endpoint reaps the connection cleanly
    await ws.incoming.put(_DISCONNECT)
    await asyncio.wait_for(task, timeout=1.0)
    assert broadcaster.connection_count == 0


@pytest.mark.asyncio
async def test_endpoint_closes_when_no_broadcaster(settings, rich_store) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=rich_store, components=DaemonComponents())
    app.state.ws_broadcaster = None
    ws = FakeWebSocket(app)
    await websocket_endpoint(ws)
    assert ws.closed is True
    assert ws.close_code == 1011
    assert ws.accepted is False


@pytest.mark.asyncio
async def test_lifespan_registers_callback_and_heartbeat(settings, rich_store) -> None:
    from netadmin.server.main import DaemonComponents, create_app

    app = create_app(settings=settings, store=rich_store, components=DaemonComponents())
    async with app.router.lifespan_context(app):
        broadcaster: WsBroadcaster = app.state.ws_broadcaster
        # the heartbeat provider is wired and yields live poll ages
        hb = broadcaster.heartbeat_frame()
        assert hb["type"] == FRAME_HEARTBEAT
        assert "jobs" in hb

        # the engine transition callback is registered: an ack fans out
        conn = broadcaster.register()
        active = rich_store.list_issues(state="active")[0]
        app.state.issue_engine.ack(int(active["id"]), now=1_700_000_000)
        frame = conn.queue.get_nowait()
        assert frame["type"] == FRAME_ISSUE_TRANSITION
        assert frame["kind"] == "acked"
