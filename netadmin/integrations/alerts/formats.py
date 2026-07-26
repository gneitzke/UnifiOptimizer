"""Provider payload builders for the outbound alert channels (section 20).

Pure functions, one pair per provider (single event + digest), mirroring the
``*_discovery`` builders in the Home Assistant bridge. Every string is truncated to
the provider's documented cap here rather than at the transport, so a long detector
title can never produce a 400 from Discord or an invalid header for ntfy.

Only facts the :class:`~netadmin.issues.models.Transition` actually carries are
rendered. There is no entity name and no issue age in a transition, so no payload
claims one -- an alert that invents a detail is worse than a terse one.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable

from netadmin.domain.types import Severity
from netadmin.integrations.alerts.models import RESOLVED, AlertEvent, DigestSummary, Payload

__all__ = [
    "discord_payload",
    "discord_digest",
    "slack_payload",
    "slack_digest",
    "ntfy_payload",
    "ntfy_digest",
    "webhook_payload",
    "webhook_digest",
    "PAYLOAD_BUILDERS",
    "DIGEST_BUILDERS",
]

# --- provider caps --------------------------------------------------------- #

DISCORD_TITLE_MAX = 256
DISCORD_DESCRIPTION_MAX = 4096
SLACK_HEADER_MAX = 150  # plain_text header block
SLACK_TEXT_MAX = 3000
NTFY_TITLE_MAX = 200
NTFY_BODY_MAX = 4096

_ELLIPSIS = "..."

# --- severity presentation ------------------------------------------------- #

# Discord embed colours. Resolved is green regardless of the issue's severity: the
# colour answers "is something wrong right now", not "how bad was it".
_DISCORD_RESOLVED_COLOR = 0x2E9E5B
_DISCORD_COLORS: dict[str, int] = {
    Severity.P1.value: 0xD7263D,  # red
    Severity.P2.value: 0xE8A33D,  # amber
    Severity.P3.value: 0x5B6B7B,  # slate
}
_DISCORD_DEFAULT_COLOR = 0x5B6B7B

# ntfy priorities: 5 = max, 3 = default. A resolve is informational.
_NTFY_RESOLVED_PRIORITY = "3"
_NTFY_PRIORITIES: dict[str, str] = {
    Severity.P1.value: "5",
    Severity.P2.value: "4",
    Severity.P3.value: "3",
}
_NTFY_DEFAULT_PRIORITY = "3"

_NTFY_RESOLVED_TAG = "white_check_mark"
_NTFY_TAGS: dict[str, str] = {
    Severity.P1.value: "rotating_light",
    Severity.P2.value: "warning",
    Severity.P3.value: "information_source",
}
_NTFY_DEFAULT_TAG = "warning"


def _truncate(text: str, limit: int) -> str:
    """Clamp ``text`` to ``limit`` characters, marking that it was cut."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(_ELLIPSIS):
        return text[:limit]
    return text[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def _header_safe(text: str, limit: int) -> str:
    """Render ``text`` safe for an HTTP header value (ntfy's ``Title``).

    Header values cannot carry newlines (a CR/LF would split the request) and are
    not reliably UTF-8 across proxies, so this folds to ASCII and strips control
    characters. An SSID or device name in a non-Latin script degrades to its
    closest ASCII form rather than breaking the request.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch if ch.isprintable() else " " for ch in folded).strip()
    cleaned = " ".join(cleaned.split())
    return _truncate(cleaned, limit) or "netadmin alert"


def _iso(ts: int) -> str:
    """A UTC ISO-8601 timestamp (storage and transport are always UTC)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _headline(event: AlertEvent) -> str:
    """The one-line summary every provider leads with."""
    if event.event == RESOLVED:
        return f"Resolved: {event.title}"
    return f"{event.severity.upper()} {event.event}: {event.title}"


def _detail_fields(event: AlertEvent, *, site_id: str) -> list[tuple[str, str]]:
    """The ordered (label, value) facts a transition genuinely carries."""
    fields: list[tuple[str, str]] = [
        ("Severity", event.severity.upper()),
        ("Detector", event.detector_key),
        ("Issue", f"#{event.issue_id}"),
        ("Site", site_id),
    ]
    occurrences = (event.transition.detail or {}).get("occurrences")
    if isinstance(occurrences, int):
        fields.append(("Occurrences", str(occurrences)))
    return fields


def _digest_headline(summary: DigestSummary) -> str:
    """e.g. ``"14 alerts coalesced: 12 opened, 2 resolved (3 P1, 11 P2)"``."""
    events = ", ".join(
        f"{count} {name}" for name, count in sorted(summary.by_event.items(), key=lambda kv: kv[0])
    )
    severities = ", ".join(
        f"{summary.by_severity[sev]} {sev.upper()}"
        for sev in (Severity.P1.value, Severity.P2.value, Severity.P3.value)
        if summary.by_severity.get(sev)
    )
    head = f"{summary.count} alerts coalesced"
    if events:
        head += f": {events}"
    if severities:
        head += f" ({severities})"
    return head


def _digest_body(summary: DigestSummary, *, site_id: str) -> str:
    """The digest body: why it exists, then a short sample of what is in it."""
    lines = [
        f"Rate limit reached on channel {summary.channel}; these were summarised "
        "rather than sent one by one.",
        f"Site: {site_id}",
        f"Window: {_iso(summary.first_ts)} to {_iso(summary.last_ts)}",
    ]
    if summary.top_titles:
        lines.append("Top: " + "; ".join(summary.top_titles))
    return "\n".join(lines)


# --- Discord --------------------------------------------------------------- #


def _discord_embed(title: str, description: str, color: int, ts: int, site_id: str) -> Payload:
    return Payload(
        json={
            "embeds": [
                {
                    "title": _truncate(title, DISCORD_TITLE_MAX),
                    "description": _truncate(description, DISCORD_DESCRIPTION_MAX),
                    "color": color,
                    "timestamp": _iso(ts),
                    "footer": {"text": _truncate(f"netadmin / {site_id}", DISCORD_TITLE_MAX)},
                }
            ]
        }
    )


def discord_payload(event: AlertEvent, *, site_id: str) -> Payload:
    """A Discord webhook embed for one event."""
    color = (
        _DISCORD_RESOLVED_COLOR
        if event.event == RESOLVED
        else _DISCORD_COLORS.get(event.severity, _DISCORD_DEFAULT_COLOR)
    )
    description = "\n".join(
        f"**{label}:** {value}" for label, value in _detail_fields(event, site_id=site_id)
    )
    return _discord_embed(_headline(event), description, color, event.ts, site_id)


def discord_digest(summary: DigestSummary, *, site_id: str) -> Payload:
    """A Discord embed summarising a coalesced batch."""
    color = _DISCORD_COLORS.get(summary.worst_severity, _DISCORD_DEFAULT_COLOR)
    return _discord_embed(
        _digest_headline(summary),
        _digest_body(summary, site_id=site_id),
        color,
        summary.last_ts,
        site_id,
    )


# --- Slack ----------------------------------------------------------------- #


def _slack_blocks(headline: str, fields: list[tuple[str, str]], site_id: str) -> Payload:
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _truncate(headline, SLACK_HEADER_MAX)},
        }
    ]
    if fields:
        blocks.append(
            {
                "type": "section",
                # Slack renders at most 10 fields per section.
                "fields": [
                    {"type": "mrkdwn", "text": _truncate(f"*{label}*\n{value}", SLACK_TEXT_MAX)}
                    for label, value in fields[:10]
                ],
            }
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": _truncate(f"netadmin / {site_id}", SLACK_TEXT_MAX)}
            ],
        }
    )
    # ``text`` is the notification fallback Slack shows on a phone lock screen.
    return Payload(json={"text": _truncate(headline, SLACK_TEXT_MAX), "blocks": blocks})


def slack_payload(event: AlertEvent, *, site_id: str) -> Payload:
    """A Slack incoming-webhook message for one event."""
    return _slack_blocks(_headline(event), _detail_fields(event, site_id=site_id), site_id)


def slack_digest(summary: DigestSummary, *, site_id: str) -> Payload:
    """A Slack message summarising a coalesced batch."""
    fields = [("Events", str(summary.count)), ("Channel", summary.channel)]
    if summary.top_titles:
        fields.append(("Top", "\n".join(summary.top_titles)))
    return _slack_blocks(_digest_headline(summary), fields, site_id)


# --- ntfy ------------------------------------------------------------------ #


def _ntfy(title: str, body: str, priority: str, tag: str) -> Payload:
    return Payload(
        content=_truncate(body, NTFY_BODY_MAX),
        headers={
            "Title": _header_safe(title, NTFY_TITLE_MAX),
            "Priority": priority,
            "Tags": tag,
        },
    )


def ntfy_payload(event: AlertEvent, *, site_id: str) -> Payload:
    """An ntfy publish: plain-text body, metadata in headers (topic is in the URL)."""
    if event.event == RESOLVED:
        priority, tag = _NTFY_RESOLVED_PRIORITY, _NTFY_RESOLVED_TAG
    else:
        priority = _NTFY_PRIORITIES.get(event.severity, _NTFY_DEFAULT_PRIORITY)
        tag = _NTFY_TAGS.get(event.severity, _NTFY_DEFAULT_TAG)
    body = "\n".join(f"{label}: {value}" for label, value in _detail_fields(event, site_id=site_id))
    return _ntfy(_headline(event), body, priority, tag)


def ntfy_digest(summary: DigestSummary, *, site_id: str) -> Payload:
    """An ntfy publish summarising a coalesced batch."""
    priority = _NTFY_PRIORITIES.get(summary.worst_severity, _NTFY_DEFAULT_PRIORITY)
    tag = _NTFY_TAGS.get(summary.worst_severity, _NTFY_DEFAULT_TAG)
    return _ntfy(_digest_headline(summary), _digest_body(summary, site_id=site_id), priority, tag)


# --- raw webhook ----------------------------------------------------------- #


def webhook_payload(event: AlertEvent, *, site_id: str) -> Payload:
    """The full machine-readable event: the HA events-topic shape plus routing keys.

    Deliberately the same field names the MQTT ``netadmin/events`` topic uses, so a
    consumer written against one works against the other.
    """
    transition = event.transition
    return Payload(
        json={
            "event": event.event,
            "source": "netadmin",
            "site_id": site_id,
            "kind": transition.kind,
            "issue_id": transition.issue_id,
            "fingerprint": transition.fingerprint,
            "detector": transition.detector_key,
            "severity": event.severity,
            "title": transition.title,
            "ts": int(transition.ts),
            "from_state": transition.from_state.value if transition.from_state else None,
            "to_state": transition.to_state.value if transition.to_state else None,
            "detail": dict(transition.detail or {}),
        }
    )


def webhook_digest(summary: DigestSummary, *, site_id: str) -> Payload:
    """The machine-readable digest: exact counts, not a prose summary."""
    return Payload(
        json={
            "event": "digest",
            "source": "netadmin",
            "site_id": site_id,
            "channel": summary.channel,
            "count": summary.count,
            "by_event": dict(summary.by_event),
            "by_severity": dict(summary.by_severity),
            "top_titles": list(summary.top_titles),
            "first_ts": summary.first_ts,
            "last_ts": summary.last_ts,
        }
    )


# --- registry -------------------------------------------------------------- #

PayloadBuilder = Callable[..., Payload]

PAYLOAD_BUILDERS: dict[str, PayloadBuilder] = {
    "discord": discord_payload,
    "slack": slack_payload,
    "ntfy": ntfy_payload,
    "webhook": webhook_payload,
}

DIGEST_BUILDERS: dict[str, PayloadBuilder] = {
    "discord": discord_digest,
    "slack": slack_digest,
    "ntfy": ntfy_digest,
    "webhook": webhook_digest,
}
