"""Provider selection, availability, and per-provider behavior."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from netadmin.llm import provider as prov
from netadmin.llm.anthropic import AnthropicProvider
from netadmin.llm.copilot import CopilotProvider
from netadmin.llm.manual import ManualProvider
from netadmin.llm.provider import (
    ProviderRuntimeError,
    ProviderUnavailableError,
    available_providers,
    build_provider,
)

# --------------------------------------------------------------------------- #
# Selection / availability
# --------------------------------------------------------------------------- #


def test_available_providers_always_lists_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(prov.shutil, "which", lambda _cmd: None)
    rows = {r["name"]: r for r in available_providers()}
    assert set(rows) == {"manual", "copilot", "anthropic"}
    assert rows["manual"]["available"] is True
    assert rows["copilot"]["available"] is False
    assert rows["anthropic"]["available"] is False


def test_anthropic_available_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("NETADMIN_ANTHROPIC_MODEL", "claude-opus-4-8")
    rows = {r["name"]: r for r in available_providers()}
    assert rows["anthropic"]["available"] is True
    assert "claude-opus-4-8" in rows["anthropic"]["detail"]
    # the key value never appears in the availability detail
    assert "sk-test-not-real" not in rows["anthropic"]["detail"]


def test_copilot_available_when_cli_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(
        prov.shutil, "which", lambda cmd: "/usr/bin/copilot" if cmd == "copilot" else None
    )
    rows = {r["name"]: r for r in available_providers()}
    assert rows["copilot"]["available"] is True
    assert "copilot" in rows["copilot"]["detail"]


def test_build_unknown_provider_raises() -> None:
    with pytest.raises(ProviderUnavailableError):
        build_provider("gpt-9000")


def test_build_manual_provider() -> None:
    p = build_provider("manual", issue_id=7, ts=123)
    assert isinstance(p, ManualProvider)
    assert p.blocking is False


def test_build_copilot_provider_unavailable_fails_at_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Copilot must probe the CLI at construction (like anthropic's from_env probes
    # the key), so an absent CLI fails inside build_provider — BEFORE any pending
    # investigations row or 'investigated' event is written. A late failure (only in
    # investigate()) would orphan a pending row and mislabel the provider.
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(prov.shutil, "which", lambda _cmd: None)
    with pytest.raises(ProviderUnavailableError):
        build_provider("copilot")


def test_build_copilot_provider_ok_when_cli_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(
        prov.shutil, "which", lambda cmd: "/usr/bin/copilot" if cmd == "copilot" else None
    )
    p = build_provider("copilot")
    assert isinstance(p, CopilotProvider)
    assert p.blocking is True


# --------------------------------------------------------------------------- #
# Manual provider
# --------------------------------------------------------------------------- #


def test_manual_writes_dossier_and_returns_none(tmp_path: Path) -> None:
    p = ManualProvider(issue_id=42, ts=99, base_dir=tmp_path)
    result = p.investigate("# dossier body\n")
    assert result is None
    assert p.output_path == tmp_path / "issue-42-99.md"
    assert p.output_path.read_text(encoding="utf-8") == "# dossier body\n"


# --------------------------------------------------------------------------- #
# Anthropic provider (absent key, happy path, refusal, http error)
# --------------------------------------------------------------------------- #


def test_anthropic_from_env_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderUnavailableError):
        AnthropicProvider.from_env()
    # build_provider surfaces the same failure
    with pytest.raises(ProviderUnavailableError):
        build_provider("anthropic")


def test_anthropic_from_env_reads_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("NETADMIN_ANTHROPIC_MODEL", raising=False)
    assert AnthropicProvider.from_env().model == "claude-sonnet-5"
    monkeypatch.setenv("NETADMIN_ANTHROPIC_MODEL", "claude-opus-4-8")
    assert AnthropicProvider.from_env().model == "claude-opus-4-8"


@respx.mock
def test_anthropic_investigate_returns_text() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "## Answers\n### Root cause\nBad cable."},
                ],
            },
        )
    )
    provider = AnthropicProvider(api_key="sk-secret", model="claude-sonnet-5")
    answer = provider.investigate("dossier text")
    assert answer == "## Answers\n### Root cause\nBad cable."
    # the key was sent in the header, never in the (mocked) body
    sent = route.calls.last.request
    assert sent.headers["x-api-key"] == "sk-secret"
    assert sent.headers["anthropic-version"] == "2023-06-01"


@respx.mock
def test_anthropic_investigate_refusal_raises() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"stop_reason": "refusal", "content": []})
    )
    provider = AnthropicProvider(api_key="sk-secret")
    with pytest.raises(ProviderRuntimeError):
        provider.investigate("dossier")


@respx.mock
def test_anthropic_investigate_http_error_raises_without_leaking_key() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
    )
    provider = AnthropicProvider(api_key="sk-super-secret")
    with pytest.raises(ProviderRuntimeError) as excinfo:
        provider.investigate("dossier")
    assert "401" in str(excinfo.value)
    assert "sk-super-secret" not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Copilot provider (detection, stdin capture, errors)
# --------------------------------------------------------------------------- #


def test_copilot_unavailable_when_cli_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(prov.shutil, "which", lambda _cmd: None)
    with pytest.raises(ProviderUnavailableError):
        CopilotProvider().investigate("dossier")


def test_copilot_captures_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(
        prov.shutil, "which", lambda cmd: "/usr/bin/copilot" if cmd == "copilot" else None
    )

    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = "## Answers\n### Root cause\nFrom copilot."
        stderr = ""

    def _fake_run(argv, **kwargs):  # noqa: ANN001, ANN003
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _Completed()

    import netadmin.llm.copilot as copilot_mod

    monkeypatch.setattr(copilot_mod.subprocess, "run", _fake_run)
    answer = CopilotProvider().investigate("the dossier")
    assert answer == "## Answers\n### Root cause\nFrom copilot."
    assert captured["argv"][0] == "copilot"
    assert captured["input"] == "the dossier"  # dossier goes on stdin


def test_copilot_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETADMIN_COPILOT_CMD", raising=False)
    monkeypatch.setattr(
        prov.shutil, "which", lambda cmd: "/usr/bin/copilot" if cmd == "copilot" else None
    )

    class _Completed:
        returncode = 3
        stdout = ""
        stderr = "boom"

    import netadmin.llm.copilot as copilot_mod

    monkeypatch.setattr(copilot_mod.subprocess, "run", lambda *a, **k: _Completed())
    with pytest.raises(ProviderRuntimeError):
        CopilotProvider().investigate("dossier")


def test_copilot_command_override_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETADMIN_COPILOT_CMD", "mytool run")
    monkeypatch.setattr(
        prov.shutil, "which", lambda cmd: "/bin/mytool" if cmd == "mytool" else None
    )
    assert prov._copilot_command() == ["mytool", "run"]
