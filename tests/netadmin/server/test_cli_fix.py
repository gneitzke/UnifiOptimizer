"""CLI ``netadmin fix`` tests: parser wiring + the dry-run/apply plumbing offline.

The controller seams are injected fakes via a monkeypatched ``build_fix_seams``, so
the command exercises the real :class:`FixService` against a real store without any
network. The FakeControllerWriter's call count proves dry-run sends nothing and a
confirmed apply sends exactly one mutation.
"""

from __future__ import annotations

import pytest

from netadmin import cli
from netadmin.domain.entities import Entity
from netadmin.domain.types import EntityType
from netadmin.fixes import service as service_mod
from netadmin.fixes.reader import FakeDeviceReader
from netadmin.fixes.service import FixSeams
from netadmin.fixes.writer import FakeControllerWriter
from netadmin.store.repository import Repository

NOW = 1_700_000_000
AP_MAC = "aa:bb:cc:00:00:01"
AP_ID = "60a1b2c3d4e5f60000000001"


def _ap_device() -> dict:
    return {
        "_id": AP_ID,
        "mac": AP_MAC,
        "type": "uap",
        "radio_table": [{"radio": "ng", "channel": 3, "ht": 20, "tx_power_mode": "high"}],
    }


@pytest.fixture
def seeded(settings, monkeypatch):
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    ap = store.upsert_entity(
        Entity(entity_type=EntityType.AP, native_id=AP_MAC, name="Office AP"), ts=NOW
    )
    radio = store.upsert_entity(
        Entity(
            entity_type=EntityType.RADIO,
            native_id=f"{AP_MAC}:ng",
            parent_id=ap,
            meta={"band": "ng"},
        ),
        ts=NOW,
    )
    issue_id = store.insert_issue(
        fingerprint="fp-channel",
        detector_key="wifi.channel_plan",
        severity="p3",
        state="active",
        first_seen_ts=NOW,
        last_seen_ts=NOW,
        title="2.4 GHz off-grid",
        entity_id=radio,
        evidence={"subtype": "channel_off_grid", "band": "2.4", "channel": 3},
    )
    store.close()

    writer = FakeControllerWriter()
    seams = FixSeams(reader=FakeDeviceReader({AP_MAC: _ap_device()}), writer=writer, closer=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(service_mod, "build_fix_seams", lambda s, *, for_apply: seams)
    return {"settings": settings, "issue_id": issue_id, "writer": writer}


def test_parser_wires_fix_subcommand() -> None:
    p = cli.build_parser()
    a = p.parse_args(["fix", "7"])
    assert a.command == "fix" and a.issue_id == 7 and a.apply is False
    b = p.parse_args(["fix", "7", "--apply", "--confirm"])
    assert b.apply is True and b.confirm is True
    c = p.parse_args(["fix", "--revert", "3"])
    assert c.revert == 3 and c.issue_id is None
    d = p.parse_args(["fix", "7", "--dry-run"])
    assert d.dry_run is True and d.apply is False


def test_fix_apply_without_confirm_is_usage_error() -> None:
    args = cli.build_parser().parse_args(["fix", "7", "--apply"])
    assert cli._cmd_fix(args) == 2


def test_fix_dry_run_and_apply_are_mutually_exclusive() -> None:
    args = cli.build_parser().parse_args(["fix", "7", "--dry-run", "--apply", "--confirm"])
    assert cli._cmd_fix(args) == 2


def test_fix_dry_run_sends_nothing(seeded, capsys) -> None:
    args = cli.build_parser().parse_args(["fix", str(seeded["issue_id"])])
    rc = cli._cmd_fix(args)
    assert rc == 0
    assert seeded["writer"].call_count == 0
    out = capsys.readouterr().out
    assert f"rest/device/{AP_ID}" in out
    assert "confirm_token:" in out


def test_fix_apply_confirmed_mutates_once(seeded) -> None:
    args = cli.build_parser().parse_args(["fix", str(seeded["issue_id"]), "--apply", "--confirm"])
    rc = cli._cmd_fix(args)
    assert rc == 0
    assert seeded["writer"].call_count == 1
    # The change landed in the ledger, marked applied.
    store = Repository.open(seeded["settings"].db_path, site_id=seeded["settings"].site_id)
    try:
        changes = store.list_changes(issue_id=seeded["issue_id"])
        assert len(changes) == 1 and changes[0]["status"] == "applied"
    finally:
        store.close()


def test_fix_unknown_issue_returns_1(seeded) -> None:
    args = cli.build_parser().parse_args(["fix", "999999"])
    assert cli._cmd_fix(args) == 1
