"""On-demand tech-visit router: ``/api/visit`` (ARCHITECTURE.md sections 3 & 12).

Kicks a one-shot :func:`~netadmin.visit.run_visit` in the background, lets the UI
poll its progress, and serves the resulting :class:`~netadmin.visit.VisitReport`.

Isolation is the point. A visit opens its **own** temporary working store (never
the daemon's loop-bound one) and does its heavy sync analysis in a worker thread
via :func:`run_in_threadpool`, so a visit run cannot block the daemon's event loop
or touch its live database. The visit connects **read-only** to the controller and
can never mutate it — the fix engine is the only mutating component, and a visit
never invokes it.

State is a single in-process run holder on ``app.state.visit_manager``: v1 is one
controller / one site, so exactly one visit runs at a time. Starting a new run
while one is active returns 409; the last finished run stays fetchable until the
next one starts. Progress is **polled** (``GET /api/visit``) — the step list
updates in place as the worker thread advances — which is the honest, race-free
surface for a cross-thread background job.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from netadmin.logging import get_logger
from netadmin.visit.runner import STEP_ORDER, VisitStep, run_visit

router = APIRouter(prefix="/api", tags=["visit"])
_log = get_logger("server.ondemand")

# Guard rails on the caller-supplied lookback so a stray request cannot ask for a
# months-long backfill + SLE sweep on a live visit.
_MAX_LOOKBACK_DAYS = 31


@dataclass
class VisitRun:
    """One background visit's live state, mutated in place by the worker thread."""

    run_id: str
    status: str  # running | done | failed
    started_ts: int
    lookback_days: Optional[int]
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    report: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    finished_ts: Optional[int] = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_ts": self.started_ts,
            "finished_ts": self.finished_ts,
            "lookback_days": self.lookback_days,
            "steps": [self.steps[sid] for sid, _ in STEP_ORDER if sid in self.steps],
            "report": self.report,
            "error": self.error,
        }


class VisitManager:
    """Holds the current/last visit run and launches new ones.

    One run at a time (v1 is single-site). The worker thread updates a run's
    ``steps`` dict in place through the progress callback; the request thread only
    ever reads a shallow snapshot, so the two never mutate the same object field
    concurrently in a way that can tear (dict value replacement under the GIL).
    """

    def __init__(self) -> None:
        self._run: Optional[VisitRun] = None
        self._task: Optional[asyncio.Task[Any]] = None

    @property
    def current(self) -> Optional[VisitRun]:
        return self._run

    def is_active(self) -> bool:
        return self._run is not None and self._run.status == "running"

    def start(self, settings: Any, lookback_days: Optional[int]) -> VisitRun:
        run = VisitRun(
            run_id=uuid.uuid4().hex[:12],
            status="running",
            started_ts=int(time.time()),
            lookback_days=lookback_days,
        )
        # Seed the step list so the UI can render the pipeline before step one runs.
        for sid, label in STEP_ORDER:
            run.steps[sid] = VisitStep(id=sid, label=label).to_dict()
        self._run = run

        def _progress(step: VisitStep) -> None:
            run.steps[step.id] = step.to_dict()

        self._task = asyncio.create_task(self._execute(run, settings, lookback_days, _progress))
        return run

    async def _execute(
        self, run: VisitRun, settings: Any, lookback_days: Optional[int], progress: Any
    ) -> None:
        try:
            report = await run_in_threadpool(
                partial(run_visit, settings, lookback_days=lookback_days, progress=progress)
            )
            run.report = report.to_dict()
            run.status = "done"
        except Exception as exc:  # noqa: BLE001 - a failed visit is a reported state
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"[:300]
            _log.warning("visit run %s failed", run.run_id, exc_info=True)
        finally:
            run.finished_ts = int(time.time())


def _manager(request: Request) -> VisitManager:
    mgr = getattr(request.app.state, "visit_manager", None)
    if mgr is None:
        mgr = VisitManager()
        request.app.state.visit_manager = mgr
    return mgr


class StartVisitBody(BaseModel):
    """Body for ``POST /api/visit``: how far back to analyze (optional)."""

    lookback_days: Optional[int] = Field(
        default=None, ge=1, le=_MAX_LOOKBACK_DAYS, description="history window in days"
    )


@router.post("/visit")
async def start_visit(request: Request, body: StartVisitBody | None = None) -> dict[str, Any]:
    """Kick a background visit run. 409 if one is already in flight."""
    mgr = _manager(request)
    if mgr.is_active():
        raise HTTPException(status_code=409, detail="a visit run is already in progress")
    lookback = body.lookback_days if body is not None else None
    run = mgr.start(request.app.state.settings, lookback)
    return run.snapshot()


@router.get("/visit")
async def get_visit(request: Request) -> dict[str, Any]:
    """The current/last visit run: status, live step list, and (once done) the report."""
    mgr = _manager(request)
    run = mgr.current
    if run is None:
        return {"status": "idle", "run_id": None, "steps": [], "report": None}
    return run.snapshot()


__all__ = ["router", "VisitManager"]
