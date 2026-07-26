"""The stdio binding: ``netadmin-mcp``.

The only module that touches the MCP SDK, and it imports it *inside*
:func:`main` rather than at module scope. That is deliberate: the SDK is an
optional extra, `netadmin.mcp.tools` must stay importable (and testable) without
it, and a user who runs ``netadmin-mcp`` without the extra deserves the one-line
``pip install "unifioptimizer[mcp]"`` instruction rather than a traceback.

Everything else here is startup hygiene for a process the user never sees the
console of. A stdio MCP server's stdout *is* the protocol channel, so every
diagnostic goes to stderr, and the two conditions that make the server useless --
no database at the resolved path, and a schema version that does not match this
build -- are reported both on stderr at startup **and** from every tool call, so
the guidance reaches whoever is actually looking.

Read-only is not an intention here, it is three separate mechanisms: the SQLite
file is opened ``mode=ro``, the connection carries ``PRAGMA query_only=ON``, and
this process never imports the fix or ingest layers, so there is no code path to
a controller even if something else went wrong (``docs/MCP_SERVER.md`` section 6).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from netadmin import __version__
from netadmin.mcp import SERVER_NAME, paths, tools
from netadmin.store.repository import Repository

__all__ = ["build_parser", "main", "serve"]

_SDK_MISSING = (
    'The MCP SDK is not installed. Run: pip install "unifioptimizer[mcp]"\n'
    "(the core install deliberately does not pull it in)."
)

# Shown to the client on connect. Claude reads this before it reads any tool, so
# it is where the "memory, not hands" distinction has to land.
_INSTRUCTIONS = (
    "UnifiOptimizer keeps the UniFi history your controller throws away: tracked "
    "issues with lifecycle, correlated incidents, SLE client-minutes, config and "
    "state changes, events, and metric baselines going back months.\n\n"
    "Use these tools for anything historical -- when something started, whether it "
    "has happened before, what changed before it broke, whether it is getting "
    "worse. They read a local SQLite file, so they still answer when the "
    "controller has forgotten and when the UnifiOptimizer daemon is not running.\n\n"
    "They are read-only and there are deliberately no live-state tools here. For "
    "the current state of the network, use a live controller tool if you have one."
)


def _eprint(message: str) -> None:
    """Diagnostics go to stderr; stdout belongs to the protocol."""
    print(message, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netadmin-mcp",
        description=(
            "Read-only MCP server over the UnifiOptimizer history store "
            "(stdio transport, for Claude Desktop / Claude Code)."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to netadmin.db. Falls back to NETADMIN_DB_PATH, then "
            "NETADMIN_DATA_DIR/netadmin.db, then ./data/netadmin.db."
        ),
    )
    return parser


def open_store(db_path: Path) -> Repository:
    """Open the store read-only. Never migrates, never creates.

    ``Repository.open(read_only=True)`` forces ``migrate=False``: a read-only
    connection could not apply a migration anyway, and an MCP server silently
    rewriting the daemon's schema would be exactly the kind of surprise this
    design exists to rule out.
    """
    return Repository.open(db_path, read_only=True)


def serve(repo: Repository) -> int:
    """Run the stdio server against an already-open store until the client exits."""
    import anyio
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    # Our version, not the SDK's: the client shows serverInfo.version in its
    # server list, and "1.28.1" there would name the wrong piece of software.
    server: Server = Server(SERVER_NAME, version=__version__, instructions=_INSTRUCTIONS)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_schema,
            )
            for spec in tools.TOOLS.values()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        # tools.call_tool never raises: an expected failure comes back as a
        # payload whose summary tells the model what to do next, which is worth
        # far more to it than an MCP error code.
        payload = tools.call_tool(repo, name, arguments)
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_run)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Console-script entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    # Checked before anything else: without the SDK there is no server to run, and
    # a missing-database message would send the user chasing the wrong problem.
    try:
        import mcp  # noqa: F401  - presence check only; serve() does the real imports
    except ImportError:
        _eprint(_SDK_MISSING)
        return 1

    db_path = paths.resolve_db_path(args.db)
    if not db_path.exists():
        _eprint(
            f"No history store at {db_path} (resolved from {paths.describe_db_source(args.db)}).\n"
            "Run `netadmin` once to create and populate it, or point this server at an "
            "existing database with --db."
        )
        return 1

    try:
        repo = open_store(db_path)
    except Exception as exc:  # pragma: no cover - unreadable/corrupt file
        _eprint(f"Could not open {db_path} read-only: {exc}")
        return 1

    gate = tools.schema_gate(repo)
    if gate is not None:
        # Not fatal: serve anyway so the same guidance reaches the model, which
        # is the only party actually reading this server's output.
        _eprint(gate)

    _eprint(
        f"unifioptimizer MCP server: {len(tools.TOOLS)} read-only tools over {db_path} "
        f"(source: {paths.describe_db_source(args.db)})."
        + (" Redaction is ON." if tools.redaction_enabled() else "")
    )

    try:
        return serve(repo)
    finally:
        repo.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
