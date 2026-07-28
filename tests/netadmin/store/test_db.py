"""Tests for the connection factory, pragmas, migration runner, and txn guard."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from netadmin.store import db


def test_pragmas_applied(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert int(conn.execute("PRAGMA synchronous").fetchone()[0]) == 1  # NORMAL
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000
    conn.close()


def test_connect_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "sub" / "dir" / "netadmin.db"
    conn = db.connect(nested)
    assert nested.parent.is_dir()
    conn.close()


def test_migration_sets_user_version(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    assert db.schema_version(conn) == 0
    applied = db.apply_migrations(conn)
    assert applied == [1, 2, 3, 4, 5, 6, 7, 8]
    assert db.schema_version(conn) == 8
    conn.close()


def test_migration_creates_all_tables(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "entities",
        "state_changes",
        "series",
        "samples",
        "samples_hourly",
        "samples_daily",
        "events",
        "poll_runs",
        "issues",
        "issue_events",
        "sle_minutes",
        "changes",
        "baselines",
        "investigations",
        "incidents",
        "incident_members",
        "app_meta",
    }
    assert expected <= names
    conn.close()


def test_without_rowid_and_partial_index(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    # WITHOUT ROWID tables carry their PK as the table's rowid replacement.
    for table in ("samples", "samples_hourly", "samples_daily", "sle_minutes", "baselines"):
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "WITHOUT ROWID" in sql.upper()
    # Partial unique index only over non-resolved issues.
    idx_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_issues_open_fp'"
    ).fetchone()[0]
    assert "WHERE" in idx_sql.upper() and "RESOLVED" in idx_sql.upper()
    # The reopen lookup index (migration 0002) is present over (fingerprint, resolved_ts).
    resolved_idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_issues_fp_resolved'"
    ).fetchone()
    assert resolved_idx is not None
    assert "RESOLVED_TS" in resolved_idx[0].upper()
    # Migration 0003 raw-tier read indexes.
    poll_idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_poll_runs_job_ts'"
    ).fetchone()
    assert poll_idx is not None
    native_idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_events_native_id'"
    ).fetchone()
    assert native_idx is not None
    # Partial: only rows that carry a native_id are indexed.
    assert "WHERE" in native_idx[0].upper() and "NATIVE_ID" in native_idx[0].upper()
    conn.close()


def test_migration_idempotent(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    first = db.apply_migrations(conn)
    second = db.apply_migrations(conn)
    third = db.apply_migrations(conn)
    assert first == [1, 2, 3, 4, 5, 6, 7, 8]
    assert second == []  # nothing re-applied
    assert third == []
    assert db.schema_version(conn) == 8
    conn.close()


def _seed_legacy_rogue_issues(conn: sqlite3.Connection) -> None:
    """Pre-0005 state: per-BSSID wifi.rogue_ap issues, one incident rooted on one."""
    with db.begin_immediate(conn):
        for i, state in enumerate(("pending", "active", "resolving", "resolved")):
            conn.execute(
                "INSERT INTO issues (fingerprint, detector_key, severity, state, "
                "first_seen_ts, last_seen_ts, title, clear_streak) "
                f"VALUES ('fp-rogue-{i}','wifi.rogue_ap','p3','{state}',1,1,'Rogue',3)"
            )
        # A neighbouring detector's issue, which must survive untouched.
        conn.execute(
            "INSERT INTO issues (fingerprint, detector_key, severity, state, "
            "first_seen_ts, last_seen_ts, title) "
            "VALUES ('fp-chan','wifi.channel_plan','p3','active',1,1,'Channel plan')"
        )
        conn.execute(
            "INSERT INTO incidents (fingerprint, root_issue_id, severity, state, "
            "first_seen_ts, last_seen_ts, title) "
            "VALUES ('inc-rogue',2,'p3','open',1,1,'Rogue AP')"
        )


def test_migration_0005_retires_legacy_rogue_ap_issues(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    conn.execute("PRAGMA user_version=4")  # rewind to the pre-split schema
    _seed_legacy_rogue_issues(conn)

    # 0006 rides along on the same rewind; it retires a different taxonomy and
    # leaves the channel_plan survivor below untouched. 0007 (app_meta) rides
    # along too -- it is schema-only and touches none of the rows asserted here,
    # as does 0008 (sticky_client, a third taxonomy this fixture never seeds).
    assert db.apply_migrations(conn) == [5, 6, 7, 8]

    rows = conn.execute(
        "SELECT state, resolved_ts FROM issues WHERE detector_key = 'wifi.rogue_ap'"
    ).fetchall()
    assert len(rows) == 4
    assert {r[0] for r in rows} == {"resolved"}  # every legacy fingerprint is obsolete
    # The three open ones were stamped now; the already-resolved one was left alone.
    assert sum(1 for r in rows if r[1] is not None) == 3

    # Other detectors are untouched: this retires a taxonomy, not the issue table.
    survivor = conn.execute(
        "SELECT state FROM issues WHERE detector_key = 'wifi.channel_plan'"
    ).fetchone()
    assert survivor[0] == "active"

    # One honest audit row per retired issue, and none for the already-resolved one.
    events = conn.execute("SELECT kind, detail FROM issue_events").fetchall()
    assert len(events) == 3
    assert {e[0] for e in events} == {"resolved"}
    assert all("superseded" in e[1] for e in events)

    # The incident rooted on a retired issue is closed, not left advertising a
    # resolved root.
    incident = conn.execute("SELECT state FROM incidents").fetchone()
    assert incident[0] == "resolved"
    conn.close()


def _seed_per_radio_channel_plan(conn: sqlite3.Connection) -> None:
    """Pre-0006 state: plan-level channel findings reported once per radio.

    The shape a six-radio site actually accumulated: co-channel conflicts counted
    from both ends, one width row per 80 MHz radio, plus the two genuinely
    per-radio sub-cases that must survive.
    """
    rows = [
        ("co_channel_reuse", "active"),
        ("co_channel_reuse", "active"),
        ("co_channel_reuse", "pending"),
        ("co_channel_reuse", "resolving"),
        ("wide_channel_dense_5ghz", "active"),
        ("wide_channel_dense_5ghz", "active"),
        ("channel_off_grid", "active"),
        ("wide_channel_24ghz", "resolving"),
        ("co_channel_reuse", "resolved"),  # already closed: no second audit row
    ]
    with db.begin_immediate(conn):
        for i, (subtype, state) in enumerate(rows):
            evidence = json.dumps({"subtype": subtype, "band": "2.4", "channel": 6})
            conn.execute(
                "INSERT INTO issues (fingerprint, detector_key, severity, state, "
                "first_seen_ts, last_seen_ts, title, evidence, clear_streak) "
                "VALUES (?, 'wifi.channel_plan', 'p3', ?, 1, 1, 'Channel plan', ?, 3)",
                (f"fp-chan-{i}", state, evidence),
            )
        # A neighbouring detector's issue, which must survive untouched.
        conn.execute(
            "INSERT INTO issues (fingerprint, detector_key, severity, state, "
            "first_seen_ts, last_seen_ts, title) "
            "VALUES ('fp-density','wifi.neighbor_density','p3','active',1,1,'Crowded')"
        )
        conn.execute(
            "INSERT INTO incidents (fingerprint, root_issue_id, severity, state, "
            "first_seen_ts, last_seen_ts, title) "
            "VALUES ('inc-chan',1,'p3','open',1,1,'Co-channel')"
        )


def test_migration_0006_retires_plan_level_channel_plan_issues(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    conn.execute("PRAGMA user_version=5")  # rewind to the pre-aggregation schema
    _seed_per_radio_channel_plan(conn)

    # 0007 (app_meta) rides along too -- schema-only, touches none of the rows
    # asserted below -- and so does 0008, which retires a different detector.
    assert db.apply_migrations(conn) == [6, 7, 8]

    states = dict(
        conn.execute(
            "SELECT json_extract(evidence, '$.subtype') || '|' || state, COUNT(*) "
            "FROM issues WHERE detector_key = 'wifi.channel_plan' GROUP BY 1"
        ).fetchall()
    )
    # Every plan-level row is retired, whatever state it was in...
    assert states == {
        "co_channel_reuse|resolved": 5,
        "wide_channel_dense_5ghz|resolved": 2,
        "channel_off_grid|active": 1,
        "wide_channel_24ghz|resolving": 1,
    }
    # ...and the per-radio sub-cases keep their lifecycle, because their
    # fingerprints are still produced by the detector unchanged.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM issues WHERE detector_key = 'wifi.channel_plan' "
            "AND resolved_ts IS NOT NULL"
        ).fetchone()[0]
        == 6  # the six open plan-level rows; the pre-resolved one keeps its NULL
    )
    # Another detector's issue is untouched: this retires a taxonomy, not a table.
    survivor = conn.execute(
        "SELECT state FROM issues WHERE detector_key = 'wifi.neighbor_density'"
    ).fetchone()
    assert survivor[0] == "active"

    # One honest audit row per retired issue, none for the already-resolved one.
    events = conn.execute("SELECT kind, detail FROM issue_events").fetchall()
    assert len(events) == 6
    assert {e[0] for e in events} == {"resolved"}
    assert all("superseded" in e[1] and "migration 0006" in e[1] for e in events)

    # The incident rooted on a retired issue is closed.
    assert conn.execute("SELECT state FROM incidents").fetchone()[0] == "resolved"
    conn.close()


def _seed_per_ap_sticky_clients(conn: sqlite3.Connection) -> None:
    """Pre-0008 state: one client holding two sticky rows, one per AP it bounces to.

    Plus a second client with a single row, the theoretical dims={} legacy row
    (current AP unknown at detection), an already-resolved row that must not get
    a second audit event, and a neighbouring detector's issue that must survive.
    """
    rows = [
        ("fp-sticky-a1", "active", "aa:bb:cc:00:00:01"),
        ("fp-sticky-a2", "resolving", "aa:bb:cc:00:00:02"),  # same client, other AP
        ("fp-sticky-b1", "pending", "aa:bb:cc:00:00:01"),
        ("fp-sticky-c1", "active", None),  # legacy row: no AP dim in the hash
        ("fp-sticky-d1", "resolved", "aa:bb:cc:00:00:01"),
    ]
    with db.begin_immediate(conn):
        for fp, state, ap in rows:
            evidence = json.dumps({"current_ap": ap} if ap else {})
            conn.execute(
                "INSERT INTO issues (fingerprint, detector_key, severity, state, "
                "first_seen_ts, last_seen_ts, title, evidence, clear_streak) "
                "VALUES (?, 'wifi.sticky_client', 'p3', ?, 1, 1, 'Sticky', ?, 3)",
                (fp, state, evidence),
            )
        conn.execute(
            "INSERT INTO issues (fingerprint, detector_key, severity, state, "
            "first_seen_ts, last_seen_ts, title) "
            "VALUES ('fp-pingpong','wifi.pingpong_roamer','p3','active',1,1,'Ping-pong')"
        )
        conn.execute(
            "INSERT INTO incidents (fingerprint, root_issue_id, severity, state, "
            "first_seen_ts, last_seen_ts, title) "
            "VALUES ('inc-sticky',1,'p3','open',1,1,'Sticky client')"
        )


def test_migration_0008_retires_per_ap_sticky_client_issues(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    conn.execute("PRAGMA user_version=7")  # rewind to the pre-rescope schema
    _seed_per_ap_sticky_clients(conn)

    assert db.apply_migrations(conn) == [8]

    # Every sticky fingerprint is retired: the ap dim lives only inside the hash,
    # so SQL cannot tell the two-AP rows from the legacy dims={} one, and
    # retiring that one is harmless (its hash is what the new scheme produces).
    rows = conn.execute(
        "SELECT state, resolved_ts FROM issues WHERE detector_key = 'wifi.sticky_client'"
    ).fetchall()
    assert len(rows) == 5
    assert {r[0] for r in rows} == {"resolved"}
    assert sum(1 for r in rows if r[1] is not None) == 4  # the pre-resolved one kept its NULL

    # Another detector's issue is untouched: this retires an identity, not a table.
    survivor = conn.execute(
        "SELECT state FROM issues WHERE detector_key = 'wifi.pingpong_roamer'"
    ).fetchone()
    assert survivor[0] == "active"

    # One honest audit row per retired issue, none for the already-resolved one.
    events = conn.execute("SELECT kind, detail FROM issue_events").fetchall()
    assert len(events) == 4
    assert {e[0] for e in events} == {"resolved"}
    assert all("superseded" in e[1] and "migration 0008" in e[1] for e in events)

    # The incident rooted on a retired issue is closed, not left advertising a
    # resolved root.
    assert conn.execute("SELECT state FROM incidents").fetchone()[0] == "resolved"
    conn.close()


def test_partial_unique_index_enforced(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    # Two open issues with the same fingerprint must collide...
    with db.begin_immediate(conn):
        conn.execute(
            "INSERT INTO issues (fingerprint, detector_key, severity, state, "
            "first_seen_ts, last_seen_ts, title) VALUES ('fp','k','p2','active',1,1,'t')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db.begin_immediate(conn):
            conn.execute(
                "INSERT INTO issues (fingerprint, detector_key, severity, state, "
                "first_seen_ts, last_seen_ts, title) VALUES ('fp','k','p2','pending',1,1,'t')"
            )
    # ...but a resolved issue with the same fingerprint is allowed.
    with db.begin_immediate(conn):
        conn.execute(
            "INSERT INTO issues (fingerprint, detector_key, severity, state, "
            "first_seen_ts, last_seen_ts, title) VALUES ('fp','k','p2','resolved',1,1,'t')"
        )
    conn.close()


def test_begin_immediate_commits_and_rolls_back(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    with db.begin_immediate(conn):
        conn.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (1, 'x', 1)")
    assert conn.execute("SELECT COUNT(*) FROM poll_runs").fetchone()[0] == 1

    with pytest.raises(RuntimeError):
        with db.begin_immediate(conn):
            conn.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (2, 'y', 1)")
            raise RuntimeError("boom")
    # rolled back: still only the first row
    assert conn.execute("SELECT COUNT(*) FROM poll_runs").fetchone()[0] == 1
    conn.close()


def test_begin_immediate_blocks_writer_not_reader(tmp_db_path: Path) -> None:
    """Two connections: a held BEGIN IMMEDIATE blocks a second writer but not a reader."""
    writer_a = db.connect(tmp_db_path)
    db.apply_migrations(writer_a)
    writer_a.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (1, 'seed', 1)")

    # Short busy_timeout so the contended writer fails fast instead of waiting 5 s.
    other = db.connect(tmp_db_path, busy_timeout_ms=100)

    with db.begin_immediate(writer_a):
        writer_a.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (2, 'held', 1)")

        # Second writer cannot take the write lock -> OperationalError (locked).
        with pytest.raises(sqlite3.OperationalError):
            with db.begin_immediate(other):
                other.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (3, 'blocked', 1)")

        # A reader on the other connection still sees committed data (WAL).
        seen = other.execute("SELECT COUNT(*) FROM poll_runs").fetchone()[0]
        assert seen == 1  # only the committed 'seed' row; the held write is uncommitted

    # After the writer commits, the reader sees both committed rows.
    assert other.execute("SELECT COUNT(*) FROM poll_runs").fetchone()[0] == 2
    writer_a.close()
    other.close()


# ---------------------------------------------------------------------------
# Read-only connections (the MCP server's mode; docs/MCP_SERVER.md section 1)
# ---------------------------------------------------------------------------


def test_read_only_connection_reads_but_cannot_write(tmp_db_path: Path) -> None:
    writer = db.connect(tmp_db_path)
    db.apply_migrations(writer)
    with db.begin_immediate(writer):
        writer.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (1, 'seed', 1)")
    writer.close()

    reader = db.connect(tmp_db_path, read_only=True)
    assert reader.execute("SELECT COUNT(*) FROM poll_runs").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        reader.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (2, 'nope', 1)")
    reader.close()


def test_read_only_sets_query_only_and_skips_journal_mode(tmp_db_path: Path) -> None:
    """``query_only`` is the second lock; ``journal_mode`` is skipped because
    setting it would itself be a header write the connection cannot make."""
    db.apply_migrations(db.connect(tmp_db_path))

    reader = db.connect(tmp_db_path, read_only=True)
    assert int(reader.execute("PRAGMA query_only").fetchone()[0]) == 1
    assert int(reader.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    assert int(reader.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000
    # The mode persisted by the writer is still what the reader sees.
    assert reader.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    reader.close()


def test_read_only_works_while_a_writer_holds_the_wal(tmp_db_path: Path) -> None:
    """The daemon-is-running case: WAL readers never block on the sole writer."""
    writer = db.connect(tmp_db_path)
    db.apply_migrations(writer)
    with db.begin_immediate(writer):
        writer.execute("INSERT INTO poll_runs (ts, job, ok) VALUES (1, 'live', 1)")

    reader = db.connect(tmp_db_path, read_only=True)
    assert reader.execute("SELECT COUNT(*) FROM poll_runs").fetchone()[0] == 1
    reader.close()
    writer.close()


def test_read_only_refuses_a_missing_file_with_a_clear_error(tmp_path: Path) -> None:
    """Better than SQLite's "unable to open database file", and it must not
    create the parent directory the way a writable connect does."""
    missing = tmp_path / "nested" / "netadmin.db"
    with pytest.raises(FileNotFoundError):
        db.connect(missing, read_only=True)
    assert not missing.parent.exists()


def test_latest_migration_version_matches_the_newest_file() -> None:
    versions = [int(p.name.split("_")[0]) for p in db.MIGRATIONS_DIR.glob("*.sql")]
    assert db.latest_migration_version() == max(versions)


def test_a_migrated_database_reports_the_latest_version(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    assert db.schema_version(conn) == db.latest_migration_version()
    conn.close()


# ---------------------------------------------------------------------------
# Startup schema gate (ARCHITECTURE.md section 23): old code must refuse to
# start against a database a newer build already migrated forward.
# ---------------------------------------------------------------------------


def test_apply_migrations_refuses_a_schema_newer_than_this_build(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    # Simulate "a newer build already migrated this database" by bumping
    # user_version past what this build's migration files know about.
    conn.execute(f"PRAGMA user_version={db.latest_migration_version() + 1}")

    with pytest.raises(db.SchemaTooNewError) as excinfo:
        db.apply_migrations(conn)

    message = str(excinfo.value)
    # The message names the backup path and the concrete restore steps -- a
    # version mismatch must never be a mystery SQL error.
    assert "data/upgrade/pre-" in message
    assert "docs/BACKUP.md" in message
    assert str(db.latest_migration_version() + 1) in message
    conn.close()


def test_schema_gate_does_not_fire_when_versions_match_or_trail(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    # A fresh database (version 0) and an up-to-date one must never raise.
    db.apply_migrations(conn)  # 0 -> latest, no error
    db.apply_migrations(conn)  # already current, no error
    conn.close()


def test_schema_gate_leaves_user_version_untouched_on_refusal(tmp_db_path: Path) -> None:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn)
    poisoned = db.latest_migration_version() + 5
    conn.execute(f"PRAGMA user_version={poisoned}")

    with pytest.raises(db.SchemaTooNewError):
        db.apply_migrations(conn)

    # Refusing to start must not itself mutate the version it is refusing over.
    assert db.schema_version(conn) == poisoned
    conn.close()
