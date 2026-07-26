"""Database discovery for the MCP server, with no ``Settings`` in sight.

:class:`netadmin.config.Settings` resolves the same path, but building it reads
``data/secrets.env`` -- and the whole point of this process is that controller
credentials never enter it (``docs/MCP_SERVER.md`` section 5/6). So the four-step
precedence is reimplemented here, deliberately, as thirty lines of ``os.environ``
lookups that cannot load a secret even by accident.

Precedence, first match wins:

1. the ``--db`` flag,
2. ``NETADMIN_DB_PATH``,
3. ``NETADMIN_DATA_DIR/netadmin.db``,
4. ``./data/netadmin.db`` relative to the working directory.

Steps 2-4 mirror :func:`netadmin.config._runtime_data_dir` exactly, so a daemon
and an MCP server started from the same environment always land on the same file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

__all__ = ["DB_BASENAME", "resolve_db_path", "describe_db_source"]

DB_BASENAME = "netadmin.db"


def resolve_db_path(explicit: Optional[str] = None) -> Path:
    """The store this process should read, by the documented precedence.

    Returns a path that may not exist; existence is the caller's gate (an
    absent file is a first-run condition with its own guidance message, not a
    resolution failure). ``~`` is expanded at every step.
    """
    if explicit:
        return Path(explicit).expanduser()
    env_db = os.environ.get("NETADMIN_DB_PATH")
    if env_db:
        return Path(env_db).expanduser()
    data_dir = os.environ.get("NETADMIN_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / DB_BASENAME
    return Path.cwd() / "data" / DB_BASENAME


def describe_db_source(explicit: Optional[str] = None) -> str:
    """Which precedence step produced the path, for the stderr startup line.

    Startup diagnostics are the only place this matters, and they matter a lot:
    "no data yet" from the wrong file is the single most confusing failure mode
    of a stdio server the user never sees the console of.
    """
    if explicit:
        return "--db flag"
    if os.environ.get("NETADMIN_DB_PATH"):
        return "NETADMIN_DB_PATH"
    if os.environ.get("NETADMIN_DATA_DIR"):
        return "NETADMIN_DATA_DIR"
    return "./data default"
