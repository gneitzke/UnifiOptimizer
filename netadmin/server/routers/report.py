"""Report router: ``GET /api/report`` -- the full network-assessment model.

The one read surface over the report assembler (``docs/ARCHITECTURE.md`` 19, an
open read per 18.1). It resolves the window, calls
:func:`netadmin.report.build_report` (which does every query and computation), and
serialises the returned model. No number is computed here and none in the UI --
the router is the thin edge the spec's "no false data" gate depends on: it renders
what the assembler returns.

Read-only by construction: it calls only the assembler (which uses ``Repository``
read methods) and ``dataclasses.asdict``. No SQL here (section 4); no writes.

``async`` deliberately: the store's SQLite connection is bound to the event-loop
thread (one process, shared loop -- section 3), so it is read on that thread.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from netadmin.report import build_report, report_to_dict
from netadmin.report.assembler import DEFAULT_WINDOW_S, MAX_WINDOW_S, MIN_WINDOW_S
from netadmin.server.serialize import get_store

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/report")
async def get_report(
    request: Request,
    window_s: Optional[int] = Query(default=None, ge=MIN_WINDOW_S, le=MAX_WINDOW_S),
) -> dict[str, Any]:
    """The full report model over ``[now - window_s, now)`` (default 7 days).

    ``window_s`` overrides the assessment window, clamped to ``[1 h, ~13 months]``.
    Every section is built from real repository queries; a section with no data
    returns an honest empty (``None`` / ``[]`` / a stated "no data"), never a
    fabricated value.
    """
    store = get_store(request)
    settings = getattr(request.app.state, "settings", None)
    span = int(window_s) if window_s is not None else DEFAULT_WINDOW_S
    model = build_report(store, settings, window_s=span)
    return report_to_dict(model)


__all__ = ["router"]
