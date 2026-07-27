"""End-to-end MCP protocol tests over the SDK's in-memory streams.

Skipped when the optional ``[mcp]`` extra is absent, which is the whole point of
making it an extra: the core install must stay importable and testable without
the SDK. When it *is* present these run the real thing -- initialize handshake,
``tools/list``, and a ``tools/call`` of the flagship tool -- over paired memory
streams instead of a subprocess, so a broken schema or a handler signature drift
fails here rather than inside a user's Claude client.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from netadmin import __version__
from netadmin.mcp import SERVER_NAME, server, tools
from netadmin.store.repository import Repository

from .conftest import DEMO_NOW

pytest.importorskip("mcp", reason="the optional [mcp] extra is not installed")

pytestmark = pytest.mark.asyncio


def _build_server(repo: Repository) -> Any:
    """The same low-level Server the stdio entry point builds, minus stdio.

    :func:`netadmin.mcp.server.serve` owns the transport, so it cannot be reused
    here; this mirrors its registrations exactly, which is what makes the tests
    meaningful. If the two drift, ``test_registered_tools_match_the_registry``
    below is the tripwire.
    """
    import mcp.types as types
    from mcp.server.lowlevel import Server

    instance: Server = Server(SERVER_NAME, version=__version__, instructions=server._INSTRUCTIONS)

    @instance.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=spec.name, description=spec.description, inputSchema=spec.input_schema)
            for spec in tools.TOOLS.values()
        ]

    @instance.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        payload = tools.call_tool(repo, name, arguments, now=DEMO_NOW)
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]

    return instance


async def test_initialize_announces_the_server(demo_repo: Repository) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_build_server(demo_repo)) as client:
        result = await client.initialize()
        assert result.serverInfo.name == SERVER_NAME
        # Our version, not the SDK's: the client's server list shows this.
        assert result.serverInfo.version == __version__
        assert result.capabilities.tools is not None
        # The instructions are what tell Claude this is memory, not hands.
        assert "history" in (result.instructions or "").lower()


async def test_list_tools_returns_all_eleven_with_usable_schemas(
    demo_repo: Repository,
) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_build_server(demo_repo)) as client:
        await client.initialize()
        listed = await client.list_tools()

    assert {tool.name for tool in listed.tools} == set(tools.TOOLS)
    for tool in listed.tools:
        assert tool.description.startswith("History:")
        assert tool.inputSchema["type"] == "object"
        assert isinstance(tool.inputSchema.get("properties"), dict)


async def test_calling_the_flagship_returns_parseable_json(demo_repo: Repository) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    issue_id = int(demo_repo.list_issues(open_only=True)[0]["id"])
    async with create_connected_server_and_client_session(_build_server(demo_repo)) as client:
        await client.initialize()
        result = await client.call_tool("netadmin_when_did_this_start", {"issue": issue_id})

    assert result.isError is not True
    payload = json.loads(result.content[0].text)
    assert list(payload)[0] == "summary"
    assert payload["issue"]["issue_id"] == issue_id
    assert payload["onset"]["at"].endswith("Z")


async def test_a_tool_error_comes_back_as_a_readable_payload(demo_repo: Repository) -> None:
    """An expected failure must not surface as a protocol error.

    A model that receives an MCP error has nowhere to go; a model that receives
    ``{"summary": "No issue 999999 ... call netadmin_issues"}`` has a next step.
    """
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(_build_server(demo_repo)) as client:
        await client.initialize()
        result = await client.call_tool("netadmin_when_did_this_start", {"issue": 999_999})

    payload = json.loads(result.content[0].text)
    assert payload["error"] == "invalid_request"
    assert "netadmin_issues" in payload["summary"]


async def test_registered_tools_match_the_registry(demo_repo: Repository) -> None:
    """Guards against this file's server drifting from the shipped one.

    ``build_server`` is now the single registration point, and ``serve`` is one
    of its two callers (the daemon's ``/mcp`` mount is the other), so the check
    follows it there and pins the delegation.
    """
    import inspect

    source = inspect.getsource(server.build_server)
    assert "tools.TOOLS.values()" in source
    assert "tools.call_tool(repo, name, arguments)" in source
    assert "build_server(repo)" in inspect.getsource(server.serve)
