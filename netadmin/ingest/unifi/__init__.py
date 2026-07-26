"""netadmin.ingest.unifi: async httpx UniFi controller client.

Three auth strategies behind one interface (API key, UniFi OS cookie+CSRF,
legacy cookie), typed read-endpoint wrappers, and an event WebSocket listener.
See ``docs/ARCHITECTURE.md`` section 5.1.
"""

from __future__ import annotations

from .auth import (
    ApiKeyAuth,
    AuthStrategy,
    LegacyCookieAuth,
    TwoFactorRequired,
    UnifiAuthError,
    UnifiConnectionError,
    UnifiError,
    UnifiOsCookieAuth,
    resolve_strategy,
)
from .client import UnifiClient
from .endpoints import Endpoints
from .ws import EventListener

__all__ = [
    "UnifiClient",
    "Endpoints",
    "EventListener",
    "AuthStrategy",
    "ApiKeyAuth",
    "UnifiOsCookieAuth",
    "LegacyCookieAuth",
    "resolve_strategy",
    "UnifiError",
    "UnifiConnectionError",
    "UnifiAuthError",
    "TwoFactorRequired",
]
