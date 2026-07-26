"""The HTTP seam for outbound alerts (section 20). One Protocol, one httpx impl.

The whole subsystem talks to the outside world through :class:`AlertTransport`, the
same seam pattern the HA bridge uses for ``MqttClient``: tests inject an in-memory
fake and no test ever opens a socket.

**Zero new dependencies.** ``httpx`` is already a core runtime dependency (the UniFi
client uses it), so outbound webhooks add nothing to the eleven-dep install.

One hard rule runs through this module: **the URL is a credential**. A Discord or
Slack webhook URL is a bearer token wearing a URL costume. Nothing here puts a URL
into an exception message, a log record, or a traceback -- network faults are
re-raised as :class:`TransportError` carrying the original exception's *type name*
only, with the cause suppressed so a logged traceback cannot leak the request URL
httpx embeds in its own exception text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Optional, Protocol, runtime_checkable

from netadmin.logging import get_logger

_log = get_logger("integrations.alerts.transport")

__all__ = [
    "TransportError",
    "PostResult",
    "AlertTransport",
    "HttpxTransport",
]

# A cap on how long the caller keeps a connection pool open per request. The
# per-channel ``timeout_s`` is what actually governs; this is the ceiling.
_MAX_TIMEOUT_S = 120.0


class TransportError(RuntimeError):
    """A network-level failure (connect error, timeout, DNS). Retryable.

    Carries only an exception type name -- never a URL, never response text.
    """


@dataclass(frozen=True)
class PostResult:
    """The outcome of one delivery attempt that reached a server.

    ``retry_after_s`` is populated only when the server sent a ``Retry-After``
    header (a 429 from Discord or Slack always does).
    """

    status_code: int
    retry_after_s: Optional[float] = None


@runtime_checkable
class AlertTransport(Protocol):
    """The slice of an HTTP client the dispatcher uses: one POST, one close."""

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        content: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_s: float = 10.0,
    ) -> PostResult:
        """POST a payload. Raises :class:`TransportError` on a network fault."""

    async def aclose(self) -> None:
        """Release any pooled connections."""


def parse_retry_after(raw: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) into seconds.

    Returns ``None`` for a missing or unparseable value, so the caller falls back
    to its own backoff rather than trusting a malformed header.
    """
    if not raw:
        return None
    text = raw.strip()
    try:
        return max(0.0, float(int(text)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time.time())


class HttpxTransport:
    """The production :class:`AlertTransport`, backed by one pooled ``httpx`` client.

    The client is created lazily on first use, so a daemon with alerts disabled
    (or with every channel inert) never constructs one. Redirects are **not**
    followed: a webhook endpoint that answers 3xx is misconfigured, and blindly
    replaying a credential-bearing POST to wherever it points is exactly the wrong
    reflex.
    """

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx  # core dependency; imported here to keep import cost lazy

            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        content: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_s: float = 10.0,
    ) -> PostResult:
        client = self._get_client()
        timeout = min(max(0.1, float(timeout_s)), _MAX_TIMEOUT_S)
        try:
            response = await client.post(
                url,
                json=json,
                content=content,
                headers=headers or None,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - normalised; the URL must not escape
            # ``from None`` is deliberate: httpx embeds the request URL in its own
            # exception text, and a chained traceback would carry the credential
            # into any log that renders it.
            raise TransportError(type(exc).__name__) from None
        return PostResult(
            status_code=int(response.status_code),
            retry_after_s=parse_retry_after(response.headers.get("retry-after")),
        )

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            _log.debug("alert transport close failed", exc_info=True)
