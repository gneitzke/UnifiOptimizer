"""Unattended investigation of newly-confirmed issues (ARCHITECTURE.md section 21).

A P1 that appears at 03:00 is worth a dossier at 03:01, not at 09:00 when someone
opens the tab. This module watches the issue engine's transition stream and runs
the *same* investigation the UI button runs, with no human in the loop.

Everything here is a consumer of seams that already exist:

* ``IssueEngine.add_callback`` -- the transition stream, shared with the WebSocket
  broadcaster and the Home Assistant bridge. :meth:`AutoInvestigator.on_transition`
  obeys that contract exactly: synchronous, non-blocking, never raising into the
  engine. It filters cheaply and drops an issue id on a bounded queue.
* :mod:`netadmin.llm.service` -- the start/complete split the API route uses. One
  worker task drains the queue on the loop thread, calls ``start_investigation``
  there (SQLite is loop-bound, section 3), runs a blocking provider's call in
  ``asyncio.to_thread``, and finishes with ``complete_investigation`` back on the
  loop. There is no second orchestration path.

Three things stand between a noisy network and an API bill:

1. **Confirmation.** ``PENDING`` issues never trigger; the engine's M-cycle confirm
   is the first debounce.
2. **Settling.** After dequeue the worker waits ``settle_s``, then re-reads the
   issue. Still ``ACTIVE`` at the configured severity, or it is dropped silently.
   A flapping issue costs nothing.
3. **Caps.** A storm guard collapses a burst down to the incident *roots*, and
   hourly/daily token buckets are a hard ceiling that skips rather than defers.

Idempotency is durable, not in-memory: at most one auto investigation per issue
id, ever. The ``investigations`` table is the source of truth, so a restart, a
reopen (which keeps the issue id), or a human who clicked first all resolve to
"a dossier already exists -- skip".

Nothing in this module touches the controller. It reads our own database and
produces text, exactly like the manual path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Optional

from netadmin.config import Settings
from netadmin.domain.types import IssueState
from netadmin.issues.engine import IssueEngine
from netadmin.issues.models import EventKind, Transition
from netadmin.llm import service
from netadmin.llm.provider import ProviderError, ProviderUnavailableError
from netadmin.store.repository import Repository

__all__ = [
    "AUTO_TRIGGER",
    "AutoInvestigateCounters",
    "AutoInvestigator",
    "build_auto_investigator",
]

_log = logging.getLogger(__name__)

# The ``investigated`` event detail label that distinguishes this path from a
# human click. No schema migration: ``issue_events.detail`` is a JSON blob.
AUTO_TRIGGER = "auto"

# Bounded so a pathological storm cannot grow the queue without limit. Dropping a
# trigger is safe: the dossier is a convenience, and the issue stays tracked.
_QUEUE_MAX = 256

# How often the worker re-checks whether a storm has subsided while holding.
_STORM_POLL_S = 5.0

_HOUR_S = 3600.0
_DAY_S = 86_400.0

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class AutoInvestigateCounters:
    """Why the auto path did or did not spend, surfaced on ``/api/health``.

    Every skip is counted under its own reason rather than folded into one
    "skipped" number: "we skipped 40 because of the hourly cap" and "we skipped 40
    because they were storm symptoms" call for completely different responses.
    """

    queued: int = 0
    ran: int = 0
    skipped_duplicate: int = 0
    skipped_settled: int = 0
    skipped_storm: int = 0
    skipped_cap: int = 0
    dropped_full: int = 0
    failed: int = 0
    last_error: Optional[str] = None


class AutoInvestigator:
    """Runs an investigation per qualifying transition, unattended.

    Built and started by the server lifespan alongside the other engine-stream
    consumers. Disabled (the default) it registers no callback, starts no task,
    and is indistinguishable from not existing.
    """

    def __init__(
        self,
        store: Repository,
        engine: IssueEngine,
        settings: Settings,
        *,
        clock: Optional[Clock] = None,
        sleeper: Optional[Sleeper] = None,
        base_dir: Optional[Path] = None,
        queue_max: int = _QUEUE_MAX,
    ) -> None:
        self._store = store
        self._engine = engine
        self._settings = settings
        self._cfg = settings.investigate.auto
        # Injected in tests so the whole settle/storm/cap machine runs on a fake
        # clock: no real waiting, and no flakiness from a real one.
        self._clock: Clock = clock or time.monotonic
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._base_dir = base_dir
        self._severities = frozenset(self._cfg.severities)
        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=queue_max)
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        # Issue ids sitting in the queue or being processed: stops a re-fire from
        # piling duplicates onto the queue behind the one already there.
        self._enqueued: set[int] = set()
        # Held across the run/complete await window, so a second queued item for
        # the same issue cannot start a parallel investigation.
        self._inflight: set[int] = set()
        self._triggers: Deque[float] = deque()
        # Latched for the whole drain of one burst. Without it the guard would
        # defeat itself: the first item sleeps out the storm window, so by the time
        # its symptoms are dequeued the trigger rate has fallen and every one of
        # them would look like a calm, unrelated issue worth its own dossier.
        self._storm_latched = False
        self._runs_hour: Deque[float] = deque()
        self._runs_day: Deque[float] = deque()
        self.counters = AutoInvestigateCounters()

    # -- lifecycle ----------------------------------------------------- #
    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled)

    @property
    def running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Register the engine callback and start the worker -- or no-op.

        Disabled, this returns having done nothing: no callback, no task. That is
        the "off by default" contract; the daemon boots identically either way.
        """
        if not self._cfg.enabled:
            _log.info("auto-investigation disabled (investigate.auto.enabled=false); not starting")
            return
        if self._task is not None:
            return
        self._running = True
        # Remove-then-add makes the subscription exactly-once no matter what came
        # before: the engine appends unconditionally, so a restart (or a start that
        # aborted after subscribing) would otherwise leave two copies registered and
        # every trigger would be counted twice.
        self._engine.remove_callback(self.on_transition)
        self._engine.add_callback(self.on_transition)
        self._task = asyncio.create_task(self._worker())
        _log.info(
            "auto-investigation started (provider %s, severities %s, "
            "settle %ss, caps %s/h %s/day)",
            self._cfg.provider,
            ",".join(sorted(self._severities)),
            self._cfg.settle_s,
            self._cfg.max_per_hour,
            self._cfg.max_per_day,
        )

    async def stop(self) -> None:
        """Unsubscribe from the engine and stop the worker.

        The callback is unregistered, not merely made inert: the engine appends
        unconditionally, so leaving it attached means a later ``start`` in the same
        process queues every qualifying transition twice. ``_running`` still guards
        the callback for anything already in flight.

        The worker is cancelled outright rather than drained: it is normally parked
        on an empty queue (so this is instant), and an investigation caught
        mid-flight has already persisted its ``pending`` row and dossier --
        recoverable by import, and not worth delaying shutdown for. Whatever was
        queued is dropped with the pending bookkeeping, so a restart begins clean
        instead of carrying an id the cancelled worker left marked as enqueued.
        """
        self._running = False
        self._engine.remove_callback(self.on_transition)
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._task = None
        # ``task_done`` per discarded item, so the queue's unfinished count lands
        # back on zero and a ``join`` after a restart cannot hang on work nobody
        # is going to do.
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
        self._enqueued.clear()
        self._inflight.clear()
        self._storm_latched = False

    # -- engine callback (sync, fire-and-forget) ----------------------- #
    def on_transition(self, transition: Transition) -> None:
        """Engine hook: cheaply filter, then enqueue the issue id.

        Never blocks and never raises into the engine -- the detect jobs that drive
        the engine share the daemon's one scheduler, so anything slow or throwing
        here would stall detection itself.
        """
        if not self._running:
            return
        issue_id = -1
        try:
            if not self._qualifies(transition):
                return
            issue_id = int(transition.issue_id)
            if issue_id in self._enqueued:
                return
            # Storm accounting counts one trigger per DISTINCT issue: the early
            # return above means a re-fire of an already-queued issue never
            # reaches here. That is the intent, since a storm is many separate
            # issues erupting at once, not one issue flapping.
            self._triggers.append(self._clock())
            self._enqueued.add(issue_id)
            self._queue.put_nowait(issue_id)
            self.counters.queued += 1
        except asyncio.QueueFull:
            self._enqueued.discard(issue_id)
            self.counters.dropped_full += 1
            _log.warning("auto-investigation queue full; dropping issue %s", issue_id)
        except Exception:  # noqa: BLE001 - isolation from the engine is the point
            self._enqueued.discard(issue_id)
            _log.warning(
                "auto-investigation trigger filter raised for issue %s; ignored",
                issue_id,
                exc_info=True,
            )

    def _qualifies(self, transition: Transition) -> bool:
        """True for a confirmed activation at a configured severity.

        ``to_state == ACTIVE`` covers all three ways an issue becomes live: the
        M-cycle confirm (``escalated`` out of ``pending``), a refire out of
        ``resolving``, and a ``reopened`` issue. ``PENDING`` never qualifies. The
        ``escalated`` clause additionally catches a severity raise on an
        already-active issue, should the engine ever emit one.
        """
        severity = getattr(transition.severity, "value", transition.severity)
        if str(severity) not in self._severities:
            return False
        if transition.to_state is IssueState.ACTIVE:
            return True
        return transition.kind == EventKind.ESCALATED

    # -- worker -------------------------------------------------------- #
    async def _worker(self) -> None:
        """Drain the queue forever, firewalling every item.

        One bad issue -- a provider blowing up, a row that vanished -- is logged
        and counted, never allowed to kill the task and silently end
        auto-investigation for the life of the process.
        """
        while True:
            issue_id = await self._queue.get()
            try:
                await self._process(issue_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the firewall
                self.counters.failed += 1
                self.counters.last_error = f"{type(exc).__name__}: {exc}"
                _log.warning(
                    "auto-investigation of issue %s failed; worker continues",
                    issue_id,
                    exc_info=True,
                )
            finally:
                self._enqueued.discard(issue_id)
                # The burst is over once nothing is waiting and the rate has
                # settled: release the latch so the next incident is judged fresh.
                if self._queue.empty() and not self._in_storm():
                    self._storm_latched = False
                self._queue.task_done()

    async def _process(self, issue_id: int) -> None:
        """Settle, hold out any storm, re-check every guard, then investigate."""
        if self._in_storm():
            self._storm_latched = True
        if self._cfg.settle_s:
            await self._sleep(float(self._cfg.settle_s))

        # Hold the queue while the trigger rate stays above threshold, then drain.
        while self._in_storm():
            self._storm_latched = True
            await self._sleep(_STORM_POLL_S)
        stormy = self._storm_latched

        # From here to the row insert there is no await: on a single-threaded event
        # loop that makes "check for an existing dossier" and "write ours" atomic
        # against a concurrent manual click.
        if not self._should_run(issue_id, stormy=stormy):
            return

        self._inflight.add(issue_id)
        try:
            await self._investigate(issue_id)
        finally:
            self._inflight.discard(issue_id)

    def _should_run(self, issue_id: int, *, stormy: bool) -> bool:
        """Every guard, cheapest and most decisive first. Synchronous by design."""
        issue = self._store.get_issue(issue_id)
        if issue is None:
            self.counters.skipped_settled += 1
            _log.debug("auto-investigation skipped issue %s: gone after settle", issue_id)
            return False
        if str(issue["state"]) != IssueState.ACTIVE.value:
            self.counters.skipped_settled += 1
            _log.info(
                "auto-investigation skipped issue %s: %s after settle, not active",
                issue_id,
                issue["state"],
            )
            return False
        if str(issue["severity"]) not in self._severities:
            self.counters.skipped_settled += 1
            _log.info(
                "auto-investigation skipped issue %s: severity dropped to %s",
                issue_id,
                issue["severity"],
            )
            return False

        # One dossier per storm, aimed at the cause rather than the noise.
        if stormy and not self._is_incident_root(issue_id):
            self.counters.skipped_storm += 1
            _log.info(
                "auto-investigation skipped issue %s: storm symptom of an investigated root",
                issue_id,
            )
            return False

        if not self._within_caps():
            return False

        if issue_id in self._inflight:
            self.counters.skipped_duplicate += 1
            return False
        if self._store.list_investigations(issue_id):
            # Any row, any provider, any status: a human or a prior run already
            # produced a dossier for this issue. Durable across restarts.
            self.counters.skipped_duplicate += 1
            _log.info(
                "auto-investigation skipped issue %s: an investigation already exists", issue_id
            )
            return False
        return True

    async def _investigate(self, issue_id: int) -> None:
        """Run the shared start/execute/complete cycle for one issue."""
        provider = self._cfg.provider
        try:
            prepared = self._start(issue_id, provider)
        except KeyError:
            self.counters.skipped_settled += 1
            return
        except ProviderUnavailableError as exc:
            if not (self._cfg.fallback_to_manual and provider != "manual"):
                self.counters.failed += 1
                self.counters.last_error = f"{type(exc).__name__}: {exc}"
                _log.warning(
                    "auto-investigation skipped issue %s: provider %s unavailable (%s)",
                    issue_id,
                    provider,
                    exc,
                )
                return
            # Never retried into spend: the free provider writes a real dossier and
            # the UI honestly says "awaiting import".
            _log.warning(
                "auto-investigation provider %s unavailable (%s); "
                "falling back to a manual dossier for issue %s",
                provider,
                exc,
                issue_id,
            )
            prepared = self._start(issue_id, "manual")

        self._note_run()
        self.counters.ran += 1
        outcome = prepared.outcome

        if prepared.run is None:
            # Manual: dossier written, row pending, answer imported later.
            return

        try:
            text = await asyncio.to_thread(prepared.run)
        except ProviderError as exc:
            # The pending row and its dossier survive; a human can re-run or import.
            # No auto-retry -- a retry loop against a paid API is how a bug becomes
            # a bill.
            self.counters.failed += 1
            self.counters.last_error = f"{type(exc).__name__}: {exc}"
            self._engine.investigated(
                outcome.issue_id,
                int(time.time()),
                detail={
                    "provider": outcome.provider,
                    "status": "failed",
                    "investigation_id": outcome.investigation_id,
                    "trigger": AUTO_TRIGGER,
                    "error": str(exc),
                },
            )
            _log.warning(
                "auto-investigation provider %s failed for issue %s; row left pending",
                outcome.provider,
                issue_id,
                exc_info=True,
            )
            return

        if text is not None:
            service.complete_investigation(
                self._store, self._engine, outcome, text, trigger=AUTO_TRIGGER
            )

    def _start(self, issue_id: int, provider_name: str) -> service.PreparedInvestigation:
        return service.start_investigation(
            self._store,
            self._engine,
            issue_id,
            provider_name,
            base_dir=self._base_dir,
            trigger=AUTO_TRIGGER,
        )

    # -- guards -------------------------------------------------------- #
    @staticmethod
    def _prune(stamps: Deque[float], now: float, window: float) -> None:
        while stamps and now - stamps[0] >= window:
            stamps.popleft()

    def _in_storm(self) -> bool:
        """More than ``storm_threshold`` qualifying triggers inside the window."""
        now = self._clock()
        self._prune(self._triggers, now, float(self._cfg.storm_window_s))
        return len(self._triggers) > int(self._cfg.storm_threshold)

    def _within_caps(self) -> bool:
        """Rolling hourly/daily token buckets. Exhaustion skips, never defers."""
        now = self._clock()
        self._prune(self._runs_hour, now, _HOUR_S)
        self._prune(self._runs_day, now, _DAY_S)
        if len(self._runs_hour) >= int(self._cfg.max_per_hour):
            self.counters.skipped_cap += 1
            _log.info("auto-investigation skipped: hourly cap %s reached", self._cfg.max_per_hour)
            return False
        if len(self._runs_day) >= int(self._cfg.max_per_day):
            self.counters.skipped_cap += 1
            _log.info("auto-investigation skipped: daily cap %s reached", self._cfg.max_per_day)
            return False
        return True

    def _note_run(self) -> None:
        now = self._clock()
        self._runs_hour.append(now)
        self._runs_day.append(now)

    def _is_incident_root(self, issue_id: int) -> bool:
        """True for an incident root or an uncorrelated issue.

        An issue in no open incident is investigated on its own merits -- the
        storm guard exists to suppress *symptoms of a cause we are already
        investigating*, not to suppress unrelated problems that happen to be
        concurrent.
        """
        incident_id = self._store.incident_id_for_issue(issue_id)
        if incident_id is None:
            return True
        incident = self._store.get_incident(incident_id)
        if incident is None:
            return True
        return int(incident["root_issue_id"]) == int(issue_id)

    # -- observability ------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        """The ``/api/health`` block. Carries no dossier text and no credentials."""
        return {
            "enabled": bool(self._cfg.enabled),
            "running": self.running,
            "provider": str(self._cfg.provider),
            "severities": sorted(self._severities),
            "queue_depth": self._queue.qsize(),
            "counters": asdict(self.counters),
        }


def build_auto_investigator(
    settings: Settings, store: Repository, engine: IssueEngine
) -> AutoInvestigator:
    """Construct the auto-investigator from settings (mirrors the HA bridge factory)."""
    return AutoInvestigator(store, engine, settings)
