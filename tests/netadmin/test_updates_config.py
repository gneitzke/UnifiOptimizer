"""UpdatesConfig: the self-update version-check cadence (section 23)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from netadmin.config import Settings, UpdatesConfig


def test_defaults() -> None:
    cfg = UpdatesConfig()
    assert cfg.check is True
    assert cfg.interval_s == 86_400


def test_settings_carries_the_default_block() -> None:
    settings = Settings(_env_file=None)
    assert settings.updates.check is True
    assert settings.updates.interval_s == 86_400


def test_env_override_disables_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATES__CHECK", "0")
    assert Settings(_env_file=None).updates.check is False
    monkeypatch.delenv("UPDATES__CHECK", raising=False)
    assert Settings(_env_file=None).updates.check is True


def test_env_override_sets_the_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATES__INTERVAL_S", "3600")
    assert Settings(_env_file=None).updates.interval_s == 3600
    monkeypatch.delenv("UPDATES__INTERVAL_S", raising=False)


def test_interval_below_the_floor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UpdatesConfig(interval_s=59)


def test_interval_at_the_floor_is_accepted() -> None:
    assert UpdatesConfig(interval_s=60).interval_s == 60


def test_dict_coercion_on_settings_construction() -> None:
    """Tests elsewhere construct Settings(updates={"check": False}) directly;
    pydantic must coerce the plain dict into UpdatesConfig."""
    settings = Settings(_env_file=None, updates={"check": False, "interval_s": 120})
    assert isinstance(settings.updates, UpdatesConfig)
    assert settings.updates.check is False
    assert settings.updates.interval_s == 120
