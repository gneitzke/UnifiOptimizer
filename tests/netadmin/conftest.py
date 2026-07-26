"""Shared fixtures for the netadmin test suite.

Hermetic by construction: the sample config never reads ``data/secrets.env``
(``_env_file=None``), so tests can run in CI with no controller credentials
present and never pick up real ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.config import Settings


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """A path to a not-yet-created SQLite file inside pytest's tmp dir."""
    return tmp_path / "netadmin.db"


@pytest.fixture
def sample_config(tmp_db_path: Path) -> Settings:
    """A fully-populated Settings with safe, obviously-fake credentials.

    ``_env_file=None`` disables the dotenv source so nothing leaks in from a
    developer's real ``data/secrets.env``.
    """
    return Settings(
        _env_file=None,
        unifi_host="unifi.test.local",
        unifi_username="tester",
        unifi_password="test-pass",  # noqa: S106 - fake fixture credential
        unifi_site="default",
        unifi_api_key=None,
        db_path=tmp_db_path,
    )
