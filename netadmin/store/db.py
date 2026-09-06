"""SQLite connection factory, pragmas, migration runner, and the write-txn guard.

This module owns everything about *how* the database file is opened and kept
honest; :mod:`netadmin.store.repository` owns *what* SQL runs against it. The
non-negotiable pragmas and the ``BEGIN IMMEDIATE`` discipline come straight from
``docs/ARCHITECTURE.md`` section 4:

- ``journal_mode=WAL`` -- readers never block the single writer.
- ``synchronous=NORMAL`` -- the WAL-safe durability/throughput trade.
- ``busy_timeout=5000`` -- wait up to 5 s for a lock instead of failing instantly.
- ``foreign_keys=ON`` -- enforced per connection.

Connections run with ``isolation_level=None`` (autocommit): Python's implicit
transaction management is disabled so we control every transaction by hand.
Writers open with :func:`begin_immediate`, which takes the write lock up front.
A read-then-upgrade transaction (plain ``BEGIN`` then a write) fails instantly
regardless of ``busy_timeout`` -- that is the known trap this module exists to
avoid.

:func:`connect` also serves the opposite need: ``read_only=True`` opens the same
file through the ``file:...?mode=ro`` URI with ``PRAGMA query_only=ON``, which is
how the MCP server (``docs/MCP_SERVER.md`` section 1) reads the store while the
daemon is the sole writer. WAL is what makes that safe; the read-only connection
never sets ``journal_mode`` because doing so is itself a write to the header.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

__all__ = [
    "MIGRATIONS_DIR",
    "SchemaTooNewError",
    "connect",
    "begin_immediate",
    "apply_migrations",
    "schema_version",
    "latest_migration_version",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Applied verbatim on every new connection, in order.
_PRAGMAS: tuple[tuple[str, object], ...] = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
)

_MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")


def connect(
    db_path: Union[str, Path],
    *,
    busy_timeout_ms: int = 5000,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Open a connection with the non-negotiable pragmas applied.

    ``busy_timeout_ms`` is exposed (default 5000, per the architecture doc) so
    tests can dial it down to make lock-contention assertions fail fast instead
    of blocking for five seconds.

    ``read_only=True`` opens the *same* file through the ``file:...?mode=ro`` URI
    and adds ``PRAGMA query_only=ON``, so a write is rejected twice over: once by
    SQLite's VFS (the file is opened O_RDONLY) and once by the connection itself.
    The ``journal_mode`` pragma is deliberately skipped -- setting it writes to
    the database header, which a read-only connection cannot do and does not need
    to: the mode is already persisted in the file by the writer. The parent
    directory is never created either, and a missing file raises
    :class:`FileNotFoundError` up front rather than SQLite's opaque "unable to
    open database file".
    """
    path = Path(db_path)
    if read_only:
        if not path.exists():
            raise FileNotFoundError(f"no database at {path}")
        # isolation_level=None -> autocommit; we manage transactions explicitly.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        for name, value in _PRAGMAS:
            if name == "journal_mode":
                continue
            conn.execute(f"PRAGMA {name}={value}")
        conn.execute("PRAGMA query_only=ON")
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        return conn

    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    for name, value in _PRAGMAS:
        conn.execute(f"PRAGMA {name}={value}")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


@contextmanager
def begin_immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a write transaction, taking the write lock up front.

    ``BEGIN IMMEDIATE`` acquires SQLite's RESERVED lock at transaction start, so
    two writers contend immediately (the loser waits out ``busy_timeout`` then
    raises ``OperationalError``) while WAL readers proceed unblocked. Commits on
    clean exit, rolls back on any exception. If the ``BEGIN`` itself fails
    (another writer holds the lock), the exception propagates and no rollback is
    attempted -- there is no open transaction to unwind.

    Crucially, a *failed COMMIT* is handled too. ``COMMIT`` can raise -- a WAL or
    disk fault, or an authorizer denial -- and SQLite then leaves the transaction
    STILL OPEN. If we simply propagated the error, that abandoned transaction
    would linger on the connection, and the very next writer (``Repository._write``
    / ``transaction`` test ``in_transaction`` to decide top-level vs nesting)
    would mistake it for a legitimate outer transaction, silently JOIN it, and a
    later rollback could then erase the work this transaction believed it had
    committed. So a COMMIT failure is caught and the still-open transaction is
    rolled back before the error propagates: the context manager guarantees that
    once it exits -- success OR failure -- there is no dangling open transaction a
    subsequent writer could join. Only a genuinely intended nesting (an outer
    ``begin_immediate`` still on the stack) leaves ``in_transaction`` true.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        try:
            conn.execute("COMMIT")
        except BaseException:
            # A failed COMMIT leaves the transaction OPEN. Return the connection
            # to a clean, known state so no later ``_write`` silently joins this
            # abandoned transaction (and later rolls back committed rows). The
            # rollback is best-effort and never masks the original COMMIT error.
            try:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
            except BaseException:
                pass
            raise


def schema_version(conn: sqlite3.Connection) -> int:
    """Current schema version from ``PRAGMA user_version`` (0 = fresh db)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def latest_migration_version(migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """Version the newest shipped migration would set (0 when none exist).

    The schema version a fully-migrated database *should* report. Read-only
    consumers that never migrate -- the MCP server (``docs/MCP_SERVER.md``
    section 1) -- compare :func:`schema_version` against this to tell "your
    database predates this build, run ``netadmin`` once" from "your database is
    newer than this build, upgrade the package".
    """
    found = _discover_migrations(migrations_dir)
    return found[-1][0] if found else 0


def _discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    """Return ``(version, path)`` for every ``NNNN_*.sql`` file, sorted."""
    found: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found


def _iter_statements(script: str) -> Iterator[str]:
    """Yield individual executable statements from a migration script.

    Line comments (``--`` to end of line) are stripped first -- some carry a
    semicolon inside the prose -- then the remainder is split on ``;``. The
    schema files hold no semicolons inside statements, no ``--`` inside string
    literals, and no triggers, so this is correct and lets each statement run in
    one explicit transaction (``executescript`` cannot -- it force-commits first).
    """
    lines: list[str] = []
    for line in script.splitlines():
        comment_at = line.find("--")
        lines.append(line if comment_at == -1 else line[:comment_at])
    for chunk in "\n".join(lines).split(";"):
        if chunk.strip():
            yield chunk.strip()


class SchemaTooNewError(RuntimeError):
    """The database's schema is newer than this build's migrations understand.

    This is the forward-only migration hazard (docs/ARCHITECTURE.md section
    23): old code must never run against a database a *newer* build already
    migrated. In normal operation this cannot happen -- schema versions only
    move forward, in lockstep with the code that applies them. It surfaces
    when a rollback restores an old venv (an interrupted or failed self-
    upgrade) without also restoring the pre-upgrade database backup, or when
    someone manually downgrades the package against a database a newer install
    already touched. Either way, guessing is worse than refusing: running old
    migration logic (or none at all) against unfamiliar tables/columns risks
    silently corrupting data this build does not know how to read.
    """


def apply_migrations(
    conn: sqlite3.Connection,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> list[int]:
    """Apply every migration newer than ``PRAGMA user_version``; return applied versions.

    Idempotent: already-applied versions are skipped, so calling this repeatedly
    (e.g. on every daemon start) is a no-op once the schema is current. Each
    migration runs in its own ``BEGIN IMMEDIATE`` transaction together with the
    ``user_version`` bump, so a failure leaves the version untouched.

    This is also the startup schema gate: when the database's current version
    is *newer* than the newest migration this build ships, :class:`SchemaTooNewError`
    is raised before anything else runs, rather than silently attempting to
    operate on an unfamiliar schema.
    """
    current = schema_version(conn)
    latest = latest_migration_version(migrations_dir)
    if current > latest:
        raise SchemaTooNewError(
            f"database schema is at version {current}, newer than the {latest} this "
            "build understands. This build is OLDER than the one that last wrote this "
            "database, so it refuses to start rather than risk corrupting data it does "
            "not know how to read.\n"
            "\n"
            "This usually means a self-upgrade's rollback restored the old daemon code "
            "but not the pre-upgrade database backup. To recover:\n"
            "  1. Stop this process.\n"
            "  2. Restore the newest pre-upgrade backup, normally "
            "data/upgrade/pre-<version>-<timestamp>.db (move the current "
            "data/netadmin.db aside first; see docs/BACKUP.md for the manual "
            "backup/restore procedure), then restart.\n"
            "  3. Or upgrade this install to a build whose latest migration is "
            f">= {current}, then restart."
        )
    applied: list[int] = []
    for version, path in _discover_migrations(migrations_dir):
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        with begin_immediate(conn):
            for statement in _iter_statements(sql):
                conn.execute(statement)
            # PRAGMA cannot be parameterized; version is a validated int.
            conn.execute(f"PRAGMA user_version={version}")
        applied.append(version)
    return applied
