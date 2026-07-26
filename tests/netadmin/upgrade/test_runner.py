"""The pip self-upgrade runner (docs/ARCHITECTURE.md section 23).

Every OS-facing operation goes through :class:`RunnerDeps`, so these tests never
launch a real subprocess, create a real venv, or make a real network call -- the
one piece of REAL I/O exercised throughout is SQLite's own online-backup API
against tiny temp databases (fast, and the exact mechanism worth verifying for
real). ``deps.config_yaml`` / ``deps.secrets_env`` / ``deps.live_venv`` are always
pointed at tmp-path fixtures, never the real repo-local ``data/`` -- this suite
must never read this developer's actual ``data/secrets.env``.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from netadmin.config import Settings
from netadmin.store.repository import Repository
from netadmin.upgrade import runner as runner_mod
from netadmin.upgrade.detect import InstallInfo
from netadmin.upgrade.journal import (
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_ROLLED_BACK,
    PHASE_STARTING,
    PHASE_SWAPPING,
    UpgradeJournal,
    journal_path_for,
    read_journal,
    write_journal,
)
from netadmin.upgrade.runner import (
    RunnerDeps,
    RunnerError,
    _backup,
    _online_backup,
    _prune_old_backups,
    _resolve_paths,
    _restore_database,
    _restore_venv,
    _snapshot_venv,
    run_upgrade,
)

TARGET = "0.4.0"
FROM = "0.3.0"


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class FakeClock:
    """A fake wall clock: ``sleep`` advances it, so timeout loops resolve in a
    handful of Python iterations instead of real wall-clock seconds."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakePopen:
    def __init__(self, pid: int = 42_424) -> None:
        self.pid = pid
        self.returncode: Optional[int] = None
        self.terminated = False
        self.killed = False

    def poll(self) -> Optional[int]:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeHealthResponse:
    def __init__(self, status_code: int = 200, ready: bool = True, version: str = TARGET) -> None:
        self.status_code = status_code
        self._ready = ready
        self._version = version

    def json(self) -> dict[str, Any]:
        return {"ready": self._ready, "version": self._version}


def _always_run_ok(*args: Any, **kwargs: Any) -> FakeCompletedProcess:
    return FakeCompletedProcess(0)


def _fail_on_upgrade_flag(*args: Any, **kwargs: Any) -> FakeCompletedProcess:
    """Succeed for every pip/venv call except the live-venv swap (``--upgrade``)."""
    cmd = args[0] if args else kwargs.get("args", [])
    if "--upgrade" in cmd:
        return FakeCompletedProcess(1, stderr="simulated swap failure")
    return FakeCompletedProcess(0)


def _always_popen(*args: Any, **kwargs: Any) -> FakePopen:
    return FakePopen()


def _never_port_in_use(host: str, port: int) -> bool:
    return False


def _noop_kill(pid: int, sig: int) -> None:
    return None


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings(tmp_db_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        db_path=tmp_db_path,
        site_id="default",
        server_host="127.0.0.1",
        server_port=18765,
    )


@pytest.fixture
def real_db(settings: Settings) -> Path:
    """A real, migrated SQLite file at ``settings.db_path`` (backups need a real file)."""
    store = Repository.open(settings.db_path, site_id=settings.site_id)
    store.close()
    return Path(settings.db_path)


@pytest.fixture
def fake_venv(tmp_path: Path) -> Path:
    """A tiny stand-in for the live venv -- a couple of small files, never the
    real interpreter running this test suite."""
    venv = tmp_path / "fake-live-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
    (venv / "marker.txt").write_text("original venv content\n", encoding="utf-8")
    return venv


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def base_deps(tmp_path: Path, fake_venv: Path, clock: FakeClock) -> RunnerDeps:
    return RunnerDeps(
        run=_always_run_ok,
        popen=_always_popen,
        sleep=clock.sleep,
        now=clock.now,
        http_get=lambda url, timeout: FakeHealthResponse(version=TARGET),
        kill=_noop_kill,
        port_in_use=_never_port_in_use,
        live_venv=fake_venv,
        config_yaml=tmp_path / "no-such-config.yaml",
        secrets_env=tmp_path / "no-such-secrets.env",
    )


def _prime_journal(
    settings: Settings, *, phase: str = PHASE_STARTING, target: str = TARGET
) -> Path:
    path = journal_path_for(settings.db_path)
    journal = UpgradeJournal(
        phase=phase,
        target_version=target,
        from_version=FROM,
        started_ts=1_700_000_000,
        updated_ts=1_700_000_000,
        daemon_pid=555,
        daemon_argv=["/fake/bin/netadmin", "daemon"],
        daemon_cwd="/fake/cwd",
        daemon_env={"NETADMIN_DATA_DIR": "/fake/cwd/data"},
    )
    write_journal(path, journal)
    return path


@pytest.fixture(autouse=True)
def _self_upgrade_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to a supported pip-venv install; individual tests override."""
    monkeypatch.setattr(
        runner_mod,
        "detect_install_method",
        lambda: InstallInfo(method="pip", variant=None, self_upgrade_supported=True),
    )


# --------------------------------------------------------------------------- #
# guard clauses: no journal / mismatch / not idle
# --------------------------------------------------------------------------- #


def test_refuses_without_a_primed_journal(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    with pytest.raises(RunnerError, match="POST /api/system/update/apply"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)


def test_refuses_target_version_mismatch(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    _prime_journal(settings, target="9.9.9")
    with pytest.raises(RunnerError, match="does not match"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)


def test_refuses_when_journal_not_idle(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    _prime_journal(settings, phase=PHASE_SWAPPING)
    with pytest.raises(RunnerError, match="not idle"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)


# --------------------------------------------------------------------------- #
# pre-flight
# --------------------------------------------------------------------------- #


def test_preflight_refuses_unsupported_install_method(
    settings: Settings, base_deps: RunnerDeps, real_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner_mod,
        "detect_install_method",
        lambda: InstallInfo(method="container", variant=None, self_upgrade_supported=False),
    )
    _prime_journal(settings)

    with pytest.raises(RunnerError, match="not supported"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)

    journal = read_journal(journal_path_for(settings.db_path))
    assert journal is not None
    assert journal.phase == PHASE_FAILED
    assert "not supported" in journal.error


def test_preflight_refuses_insufficient_disk(
    settings: Settings, base_deps: RunnerDeps, real_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_mod.shutil, "disk_usage", lambda path: type("U", (), {"free": 1})())
    _prime_journal(settings)

    with pytest.raises(RunnerError, match="insufficient disk"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)

    journal = read_journal(journal_path_for(settings.db_path))
    assert journal is not None and journal.phase == PHASE_FAILED


def test_failure_before_swap_never_touches_venv_or_db(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    """pip download fails: nothing live was ever touched, so the journal is
    'failed', not 'rolled_back', and no rollback venv snapshot exists."""
    base_deps.run = lambda *a, **kw: FakeCompletedProcess(1, stderr="network unreachable")
    _prime_journal(settings)

    with pytest.raises(RunnerError):
        run_upgrade(TARGET, settings=settings, deps=base_deps)

    journal = read_journal(journal_path_for(settings.db_path))
    assert journal is not None
    assert journal.phase == PHASE_FAILED
    assert "pip download failed" in journal.error
    paths = _resolve_paths(settings, base_deps)
    assert not paths.rollback_venv.exists()
    # The live venv (fake_venv) is completely untouched.
    assert (base_deps.live_venv / "marker.txt").read_text() == "original venv content\n"


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


def test_happy_path_completes_and_cleans_up(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    _prime_journal(settings)

    journal = run_upgrade(TARGET, settings=settings, deps=base_deps)

    assert journal.phase == PHASE_DONE
    assert journal.error is None
    assert journal.respawned_pid is not None  # nothing was "listening", so we self-respawned

    on_disk = read_journal(journal_path_for(settings.db_path))
    assert on_disk is not None and on_disk.phase == PHASE_DONE

    paths = _resolve_paths(settings, base_deps)
    # Success cleans up the staged venv + the rollback snapshot...
    assert not paths.rollback_venv.exists()
    assert not (paths.upgrade_dir / f"venv-{TARGET}").exists()
    # ...but KEEPS the pre-upgrade database backup.
    backups = list(paths.upgrade_dir.glob(f"pre-{FROM}-*.db"))
    assert len(backups) == 1
    # The live venv was never actually swapped out from under it (deps.run is
    # faked), so its original marker file survives untouched.
    assert (base_deps.live_venv / "marker.txt").read_text() == "original venv content\n"


def test_happy_path_uses_pip_download_stage_and_swap(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    calls: list[list[str]] = []

    def _recording_run(*args: Any, **kwargs: Any) -> FakeCompletedProcess:
        calls.append(list(args[0]))
        return FakeCompletedProcess(0)

    base_deps.run = _recording_run
    _prime_journal(settings)

    run_upgrade(TARGET, settings=settings, deps=base_deps)

    joined = [" ".join(c) for c in calls]
    assert any("pip download" in c and TARGET in c for c in joined)
    assert any("-m venv" in c for c in joined)
    assert any("--no-index" in c and "--upgrade" not in c for c in joined)  # staged install
    assert any("--upgrade" in c and TARGET in c for c in joined)  # live swap


# --------------------------------------------------------------------------- #
# swap failure -> auto-rollback
# --------------------------------------------------------------------------- #


def test_swap_failure_triggers_full_rollback(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    server_port = settings.server_port

    def _http_get(url: str, timeout: float) -> FakeHealthResponse:
        # The "real" daemon (fixed server_port) still/again reports the OLD
        # version; the staged copy (a random smoke-test port) reports TARGET.
        if f":{server_port}/" in url:
            return FakeHealthResponse(version=FROM)
        return FakeHealthResponse(version=TARGET)

    base_deps.run = _fail_on_upgrade_flag
    base_deps.http_get = _http_get
    _prime_journal(settings)

    with pytest.raises(RunnerError, match="live venv swap failed"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)

    journal = read_journal(journal_path_for(settings.db_path))
    assert journal is not None
    assert journal.phase == PHASE_ROLLED_BACK
    assert "live venv swap failed" in journal.error

    # The venv was restored: the rollback snapshot is gone, the live venv is back
    # (byte-for-byte what it was, since nothing besides the fake pip call ran).
    paths = _resolve_paths(settings, base_deps)
    assert not paths.rollback_venv.exists()
    assert (base_deps.live_venv / "marker.txt").read_text() == "original venv content\n"

    # The database was restored from the pre-upgrade backup; the "corrupted"
    # (never actually touched, in this fake) live db was moved aside.
    assert Path(str(settings.db_path) + ".upgrade-failed").exists()
    assert Path(settings.db_path).exists()


def test_rollback_itself_failing_is_reported_as_failed_not_rolled_back(
    settings: Settings, base_deps: RunnerDeps, real_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_deps.run = _fail_on_upgrade_flag
    _prime_journal(settings)
    monkeypatch.setattr(runner_mod, "_find_backup", lambda paths, from_version: None)

    with pytest.raises(RunnerError, match="AUTO-ROLLBACK ALSO FAILED"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)

    journal = read_journal(journal_path_for(settings.db_path))
    assert journal is not None
    assert journal.phase == PHASE_FAILED
    assert "AUTO-ROLLBACK ALSO FAILED" in journal.error
    assert "no pre-upgrade database backup" in journal.error


# --------------------------------------------------------------------------- #
# smoke test failure (before anything live is touched)
# --------------------------------------------------------------------------- #


def test_smoke_test_version_mismatch_fails_cleanly(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    base_deps.http_get = lambda url, timeout: FakeHealthResponse(version="0.0.0-wrong")
    _prime_journal(settings)

    with pytest.raises(RunnerError, match="did not report healthy"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)

    journal = read_journal(journal_path_for(settings.db_path))
    assert journal is not None
    assert journal.phase == PHASE_FAILED  # nothing live was touched yet
    paths = _resolve_paths(settings, base_deps)
    assert not paths.rollback_venv.exists()


def test_smoke_test_staged_process_exits_early(
    settings: Settings, base_deps: RunnerDeps, real_db: Path
) -> None:
    class DeadOnArrival(FakePopen):
        def poll(self) -> Optional[int]:
            return 1  # already exited

    base_deps.popen = lambda *a, **kw: DeadOnArrival()
    _prime_journal(settings)

    with pytest.raises(RunnerError, match="exited early"):
        run_upgrade(TARGET, settings=settings, deps=base_deps)


# --------------------------------------------------------------------------- #
# unit tests for the filesystem-facing helpers
# --------------------------------------------------------------------------- #


def test_online_backup_produces_a_working_copy(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()

    dest = tmp_path / "backup" / "copy.db"
    _online_backup(src, dest)

    assert dest.exists()
    copy_conn = sqlite3.connect(dest)
    row = copy_conn.execute("SELECT v FROM t").fetchone()
    copy_conn.close()
    assert row == ("hello",)


def test_prune_old_backups_keeps_only_the_last_three(tmp_path: Path) -> None:
    upgrade_dir = tmp_path / "upgrade"
    upgrade_dir.mkdir()
    for i, ts in enumerate([100, 200, 300, 400, 500]):
        stem = f"pre-0.1.{i}-{ts}"
        (upgrade_dir / f"{stem}.db").write_text("db", encoding="utf-8")
        (upgrade_dir / f"{stem}.config.yaml").write_text("cfg", encoding="utf-8")
        # Backdate mtimes so ordering is deterministic regardless of write speed.
        import os

        os.utime(upgrade_dir / f"{stem}.db", (ts, ts))

    _prune_old_backups(upgrade_dir, keep=3)

    remaining_db = sorted(p.name for p in upgrade_dir.glob("pre-*.db"))
    assert remaining_db == ["pre-0.1.2-300.db", "pre-0.1.3-400.db", "pre-0.1.4-500.db"]
    # Sibling config copies for pruned backups are removed too.
    assert not (upgrade_dir / "pre-0.1.0-100.config.yaml").exists()
    assert (upgrade_dir / "pre-0.1.4-500.config.yaml").exists()


def test_restore_database_moves_current_aside_and_drops_wal_shm(tmp_path: Path) -> None:
    db = tmp_path / "netadmin.db"
    db.write_text("new-migrated-content", encoding="utf-8")
    (Path(str(db) + "-wal")).write_text("wal", encoding="utf-8")
    (Path(str(db) + "-shm")).write_text("shm", encoding="utf-8")
    backup = tmp_path / "pre-0.3.0-123.db"
    backup.write_text("old-backup-content", encoding="utf-8")

    _restore_database(db, backup)

    assert db.read_text() == "old-backup-content"
    assert Path(str(db) + ".upgrade-failed").read_text() == "new-migrated-content"
    assert not Path(str(db) + "-wal").exists()
    assert not Path(str(db) + "-shm").exists()


def test_snapshot_then_restore_venv_round_trips(tmp_path: Path) -> None:
    live = tmp_path / "live-venv"
    live.mkdir()
    (live / "marker.txt").write_text("v1", encoding="utf-8")
    rollback = tmp_path / "venv-rollback"

    from netadmin.upgrade.runner import RunnerPaths

    paths = RunnerPaths(
        live_venv=live,
        upgrade_dir=tmp_path,
        journal_path=tmp_path / "journal.json",
        cache_dir=tmp_path / "cache",
        rollback_venv=rollback,
    )
    _snapshot_venv(paths)
    assert rollback.exists()

    # Simulate a broken swap: corrupt the live venv.
    (live / "marker.txt").write_text("corrupted", encoding="utf-8")

    _restore_venv(paths)
    assert (live / "marker.txt").read_text() == "v1"
    assert not rollback.exists()  # consumed by the restore


def test_restore_venv_without_a_snapshot_raises(tmp_path: Path) -> None:
    from netadmin.upgrade.runner import RunnerPaths

    paths = RunnerPaths(
        live_venv=tmp_path / "live",
        upgrade_dir=tmp_path,
        journal_path=tmp_path / "journal.json",
        cache_dir=tmp_path / "cache",
        rollback_venv=tmp_path / "no-such-rollback",
    )
    with pytest.raises(RunnerError, match="no venv rollback snapshot"):
        _restore_venv(paths)


def test_backup_copies_config_and_secrets_when_present(
    settings: Settings, base_deps: RunnerDeps, real_db: Path, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("netadmin: {}\n", encoding="utf-8")
    secrets = tmp_path / "secrets.env"
    secrets.write_text("UNIFI_HOST=example\n", encoding="utf-8")
    base_deps.config_yaml = config
    base_deps.secrets_env = secrets

    paths = _resolve_paths(settings, base_deps)
    _backup(settings, paths, base_deps, FROM)

    config_backups = list(paths.upgrade_dir.glob("pre-*.config.yaml"))
    secrets_backups = list(paths.upgrade_dir.glob("pre-*.secrets.env"))
    assert len(config_backups) == 1
    assert len(secrets_backups) == 1
    import stat

    assert stat.S_IMODE(secrets_backups[0].stat().st_mode) == 0o600
