"""CLI smoke tests for ``netadmin visit``.

The runner is stubbed (exercised for real in ``test_runner``); these assert the
subcommand wiring: credential resolution, the unconfigured guard, ``--out``
format handling, and exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from netadmin import cli
from netadmin.config import Settings
from netadmin.visit.runner import STEP_ORDER, VisitReport, VisitStep


def _report() -> VisitReport:
    return VisitReport(
        started_ts=1_900_000_000,
        finished_ts=1_900_000_010,
        window_start_ts=1_899_990_000,
        window_end_ts=1_900_000_000,
        site_id="default",
        lookback_days=2,
        controller_host="unifi.local",
        headline_score=0.88,
        sles={"sles": {}},
        issues=[
            {
                "detector_key": "wifi.channel_plan",
                "severity": "p3",
                "state": "active",
                "title": "Channel-plan issue",
                "entity": {"name": "ap-office", "native_id": "aa:bb", "entity_id": 1},
                "evidence": {},
                "confounders": [],
            }
        ],
        issue_counts={"total": 1, "p1": 0, "p2": 0, "p3": 1, "open": 1},
        topology={"entity_count": 5, "by_type": {"ap": 1}, "devices": []},
        coverage=[],
        caveats=[],
        steps=[VisitStep(id=s, label=lbl).to_dict() for s, lbl in STEP_ORDER],
        db_path=None,
    )


@pytest.fixture
def clean_settings(monkeypatch):
    """Hermetic, unconfigured settings regardless of a local data/secrets.env."""
    settings = Settings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def stub_run(monkeypatch):
    calls: list[dict] = []

    def _run(settings, *, lookback_days=None, progress=None):
        calls.append({"host": settings.unifi.host, "lookback_days": lookback_days})
        if progress:
            for sid, label in STEP_ORDER:
                progress(VisitStep(id=sid, label=label, status="ok"))
        return _report()

    monkeypatch.setattr("netadmin.visit.run_visit", _run)
    return calls


def test_visit_parser_accepts_flags():
    args = cli.build_parser().parse_args(
        ["visit", "--host", "h", "--username", "u", "--password", "p", "--lookback-days", "3"]
    )
    assert args.host == "h" and args.lookback_days == 3


def test_visit_unconfigured_returns_1(clean_settings, stub_run, capsys):
    rc = cli.main(["visit"])
    assert rc == 1
    assert stub_run == []  # never attempted a run without credentials


def test_visit_runs_and_prints_summary(clean_settings, stub_run, capsys):
    rc = cli.main(["visit", "--host", "1.2.3.4", "--api-key", "k", "--lookback-days", "3"])
    assert rc == 0
    assert stub_run[0] == {"host": "1.2.3.4", "lookback_days": 3}
    out = capsys.readouterr().out
    assert "Tech visit" in out and "Channel-plan issue" in out


def test_visit_writes_json_report(clean_settings, stub_run, tmp_path: Path):
    out = tmp_path / "report.json"
    rc = cli.main(["visit", "--host", "1.2.3.4", "--api-key", "k", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["controller_host"] == "unifi.local"


def test_visit_writes_html_report(clean_settings, stub_run, tmp_path: Path):
    out = tmp_path / "report.html"
    rc = cli.main(["visit", "--host", "1.2.3.4", "--api-key", "k", "--out", str(out)])
    assert rc == 0
    assert out.read_text().startswith("<!doctype html>")


def test_visit_rejects_bad_out_extension(clean_settings, stub_run, tmp_path: Path):
    rc = cli.main(
        ["visit", "--host", "1.2.3.4", "--api-key", "k", "--out", str(tmp_path / "r.txt")]
    )
    assert rc == 2
