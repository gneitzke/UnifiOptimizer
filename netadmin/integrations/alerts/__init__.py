"""Outbound alert channels: Discord, Slack, ntfy, and raw webhooks (section 20).

The public surface is :func:`build_alert_dispatcher` plus the dispatcher it
returns; everything else (policy, formats, transport) is an internal seam the tests
drive directly.
"""

from netadmin.integrations.alerts.dispatcher import AlertDispatcher, build_alert_dispatcher
from netadmin.integrations.alerts.models import (
    ALERT_EVENTS,
    OPENED,
    REOPENED,
    RESOLVED,
    AlertEvent,
    ChannelStatus,
    DigestSummary,
    Payload,
)
from netadmin.integrations.alerts.transport import AlertTransport, PostResult, TransportError

__all__ = [
    "AlertDispatcher",
    "build_alert_dispatcher",
    "AlertEvent",
    "AlertTransport",
    "ChannelStatus",
    "DigestSummary",
    "Payload",
    "PostResult",
    "TransportError",
    "ALERT_EVENTS",
    "OPENED",
    "REOPENED",
    "RESOLVED",
]
