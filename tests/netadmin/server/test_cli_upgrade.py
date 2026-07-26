"""CLI ``netadmin upgrade run`` tests: parser wiring + the runner hand-off.

The actual nine-step procedure is :mod:`netadmin.upgrade.runner`'s own concern
(covered by ``tests/netadmin/upgrade/test_runner.py``); these tests only pin that
the CLI wires ``--target`` through to :func:`run_upgrade` and translates its
outcome into the right exit code and log line.
"""

from __future__ import annotations

import pytest

from netadmin import cli
from netadmin.upgrade import runner as runner_mod
from netadmin.upgrade.journal import UpgradeJournal
from netadmin.upgrade.runner import RunnerError


def test_parser_requires_a_target() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["upgrade", "run"])


def test_parser_wires_upgrade_run_target() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["upgrade", "run", "--target", "0.4.0"])
    assert args.command == "upgrade"
    assert args.upgrade_command == "run"
    assert args.target == "0.4.0"
    assert args.func is cli._cmd_upgrade_run


def test_upgrade_requires_a_subcommand() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["upgrade"])


def test_cmd_upgrade_run_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_run_upgrade(
        target_version: str, *, settings: object, deps: object = None
    ) -> UpgradeJournal:
        calls.append((target_version, settings))
        return UpgradeJournal(
            phase="done",
            target_version=target_version,
            from_version="0.3.0",
            started_ts=1,
            updated_ts=2,
        )

    monkeypatch.setattr(runner_mod, "run_upgrade", fake_run_upgrade)
    args = cli.build_parser().parse_args(["upgrade", "run", "--target", "0.4.0"])

    assert cli._cmd_upgrade_run(args) == 0
    assert calls[0][0] == "0.4.0"


def test_cmd_upgrade_run_reports_runner_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_upgrade(
        target_version: str, *, settings: object, deps: object = None
    ) -> UpgradeJournal:
        raise RunnerError("simulated failure: pip download failed")

    monkeypatch.setattr(runner_mod, "run_upgrade", fake_run_upgrade)
    args = cli.build_parser().parse_args(["upgrade", "run", "--target", "0.4.0"])

    assert cli._cmd_upgrade_run(args) == 1


def test_cmd_upgrade_run_reports_an_unexpected_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_upgrade(
        target_version: str, *, settings: object, deps: object = None
    ) -> UpgradeJournal:
        raise RuntimeError("something truly unexpected")

    monkeypatch.setattr(runner_mod, "run_upgrade", fake_run_upgrade)
    args = cli.build_parser().parse_args(["upgrade", "run", "--target", "0.4.0"])

    assert cli._cmd_upgrade_run(args) == 1
