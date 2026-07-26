"""Tests for the ``netadmin-mcp`` entry point's startup behaviour.

None of these need the MCP SDK, which is the point: the two ways a user's first
run fails -- no database at the resolved path, and no SDK installed -- must both
produce a one-line instruction on stderr and exit 1, whether or not the extra is
present. Everything else about stdio is covered by ``test_protocol.py``.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from netadmin.mcp import server
from netadmin.store.repository import Repository


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NETADMIN_DB_PATH", "NETADMIN_DATA_DIR", "NETADMIN_MCP_REDACT"):
        monkeypatch.delenv(name, raising=False)


def test_missing_sdk_prints_the_install_line_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``None`` in ``sys.modules`` is how Python spells "this import fails"."""
    monkeypatch.setitem(sys.modules, "mcp", None)
    assert server.main(["--db", "/nonexistent.db"]) == 1
    err = capsys.readouterr().err
    assert 'pip install "unifioptimizer[mcp]"' in err


def test_the_sdk_is_checked_before_the_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Otherwise a user without the extra chases a database problem they do not have."""
    monkeypatch.setitem(sys.modules, "mcp", None)
    server.main(["--db", "/nonexistent.db"])
    err = capsys.readouterr().err
    assert "No history store" not in err


def test_missing_database_explains_how_to_create_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    pytest.importorskip("mcp", reason="the optional [mcp] extra is not installed")
    missing = tmp_path / "netadmin.db"
    assert server.main(["--db", str(missing)]) == 1
    err = capsys.readouterr().err
    assert str(missing) in err
    assert "--db flag" in err
    assert "Run `netadmin` once" in err


def test_open_store_is_read_only_and_does_not_migrate(tmp_path: Path) -> None:
    path = tmp_path / "netadmin.db"
    Repository.open(path).close()

    repo = server.open_store(path)
    try:
        assert int(repo.connection.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.OperationalError):
            repo.connection.execute("DELETE FROM issues")
    finally:
        repo.close()


def test_stdout_is_never_used_for_diagnostics(capsys: pytest.CaptureFixture[str]) -> None:
    """A stdio server's stdout is the protocol channel; a stray print corrupts it."""
    server._eprint("hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "hello"


def test_parser_defaults_to_environment_resolution() -> None:
    assert server.build_parser().parse_args([]).db is None
