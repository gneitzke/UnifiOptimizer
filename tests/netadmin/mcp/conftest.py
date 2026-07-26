"""Fixtures for the MCP test suite.

Every repository fixture here is opened **read-only**, on purpose. The invariant
that matters most for this component is that no tool path ever writes, and the
cheapest way to keep that honest is to make the read-only connection the only
one the tests ever see: a read path that lazily writes fails the whole suite,
not one dedicated test.

The populated scenario is the demo generator (fictional network, no real data),
seeded once per session because it is the same deterministic bytes every time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from netadmin.demo.seed import DEFAULT_NOW, seed_demo
from netadmin.store.repository import Repository

# The demo dataset's fixed anchor. Every assertion about windows and ages is
# relative to this rather than the wall clock, so the suite cannot rot.
DEMO_NOW = DEFAULT_NOW


@pytest.fixture(scope="session")
def demo_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A fully-populated demo store, generated once for the whole session."""
    path = tmp_path_factory.mktemp("mcp-demo") / "netadmin-demo.db"
    seed_demo(path)
    return path


@pytest.fixture
def demo_repo(demo_db_path: Path) -> Iterator[Repository]:
    """The demo store as the MCP server sees it: ``mode=ro`` + ``query_only``."""
    repo = Repository.open(demo_db_path, read_only=True)
    yield repo
    repo.close()


@pytest.fixture
def empty_repo(tmp_path: Path) -> Iterator[Repository]:
    """A migrated but completely empty store, opened read-only.

    The "fresh install, nothing collected yet" case. Every tool must answer it
    with a sentence, not an exception and not a fabricated zero.
    """
    path = tmp_path / "netadmin.db"
    Repository.open(path).close()
    repo = Repository.open(path, read_only=True)
    yield repo
    repo.close()


@pytest.fixture
def demo_params(demo_repo: Repository) -> dict[str, dict[str, Any]]:
    """Minimal valid arguments for every tool, resolved against the demo store.

    Used by the sweeps (read-only, redaction, caps) so they exercise the tools on
    real rows rather than on error paths, which would prove nothing.
    """
    issue_id = int(demo_repo.list_issues(open_only=True)[0]["id"])
    client = next(
        row
        for row in demo_repo.list_entities("client")
        if demo_repo.get_series(row["entity_id"], "rssi")
    )
    client_id = int(client["entity_id"])
    return {
        "netadmin_overview": {},
        "netadmin_when_did_this_start": {"issue": issue_id},
        "netadmin_has_this_happened_before": {"issue": issue_id},
        "netadmin_issues": {},
        "netadmin_incidents": {},
        "netadmin_sle_trend": {"window": "7d"},
        "netadmin_what_changed": {"window": "7d"},
        "netadmin_worst_offenders": {"window": "7d"},
        "netadmin_metric_history": {"entity": str(client_id), "metric": "rssi"},
        "netadmin_events_around": {"issue": issue_id, "radius": "6h"},
        "netadmin_client_experience": {"entity": str(client_id)},
    }
