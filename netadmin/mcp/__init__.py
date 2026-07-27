"""Read-only MCP server: the history store as Claude's memory of the network.

``docs/MCP_SERVER.md`` is the authoritative design. In one paragraph: the other
UniFi MCP servers are Claude's *hands* on a live controller that keeps about a
day of stats; this one is Claude's *memory*. It opens the same SQLite store the
daemon writes, read-only, over stdio, and answers the questions a live query
cannot -- when did this start, has it happened before, what changed just before
it broke.

Layering, and the reason this package is separate rather than a router on the
existing API:

* :mod:`netadmin.mcp.paths` resolves the database path from flags and
  environment **without** constructing :class:`netadmin.config.Settings`, so
  ``data/secrets.env`` is never read and controller credentials never enter this
  process.
* :mod:`netadmin.mcp.format` holds the output-discipline primitives (row caps,
  series downsampling, timestamp rendering, evidence trimming, optional
  redaction) that :mod:`netadmin.mcp.tools` applies centrally.
* :mod:`netadmin.mcp.tools` is transport-agnostic: plain
  ``(Repository, params) -> dict`` functions, unit-testable with no SDK present.
* :mod:`netadmin.mcp.server` owns the stdio binding and
  :func:`netadmin.mcp.server.build_server`, the one place the 11 tools are
  registered on an SDK server object.
* The SDK is imported lazily, inside functions, by the only two modules that
  need it -- :mod:`netadmin.mcp.server` and the daemon's remote mount
  :mod:`netadmin.server.mcp_mount` -- so ``pip install unifioptimizer`` without
  the ``[mcp]`` extra still imports cleanly and the daemon still boots.

Nothing here imports :mod:`netadmin.fixes` or :mod:`netadmin.ingest`. That is
the third read-only guarantee, after SQLite's ``mode=ro`` and ``PRAGMA
query_only=ON``: the code that could mutate a controller is not reachable from
this process at all.
"""

from __future__ import annotations

__all__ = ["SERVER_NAME", "TOOL_PREFIX"]

# The name the client sees in its MCP server list, and the prefix every tool
# carries so a co-loaded live-controller server never collides with this one.
SERVER_NAME = "unifioptimizer"
TOOL_PREFIX = "netadmin_"
