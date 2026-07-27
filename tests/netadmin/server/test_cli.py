"""CLI tests: parser wiring, the status/token commands, and daemon uvicorn config."""

from __future__ import annotations

import httpx
import pytest

from netadmin import cli
from netadmin.config import Settings


def test_parser_has_daemon_and_status() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["daemon"]).command == "daemon"
    assert parser.parse_args(["daemon", "--port", "9000"]).port == 9000
    status = parser.parse_args(["status", "--json"])
    assert status.command == "status"
    assert status.json is True


def test_parser_has_token() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["token"]).command == "token"


def test_token_prints_the_configured_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(_env_file=None, netadmin_api_token="s3cr3t-token")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    args = cli.build_parser().parse_args(["token"])
    assert cli._cmd_token(args) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "s3cr3t-token"


def test_token_errors_when_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    args = cli.build_parser().parse_args(["token"])
    assert cli._cmd_token(args) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "NETADMIN_API_TOKEN" in out.err


def test_parser_has_mcp_token() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["mcp-token"])
    assert args.command == "mcp-token"
    assert args.regenerate is False
    assert parser.parse_args(["mcp-token", "--regenerate"]).regenerate is True


def test_mcp_token_prints_the_configured_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(_env_file=None, netadmin_mcp_token="s3cr3t-mcp-token")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    args = cli.build_parser().parse_args(["mcp-token"])
    assert cli._cmd_mcp_token(args) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "s3cr3t-mcp-token"


def test_mcp_token_errors_when_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    args = cli.build_parser().parse_args(["mcp-token"])
    assert cli._cmd_mcp_token(args) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "NETADMIN_MCP_TOKEN" in out.err


def test_mcp_token_regenerate_mints_and_persists(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    # write_secrets itself is exhaustively tested in test_setup_secrets.py; here we
    # only pin the CLI's own contract: it calls write_secrets with the right key and
    # prints the freshly-minted token. Faked so this never touches the real
    # data/secrets.env.
    calls: list[dict[str, str]] = []

    def fake_write_secrets(updates, **kwargs):
        calls.append(dict(updates))
        target = tmp_path / "secrets.env"
        target.write_text(f"NETADMIN_MCP_TOKEN={updates['NETADMIN_MCP_TOKEN']}\n")
        return target

    monkeypatch.setattr(cli, "write_secrets", fake_write_secrets)
    args = cli.build_parser().parse_args(["mcp-token", "--regenerate"])
    assert cli._cmd_mcp_token(args) == 0
    new_token = capsys.readouterr().out.strip()
    assert new_token
    assert calls == [{"NETADMIN_MCP_TOKEN": new_token}]


def test_status_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float = 5.0) -> httpx.Response:
        assert url.endswith("/api/health")
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "uptime_s": 42,
                "entities": {"total": 3},
                "backfill": "done",
                "jobs": [
                    {
                        "job": "device",
                        "status": "ok",
                        "last_success_age_s": 5,
                        "consecutive_failures": 0,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    args = cli.build_parser().parse_args(["status"])
    assert cli._cmd_status(args) == 0


def test_status_degraded_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float = 5.0) -> httpx.Response:
        return httpx.Response(200, json={"status": "degraded", "jobs": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    args = cli.build_parser().parse_args(["status"])
    assert cli._cmd_status(args) == 2


def test_status_unreachable_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float = 5.0) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    args = cli.build_parser().parse_args(["status", "--port", "9999"])
    assert cli._cmd_status(args) == 1


def test_daemon_builds_single_worker_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    captured: dict[str, object] = {}

    class FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    args = cli.build_parser().parse_args(["daemon", "--host", "127.0.0.1", "--port", "8899"])
    assert cli._cmd_daemon(args) == 0
    config = captured["config"]
    assert captured["ran"] is True
    assert config.port == 8899
    assert config.host == "127.0.0.1"
    # single worker: multi-worker schedulers double-fire (section 2)
    assert config.workers == 1
