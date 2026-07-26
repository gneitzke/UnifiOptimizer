"""Provider payload shapes (section 20): golden bodies, caps, and header safety."""

from __future__ import annotations

import json

import pytest

from netadmin.integrations.alerts.formats import _header_safe  # noqa: PLC2701 - test-internal
from netadmin.integrations.alerts.formats import (
    DISCORD_DESCRIPTION_MAX,
    DISCORD_TITLE_MAX,
    NTFY_TITLE_MAX,
    SLACK_HEADER_MAX,
    discord_digest,
    discord_payload,
    ntfy_digest,
    ntfy_payload,
    slack_digest,
    slack_payload,
    webhook_digest,
    webhook_payload,
)
from netadmin.integrations.alerts.models import OPENED, REOPENED, RESOLVED, DigestSummary

from .conftest import NOW, alert_event

SITE = "default"


def _digest(**kw: object) -> DigestSummary:
    base: dict[str, object] = {
        "channel": "ops",
        "count": 14,
        "by_event": {OPENED: 12, RESOLVED: 2},
        "by_severity": {"p1": 3, "p2": 11},
        "top_titles": ["ap-office offline", "rx_errors on port 5"],
        "first_ts": NOW,
        "last_ts": NOW + 120,
    }
    base.update(kw)
    return DigestSummary(**base)  # type: ignore[arg-type]


# --- Discord --------------------------------------------------------------- #


def test_discord_payload_is_a_single_embed() -> None:
    payload = discord_payload(alert_event(OPENED, severity="p1"), site_id=SITE)
    assert payload.content is None
    embed = payload.json["embeds"][0]
    assert embed["title"] == "P1 opened: rx_errors climbing on port 5"
    assert "**Detector:** wired.bad_cable" in embed["description"]
    assert "**Issue:** #1" in embed["description"]
    assert embed["timestamp"] == "2030-03-17T17:46:40Z"
    assert embed["footer"]["text"] == "netadmin / default"
    assert json.loads(json.dumps(payload.json)) == payload.json


@pytest.mark.parametrize(
    ("event", "severity", "color"),
    [
        (OPENED, "p1", 0xD7263D),
        (OPENED, "p2", 0xE8A33D),
        (OPENED, "p3", 0x5B6B7B),
        (REOPENED, "p1", 0xD7263D),
        # Resolved is green whatever the severity was: the colour answers
        # "is something wrong right now", not "how bad was it".
        (RESOLVED, "p1", 0x2E9E5B),
        (RESOLVED, "p3", 0x2E9E5B),
    ],
)
def test_discord_colour_map(event: str, severity: str, color: int) -> None:
    payload = discord_payload(alert_event(event, severity=severity), site_id=SITE)
    assert payload.json["embeds"][0]["color"] == color


def test_discord_truncates_to_the_documented_caps() -> None:
    payload = discord_payload(alert_event(OPENED, title="x" * 5000), site_id=SITE)
    embed = payload.json["embeds"][0]
    assert len(embed["title"]) == DISCORD_TITLE_MAX
    assert embed["title"].endswith("...")
    assert len(embed["description"]) <= DISCORD_DESCRIPTION_MAX


def test_discord_digest_summarises_counts() -> None:
    payload = discord_digest(_digest(), site_id=SITE)
    embed = payload.json["embeds"][0]
    assert embed["title"] == "14 alerts coalesced: 12 opened, 2 resolved (3 P1, 11 P2)"
    assert "Top: ap-office offline; rx_errors on port 5" in embed["description"]
    assert embed["color"] == 0xD7263D  # worst severity present


# --- Slack ----------------------------------------------------------------- #


def test_slack_payload_has_a_fallback_and_blocks() -> None:
    payload = slack_payload(alert_event(OPENED, severity="p2"), site_id=SITE)
    body = payload.json
    assert body["text"] == "P2 opened: rx_errors climbing on port 5"
    kinds = [block["type"] for block in body["blocks"]]
    assert kinds == ["header", "section", "context"]
    fields = [f["text"] for f in body["blocks"][1]["fields"]]
    assert "*Severity*\nP2" in fields
    assert "*Detector*\nwired.bad_cable" in fields


def test_slack_header_is_capped() -> None:
    payload = slack_payload(alert_event(OPENED, title="y" * 400), site_id=SITE)
    header = payload.json["blocks"][0]["text"]["text"]
    assert len(header) == SLACK_HEADER_MAX


def test_slack_never_exceeds_ten_section_fields() -> None:
    payload = slack_payload(alert_event(OPENED), site_id=SITE)
    assert len(payload.json["blocks"][1]["fields"]) <= 10


def test_slack_digest_carries_the_sample() -> None:
    payload = slack_digest(_digest(), site_id=SITE)
    fields = [f["text"] for f in payload.json["blocks"][1]["fields"]]
    assert "*Events*\n14" in fields
    assert any("ap-office offline" in f for f in fields)


# --- ntfy ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("event", "severity", "priority", "tag"),
    [
        (OPENED, "p1", "5", "rotating_light"),
        (OPENED, "p2", "4", "warning"),
        (OPENED, "p3", "3", "information_source"),
        (RESOLVED, "p1", "3", "white_check_mark"),
    ],
)
def test_ntfy_priority_and_tags(event: str, severity: str, priority: str, tag: str) -> None:
    payload = ntfy_payload(alert_event(event, severity=severity), site_id=SITE)
    assert payload.headers["Priority"] == priority
    assert payload.headers["Tags"] == tag
    assert payload.json is None
    assert "Detector: wired.bad_cable" in payload.content


def test_ntfy_title_is_header_safe() -> None:
    """A title with a newline would split the request; a non-ASCII name would
    produce a header no proxy is required to carry. Both are folded here."""
    payload = ntfy_payload(
        alert_event(OPENED, title="ap-café\r\nX-Injected: evil"),
        site_id=SITE,
    )
    title = payload.headers["Title"]
    assert "\n" not in title and "\r" not in title
    assert "X-Injected" in title  # kept as text, not as a header
    title.encode("ascii")  # must not raise
    assert "cafe" in title


def test_ntfy_title_degrades_rather_than_failing_on_a_non_ascii_title() -> None:
    """An unrepresentable device name costs detail, never the notification."""
    payload = ntfy_payload(alert_event(OPENED, title="日本語"), site_id=SITE)
    title = payload.headers["Title"]
    title.encode("ascii")  # must not raise
    assert "opened" in title


def test_header_safe_falls_back_when_nothing_survives_the_fold() -> None:
    assert _header_safe("日本語", NTFY_TITLE_MAX) == "netadmin alert"


def test_ntfy_title_is_capped() -> None:
    payload = ntfy_payload(alert_event(OPENED, title="z" * 900), site_id=SITE)
    assert len(payload.headers["Title"]) == NTFY_TITLE_MAX


def test_ntfy_digest_uses_the_worst_severity() -> None:
    payload = ntfy_digest(_digest(), site_id=SITE)
    assert payload.headers["Priority"] == "5"
    assert "Rate limit reached on channel ops" in payload.content


# --- raw webhook ----------------------------------------------------------- #


def test_webhook_payload_matches_the_ha_events_shape() -> None:
    payload = webhook_payload(alert_event(OPENED, severity="p1"), site_id="site-a")
    body = payload.json
    assert body == {
        "event": "opened",
        "source": "netadmin",
        "site_id": "site-a",
        "kind": "escalated",
        "issue_id": 1,
        "fingerprint": "fp-1",
        "detector": "wired.bad_cable",
        "severity": "p1",
        "title": "rx_errors climbing on port 5",
        "ts": NOW,
        "from_state": "pending",
        "to_state": "active",
        "detail": {"reason": "m_reached", "m": 3, "occurrences": 3},
    }
    assert json.loads(json.dumps(body)) == body


def test_webhook_digest_is_machine_readable() -> None:
    body = webhook_digest(_digest(), site_id=SITE).json
    assert body["event"] == "digest"
    assert body["count"] == 14
    assert body["by_event"] == {OPENED: 12, RESOLVED: 2}
    assert body["by_severity"] == {"p1": 3, "p2": 11}
    assert json.loads(json.dumps(body)) == body
