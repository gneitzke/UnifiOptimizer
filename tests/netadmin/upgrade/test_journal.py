"""The self-upgrade journal (docs/ARCHITECTURE.md section 23): read/write/atomicity."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from netadmin.upgrade.journal import (
    IN_PROGRESS_PHASES,
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_ROLLED_BACK,
    PHASE_STARTING,
    PHASE_SWAPPING,
    STALE_AFTER_S,
    TERMINAL_PHASES,
    UpgradeJournal,
    journal_path_for,
    read_journal,
    write_journal,
)


def _journal(**overrides: object) -> UpgradeJournal:
    base = dict(
        phase=PHASE_STARTING,
        target_version="0.4.0",
        from_version="0.3.0",
        started_ts=1_000,
        updated_ts=1_000,
    )
    base.update(overrides)
    return UpgradeJournal(**base)  # type: ignore[arg-type]


def test_journal_path_for_lives_next_to_the_database(tmp_path: Path) -> None:
    db = tmp_path / "sub" / "netadmin.db"
    assert journal_path_for(db) == (tmp_path / "sub" / "upgrade" / "journal.json").resolve()


def test_read_journal_missing_file_is_none(tmp_path: Path) -> None:
    assert read_journal(tmp_path / "nope" / "journal.json") is None


def test_read_journal_corrupt_json_is_none(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_journal(path) is None


def test_read_journal_non_object_json_is_none(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_journal(path) is None


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "upgrade" / "journal.json"
    journal = _journal(
        daemon_pid=4242,
        daemon_argv=["/venv/bin/netadmin", "daemon"],
        daemon_cwd="/srv/netadmin",
        daemon_env={"NETADMIN_DATA_DIR": "/srv/netadmin/data"},
        respawned_pid=None,
        error=None,
    )

    write_journal(path, journal)
    back = read_journal(path)

    assert back == journal


def test_write_journal_creates_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "journal.json"
    write_journal(path, _journal())
    assert path.exists()


def test_write_journal_is_chmod_600(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    write_journal(path, _journal())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_journal_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    upgrade_dir = tmp_path / "upgrade"
    write_journal(upgrade_dir / "journal.json", _journal())
    leftovers = list(upgrade_dir.glob(".journal-*.tmp"))
    assert leftovers == []


def test_read_journal_ignores_unknown_keys_forward_compat(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    write_journal(path, _journal())
    # Simulate a future field a newer build might add.
    import json

    data = json.loads(path.read_text())
    data["some_future_field"] = "whatever"
    path.write_text(json.dumps(data), encoding="utf-8")

    back = read_journal(path)
    assert back is not None
    assert back.phase == PHASE_STARTING


def test_in_progress_property() -> None:
    assert _journal(phase=PHASE_STARTING).in_progress is True
    assert _journal(phase=PHASE_SWAPPING).in_progress is True
    assert _journal(phase=PHASE_DONE).in_progress is False
    assert _journal(phase=PHASE_FAILED).in_progress is False
    assert _journal(phase=PHASE_ROLLED_BACK).in_progress is False


def test_phase_sets_are_disjoint_and_exhaustive_over_known_phases() -> None:
    assert IN_PROGRESS_PHASES.isdisjoint(TERMINAL_PHASES)
    all_phases = IN_PROGRESS_PHASES | TERMINAL_PHASES
    assert len(all_phases) == len(IN_PROGRESS_PHASES) + len(TERMINAL_PHASES)


# --------------------------------------------------------------------------- #
# Abandonment: a killed runner must not disable upgrading forever.
# --------------------------------------------------------------------------- #
def test_pid_alive_reports_this_process() -> None:
    from netadmin.upgrade.journal import _pid_alive

    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(999_999) is False
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


def _inflight(**kw) -> UpgradeJournal:
    base = dict(
        phase=PHASE_SWAPPING,
        target_version="1.0.0",
        from_version="0.9.0",
        started_ts=0,
        updated_ts=0,
    )
    base.update(kw)
    return UpgradeJournal(**base)


def test_abandoned_when_the_runner_pid_is_gone() -> None:
    j = _inflight(runner_pid=999_999, updated_ts=1_000)
    assert j.is_abandoned(1_010) is True  # fresh timestamp, but no such process
    assert j.is_active(1_010) is False


def test_not_abandoned_while_the_runner_is_alive() -> None:
    j = _inflight(runner_pid=os.getpid(), updated_ts=1_000)
    assert j.is_abandoned(1_010) is False
    assert j.is_active(1_010) is True


def test_abandoned_by_staleness_when_no_pid_was_recorded() -> None:
    """Covers a crash before the runner stamped its pid, and a rebooted host."""
    j = _inflight(updated_ts=0)
    assert j.is_abandoned(STALE_AFTER_S + 1) is True
    assert j.is_abandoned(STALE_AFTER_S - 1) is False


def test_a_finished_upgrade_is_never_abandoned() -> None:
    for phase in ("done", "rolled_back", "failed"):
        j = _inflight(phase=phase, updated_ts=0)
        assert j.is_abandoned(10**9) is False
        assert j.is_active(10**9) is False
