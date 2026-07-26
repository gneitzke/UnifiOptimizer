"""UniFi controller event WebSocket listener (ARCHITECTURE.md 5.1).

Connects to ``wss://<host>/proxy/network/wss/s/{site}/events`` (or the legacy
``/wss/...`` path), reusing the authenticated session's cookies or API key from
the :class:`~netadmin.ingest.unifi.client.UnifiClient`. Exposes an async
generator of parsed :class:`~netadmin.ingest.unifi.models.Event` objects and
reconnects on drop with capped exponential backoff.

Standalone by design (Phase 1): no scheduler, no supervisor here. The collector
(section 5.2) will own restart supervision; this class only needs to yield
events and survive reconnects when iterated directly.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import AsyncIterator, Optional

import websockets
from websockets.asyncio.client import connect as ws_connect

from netadmin.logging import get_logger

from .auth import UnifiAuthError
from .client import UnifiClient
from .models import Event

logger = get_logger("ingest.unifi.ws")


class EventListener:
    """Async event stream over the controller WebSocket, with reconnects."""

    def __init__(
        self,
        client: UnifiClient,
        *,
        verify_ssl: bool = False,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
    ) -> None:
        self._client = client
        self._verify_ssl = verify_ssl
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Signal the generator to finish after the current message/backoff."""
        self._stop.set()

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if self._client.host.startswith("wss://") or self._client.host.startswith("https://"):
            ctx = ssl.create_default_context()
            if not self._verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    async def events(self) -> AsyncIterator[Event]:
        """Yield parsed events forever, reconnecting with capped backoff.

        Terminates only when :meth:`stop` is called or the iterator is closed.
        """
        strategy = await self._client.connect()
        url = strategy.ws_url(self._client.host, self._client.site)
        backoff = self._backoff_base

        while not self._stop.is_set():
            headers = strategy.ws_headers(self._client.http.cookies)
            try:
                async with ws_connect(
                    url,
                    additional_headers=headers,
                    ssl=self._ssl_context(),
                    ping_interval=self._ping_interval,
                    ping_timeout=self._ping_timeout,
                    open_timeout=15,
                ) as socket:
                    logger.info("WebSocket connected: %s", url)
                    backoff = self._backoff_base  # reset on a clean connect
                    async for raw in socket:
                        if self._stop.is_set():
                            break
                        for event in self._parse(raw):
                            yield event
            except asyncio.CancelledError:
                raise
            except websockets.InvalidStatus as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (401, 403):
                    logger.info("WebSocket handshake %s; forcing re-auth.", status)
                    try:
                        # connect() would no-op while the strategy is still marked
                        # authenticated; relogin() forces a fresh login so the next
                        # handshake carries new session material, not stale cookies.
                        strategy = await self._client.relogin()
                        url = strategy.ws_url(self._client.host, self._client.site)
                    except UnifiAuthError as auth_exc:
                        logger.error("WebSocket re-auth failed: %s", auth_exc)
                        raise
                else:
                    logger.warning("WebSocket handshake rejected: %s", exc)
            except (OSError, websockets.WebSocketException) as exc:
                logger.warning("WebSocket dropped (%s); reconnecting.", type(exc).__name__)

            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_max)

    @staticmethod
    def _parse(raw: str | bytes) -> list[Event]:
        """Parse one WS frame into zero or more events.

        UniFi frames look like ``{"meta": {"message": "events"}, "data": [...]}``.
        Non-event control frames (device sync, speed-test progress, ...) carry a
        different ``meta.message`` and are skipped.
        """
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            logger.debug("Skipping non-JSON WS frame.")
            return []
        if not isinstance(payload, dict):
            return []

        meta = payload.get("meta", {})
        message = meta.get("message") if isinstance(meta, dict) else None
        data = payload.get("data", [])
        if not isinstance(data, list):
            return []

        # Accept explicit event frames; also accept frames whose rows look like
        # events (have a "key") when meta.message is absent.
        if message not in (None, "events", "event"):
            if not any(isinstance(r, dict) and "key" in r for r in data):
                return []

        events: list[Event] = []
        for row in data:
            if isinstance(row, dict) and ("key" in row or "_id" in row):
                events.append(Event.model_validate(row))
        return events


__all__ = ["EventListener"]
