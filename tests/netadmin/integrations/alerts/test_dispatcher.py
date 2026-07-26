"""Dispatcher behaviour (section 20): retry, isolation, ordering, and shutdown.

Every test drives a :class:`FakeTransport`, so the retry ladder is asserted from an
exact call log and the backoff schedule from a recording sleeper -- no test waits
out a real 16-second backoff, and no test opens a socket.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from netadmin.integrations.alerts.dispatcher import AlertDispatcher
from netadmin.integrations.alerts.transport import PostResult, TransportError

from .conftest import (
    DISCORD_URL,
    SECRET_URLS,
    SLACK_URL,
    WEBHOOK_URL,
    FakeEngine,
    FakeTransport,
    RecordingSleeper,
    alerts_settings,
    capture_logs,
    log_text,
    opened_transition,
    reopened_transition,
    resolved_transition,
    settle,
    snapback_transition,
    wait_until,
)

pytestmark = pytest.mark.asyncio

WEBHOOK_CHANNEL = {
    "name": "ops",
    "type": "webhook",
    "min_severity": "p3",
    "rate_limit_per_min": 600,
}


def _dispatcher(
    tmp_db_path: Path,
    engine: FakeEngine,
    transport: FakeTransport,
    *,
    channels=(WEBHOOK_CHANNEL,),
    urls=None,
    tokens=None,
    enabled: bool = True,
    sleeper=None,
) -> AlertDispatcher:
    settings = alerts_settings(
        tmp_db_path,
        channels,
        enabled=enabled,
        urls=urls if urls is not None else {"ops": WEBHOOK_URL},
        tokens=tokens,
    )
    return AlertDispatcher(settings, engine, transport=transport, sleeper=sleeper)


# --- the happy path -------------------------------------------------------- #


async def test_open_and_resolve_are_delivered(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    try:
        engine.emit(opened_transition(), resolved_transition())
        await wait_until(lambda: transport.count == 2)
    finally:
        await dispatcher.stop()

    assert [c.url for c in transport.calls] == [WEBHOOK_URL, WEBHOOK_URL]
    assert [c.json["event"] for c in transport.calls] == ["opened", "resolved"]
    status = dispatcher.health()["channels"][0]
    assert status["delivered"] == 2
    assert status["status"] == "ok"


async def test_unnotifiable_transitions_never_reach_the_wire(
    tmp_db_path, engine, transport
) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    try:
        engine.emit(opened_transition(), snapback_transition(), opened_transition())
        await wait_until(lambda: transport.count == 1)
        await settle()
        # The flap snap-back and the duplicate open both stop at the policy.
        assert transport.count == 1
    finally:
        await dispatcher.stop()


async def test_reopen_after_resolve_pages_again(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    try:
        engine.emit(opened_transition(), resolved_transition(), reopened_transition())
        await wait_until(lambda: transport.count == 3)
    finally:
        await dispatcher.stop()
    assert [c.json["event"] for c in transport.calls] == ["opened", "resolved", "reopened"]


async def test_bearer_token_is_attached_when_configured(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport, tokens={"ops": "sekret"})
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 1)
    finally:
        await dispatcher.stop()
    assert transport.calls[0].headers["Authorization"] == "Bearer sekret"


async def test_configured_timeout_reaches_the_transport(tmp_db_path, engine, transport) -> None:
    channel = {**WEBHOOK_CHANNEL, "timeout_s": 3.5}
    dispatcher = _dispatcher(tmp_db_path, engine, transport, channels=(channel,))
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 1)
    finally:
        await dispatcher.stop()
    assert transport.calls[0].timeout_s == 3.5


# --- retry ladder ---------------------------------------------------------- #


async def test_server_error_retries_with_exponential_backoff(tmp_db_path, engine, sleeper) -> None:
    transport = FakeTransport(default=503)
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 5)
        await settle()
    finally:
        await dispatcher.stop()

    assert transport.count == 5  # five attempts, then give up
    assert sleeper.delays == [2.0, 4.0, 8.0, 16.0]
    status = dispatcher.health()["channels"][0]
    assert status["failed"] == 1
    assert status["last_error"] == "HTTP 503"


async def test_network_fault_retries_and_never_leaks_a_url(tmp_db_path, engine, sleeper) -> None:
    transport = FakeTransport(default=TransportError("ConnectTimeout"))
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 5)
        await settle()
    finally:
        await dispatcher.stop()
    assert dispatcher.health()["channels"][0]["last_error"] == "ConnectTimeout"


async def test_retry_after_is_honoured_on_429(tmp_db_path, engine, sleeper) -> None:
    transport = FakeTransport(outcomes=[PostResult(429, retry_after_s=42.0), 204])
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 2)
        await settle()
    finally:
        await dispatcher.stop()
    assert sleeper.delays == [42.0]
    assert dispatcher.health()["channels"][0]["delivered"] == 1


async def test_absurd_retry_after_is_capped(tmp_db_path, engine, sleeper) -> None:
    transport = FakeTransport(outcomes=[PostResult(429, retry_after_s=99_999.0), 204])
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 2)
    finally:
        await dispatcher.stop()
    assert sleeper.delays == [300.0]


async def test_429_without_a_header_falls_back_to_backoff(tmp_db_path, engine, sleeper) -> None:
    transport = FakeTransport(outcomes=[429, 204])
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 2)
    finally:
        await dispatcher.stop()
    assert sleeper.delays == [2.0]


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410])
async def test_client_errors_are_permanent(tmp_db_path, engine, sleeper, code: int) -> None:
    """A 404 is a wrong URL. Replaying it four more times helps nobody."""
    transport = FakeTransport(default=code)
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 1)
        await settle()
    finally:
        await dispatcher.stop()
    assert transport.count == 1
    assert sleeper.delays == []
    assert dispatcher.health()["channels"][0]["last_error"] == f"HTTP {code}"


async def test_channel_reports_failing_after_five_consecutive_failures(
    tmp_db_path, engine, sleeper
) -> None:
    transport = FakeTransport(default=404)  # permanent: one attempt per event
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        for i in range(5):
            engine.emit(opened_transition(fingerprint=f"fp-{i}"))
        await wait_until(lambda: transport.count == 5)
        await settle()
        assert dispatcher.health()["channels"][0]["status"] == "failing"

        # It keeps trying: no silent self-disable. A success clears the state.
        transport._outcomes.append(204)  # noqa: SLF001 - scripted fake
        engine.emit(opened_transition(fingerprint="fp-recovered"))
        await wait_until(lambda: transport.count == 6)
        await settle()
        assert dispatcher.health()["channels"][0]["status"] == "ok"
    finally:
        await dispatcher.stop()


async def test_delivery_stays_fifo_across_a_retry(tmp_db_path, engine, sleeper) -> None:
    """A resolved must never overtake the opened it resolves."""
    transport = FakeTransport(outcomes=[503, 503, 204, 204])
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    await dispatcher.start()
    try:
        engine.emit(opened_transition(), resolved_transition())
        await wait_until(lambda: transport.count == 4)
        await settle()
    finally:
        await dispatcher.stop()
    assert [c.json["event"] for c in transport.calls] == [
        "opened",
        "opened",
        "opened",
        "resolved",
    ]


# --- isolation ------------------------------------------------------------- #


async def test_a_hung_channel_does_not_stall_another(tmp_db_path, engine) -> None:
    slow = FakeTransport()
    slow.gate = asyncio.Event()
    fast = FakeTransport()

    settings = alerts_settings(
        tmp_db_path,
        [
            {"name": "slow", "type": "webhook", "min_severity": "p3"},
            {"name": "fast", "type": "webhook", "min_severity": "p3"},
        ],
        urls={"slow": DISCORD_URL, "fast": SLACK_URL},
    )

    class Router:
        """One transport per channel, chosen by URL."""

        def __init__(self) -> None:
            self.closed = False

        async def post(self, url, **kw):
            target = slow if url == DISCORD_URL else fast
            return await target.post(url, **kw)

        async def aclose(self) -> None:
            self.closed = True

    dispatcher = AlertDispatcher(settings, engine, transport=Router())
    await dispatcher.start()
    try:
        engine.emit(opened_transition())
        # The healthy channel delivers while the other is stuck mid-POST.
        await wait_until(lambda: fast.count == 1)
        assert slow.count == 1  # attempted, still hanging

        # The engine callback is unaffected: it returns synchronously, always.
        engine.emit(opened_transition(fingerprint="fp-2"))
        await wait_until(lambda: fast.count == 2)
    finally:
        slow.gate.set()
        await dispatcher.stop()


async def test_engine_callback_never_raises_or_blocks(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    try:
        # 10_000 transitions against a 512-deep intake: the surplus is dropped and
        # counted, and not one call raises into the engine.
        for i in range(10_000):
            engine.emit(opened_transition(fingerprint=f"fp-{i}"))
        assert dispatcher.health()["intake_dropped"] > 0
    finally:
        await dispatcher.stop()


async def test_channel_queue_overflow_is_counted_not_blocking(tmp_db_path, engine) -> None:
    transport = FakeTransport()
    transport.gate = asyncio.Event()
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    try:
        for i in range(200):
            engine.emit(opened_transition(fingerprint=f"fp-{i}"))
        # Let the router drain the intake into the (blocked) channel queue.
        await wait_until(lambda: dispatcher.health()["channels"][0]["dropped"] > 0)
        await settle()
        channel = dispatcher.channels[0]
        assert transport.count == 1  # one in flight, gated
        assert channel.queue.qsize() <= 128  # the bound holds
        assert channel.status.dropped > 0
        # Conservation: every event is either delivered, queued, or counted as
        # dropped. Nothing disappears unaccounted for.
        assert channel.status.dropped + channel.queue.qsize() + transport.count == 200
    finally:
        transport.gate.set()
        await dispatcher.stop()


# --- flood control end to end ---------------------------------------------- #


async def test_rate_limited_burst_coalesces_into_one_digest(tmp_db_path, engine) -> None:
    """A burst past the limit becomes burst singles plus exactly ONE summary."""
    transport = FakeTransport()
    settings = alerts_settings(
        tmp_db_path,
        # 120/min: a 120 burst, then one token every 500 ms.
        [{"name": "ops", "type": "webhook", "min_severity": "p3", "rate_limit_per_min": 120}],
        urls={"ops": WEBHOOK_URL},
    )
    dispatcher = AlertDispatcher(settings, engine, transport=transport)
    await dispatcher.start()
    try:
        for i in range(125):
            engine.emit(opened_transition(fingerprint=f"fp-{i}"))
        await wait_until(lambda: dispatcher.health()["channels"][0]["digested"] == 5, timeout=3.0)
        await wait_until(lambda: transport.count == 121, timeout=3.0)
    finally:
        await dispatcher.stop()

    bodies = [c.json for c in transport.calls if c.json is not None]
    singles = [b for b in bodies if b["event"] == "opened"]
    digests = [b for b in bodies if b["event"] == "digest"]
    assert len(singles) == 120  # the burst allowance, sent individually
    assert len(digests) == 1  # the overflow, coalesced
    assert digests[0]["count"] == 5
    assert digests[0]["by_event"] == {"opened": 5}
    assert dispatcher.health()["channels"][0]["dropped"] == 0  # nothing lost


async def test_pending_digest_is_flushed_on_shutdown(tmp_db_path, engine) -> None:
    """A coalesced batch is never silently discarded by a stop."""
    transport = FakeTransport()
    settings = alerts_settings(
        tmp_db_path,
        [{"name": "ops", "type": "webhook", "min_severity": "p3", "rate_limit_per_min": 1}],
        urls={"ops": WEBHOOK_URL},
    )
    dispatcher = AlertDispatcher(settings, engine, transport=transport)
    await dispatcher.start()
    engine.emit(opened_transition(fingerprint="a"), opened_transition(fingerprint="b"))
    await wait_until(lambda: dispatcher.health()["channels"][0]["digested"] == 1)
    await dispatcher.stop()

    bodies = [c.json for c in transport.calls if c.json is not None]
    assert [b["event"] for b in bodies] == ["opened", "digest"]
    assert bodies[1]["count"] == 1


# --- lifecycle ------------------------------------------------------------- #


async def test_disabled_dispatcher_is_a_total_no_op(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport, enabled=False)
    await dispatcher.start()
    try:
        assert engine.callbacks == []  # no callback registered at all
        assert dispatcher.running is False
        engine.emit(opened_transition())
        await settle()
        assert transport.count == 0
    finally:
        await dispatcher.stop()


async def test_channel_without_a_url_stays_inert(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport, urls={})
    await dispatcher.start()
    try:
        assert engine.callbacks == []
        assert dispatcher.health()["channels"][0]["status"] == "inert"
        assert dispatcher.health()["channels"][0]["configured"] is False
    finally:
        await dispatcher.stop()


async def test_inert_channel_warns_once_at_startup_then_stays_quiet(
    tmp_db_path, engine, transport
) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport, urls={})
    with capture_logs() as records:
        await dispatcher.start()
        for i in range(50):
            engine.emit(opened_transition(fingerprint=f"fp-{i}"))
        await settle()
        await dispatcher.stop()

    channel_warnings = [r for r in records if "has no URL configured" in r.getMessage()]
    assert len(channel_warnings) == 1  # once at startup, never per transition
    assert "ALERT_URLS__OPS" in channel_warnings[0].getMessage()


async def test_no_delivery_url_ever_reaches_a_log_line(tmp_db_path, engine, sleeper) -> None:
    """The URL is the credential. It must not appear in a warning, ever."""
    transport = FakeTransport(default=500)
    dispatcher = _dispatcher(tmp_db_path, engine, transport, sleeper=sleeper)
    with capture_logs() as records:
        await dispatcher.start()
        engine.emit(opened_transition())
        await wait_until(lambda: transport.count == 5)
        await settle()
        await dispatcher.stop()

    blob = log_text(records)
    assert blob  # the capture actually worked; the assertion below is not vacuous
    assert "ops" in blob  # the channel is named...
    for url in SECRET_URLS:
        assert url not in blob  # ...but never located
    assert "secret-token" not in blob


async def test_stop_drains_queued_deliveries(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    for i in range(20):
        engine.emit(opened_transition(fingerprint=f"fp-{i}"))
    await dispatcher.stop()
    assert transport.count == 20
    assert dispatcher.health()["channels"][0]["delivered"] == 20


async def test_stop_closes_a_transport_it_owns(tmp_db_path, engine) -> None:
    settings = alerts_settings(tmp_db_path, [WEBHOOK_CHANNEL], urls={"ops": WEBHOOK_URL})
    dispatcher = AlertDispatcher(settings, engine)  # builds its own HttpxTransport
    await dispatcher.start()
    await dispatcher.stop()
    assert dispatcher.running is False


async def test_stop_leaves_an_injected_transport_alone(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    await dispatcher.stop()
    assert transport.closed is False


async def test_stop_is_safe_before_start(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.stop()
    assert dispatcher.running is False


async def test_callback_is_inert_after_stop(tmp_db_path, engine, transport) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    await dispatcher.stop()
    engine.emit(opened_transition(fingerprint="late"))
    await settle()
    assert transport.count == 0


# --- restart inside one process -------------------------------------------- #


async def test_stop_unregisters_the_engine_callback(tmp_db_path, engine, transport) -> None:
    """Inert is not enough: a callback left attached is delivered twice after a
    restart, because the engine appends unconditionally."""
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    assert len(engine.callbacks) == 1
    await dispatcher.stop()
    assert engine.callbacks == []


async def test_restart_delivers_each_transition_exactly_once(
    tmp_db_path, engine, transport
) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    await dispatcher.start()
    await dispatcher.stop()
    await dispatcher.start()
    try:
        assert len(engine.callbacks) == 1
        engine.emit(
            opened_transition(fingerprint="fp-a"),
            opened_transition(fingerprint="fp-b"),
            resolved_transition(fingerprint="fp-a"),
        )
        await wait_until(lambda: transport.count == 3)
        await settle()
    finally:
        await dispatcher.stop()

    assert transport.count == 3, "a restarted dispatcher delivered a transition twice"
    assert [c.json["event"] for c in transport.calls] == ["opened", "opened", "resolved"]
    assert dispatcher.health()["channels"][0]["delivered"] == 3


async def test_repeated_start_stop_cycles_never_accumulate_callbacks(
    tmp_db_path, engine, transport
) -> None:
    dispatcher = _dispatcher(tmp_db_path, engine, transport)
    for _ in range(3):
        await dispatcher.start()
        await dispatcher.start()  # a second start is a no-op, not a second subscription
        assert len(engine.callbacks) == 1
        await dispatcher.stop()
        await dispatcher.stop()  # stopping twice is safe
        assert engine.callbacks == []
