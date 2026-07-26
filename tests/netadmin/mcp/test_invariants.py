"""Invariants that must hold for *every* tool, swept rather than spot-checked.

A per-tool test proves one tool behaves. These prove the properties the whole
component is sold on, and they are written as sweeps over
:data:`netadmin.mcp.tools.TOOLS` on purpose: a twelfth tool added later is
covered the moment it is registered, with no chance of quietly opting out of
read-only, caps, honest empty answers or redaction.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from netadmin.mcp import format as fmt
from netadmin.mcp import tools
from netadmin.store import db as _db
from netadmin.store.repository import Repository

from .conftest import DEMO_NOW

# A full 6-octet MAC in hex. After redaction the last three octets are "xx", which
# is not hex, so a surviving match means a MAC leaked.
_MAC_RE = re.compile(r"\b[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\b")

# Arguments that make each tool reach a *real* code path on an empty store rather
# than short-circuiting on a missing argument.
_EMPTY_DB_PARAMS: dict[str, dict[str, Any]] = {
    "netadmin_when_did_this_start": {"issue": 1},
    "netadmin_has_this_happened_before": {"issue": 1},
    "netadmin_events_around": {"at": "2030-01-01T00:00:00Z"},
    "netadmin_metric_history": {"entity": "aa:bb:cc:dd:ee:ff", "metric": "rssi"},
    "netadmin_client_experience": {"entity": "aa:bb:cc:dd:ee:ff"},
}


def test_every_tool_is_registered_with_the_prefix_and_routing_sentence() -> None:
    assert len(tools.TOOLS) == 11
    for name, spec in tools.TOOLS.items():
        assert name.startswith("netadmin_")
        assert spec.description.startswith("History:")
        assert (
            "from the local UnifiOptimizer history store; works even when the "
            "controller has forgotten or the daemon is down" in spec.description
        )
        assert "Read-only" in spec.description
        assert spec.input_schema["type"] == "object"


def test_no_live_state_twin_is_shipped() -> None:
    """The routing story only works if this server never duplicates live state."""
    forbidden = ("list_devices", "list_clients", "get_device", "reboot", "apply", "set_")
    assert not [name for name in tools.TOOLS if any(word in name for word in forbidden)]


# --------------------------------------------------------------------------- #
# Read-only
# --------------------------------------------------------------------------- #
def test_the_connection_itself_refuses_writes(demo_repo: Repository) -> None:
    assert int(demo_repo.connection.execute("PRAGMA query_only").fetchone()[0]) == 1
    with pytest.raises(sqlite3.OperationalError):
        demo_repo.connection.execute("DELETE FROM issues")


def test_every_tool_runs_clean_against_a_read_only_store(
    demo_repo: Repository, demo_params: dict[str, dict[str, Any]]
) -> None:
    """The sweep that also catches a read path which lazily writes.

    ``get_series`` interning, rollup metric caching and baseline lookups all sit
    one refactor away from an INSERT. Running every tool against a ``mode=ro``
    connection turns that class of mistake into a test failure instead of a
    runtime error in a user's Claude session.
    """
    for name in tools.TOOLS:
        result = tools.call_tool(demo_repo, name, demo_params[name], now=DEMO_NOW)
        assert result.get("error") != "store_error", (name, result["summary"])
        assert result["summary"], name


def _imported_modules(source: Path) -> set[str]:
    """Every module name the file imports, from its AST rather than its text.

    Parsed, not grepped: the module docstrings *discuss* ``netadmin.config`` at
    length precisely because not importing it is the point, and a substring
    search would flag the explanation as the violation.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_mcp_package_never_imports_the_mutating_layers() -> None:
    """The third read-only guarantee: no reachable code path can change anything.

    ``netadmin.fixes`` and ``netadmin.ingest`` are the only code in the project
    that can change a controller, and ``netadmin.config`` is the only code that
    reads ``data/secrets.env``. None of the three is importable from this
    process, so neither a bug nor a prompt injection has anything to reach for.
    """
    forbidden = ("netadmin.fixes", "netadmin.ingest", "netadmin.config")
    for source in Path(tools.__file__).parent.glob("*.py"):
        for module in _imported_modules(source):
            assert not module.startswith(forbidden), (source.name, module)


# --------------------------------------------------------------------------- #
# Caps and size
# --------------------------------------------------------------------------- #
def _row_lists(node: Any) -> list[list[Any]]:
    """Every list of rows (dicts) anywhere in a payload."""
    found: list[list[Any]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        values = current.values() if isinstance(current, dict) else current
        if not isinstance(current, (dict, list)):
            continue
        for value in values:
            if isinstance(value, list):
                if value and all(isinstance(item, dict) for item in value):
                    found.append(value)
                stack.append(value)
            elif isinstance(value, dict):
                stack.append(value)
    return found


def test_no_tool_can_exceed_the_hard_row_cap_or_the_size_budget(
    demo_repo: Repository, demo_params: dict[str, dict[str, Any]]
) -> None:
    for name in tools.TOOLS:
        params = dict(demo_params[name], limit=9999)
        result = tools.call_tool(demo_repo, name, params, now=DEMO_NOW)
        for rows in _row_lists(result):
            assert len(rows) <= fmt.MAX_LIMIT, (name, len(rows))
        assert len(json.dumps(result, default=str).encode()) <= fmt.MAX_RESPONSE_BYTES, name


def test_a_clipped_list_admits_it(demo_repo: Repository) -> None:
    result = tools.call_tool(demo_repo, "netadmin_issues", {"limit": 2}, now=DEMO_NOW)
    block = result["issues"]
    assert len(block["items"]) == 2
    assert block["total"] > 2
    assert block["truncated"] is True


def test_the_defensive_sweep_clips_a_list_a_handler_built_by_hand() -> None:
    capped = tools._cap_rows({"rows": [{"n": n} for n in range(500)]})
    assert len(capped["rows"]) == fmt.MAX_LIMIT


def test_the_sweep_leaves_a_downsampled_series_alone() -> None:
    points = [["2030-01-01T00:00:00Z", 1.0]] * 96
    assert len(tools._cap_rows({"points": points})["points"]) == 96


def test_every_payload_leads_with_summary(
    demo_repo: Repository, demo_params: dict[str, dict[str, Any]]
) -> None:
    for name in tools.TOOLS:
        result = tools.call_tool(demo_repo, name, demo_params[name], now=DEMO_NOW)
        assert list(result)[0] == "summary", name
        assert result["summary"].count(".") <= 4, (name, result["summary"])


# --------------------------------------------------------------------------- #
# Empty store
# --------------------------------------------------------------------------- #
def test_a_fresh_empty_store_answers_honestly_on_every_tool(empty_repo: Repository) -> None:
    """No exceptions, no fabricated zeros -- a sentence saying there is nothing."""
    for name in tools.TOOLS:
        result = tools.call_tool(empty_repo, name, _EMPTY_DB_PARAMS.get(name, {}), now=DEMO_NOW)
        assert result.get("error") != "store_error", name
        summary = result["summary"]
        assert summary
        assert re.search(r"\bno\b|\bnothing\b|\bnot\b", summary, re.IGNORECASE), (name, summary)


def test_the_empty_store_hint_points_at_the_fix(empty_repo: Repository) -> None:
    result = tools.call_tool(empty_repo, "netadmin_overview", {}, now=DEMO_NOW)
    assert "netadmin" in result["summary"]


# --------------------------------------------------------------------------- #
# Schema gate
# --------------------------------------------------------------------------- #
def _repo_at_version(tmp_path: Path, version: int) -> Repository:
    path = tmp_path / "netadmin.db"
    writable = Repository.open(path)
    writable.connection.execute(f"PRAGMA user_version={version}")
    writable.close()
    return Repository.open(path, read_only=True)


def test_a_current_store_passes_the_gate(empty_repo: Repository) -> None:
    assert tools.schema_gate(empty_repo) is None


def test_an_older_store_is_told_to_migrate(tmp_path: Path) -> None:
    repo = _repo_at_version(tmp_path, 1)
    try:
        gate = tools.schema_gate(repo)
        assert gate is not None and "Run `netadmin` once" in gate
        result = tools.call_tool(repo, "netadmin_overview", {}, now=DEMO_NOW)
        assert result["error"] == "schema_mismatch"
        assert result["summary"] == gate
    finally:
        repo.close()


def test_a_newer_store_is_told_to_upgrade_the_package(tmp_path: Path) -> None:
    repo = _repo_at_version(tmp_path, _db.latest_migration_version() + 5)
    try:
        gate = tools.schema_gate(repo)
        assert gate is not None and "pip install -U unifioptimizer" in gate
    finally:
        repo.close()


def test_the_gate_short_circuits_every_tool(tmp_path: Path) -> None:
    repo = _repo_at_version(tmp_path, 1)
    try:
        for name in tools.TOOLS:
            result = tools.call_tool(repo, name, {}, now=DEMO_NOW)
            assert result["error"] == "schema_mismatch", name
    finally:
        repo.close()


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def test_redaction_is_off_unless_asked_for(
    demo_repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NETADMIN_MCP_REDACT", raising=False)
    assert tools.redaction_enabled() is False
    result = tools.call_tool(demo_repo, "netadmin_issues", {}, now=DEMO_NOW)
    assert _MAC_RE.search(json.dumps(result, default=str))


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_redaction_env_flag_accepts_the_usual_truthy_spellings(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETADMIN_MCP_REDACT", value)
    assert tools.redaction_enabled() is True


def test_redaction_masks_every_mac_and_name_across_every_tool(
    demo_repo: Repository,
    demo_params: dict[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETADMIN_MCP_REDACT", "1")
    real_names = {
        str(row["name"])
        for row in demo_repo.list_entities()
        if row["name"] and len(str(row["name"])) >= 4
    }
    for name in tools.TOOLS:
        encoded = json.dumps(
            tools.call_tool(demo_repo, name, demo_params[name], now=DEMO_NOW), default=str
        )
        assert not _MAC_RE.search(encoded), (name, _MAC_RE.search(encoded).group(0))
        leaked = [real for real in real_names if real in encoded]
        assert not leaked, (name, leaked)


def test_redaction_keeps_entity_ids_so_drill_downs_still_work(
    demo_repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETADMIN_MCP_REDACT", "1")
    redacted = tools.call_tool(demo_repo, "netadmin_issues", {}, now=DEMO_NOW)
    monkeypatch.delenv("NETADMIN_MCP_REDACT")
    plain = tools.call_tool(demo_repo, "netadmin_issues", {}, now=DEMO_NOW)

    redacted_ids = [row["issue_id"] for row in redacted["issues"]["items"]]
    plain_ids = [row["issue_id"] for row in plain["issues"]["items"]]
    assert redacted_ids == plain_ids
    assert [row["entity"]["entity_id"] for row in redacted["issues"]["items"] if row["entity"]] == [
        row["entity"]["entity_id"] for row in plain["issues"]["items"] if row["entity"]
    ]


def test_a_redacted_name_is_stable_across_calls(
    demo_repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETADMIN_MCP_REDACT", "1")
    first = tools.call_tool(demo_repo, "netadmin_issues", {}, now=DEMO_NOW)
    second = tools.call_tool(demo_repo, "netadmin_worst_offenders", {}, now=DEMO_NOW)
    names = {
        row["entity"]["entity_id"]: row["entity"]["name"]
        for row in first["issues"]["items"]
        if row["entity"]
    }
    for row in second["offenders"]["items"]:
        entity = row["entity"]
        if entity and entity["entity_id"] in names:
            assert entity["name"] == names[entity["entity_id"]]
