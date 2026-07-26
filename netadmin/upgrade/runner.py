"""The pip self-upgrade runner (docs/ARCHITECTURE.md section 23): ``netadmin upgrade run``.

Spawned detached (``start_new_session=True``) by ``POST /api/system/update/apply``
so it outlives the very daemon restart it performs. Never invoked directly by a
user -- the API primes ``data/upgrade/journal.json`` with the live daemon's
pid/argv/cwd/env (:mod:`netadmin.upgrade.journal`) before spawning this, and
:func:`run_upgrade` refuses to start without a matching, idle journal already
in place.

The nine steps, in order, each one recorded in the journal before it runs so a
crash mid-upgrade leaves an honest trail:

1. **Pre-flight** -- this install is a real, writable pip venv
   (``self_upgrade_supported``); the journal is idle (checked structurally, by
   requiring the journal already be in :data:`PHASE_STARTING`, never re-entered);
   free disk is at least twice the live venv plus the database. Then
   ``pip download`` the target into a cache dir -- pip verifies the sha256 itself,
   so a corrupt download dies here without touching anything live.
2. **Stage** -- a fresh venv at ``data/upgrade/venv-<target>``, installed offline
   from that cache. The live venv is untouched.
3. **Smoke test** -- the staged build against a *copy* of the real data: an online
   backup of the database plus a copy of ``config.yaml`` (never ``secrets.env``)
   into a temp dir, the staged ``netadmin daemon`` launched on a random loopback
   port against that copy, polled for a healthy ``/api/health`` reporting the
   target version, then killed. This is what exercises the new migrations against
   a copy before they ever touch the real database.
4. **Pre-upgrade backup** -- an online backup of the database plus copies of
   ``config.yaml`` and ``secrets.env`` (chmod 600), retaining the last three.
5. **Rollback anchor** -- the live venv copied aside to ``venv-rollback``, so a
   restore is a ``rm`` and a ``mv`` back to the *same* absolute path (keeping
   shebangs and any supervisor's recorded path valid).
6. **Swap** -- offline ``pip install --upgrade`` into the live venv.
7. **Restart** -- ``SIGTERM`` the recorded daemon pid, wait for the port to free;
   if nothing brings it back within 15 s, respawn it detached from the recorded
   argv/cwd/env.
8. **Verify** -- poll ``/api/health`` for up to 120 s for ``ready`` + the target
   version.
9. **Auto-rollback** -- any failure from step 6 onward (the point the live venv
   or database could have changed) stops whatever is now running, restores the
   venv from its step-5 snapshot, restores the pre-upgrade database (moving the
   current one aside as ``.upgrade-failed`` and dropping its ``-wal``/``-shm``
   files first -- the new version has already migrated the real one forward, so
   it cannot simply be left in place), respawns, and verifies the *old* version's
   health. A failure before step 6 never touched anything live, so it needs no
   rollback -- the journal just records ``failed`` and the next attempt starts
   clean.

Every OS-facing operation (subprocess calls, the health poll, signalling, sleep,
port connectivity) goes through :class:`RunnerDeps`, an injectable seam mirroring
:class:`netadmin.upgrade.checker.VersionChecker`'s constructor injection, so tests
drive the full state machine -- including every failure/rollback branch -- with
fakes, never a real network call, venv, or subprocess.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from netadmin.config import CONFIG_YAML, SECRETS_ENV, Settings
from netadmin.logging import get_logger
from netadmin.upgrade.detect import detect_install_method
from netadmin.upgrade.journal import (
    PHASE_BACKING_UP,
    PHASE_DONE,
    PHASE_DOWNLOADING,
    PHASE_FAILED,
    PHASE_PREFLIGHT,
    PHASE_RESTARTING,
    PHASE_ROLLED_BACK,
    PHASE_SMOKE_TESTING,
    PHASE_STAGING,
    PHASE_STARTING,
    PHASE_SWAPPING,
    PHASE_VERIFYING,
    UpgradeJournal,
    journal_path_for,
    read_journal,
    write_journal,
)

__all__ = ["RunnerError", "RunnerDeps", "RunnerPaths", "run_upgrade"]

_log = get_logger("upgrade.runner")

_PIP_TIMEOUT_S = 600.0
_VENV_TIMEOUT_S = 120.0
_SMOKE_TEST_TIMEOUT_S = 30.0
_RESTART_GRACE_S = 15.0
_VERIFY_TIMEOUT_S = 120.0
_BACKUP_RETAIN = 3
_DIST_NAME = "unifioptimizer"


class RunnerError(RuntimeError):
    """A step of the upgrade failed. The journal already records why."""


# --------------------------------------------------------------------------- #
# small filesystem/process/network helpers (defined before RunnerDeps, which
# references some of them as default values)
# --------------------------------------------------------------------------- #


def _default_http_get(url: str, timeout: float) -> httpx.Response:
    return httpx.get(url, timeout=timeout)


def _default_port_in_use(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _tail(text: Optional[str], n: int = 4000) -> str:
    return (text or "")[-n:]


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _db_size_bytes(db_path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        try:
            if p.exists():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _online_backup(src: Path, dest: Path) -> None:
    """SQLite's own online backup API: consistent against a live writer (docs/BACKUP.md)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / "bin" / "python"


def _venv_netadmin(venv_dir: Path) -> Path:
    return venv_dir / "bin" / "netadmin"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# --------------------------------------------------------------------------- #
# injectable seams
# --------------------------------------------------------------------------- #


@dataclass
class RunnerDeps:
    """Injectable OS-facing seams. Production defaults are the real thing.

    ``live_venv`` defaults to ``sys.prefix`` -- correct in production, where this
    process's own interpreter IS the live venv (``self_upgrade_supported`` gates on
    exactly that) -- but is a seam here rather than read inline so a test never
    accidentally snapshots/restores the real interpreter environment running the
    test suite itself.

    ``config_yaml`` / ``secrets_env`` default to the real
    :data:`netadmin.config.CONFIG_YAML` / :data:`netadmin.config.SECRETS_ENV`
    paths -- correct in production -- but are seams for the same reason as
    ``live_venv``: a test importing this module from a source checkout would
    otherwise back up (and, for the smoke-test copy, read) whatever real
    ``data/config.yaml`` / ``data/secrets.env`` happen to sit next to that
    checkout, which is exactly the kind of accidental real-secrets touch the
    no-secrets-in-tests discipline exists to prevent.

    ``port_in_use`` is real TCP connectivity by default; a seam mainly so a
    test's restart/rollback assertions never depend on what else happens to be
    listening on the test host's ports (e.g. a real netadmin daemon on the
    default 8765 in dev).
    """

    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run
    popen: Callable[..., "subprocess.Popen[Any]"] = subprocess.Popen
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time
    http_get: Callable[[str, float], httpx.Response] = _default_http_get
    kill: Callable[[int, int], None] = os.kill
    port_in_use: Callable[[str, int], bool] = _default_port_in_use
    live_venv: Path = field(default_factory=lambda: Path(sys.prefix))
    config_yaml: Path = field(default_factory=lambda: CONFIG_YAML)
    secrets_env: Path = field(default_factory=lambda: SECRETS_ENV)


@dataclass(frozen=True)
class RunnerPaths:
    """Every filesystem location this run touches."""

    live_venv: Path
    upgrade_dir: Path
    journal_path: Path
    cache_dir: Path
    rollback_venv: Path


def _resolve_paths(settings: Settings, deps: RunnerDeps) -> RunnerPaths:
    upgrade_dir = Path(settings.db_path).resolve().parent / "upgrade"
    return RunnerPaths(
        live_venv=deps.live_venv,
        upgrade_dir=upgrade_dir,
        journal_path=journal_path_for(settings.db_path),
        cache_dir=upgrade_dir / "cache",
        rollback_venv=upgrade_dir / "venv-rollback",
    )


# --------------------------------------------------------------------------- #
# process control helpers
# --------------------------------------------------------------------------- #


def _stop_daemon(pid: Optional[int], deps: RunnerDeps) -> None:
    if pid is None:
        return
    try:
        deps.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _respawn(journal: UpgradeJournal, deps: RunnerDeps) -> Optional[int]:
    if not journal.daemon_argv:
        raise RunnerError("no daemon argv recorded in the journal; cannot respawn the daemon")
    proc = deps.popen(
        list(journal.daemon_argv),
        cwd=journal.daemon_cwd or None,
        env=dict(journal.daemon_env) if journal.daemon_env else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return getattr(proc, "pid", None)


def _terminate(proc: "subprocess.Popen[Any]") -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass
    try:
        proc.wait(timeout=5.0)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# step 1: pre-flight + download
# --------------------------------------------------------------------------- #


def _preflight(settings: Settings, paths: RunnerPaths) -> None:
    info = detect_install_method()
    if not info.self_upgrade_supported:
        raise RunnerError(f"self-upgrade is not supported on this install (method={info.method})")
    venv_size = _dir_size_bytes(paths.live_venv)
    db_size = _db_size_bytes(Path(settings.db_path))
    required = 2 * (venv_size + db_size)
    free = shutil.disk_usage(paths.upgrade_dir.parent).free
    if free < required:
        raise RunnerError(
            f"insufficient disk space: {free} bytes free, need >= {required} "
            f"(2x the {venv_size}-byte venv plus {db_size}-byte database)"
        )


def _download(target_version: str, paths: RunnerPaths, deps: RunnerDeps) -> None:
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    result = deps.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            f"{_DIST_NAME}=={target_version}",
            "--dest",
            str(paths.cache_dir),
        ],
        capture_output=True,
        text=True,
        timeout=_PIP_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RunnerError(f"pip download failed (exit {result.returncode}): {_tail(result.stderr)}")


# --------------------------------------------------------------------------- #
# step 2: stage
# --------------------------------------------------------------------------- #


def _stage(target_version: str, paths: RunnerPaths, deps: RunnerDeps) -> Path:
    venv_dir = paths.upgrade_dir / f"venv-{target_version}"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    result = deps.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=_VENV_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RunnerError(f"staged venv creation failed: {_tail(result.stderr)}")
    result = deps.run(
        [
            str(_venv_python(venv_dir)),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(paths.cache_dir),
            f"{_DIST_NAME}=={target_version}",
        ],
        capture_output=True,
        text=True,
        timeout=_PIP_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RunnerError(f"offline install into the staged venv failed: {_tail(result.stderr)}")
    return venv_dir


# --------------------------------------------------------------------------- #
# step 3: smoke test
# --------------------------------------------------------------------------- #


def _smoke_test(venv_dir: Path, settings: Settings, target_version: str, deps: RunnerDeps) -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="netadmin-smoke-"))
    try:
        smoke_data = tmp_root / "data"
        smoke_data.mkdir(parents=True)
        _online_backup(Path(settings.db_path), smoke_data / "netadmin.db")
        if deps.config_yaml.exists():
            shutil.copy2(deps.config_yaml, smoke_data / "config.yaml")
        # Deliberately no secrets.env: the smoke test never needs (and must never
        # see) controller/broker credentials, only that the app boots and migrates.

        port = _free_loopback_port()
        env = dict(os.environ)
        env["NETADMIN_DATA_DIR"] = str(smoke_data)
        env.pop("NETADMIN_API_TOKEN", None)

        proc = deps.popen(
            [str(_venv_netadmin(venv_dir)), "daemon", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(tmp_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            url = f"http://127.0.0.1:{port}/api/health"
            deadline = deps.now() + _SMOKE_TEST_TIMEOUT_S
            last_err: Any = None
            while deps.now() < deadline:
                if proc.poll() is not None:
                    raise RunnerError(
                        f"staged {target_version} exited early (code {proc.returncode}) "
                        "during the smoke test"
                    )
                try:
                    resp = deps.http_get(url, 2.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("ready") and data.get("version") == target_version:
                            return
                except Exception as exc:  # noqa: BLE001 - keep polling until the deadline
                    last_err = exc
                deps.sleep(0.5)
            raise RunnerError(
                f"staged {target_version} did not report healthy within "
                f"{_SMOKE_TEST_TIMEOUT_S:.0f}s (last error: {last_err})"
            )
        finally:
            _terminate(proc)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# step 4: pre-upgrade backup
# --------------------------------------------------------------------------- #


def _backup_stem(from_version: str, ts: int) -> str:
    return f"pre-{from_version}-{ts}"


def _backup(settings: Settings, paths: RunnerPaths, deps: RunnerDeps, from_version: str) -> Path:
    paths.upgrade_dir.mkdir(parents=True, exist_ok=True)
    stem = _backup_stem(from_version, int(deps.now()))
    db_backup = paths.upgrade_dir / f"{stem}.db"
    _online_backup(Path(settings.db_path), db_backup)
    os.chmod(db_backup, 0o600)
    if deps.config_yaml.exists():
        shutil.copy2(deps.config_yaml, paths.upgrade_dir / f"{stem}.config.yaml")
    if deps.secrets_env.exists():
        secrets_backup = paths.upgrade_dir / f"{stem}.secrets.env"
        shutil.copy2(deps.secrets_env, secrets_backup)
        os.chmod(secrets_backup, 0o600)
    _prune_old_backups(paths.upgrade_dir)
    return db_backup


def _prune_old_backups(upgrade_dir: Path, keep: int = _BACKUP_RETAIN) -> None:
    backups = sorted(upgrade_dir.glob("pre-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in backups[keep:]:
        stem = stale.name[: -len(".db")]
        for suffix in (".db", ".config.yaml", ".secrets.env"):
            candidate = upgrade_dir / f"{stem}{suffix}"
            if candidate.exists():
                candidate.unlink()


def _find_backup(paths: RunnerPaths, from_version: str) -> Optional[Path]:
    candidates = sorted(
        paths.upgrade_dir.glob(f"pre-{from_version}-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------- #
# step 5: rollback anchor + venv restore
# --------------------------------------------------------------------------- #


def _snapshot_venv(paths: RunnerPaths) -> None:
    if paths.rollback_venv.exists():
        shutil.rmtree(paths.rollback_venv)
    shutil.copytree(paths.live_venv, paths.rollback_venv, symlinks=True)


def _restore_venv(paths: RunnerPaths) -> None:
    if not paths.rollback_venv.exists():
        raise RunnerError(f"no venv rollback snapshot at {paths.rollback_venv}; cannot restore")
    if paths.live_venv.exists():
        shutil.rmtree(paths.live_venv)
    shutil.move(str(paths.rollback_venv), str(paths.live_venv))


# --------------------------------------------------------------------------- #
# step 6: swap
# --------------------------------------------------------------------------- #


def _swap(target_version: str, paths: RunnerPaths, deps: RunnerDeps) -> None:
    result = deps.run(
        [
            str(_venv_python(paths.live_venv)),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(paths.cache_dir),
            "--upgrade",
            f"{_DIST_NAME}=={target_version}",
        ],
        capture_output=True,
        text=True,
        timeout=_PIP_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RunnerError(f"live venv swap failed: {_tail(result.stderr)}")


# --------------------------------------------------------------------------- #
# steps 7 + 8: restart, verify
# --------------------------------------------------------------------------- #


def _restart(journal: UpgradeJournal, settings: Settings, deps: RunnerDeps) -> None:
    """Stop whichever daemon is currently recorded as running, then bring one back.

    Reused for both the forward restart (step 7, stopping the pre-upgrade daemon)
    and the rollback restart (stopping whatever the forward path most recently
    started) -- ``respawned_pid`` takes priority over the original ``daemon_pid``
    so the second call stops the *right* process.
    """
    pid = journal.respawned_pid or journal.daemon_pid
    _stop_daemon(pid, deps)
    host, port = settings.server_host, settings.server_port
    deadline = deps.now() + _RESTART_GRACE_S
    # First wait for the port to actually free (the SIGTERM'd process closing its
    # socket); then watch the remaining budget for a supervisor to bring it back
    # on its own before giving up and respawning ourselves.
    while deps.now() < deadline and deps.port_in_use(host, port):
        deps.sleep(0.25)
    while deps.now() < deadline:
        if deps.port_in_use(host, port):
            return  # a supervisor beat us to it
        deps.sleep(0.5)
    if deps.port_in_use(host, port):
        return
    journal.respawned_pid = _respawn(journal, deps)


def _verify(settings: Settings, expect_version: str, deps: RunnerDeps) -> None:
    host = "127.0.0.1" if settings.server_host in ("0.0.0.0", "::", "") else settings.server_host
    url = f"http://{host}:{settings.server_port}/api/health"
    deadline = deps.now() + _VERIFY_TIMEOUT_S
    last_err: Any = None
    while deps.now() < deadline:
        try:
            resp = deps.http_get(url, 3.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ready") and data.get("version") == expect_version:
                    return
        except Exception as exc:  # noqa: BLE001 - keep polling until the deadline
            last_err = exc
        deps.sleep(1.0)
    raise RunnerError(
        f"daemon did not report ready+{expect_version} within "
        f"{_VERIFY_TIMEOUT_S:.0f}s (last error: {last_err})"
    )


# --------------------------------------------------------------------------- #
# step 9: auto-rollback
# --------------------------------------------------------------------------- #


def _rollback(
    journal: UpgradeJournal, settings: Settings, paths: RunnerPaths, deps: RunnerDeps
) -> None:
    _restore_venv(paths)
    backup = _find_backup(paths, journal.from_version)
    if backup is None:
        raise RunnerError(
            f"no pre-upgrade database backup found for {journal.from_version}; "
            "cannot safely restore -- see docs/BACKUP.md"
        )
    _restore_database(Path(settings.db_path), backup)
    _restart(journal, settings, deps)
    _verify(settings, journal.from_version, deps)


def _restore_database(db_path: Path, backup: Path) -> None:
    """Move the (already-migrated-forward) live database aside, then restore the backup.

    The new version already migrated the real database forward by the time this
    runs (the smoke test only migrated a *copy*; the swap step's daemon restart is
    what applies migrations for real, on startup). Leaving that migrated file in
    place next to a restored old-version venv is exactly the forward-only-migration
    hazard the ``SchemaTooNewError`` startup gate guards (docs/ARCHITECTURE.md 23),
    so it is moved aside as ``.upgrade-failed`` for inspection, never deleted.
    """
    failed_path = db_path.with_name(db_path.name + ".upgrade-failed")
    if db_path.exists():
        if failed_path.exists():
            failed_path.unlink()
        shutil.move(str(db_path), str(failed_path))
    for suffix in ("-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    shutil.copy2(backup, db_path)


def _cleanup_on_success(paths: RunnerPaths, venv_dir: Path) -> None:
    for stale in (venv_dir, paths.rollback_venv):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def run_upgrade(
    target_version: str, *, settings: Settings, deps: Optional[RunnerDeps] = None
) -> UpgradeJournal:
    """Run the full nine-step self-upgrade to ``target_version``.

    Requires a journal already primed in :data:`PHASE_STARTING` by the API handler
    (with ``target_version`` matching and the daemon's pid/argv/cwd/env recorded);
    this is deliberately not something a bare CLI invocation can improvise, since
    only the running daemon knows how to bring itself back up. Every exception is
    caught, classified (rolled back vs. failed outright, depending on whether the
    live venv/database were ever touched), recorded in the journal, and re-raised
    as a :class:`RunnerError` so the CLI exits non-zero.
    """
    deps = deps or RunnerDeps()
    paths = _resolve_paths(settings, deps)

    journal = read_journal(paths.journal_path)
    if journal is None:
        raise RunnerError(
            f"no upgrade journal at {paths.journal_path}; netadmin upgrade run must be "
            "launched by POST /api/system/update/apply, not invoked directly"
        )
    if journal.target_version != target_version:
        raise RunnerError(
            f"journal target {journal.target_version!r} does not match requested "
            f"{target_version!r}"
        )
    if journal.phase != PHASE_STARTING and journal.is_active(int(deps.now())):
        raise RunnerError(f"journal is not idle (phase={journal.phase!r}); refusing to run again")
    # Stamp our own pid so a later attempt can tell "still running" from
    # "killed half way": without it an interrupted upgrade looks in-flight forever.
    journal.runner_pid = os.getpid()

    def _advance(phase: str) -> None:
        journal.phase = phase
        journal.updated_ts = int(deps.now())
        write_journal(paths.journal_path, journal)

    swap_started = False
    try:
        _advance(PHASE_PREFLIGHT)
        _preflight(settings, paths)

        _advance(PHASE_DOWNLOADING)
        _download(target_version, paths, deps)

        _advance(PHASE_STAGING)
        venv_dir = _stage(target_version, paths, deps)

        _advance(PHASE_SMOKE_TESTING)
        _smoke_test(venv_dir, settings, target_version, deps)

        _advance(PHASE_BACKING_UP)
        _backup(settings, paths, deps, journal.from_version)
        _snapshot_venv(paths)

        _advance(PHASE_SWAPPING)
        swap_started = True
        _swap(target_version, paths, deps)

        _advance(PHASE_RESTARTING)
        _restart(journal, settings, deps)

        _advance(PHASE_VERIFYING)  # re-persists the (possibly updated) respawned_pid
        _verify(settings, target_version, deps)

        journal.error = None
        _advance(PHASE_DONE)
        _cleanup_on_success(paths, venv_dir)
        _log.info("self-upgrade to %s completed", target_version)
        return journal

    except Exception as exc:  # noqa: BLE001 - the never-brick boundary
        reason = f"{type(exc).__name__}: {exc}"
        rolled_back = False
        if swap_started:
            try:
                _rollback(journal, settings, paths, deps)
                rolled_back = True
            except Exception as rollback_exc:  # noqa: BLE001
                reason = (
                    f"{reason} | AUTO-ROLLBACK ALSO FAILED: {type(rollback_exc).__name__}: "
                    f"{rollback_exc}. Manual recovery required -- see docs/BACKUP.md and "
                    f"{paths.upgrade_dir}."
                )
                _log.error("self-upgrade rollback FAILED: %s", reason)
        journal.error = reason
        journal.phase = PHASE_ROLLED_BACK if rolled_back else PHASE_FAILED
        journal.updated_ts = int(deps.now())
        write_journal(paths.journal_path, journal)
        _log.error("self-upgrade to %s %s: %s", target_version, journal.phase, reason)
        raise RunnerError(reason) from exc
