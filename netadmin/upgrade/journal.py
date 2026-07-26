"""The self-upgrade journal (docs/ARCHITECTURE.md section 23).

A single file, ``data/upgrade/journal.json``, that is three things at once:

1. **The lock.** Its ``phase`` is how ``POST /api/system/update/apply`` and the
   runner itself both know whether an upgrade is already in flight, so a second
   click (or a stale browser tab) 409s instead of racing a real one.
2. **The progress record.** ``GET /api/system/update`` reads it to show
   ``upgrade_state`` in the banner without touching PyPI or the runner process.
3. **The restart recipe.** Before spawning the detached runner, the API records
   the *daemon's own* pid, argv, cwd, and environment here -- the runner has no
   other way to know how to stop the old process and respawn the new one with
   the exact command line it was originally started with.

Written with an atomic ``os.replace`` (mirrors :func:`netadmin.config.write_secrets`)
so a crash mid-write never leaves a half-written, unparseable journal behind, and
chmod'd ``0600`` because it can carry a copy of the daemon's environment, which may
include credentials the daemon itself was started with.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "JOURNAL_FILENAME",
    "PHASE_STARTING",
    "PHASE_PREFLIGHT",
    "PHASE_DOWNLOADING",
    "PHASE_STAGING",
    "PHASE_SMOKE_TESTING",
    "PHASE_BACKING_UP",
    "PHASE_SWAPPING",
    "PHASE_RESTARTING",
    "PHASE_VERIFYING",
    "PHASE_DONE",
    "PHASE_ROLLED_BACK",
    "PHASE_FAILED",
    "IN_PROGRESS_PHASES",
    "TERMINAL_PHASES",
    "UpgradeJournal",
    "journal_path_for",
    "read_journal",
    "write_journal",
]

JOURNAL_FILENAME = "journal.json"

# The state machine the runner drives, in order. Each is written to disk before
# the corresponding step starts, so a crash mid-upgrade leaves an honest record of
# exactly how far it got.
PHASE_STARTING = "starting"
PHASE_PREFLIGHT = "preflight"
PHASE_DOWNLOADING = "downloading"
PHASE_STAGING = "staging"
PHASE_SMOKE_TESTING = "smoke_testing"
PHASE_BACKING_UP = "backing_up"
PHASE_SWAPPING = "swapping"
PHASE_RESTARTING = "restarting"
PHASE_VERIFYING = "verifying"

# Terminal phases: the runner has stopped touching anything.
PHASE_DONE = "done"
PHASE_ROLLED_BACK = "rolled_back"
PHASE_FAILED = "failed"

IN_PROGRESS_PHASES = frozenset(
    {
        PHASE_STARTING,
        PHASE_PREFLIGHT,
        PHASE_DOWNLOADING,
        PHASE_STAGING,
        PHASE_SMOKE_TESTING,
        PHASE_BACKING_UP,
        PHASE_SWAPPING,
        PHASE_RESTARTING,
        PHASE_VERIFYING,
    }
)
TERMINAL_PHASES = frozenset({PHASE_DONE, PHASE_ROLLED_BACK, PHASE_FAILED})

# How long an in-flight record may go untouched before it is treated as abandoned.
# Comfortably longer than a real upgrade (download, staged venv, smoke test, swap,
# restart, verify) so a slow link never looks dead, short enough that a crash does
# not lock upgrading out for a working day.
STALE_AFTER_S = 30 * 60


def _pid_alive(pid: int) -> bool:
    """True if a process with this pid exists and we may signal it.

    ``os.kill(pid, 0)`` sends nothing; it only performs the existence and
    permission check. EPERM means the process exists under another user, which
    still counts as alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class UpgradeJournal:
    """One upgrade attempt's full state, as persisted to ``journal.json``.

    ``daemon_pid`` / ``daemon_argv`` / ``daemon_cwd`` / ``daemon_env`` are captured
    once, by the API handler, from the *running daemon's own* process (``os.getpid()``,
    ``sys.argv``, ``os.getcwd()``, ``os.environ``) immediately before it spawns the
    detached runner -- they are how the runner, a separate process with no memory of
    how the daemon was originally launched, can stop it and bring an equivalent
    process back up. ``respawned_pid`` is filled in by the runner itself only if it
    had to respawn the daemon by hand (no supervisor beat it to the port).
    """

    phase: str
    target_version: str
    from_version: str
    started_ts: int
    updated_ts: int
    daemon_pid: Optional[int] = None
    daemon_argv: list[str] = field(default_factory=list)
    daemon_cwd: Optional[str] = None
    daemon_env: dict[str, str] = field(default_factory=dict)
    respawned_pid: Optional[int] = None
    # The runner's OWN pid, written by the runner as it starts. This is what makes
    # an abandoned journal recoverable: without it, a runner killed mid-phase leaves
    # a record that looks in-flight forever, and every future upgrade is refused.
    runner_pid: Optional[int] = None
    error: Optional[str] = None

    @property
    def in_progress(self) -> bool:
        """Phase says an upgrade was underway. Says nothing about whether it still is."""
        return self.phase in IN_PROGRESS_PHASES

    def is_abandoned(self, now: int, *, max_age_s: int = STALE_AFTER_S) -> bool:
        """An in-flight record no live runner is working on any more.

        A SIGKILL, an OOM kill, or a host reboot mid-upgrade leaves the phase
        frozen. Treating that as "in flight" forever means one crash permanently
        disables upgrading, which is its own kind of brick. Two independent
        signals, either sufficient:

        * the recorded runner pid is gone (authoritative when we have a pid), or
        * the record has not been touched in ``max_age_s`` (the backstop for a
          crash before the pid was written, and for a pid reused by another
          process after a reboot).
        """
        if not self.in_progress:
            return False
        if self.runner_pid is not None and not _pid_alive(self.runner_pid):
            return True
        return (now - self.updated_ts) > max_age_s

    def is_active(self, now: int, *, max_age_s: int = STALE_AFTER_S) -> bool:
        """In flight AND someone is still working on it."""
        return self.in_progress and not self.is_abandoned(now, max_age_s=max_age_s)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UpgradeJournal":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def journal_path_for(db_path: Path | str) -> Path:
    """The journal path for an install, derived from its database path.

    Upgrade artifacts (the journal, the venv snapshots, the pre-upgrade backups)
    live in an ``upgrade/`` directory next to the database rather than under a
    fixed ``NETADMIN_DATA_DIR``-derived constant, so a non-default ``db_path``
    (a test, an unusual deploy) keeps everything for one install colocated.
    """
    return Path(db_path).resolve().parent / "upgrade" / JOURNAL_FILENAME


def read_journal(path: Path) -> Optional[UpgradeJournal]:
    """The journal at ``path``, or ``None`` if it does not exist / is unreadable.

    A missing or corrupt journal is treated as "no upgrade has ever run" rather
    than raised -- a health/status read must never fail because of a stray file.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return UpgradeJournal.from_dict(data)
    except TypeError:
        return None


def write_journal(path: Path, journal: UpgradeJournal) -> None:
    """Persist ``journal`` atomically (same-dir temp file + ``os.replace``), chmod 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(journal.as_dict(), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".journal-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
