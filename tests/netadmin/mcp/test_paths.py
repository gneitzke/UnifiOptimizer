"""Tests for MCP database discovery (``docs/MCP_SERVER.md`` section 5).

Two things are being protected here. The obvious one is that the four-step
precedence matches the daemon's, so the same environment always points both
processes at the same file. The load-bearing one is that resolving a path never
constructs :class:`netadmin.config.Settings` -- the MCP process must not be able
to read ``data/secrets.env``, and the way to guarantee that is to keep the
resolver too small to do so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadmin.mcp import paths

_ENV_VARS = ("NETADMIN_DB_PATH", "NETADMIN_DATA_DIR")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_explicit_flag_wins_over_everything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NETADMIN_DB_PATH", str(tmp_path / "env.db"))
    monkeypatch.setenv("NETADMIN_DATA_DIR", str(tmp_path))
    assert paths.resolve_db_path(str(tmp_path / "flag.db")) == tmp_path / "flag.db"
    assert paths.describe_db_source(str(tmp_path / "flag.db")) == "--db flag"


def test_db_path_env_beats_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NETADMIN_DB_PATH", str(tmp_path / "env.db"))
    monkeypatch.setenv("NETADMIN_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.resolve_db_path() == tmp_path / "env.db"
    assert paths.describe_db_source() == "NETADMIN_DB_PATH"


def test_data_dir_supplies_the_default_basename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NETADMIN_DATA_DIR", str(tmp_path))
    assert paths.resolve_db_path() == tmp_path / paths.DB_BASENAME
    assert paths.describe_db_source() == "NETADMIN_DATA_DIR"


def test_falls_back_to_data_relative_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert paths.resolve_db_path() == tmp_path / "data" / paths.DB_BASENAME
    assert paths.describe_db_source() == "./data default"


def test_tilde_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETADMIN_DB_PATH", "~/somewhere/netadmin.db")
    resolved = paths.resolve_db_path()
    assert "~" not in str(resolved)
    assert resolved.is_absolute()


def test_resolution_never_touches_settings_or_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The privacy guarantee, asserted rather than assumed.

    ``netadmin.config`` reads ``data/secrets.env`` when its Settings are built.
    If this module ever grew an import of it, controller credentials would be
    live in the MCP process. Poisoning ``Settings`` makes that a test failure.
    """
    import netadmin.config as config

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the MCP path resolver must never build Settings")

    monkeypatch.setattr(config, "Settings", _explode)
    monkeypatch.setattr(config, "get_settings", _explode)
    assert paths.resolve_db_path("x.db") == Path("x.db")


def test_a_missing_file_is_still_resolved(tmp_path: Path) -> None:
    """Existence is the caller's gate: an absent file has its own guidance."""
    target = tmp_path / "nope.db"
    assert paths.resolve_db_path(str(target)) == target
    assert not target.exists()
