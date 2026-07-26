"""The `netadmin investigate` CLI glue (hermetic — never touches the live DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

import netadmin.cli as cli
import netadmin.llm.manual as manual_mod
from netadmin.config import Settings
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.store.repository import Repository

BASE_TS = 1_700_000_000


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, int]:
    """A tmp DB with one issue, wired into the CLI's settings + dossier dir."""
    db_path = tmp_path / "cli.db"
    dossier_dir = tmp_path / "dossiers"
    store = Repository.open(db_path, site_id="default")
    port = store.upsert_entity(
        Entity(entity_type=EntityType.PORT, native_id="aa:bb:cc:00:00:02:5", name="Port 5"),
        ts=BASE_TS,
    )
    issue_id = store.insert_issue(
        fingerprint="fp-cli",
        detector_key="wired.bad_cable",
        severity="p2",
        state="active",
        first_seen_ts=BASE_TS,
        last_seen_ts=BASE_TS + 600,
        title="rx_errors climbing on Port 5",
        entity_id=port,
        evidence={"rx_errors_per_min": 42},
    )
    store.close()

    settings = Settings(_env_file=None, db_path=db_path, site_id="default")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(manual_mod, "default_base_dir", lambda: dossier_dir)
    return db_path, issue_id


def test_cli_manual_investigate_writes_pending(cli_env: tuple[Path, int]) -> None:
    db_path, issue_id = cli_env
    rc = cli.main(["investigate", str(issue_id), "--provider", "manual"])
    assert rc == 0

    store = Repository.open(db_path, site_id="default")
    try:
        invs = store.list_investigations(issue_id)
        assert len(invs) == 1
        assert invs[0]["status"] == "pending"
        assert invs[0]["provider"] == "manual"
    finally:
        store.close()


def test_cli_import_round_trip(cli_env: tuple[Path, int], tmp_path: Path) -> None:
    db_path, issue_id = cli_env
    assert cli.main(["investigate", str(issue_id), "--provider", "manual"]) == 0

    response = tmp_path / "answer.md"
    response.write_text("## Answers\n### Root cause\nBad cable.\n", encoding="utf-8")
    rc = cli.main(["investigate", "import", str(response), "--issue", str(issue_id)])
    assert rc == 0

    store = Repository.open(db_path, site_id="default")
    try:
        inv = store.list_investigations(issue_id)[0]
        assert inv["status"] == "answered"
        assert "Bad cable." in inv["response_md"]
    finally:
        store.close()


def test_cli_import_missing_args_is_usage_error(cli_env: tuple[Path, int]) -> None:
    _db_path, issue_id = cli_env
    # 'import' without a file / --issue is a usage error (exit 2)
    assert cli.main(["investigate", "import"]) == 2


def test_cli_unknown_issue_exits_1(cli_env: tuple[Path, int]) -> None:
    assert cli.main(["investigate", "999999", "--provider", "manual"]) == 1
