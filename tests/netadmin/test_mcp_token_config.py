"""``Settings.mcp_token`` (``NETADMIN_MCP_TOKEN``) — Gitea #29.

Settings-only groundwork for the remote MCP mount (docs/MCP_SERVER.md): a
dedicated, independently-rotatable credential, read only from the environment /
``data/secrets.env`` and deliberately carrying NO fallback to
``NETADMIN_API_TOKEN`` in either direction (that token authorizes controller
mutations; this one must stay read-only-by-construction).
"""

from __future__ import annotations

import pytest

from netadmin.config import Settings


def test_mcp_token_defaults_to_none() -> None:
    assert Settings(_env_file=None).mcp_token is None


def test_mcp_token_reads_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETADMIN_MCP_TOKEN", "s3cr3t-mcp-token")
    assert Settings(_env_file=None).mcp_token == "s3cr3t-mcp-token"


def test_mcp_token_reads_from_explicit_kwarg() -> None:
    # The path a caller uses when overriding a loaded Settings object, mirroring
    # api_token's own test-facing seam.
    settings = Settings(_env_file=None, netadmin_mcp_token="kwarg-token")
    assert settings.mcp_token == "kwarg-token"


def test_mcp_token_whitespace_only_is_treated_as_unset() -> None:
    # A blank line in secrets.env must not silently arm an unusable token.
    settings = Settings(_env_file=None, netadmin_mcp_token="   ")
    assert settings.mcp_token is None


def test_mcp_token_is_stripped() -> None:
    settings = Settings(_env_file=None, netadmin_mcp_token="  padded-token  ")
    assert settings.mcp_token == "padded-token"


def test_mcp_token_has_no_fallback_to_api_token() -> None:
    """The API token must never authorize the (future) MCP surface, or vice versa."""
    settings = Settings(_env_file=None, netadmin_api_token="api-only-token")
    assert settings.api_token == "api-only-token"
    assert settings.mcp_token is None

    settings = Settings(_env_file=None, netadmin_mcp_token="mcp-only-token")
    assert settings.mcp_token == "mcp-only-token"
    assert settings.api_token is None
