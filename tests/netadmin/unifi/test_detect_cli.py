"""The ``netadmin detect`` CLI + shared playbook rendering.

Exercises :func:`format_console_report` / :func:`secrets_env_lines` per console
kind (the CLI and the docs generator both read the same ``PLAYBOOK``) and drives
the CLI end to end with a stubbed detector — no controller is ever contacted.
"""

from __future__ import annotations

import json

import pytest

from netadmin import cli
from netadmin.ingest.unifi.detect import (
    AUTH_API_KEY,
    AUTH_LEGACY_COOKIE,
    AUTH_UNIFI_OS_COOKIE,
    KIND_CLOUDKEY_GEN2,
    KIND_CLOUDKEY_GEN2_PLUS,
    KIND_LEGACY_SOFTWARE,
    KIND_UCG,
    KIND_UDM,
    KIND_UDM_PRO,
    KIND_UDM_SE,
    KIND_UDR,
    KIND_UDW,
    KIND_UNIFI_OS_SERVER,
    KIND_UNKNOWN_UNIFI_OS,
    KIND_UNREACHABLE,
    ConsoleInfo,
    classify_model,
    format_console_report,
    secrets_env_lines,
)

HOST = "https://ctrl.test"


# --------------------------------------------------------------------------- #
# Model classification (pure, no HTTP)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "shortname,expected",
    [
        ("UCK-G2", KIND_CLOUDKEY_GEN2),
        ("UCK-G2-Plus", KIND_CLOUDKEY_GEN2_PLUS),
        ("UCKP", KIND_CLOUDKEY_GEN2_PLUS),
        ("UDM", KIND_UDM),
        ("UDMPRO", KIND_UDM_PRO),
        ("UDM-Pro", KIND_UDM_PRO),
        ("UDMPROSE", KIND_UDM_SE),
        ("UDM-SE", KIND_UDM_SE),
        ("UDR", KIND_UDR),
        ("UDW", KIND_UDW),
        ("UCG-Ultra", KIND_UCG),
        ("UCG-Max", KIND_UCG),
        ("UniFiOSServer", KIND_UNIFI_OS_SERVER),
        ("something-weird", None),
    ],
)
def test_classify_model_shortnames(shortname, expected):
    assert classify_model(shortname) == expected


def test_classify_model_specific_before_generic():
    # UDM-SE and UDM-Pro must not collapse into the bare UDM bucket.
    assert classify_model("UDMPROSE") == KIND_UDM_SE
    assert classify_model("UDMPRO") == KIND_UDM_PRO


def test_classify_model_falls_through_candidates():
    assert classify_model(None, "", "UDM-Pro") == KIND_UDM_PRO


# --------------------------------------------------------------------------- #
# secrets.env lines per auth path
# --------------------------------------------------------------------------- #
def test_secrets_env_api_key_path():
    info = ConsoleInfo(
        kind=KIND_UDM_PRO,
        is_unifi_os=True,
        api_key_supported=True,
        recommended_auth=AUTH_API_KEY,
        network_version="9.0.114",
    )
    lines = secrets_env_lines(info, HOST)
    assert f"UNIFI_HOST={HOST}" in lines
    assert any(line.startswith("UNIFI_API_KEY=") for line in lines)
    assert "UNIFI_SITE=default" in lines
    assert not any(line.startswith("UNIFI_PASSWORD=") for line in lines)


def test_secrets_env_unifi_os_cookie_path():
    info = ConsoleInfo(
        kind=KIND_UDM_PRO,
        is_unifi_os=True,
        api_key_supported=False,
        recommended_auth=AUTH_UNIFI_OS_COOKIE,
        network_version="8.6.9",
    )
    lines = secrets_env_lines(info, HOST)
    assert f"UNIFI_HOST={HOST}" in lines
    assert any(line.startswith("UNIFI_USERNAME=") for line in lines)
    assert any(line.startswith("UNIFI_PASSWORD=") for line in lines)
    assert not any(line.startswith("UNIFI_API_KEY=") for line in lines)


def test_secrets_env_legacy_cookie_path_uses_8443():
    info = ConsoleInfo(
        kind=KIND_LEGACY_SOFTWARE,
        is_unifi_os=False,
        api_key_supported=False,
        recommended_auth=AUTH_LEGACY_COOKIE,
        network_version="7.5.176",
    )
    lines = secrets_env_lines(info, HOST)
    host_line = next(line for line in lines if line.startswith("UNIFI_HOST="))
    assert host_line.endswith(":8443")
    assert any(line.startswith("UNIFI_USERNAME=") for line in lines)


# --------------------------------------------------------------------------- #
# Report rendering picks the right playbook branch
# --------------------------------------------------------------------------- #
def test_report_api_key_branch_shows_integrations_steps():
    info = ConsoleInfo(
        kind=KIND_UDM_PRO,
        model="UniFi Dream Machine Pro",
        is_unifi_os=True,
        api_key_supported=True,
        recommended_auth=AUTH_API_KEY,
        network_version="9.0.114",
    )
    report = format_console_report(info, HOST)
    assert "UniFi Dream Machine Pro" in report
    assert "udm_pro" in report
    assert "Create the API key:" in report
    assert "Control Plane -> Integrations" in report
    assert "supported (Network 9.0+)" in report
    assert "UNIFI_API_KEY=<paste-your-api-key>" in report


def test_report_cookie_branch_for_below_9():
    info = ConsoleInfo(
        kind=KIND_UDM_PRO,
        model="UniFi Dream Machine Pro",
        is_unifi_os=True,
        api_key_supported=False,
        recommended_auth=AUTH_UNIFI_OS_COOKIE,
        network_version="8.6.9",
    )
    report = format_console_report(info, HOST)
    assert "not available (needs Network 9.0+)" in report
    assert "Set up authentication:" in report
    assert "local access only" in report.lower() or "local admin" in report.lower()
    assert "UNIFI_PASSWORD=<password>" in report


def test_report_legacy_shows_local_admin_cookie_path():
    info = ConsoleInfo(
        kind=KIND_LEGACY_SOFTWARE,
        model="UniFi Network (self-hosted)",
        is_unifi_os=False,
        api_key_supported=False,
        recommended_auth=AUTH_LEGACY_COOKIE,
        network_version="7.5.176",
    )
    report = format_console_report(info, HOST)
    assert "no API-key support" in report
    assert "/api/login" in report
    assert ":8443" in report


def test_report_unknown_unifi_os_still_actionable():
    info = ConsoleInfo(
        kind=KIND_UNKNOWN_UNIFI_OS,
        model=None,
        is_unifi_os=True,
        api_key_supported=True,
        recommended_auth=AUTH_API_KEY,
        network_version="9.0.0",
        detail="console model is not exposed without authentication",
    )
    report = format_console_report(info, HOST)
    assert "unknown_unifi_os" in report
    assert "Create the API key:" in report
    assert "Note:" in report


def test_report_unreachable_guides_recovery():
    info = ConsoleInfo(
        kind=KIND_UNREACHABLE,
        is_unifi_os=False,
        api_key_supported=False,
        recommended_auth="none",
        reachable=False,
        detail="no UniFi console answered the read-only probe",
    )
    report = format_console_report(info, HOST)
    assert "unknown (console unreachable)" in report
    assert "netadmin detect --host" in report


# --------------------------------------------------------------------------- #
# CLI end to end (detector stubbed — no network)
# --------------------------------------------------------------------------- #
def _stub_detect(monkeypatch, info: ConsoleInfo):
    async def fake(host, **kwargs):
        return info

    monkeypatch.setattr("netadmin.ingest.unifi.detect.detect_console", fake)


def test_cli_detect_text_output(monkeypatch, capsys):
    info = ConsoleInfo(
        kind=KIND_UDM_PRO,
        model="UniFi Dream Machine Pro",
        is_unifi_os=True,
        api_key_supported=True,
        recommended_auth=AUTH_API_KEY,
        network_version="9.0.114",
    )
    _stub_detect(monkeypatch, info)
    rc = cli.main(["detect", "--host", HOST])
    assert rc == 0
    out = capsys.readouterr().out
    assert "UniFi Dream Machine Pro" in out
    assert "Create the API key:" in out


def test_cli_detect_json_output(monkeypatch, capsys):
    info = ConsoleInfo(
        kind=KIND_UDM_PRO,
        model="UniFi Dream Machine Pro",
        is_unifi_os=True,
        api_key_supported=True,
        recommended_auth=AUTH_API_KEY,
        network_version="9.0.114",
    )
    _stub_detect(monkeypatch, info)
    rc = cli.main(["detect", "--host", HOST, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == KIND_UDM_PRO
    assert payload["api_key_supported"] is True
    assert payload["recommended_auth"] == AUTH_API_KEY


def test_cli_detect_unreachable_exit_code(monkeypatch, capsys):
    info = ConsoleInfo(
        kind=KIND_UNREACHABLE,
        is_unifi_os=False,
        api_key_supported=False,
        recommended_auth="none",
        reachable=False,
    )
    _stub_detect(monkeypatch, info)
    rc = cli.main(["detect", "--host", HOST])
    assert rc == 1


def test_cli_detect_requires_host():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["detect"])
