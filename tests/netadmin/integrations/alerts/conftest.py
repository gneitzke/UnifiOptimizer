"""Fakes and builders for the outbound-alert tests.

Nothing here opens a socket. :class:`FakeTransport` satisfies the ``AlertTransport``
Protocol and records every attempted delivery, so the retry ladder, the ordering
guarantee, and the per-channel isolation are all asserted against exact call logs
rather than timing. :class:`RecordingSleeper` replaces ``asyncio.sleep`` so backoff
schedules are verified instantly instead of waited out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional, Sequence

import pytest

from netadmin.config import Settings
from netadmin.domain.types import IssueState, Severity
from netadmin.integrations.alerts.models import OPENED, RESOLVED, AlertEvent
from netadmin.integrations.alerts.transport import PostResult
from netadmin.issues.models import EventKind, Transition

NOW = 1_900_000_000
DISCORD_URL = "https://discord.test/api/webhooks/1/secret-token-aaa"
SLACK_URL = "https://hooks.slack.test/services/T0/B0/secret-token-bbb"
NTFY_URL = "https://ntfy.test/netadmin-topic"
WEBHOOK_URL = "https://hooks.example.test/inbound/secret-token-ccc"

# Every fake URL above, for the "no credential ever reaches a log or health doc" checks.
SECRET_URLS = (DISCORD_URL, SLACK_URL, NTFY_URL, WEBHOOK_URL)


# --- transitions ----------------------------------------------------------- #


def make_transition(
    kind: str,
    *,
    fingerprint: str = "fp-1",
    severity: str = "p2",
    title: str = "rx_errors climbing on port 5",
    detector_key: str = "wired.bad_cable",
    issue_id: int = 1,
    ts: int = NOW,
    from_state: Optional[IssueState] = None,
    to_state: Optional[IssueState] = None,
    detail: Optional[dict[str, Any]] = None,
) -> Transition:
    return Transition(
        issue_id=issue_id,
        fingerprint=fingerprint,
        detector_key=detector_key,
        severity=Severity(severity),
        title=title,
        kind=kind,
        ts=ts,
        from_state=from_state,
        to_state=to_state,
        detail=dict(detail or {}),
    )


def opened_transition(**kw: Any) -> Transition:
    """The real "a problem started" shape: pending -> active on M consecutive fires."""
    kw.setdefault("detail", {"reason": "m_reached", "m": 3, "occurrences": 3})
    return make_transition(
        EventKind.ESCALATED,
        from_state=IssueState.PENDING,
        to_state=IssueState.ACTIVE,
        **kw,
    )


def snapback_transition(**kw: Any) -> Transition:
    """A flap: resolving -> active. Already announced, so never re-announced."""
    kw.setdefault("detail", {"reason": "refire_during_resolving"})
    return make_transition(
        EventKind.ESCALATED,
        from_state=IssueState.RESOLVING,
        to_state=IssueState.ACTIVE,
        **kw,
    )


def reopened_transition(**kw: Any) -> Transition:
    kw.setdefault("detail", {"reopened_from": 1})
    return make_transition(
        EventKind.REOPENED,
        from_state=IssueState.RESOLVED,
        to_state=IssueState.ACTIVE,
        **kw,
    )


def resolved_transition(**kw: Any) -> Transition:
    kw.setdefault("detail", {"clear_streak": 6})
    return make_transition(
        EventKind.RESOLVED,
        from_state=IssueState.ACTIVE,
        to_state=IssueState.RESOLVED,
        **kw,
    )


def detected_transition(**kw: Any) -> Transition:
    """Unconfirmed noise: never notified."""
    kw.setdefault("detail", {"severity": "p2"})
    return make_transition(EventKind.DETECTED, from_state=None, to_state=IssueState.PENDING, **kw)


def alert_event(event: str = OPENED, **kw: Any) -> AlertEvent:
    builder = opened_transition if event != RESOLVED else resolved_transition
    return AlertEvent(event=event, transition=builder(**kw))


# --- fakes ----------------------------------------------------------------- #


@dataclass
class Call:
    """One attempted delivery."""

    url: str
    json: Any
    content: Optional[str]
    headers: dict[str, str]
    timeout_s: float


class FakeTransport:
    """An in-memory ``AlertTransport``: scripted outcomes, full call log, a gate.

    ``outcomes`` is consumed one per call; each entry is an HTTP status int, a
    :class:`PostResult`, or an exception instance to raise. Once exhausted,
    ``default`` is used. ``gate`` (an ``asyncio.Event``) lets a test hang a channel
    mid-delivery to prove another channel keeps flowing.
    """

    def __init__(
        self,
        *,
        outcomes: Sequence[Any] = (),
        default: Any = 204,
    ) -> None:
        self._outcomes = list(outcomes)
        self._default = default
        self.calls: list[Call] = []
        self.closed = False
        self.gate: Optional[asyncio.Event] = None

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        content: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_s: float = 10.0,
    ) -> PostResult:
        self.calls.append(Call(url, json, content, dict(headers or {}), timeout_s))
        if self.gate is not None:
            await self.gate.wait()
        outcome = self._outcomes.pop(0) if self._outcomes else self._default
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, PostResult):
            return outcome
        return PostResult(int(outcome))

    async def aclose(self) -> None:
        self.closed = True

    # -- assertion helpers --
    @property
    def count(self) -> int:
        return len(self.calls)

    def urls(self) -> list[str]:
        return [c.url for c in self.calls]

    def bodies(self) -> list[Any]:
        return [c.json if c.json is not None else c.content for c in self.calls]


class RecordingSleeper:
    """A drop-in for ``asyncio.sleep`` that records the delay and returns at once."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(float(delay))
        # Yield so the loop still interleaves; the wait itself is elided.
        await asyncio.sleep(0)


class FakeEngine:
    """The slice of ``IssueEngine`` the dispatcher touches: add/remove callback.

    ``add_callback`` appends unconditionally and ``remove_callback`` is idempotent,
    exactly like the real engine, so a subscription bug shows up here too.
    """

    def __init__(self) -> None:
        self.callbacks: list[Callable[[Transition], None]] = []

    def add_callback(self, callback: Callable[[Transition], None]) -> None:
        self.callbacks.append(callback)

    def remove_callback(self, callback: Callable[[Transition], None]) -> bool:
        remaining = [cb for cb in self.callbacks if cb != callback]
        removed = len(remaining) != len(self.callbacks)
        self.callbacks = remaining
        return removed

    def emit(self, *transitions: Transition) -> None:
        """Fire transitions the way the engine does: synchronously, in order."""
        for transition in transitions:
            for callback in list(self.callbacks):
                callback(transition)


@dataclass
class FakeClock:
    """A monotonic clock a test advances by hand."""

    now: float = 1000.0
    _log: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- settings builder ------------------------------------------------------ #


def alerts_settings(
    tmp_db_path: Any,
    channels: Sequence[dict[str, Any]],
    *,
    enabled: bool = True,
    urls: Optional[dict[str, str]] = None,
    tokens: Optional[dict[str, str]] = None,
    site_id: str = "default",
) -> Settings:
    """A hermetic Settings with the alert block populated (``_env_file=None``)."""
    return Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id=site_id,
        alerts={"enabled": enabled, "channels": list(channels)},
        alert_urls=dict(urls or {}),
        alert_tokens=dict(tokens or {}),
    )


# --- async helpers --------------------------------------------------------- #


async def wait_until(
    predicate: Callable[[], bool], *, timeout: float = 2.0, interval: float = 0.002
) -> None:
    """Poll ``predicate`` until true, failing the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not reached within timeout")


async def settle(times: int = 12) -> None:
    """Let queued dispatcher work run to quiescence without asserting on it."""
    for _ in range(times):
        await asyncio.sleep(0)


@contextlib.contextmanager
def capture_logs(name: str = "netadmin") -> Iterator[list[logging.LogRecord]]:
    """Capture records from the ``netadmin`` logger tree.

    ``pytest``'s ``caplog`` attaches to the *root* logger, and
    :func:`netadmin.logging.configure_logging` sets ``propagate = False`` on the
    ``netadmin`` logger so package logs never reach root. Attaching directly is the
    only way to actually see what the daemon logs -- which matters here, because
    "no credential ever reaches a log line" is the property under test.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(name)
    handler = _Collector(level=logging.DEBUG)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def log_text(records: Sequence[logging.LogRecord]) -> str:
    """Every captured record rendered the way a log file would render it."""
    return "\n".join(record.getMessage() for record in records)


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def sleeper() -> RecordingSleeper:
    return RecordingSleeper()
