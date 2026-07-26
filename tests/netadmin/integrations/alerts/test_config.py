"""Alert configuration: yaml structure, secrets in secrets.env, and validation.

The split is the point: everything structural is declarable in ``config.yaml``, and
the one thing that is a credential -- the delivery URL -- is only ever readable from
``data/secrets.env`` or the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

import netadmin.config as config_module
from netadmin.config import AlertChannelConfig, AlertsConfig, AlertSecrets, Settings

from .conftest import DISCORD_URL, NTFY_URL


@pytest.fixture(autouse=True)
def _no_ambient_alert_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the tests hermetic against a developer's real environment."""
    for key in list(os.environ):
        if key.upper().startswith(("ALERT_URLS__", "ALERT_TOKENS__")):
            monkeypatch.delenv(key, raising=False)


# --- defaults -------------------------------------------------------------- #


def test_alerts_are_off_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.alerts.enabled is False
    assert settings.alerts.channels == []


def test_channel_defaults_match_the_documented_contract() -> None:
    channel = AlertChannelConfig(name="ops", type="discord")
    assert channel.min_severity == "p2"
    assert channel.events == ["opened", "reopened", "resolved"]
    assert channel.timeout_s == 10.0
    assert channel.rate_limit_per_min == 10


# --- yaml structure -------------------------------------------------------- #


def test_channels_parse_from_config_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        """
netadmin:
  alerts:
    enabled: true
    channels:
      - name: discord_ops
        type: discord
        min_severity: p1
        events: [opened, resolved]
        timeout_s: 5
        rate_limit_per_min: 30
      - name: ntfy_phone
        type: ntfy
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_YAML", yaml_path)

    settings = Settings(_env_file=None)
    assert settings.alerts.enabled is True
    first, second = settings.alerts.channels
    assert (first.name, first.type, first.min_severity) == ("discord_ops", "discord", "p1")
    assert first.events == ["opened", "resolved"]
    assert first.timeout_s == 5
    assert first.rate_limit_per_min == 30
    assert (second.name, second.type, second.min_severity) == ("ntfy_phone", "ntfy", "p2")


def test_a_url_in_config_yaml_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A URL pasted into yaml must fail loudly, not be silently dropped.

    ``data/config.yaml`` is a tracked file. Silently ignoring the key gave the
    operator an inert channel *and* a live webhook credential staged for commit,
    with nothing to tell them either had happened. Refusing the key names the
    mistake at startup, and catches option typos in the same stroke.
    """
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        f"""
netadmin:
  alerts:
    enabled: true
    channels:
      - name: discord_ops
        type: discord
        url: {DISCORD_URL}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_YAML", yaml_path)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    # The error must name the offending key so the fix is obvious.
    assert "url" in str(excinfo.value)

    # And the safe form still loads, with the URL coming only from the environment.
    yaml_path.write_text(
        """
netadmin:
  alerts:
    enabled: true
    channels:
      - name: discord_ops
        type: discord
""",
        encoding="utf-8",
    )
    settings = Settings(_env_file=None)
    channel = settings.alerts.channels[0]
    assert not hasattr(channel, "url")
    assert settings.alert_secrets.url_for("discord_ops") is None


# --- secrets --------------------------------------------------------------- #


def test_urls_and_tokens_load_from_secrets_env(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text(
        f"ALERT_URLS__DISCORD_OPS={DISCORD_URL}\n"
        f"ALERT_URLS__NTFY_PHONE={NTFY_URL}\n"
        "ALERT_TOKENS__NTFY_PHONE=tk_abc123\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=str(secrets))

    alert_secrets = settings.alert_secrets
    assert alert_secrets.url_for("discord_ops") == DISCORD_URL
    assert alert_secrets.url_for("ntfy_phone") == NTFY_URL
    assert alert_secrets.token_for("ntfy_phone") == "tk_abc123"
    assert alert_secrets.token_for("discord_ops") is None


def test_url_lookup_is_case_insensitive_and_whitespace_tolerant() -> None:
    """A trailing newline in a hand-edited secrets.env must not break delivery."""
    secrets = AlertSecrets(urls={"DISCORD_OPS": f"  {DISCORD_URL}\t"}, tokens={})
    assert secrets.url_for("discord_ops") == DISCORD_URL
    assert secrets.url_for("DiScOrD_oPs") == DISCORD_URL


def test_blank_secret_values_are_treated_as_unset() -> None:
    secrets = AlertSecrets(urls={"ops": "   "}, tokens={"ops": ""})
    assert secrets.url_for("ops") is None
    assert secrets.token_for("ops") is None


def test_a_channel_with_no_url_resolves_to_none() -> None:
    settings = Settings(
        _env_file=None,
        alerts={"enabled": True, "channels": [{"name": "orphan", "type": "slack"}]},
    )
    assert settings.alert_secrets.url_for("orphan") is None


def test_settings_repr_never_carries_a_delivery_url() -> None:
    settings = Settings(
        _env_file=None,
        alert_urls={"discord_ops": DISCORD_URL},
        alert_tokens={"discord_ops": "tk_abc123"},
    )
    blob = repr(settings)
    assert DISCORD_URL not in blob
    assert "tk_abc123" not in blob


# --- validation ------------------------------------------------------------ #


@pytest.mark.parametrize("name", ["Discord Ops", "discord-ops", "DISCORD", "ops!", ""])
def test_channel_names_are_restricted_to_the_env_safe_alphabet(name: str) -> None:
    """The name keys ``ALERT_URLS__<NAME>``, so it must survive as an env var name."""
    with pytest.raises(ValidationError):
        AlertChannelConfig(name=name, type="discord")


def test_unknown_channel_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AlertChannelConfig(name="ops", type="telegram")


def test_unknown_event_name_is_rejected_loudly() -> None:
    """Silently never firing is the worst possible failure mode for an alert."""
    with pytest.raises(ValidationError, match="unknown alert event"):
        AlertChannelConfig(name="ops", type="discord", events=["opened", "escalated"])


def test_empty_event_list_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AlertChannelConfig(name="ops", type="discord", events=[])


def test_duplicate_event_names_collapse() -> None:
    channel = AlertChannelConfig(name="ops", type="discord", events=["opened", "opened"])
    assert channel.events == ["opened"]


def test_duplicate_channel_names_are_rejected() -> None:
    """Two channels named the same would silently share one secret."""
    with pytest.raises(ValidationError, match="duplicate alert channel name"):
        AlertsConfig(
            enabled=True,
            channels=[
                {"name": "ops", "type": "discord"},
                {"name": "ops", "type": "slack"},
            ],
        )


@pytest.mark.parametrize("timeout", [0, -1, 500])
def test_timeout_bounds_are_enforced(timeout: float) -> None:
    with pytest.raises(ValidationError):
        AlertChannelConfig(name="ops", type="discord", timeout_s=timeout)


@pytest.mark.parametrize("rate", [0, -5, 10_000])
def test_rate_limit_bounds_are_enforced(rate: int) -> None:
    with pytest.raises(ValidationError):
        AlertChannelConfig(name="ops", type="discord", rate_limit_per_min=rate)
