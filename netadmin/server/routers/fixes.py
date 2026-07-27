"""Fixes router: dry-run plan, gated apply, and revert (ARCHITECTURE.md 9 & 12).

The fix engine is the only component that can change the controller, so this
router is written so that *reading* a fix plan can never mutate anything and
*applying* one is gated at every step:

* ``GET /api/issues/{id}/fix-plan`` -- builds a **read-only** service (a device
  reader, never a writer) and renders the exact payloads a real apply would send,
  plus the confirm token, the verification state, and any changes already applied
  for this issue. A GET builds no writer and writes no row: viewing a fix is inert.
* ``POST /api/issues/{id}/fix/apply`` -- requires an explicit ``confirm: true`` and
  the ``confirm_token`` the human read from the dry-run. Only here is a
  :class:`RealControllerWriter` constructed, and the applier re-checks the token
  against the freshly re-read device, every precondition, and the min-RSSI rail
  before a single call goes out. A genuine apply arms the section-7 window.
* ``POST /api/issues/{id}/fix/revert`` -- replays a change's stored before-state.

The controller seams are taken from ``app.state.fix_seams`` when injected (tests,
fully offline) and otherwise built per-request from the configured credentials and
torn down after. The daemon lifespan wires no apply anywhere; the only apply
triggers are this endpoint and the CLI.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from netadmin.fixes.models import (
    ApplyResult,
    ConfirmTokenError,
    DryRunResult,
    FixError,
    MaxStepsExceeded,
    PreconditionDrift,
    SafetyViolation,
    WriterRequired,
)
from netadmin.fixes.service import FixSeams, FixService, IssueNotFound
from netadmin.issues.engine import IssueEngine
from netadmin.server.serialize import decode_json, get_store
from netadmin.store.repository import Repository

router = APIRouter(prefix="/api", tags=["fixes"])


def _engine(request: Request, store: Repository) -> IssueEngine:
    """The shared issue engine, or a throwaway one bound to this store.

    Mirrors the issues router: in the daemon the lifespan's engine (with the WS
    broadcaster attached) is reused so a ``fix_applied`` event fans out on ``/ws``;
    in router tests without a lifespan a store-bound engine still writes the trail.
    """
    engine = request.app.state.issue_engine
    if engine is not None:
        return engine
    from netadmin.issues.store_repository import StoreIssueRepository

    return IssueEngine(StoreIssueRepository(store))


def _seams(request: Request, *, for_apply: bool) -> tuple[FixSeams, bool]:
    """Injected fix seams (tests) or freshly built ones (daemon). Returns (seams, owns).

    ``owns`` is True when this request built the seams and must close them. An
    injected ``app.state.fix_seams`` is never closed here -- the test owns it.
    """
    injected = getattr(request.app.state, "fix_seams", None)
    if injected is not None:
        return injected, False
    from netadmin.fixes.service import build_fix_seams

    settings = request.app.state.settings
    try:
        return build_fix_seams(settings, for_apply=for_apply), True
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"controller not configured for fixes: {exc}",
        )


async def _close(seams: FixSeams, owns: bool) -> None:
    if owns and seams.closer is not None:
        try:
            await seams.closer()
        except Exception:  # noqa: BLE001 - teardown must never surface as a request error
            pass


def _service(request: Request, *, for_apply: bool) -> tuple[FixService, FixSeams, bool, Repository]:
    store = get_store(request)
    engine = _engine(request, store)
    seams, owns = _seams(request, for_apply=for_apply)
    service = FixService(
        store,
        engine,
        device_reader=seams.reader,
        writer=seams.writer if for_apply else None,
    )
    return service, seams, owns, store


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #
def _change_dict(row: sqlite3.Row, entities: Optional[Mapping[int, Any]] = None) -> dict[str, Any]:
    before = decode_json(row["before_json"], {})
    after = decode_json(row["after_json"], {})
    revertible = bool(isinstance(before, dict) and before.get("body") and before.get("endpoint"))
    # Which device this change touched. A joint band re-plan ledgers one row per
    # radio moved, so without this the UI renders N identical "Channel change /
    # applied" cards and the operator cannot tell which AP a Revert button belongs
    # to. The column was always stored; it was simply never serialized.
    entity_id = row["entity_id"] if "entity_id" in row.keys() else None
    entity = (entities or {}).get(int(entity_id)) if entity_id is not None else None
    return {
        "id": int(row["id"]),
        "ts": int(row["ts"]),
        "issue_id": row["issue_id"],
        "action": row["action"],
        "status": row["status"],
        "reverted_ts": row["reverted_ts"],
        "before": before,
        "after": after,
        "revertible": revertible,
        "entity_id": int(entity_id) if entity_id is not None else None,
        "entity_name": (entity["name"] if entity is not None else None),
        "entity_native_id": (entity["native_id"] if entity is not None else None),
    }


def _changes_for(store: Any, issue_id: int) -> list[dict[str, Any]]:
    """Ledger rows for an issue, each naming the device it touched."""
    rows = list(store.list_changes(issue_id=issue_id))
    ids = {int(r["entity_id"]) for r in rows if "entity_id" in r.keys() and r["entity_id"]}
    entities = store.entities_by_ids(ids) if ids else {}
    return [_change_dict(r, entities) for r in rows]


def _verification_dict(service: FixService, issue_id: int) -> dict[str, Any]:
    v = service.verification(issue_id)
    return {
        "status": v.status.value,
        "armed_ts": v.armed_ts,
        "window_end_ts": v.window_end_ts,
        "resolved_ts": v.resolved_ts,
    }


def _target_entry(payload: dict[str, Any], target_native_id: str) -> Optional[dict[str, Any]]:
    """The radio/port entry a step's payload changes, matched by native-id suffix."""
    suffix = target_native_id.rsplit(":", 1)[-1]
    for radio in payload.get("radio_table") or []:
        if radio.get("radio") == suffix:
            return radio
    try:
        idx = int(suffix)
    except (TypeError, ValueError):
        return None
    for port in payload.get("port_table") or []:
        try:
            if int(port.get("port_idx")) == idx:
                return port
        except (TypeError, ValueError):
            continue
    return None


def _step_diff(step: dict[str, Any]) -> dict[str, Any]:
    """Compact ``{attr: {before, after}}`` for a step, from its precondition + payload.

    ``before`` is the precondition's expected (current) value; ``after`` is the
    value the payload sets on the same target entry. A transient command (a
    power-cycle carries no ``radio_table``/``port_table``) yields no config diff.
    """
    expected = (step.get("precondition") or {}).get("expected") or {}
    entry = _target_entry(step.get("payload") or {}, str(step.get("target") or ""))
    diff: dict[str, Any] = {}
    for key, before in expected.items():
        after = entry.get(key) if entry is not None else None
        diff[key] = {"before": before, "after": after}
    return diff


def _enrich_step(step: dict[str, Any]) -> dict[str, Any]:
    """A rendered step plus a UI-friendly ``diff`` (kept alongside the raw payload)."""
    return {**step, "diff": _step_diff(step)}


def _plan_payload(
    dry: DryRunResult, service: FixService, store: Repository, issue_id: int
) -> dict[str, Any]:
    row = store.get_issue(issue_id)
    fix_state = row["fix_state"] if row is not None else None
    changes = _changes_for(store, issue_id)
    return {
        "issue_id": issue_id,
        "detector_key": dry.plan.detector_key,
        "title": dry.plan.title,
        "entity_native_id": dry.plan.entity_native_id,
        "advisory": dry.advisory,
        "manual_action_required": dry.manual_action_required,
        "confirm_token": dry.confirm_token,
        "device_count": dry.plan.device_count,
        "steps": [_enrich_step(s) for s in dry.rendered],
        "fix_state": fix_state,
        "verification": _verification_dict(service, issue_id),
        "changes": changes,
    }


def _apply_payload(
    result: ApplyResult, service: FixService, store: Repository, issue_id: int
) -> dict[str, Any]:
    row = store.get_issue(issue_id)
    return {
        "issue_id": issue_id,
        "applied": result.applied,
        "aborted_reason": result.aborted_reason,
        "change_ids": result.change_ids,
        "steps": [
            {
                "action": s.step.action.value,
                "status": s.status,
                "change_id": s.change_id,
                "status_code": s.write.status_code if s.write is not None else None,
                "error": s.error,
            }
            for s in result.steps
        ],
        "fix_state": row["fix_state"] if row is not None else None,
        "verification": _verification_dict(service, issue_id),
        "changes": _changes_for(store, issue_id),
    }


# --------------------------------------------------------------------------- #
# Bodies
# --------------------------------------------------------------------------- #
class ApplyBody(BaseModel):
    """Body for ``POST .../fix/apply``: the explicit confirmation + reviewed token."""

    confirm: bool = Field(..., description="must be true — the explicit human apply gate")
    confirm_token: str = Field(
        ..., min_length=1, description="the token from the dry-run the human reviewed"
    )


class RevertBody(BaseModel):
    """Body for ``POST .../fix/revert``: which ledger change to restore."""

    change_id: int = Field(..., ge=1, description="the changes-ledger row to revert")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/issues/{issue_id}/fix-history")
async def get_fix_history(request: Request, issue_id: int) -> dict[str, Any]:
    """Already-applied changes + verification for an issue. Store-only, ever.

    Unlike ``fix-plan`` this builds no reader and touches no device: ``changes``
    and ``verification`` are both pure ledger/engine reads, so a resolved issue's
    "what was applied, offer revert" can render the instant the issue page loads
    -- gitea #26 -- rather than forcing the operator through the live-dry-run
    gate meant for previewing a *new* remediation, just to see history the
    ledger already has. 404 for an unknown issue.
    """
    store = get_store(request)
    row = store.get_issue(issue_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    engine = _engine(request, store)
    service = FixService(store, engine)  # no device_reader/writer: DB reads only
    return {
        "issue_id": issue_id,
        "fix_state": row["fix_state"],
        "verification": _verification_dict(service, issue_id),
        "changes": _changes_for(store, issue_id),
    }


@router.get("/issues/{issue_id}/fix-plan")
async def get_fix_plan(request: Request, issue_id: int) -> dict[str, Any]:
    """The dry-run fix plan for an issue: exact payloads, token, verification.

    Read-only and side-effect-free: builds a reader-only service (no writer), sends
    nothing, and writes no row. 404 for an unknown issue, 422 when the issue has no
    fixable entity, 503 when the controller is unconfigured.
    """
    service, seams, owns, store = _service(request, for_apply=False)
    try:
        dry = await service.dry_run(issue_id)
    except IssueNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FixError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        await _close(seams, owns)
    return _plan_payload(dry, service, store, issue_id)


@router.post("/issues/{issue_id}/fix/apply")
async def apply_fix(request: Request, issue_id: int, body: ApplyBody) -> dict[str, Any]:
    """Apply an issue's fix. Requires ``confirm: true`` and the reviewed token.

    The whole applier gate runs here (token re-match against the re-read device,
    per-step precondition drift, the min-RSSI rail, the max-N guard). A confirm
    that is not exactly ``true`` is refused before any controller contact. On a
    genuine apply the section-7 verification window is armed.
    """
    if body.confirm is not True:
        raise HTTPException(status_code=400, detail="apply requires confirm: true")

    service, seams, owns, store = _service(request, for_apply=True)
    try:
        result = await service.apply(issue_id, confirm_token=body.confirm_token)
    except IssueNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ConfirmTokenError as exc:
        # The plan changed since the dry-run (or no matching token): re-review.
        raise HTTPException(
            status_code=409, detail=f"{exc} — re-open the fix plan and confirm again"
        )
    except PreconditionDrift as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (SafetyViolation, MaxStepsExceeded) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except WriterRequired as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FixError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        await _close(seams, owns)
    return _apply_payload(result, service, store, issue_id)


@router.post("/issues/{issue_id}/fix/revert")
async def revert_fix(request: Request, issue_id: int, body: RevertBody) -> dict[str, Any]:
    """Revert a change from this issue's ledger, restoring its before-state.

    404 for an unknown issue or a change that does not belong to it; 422 when the
    change is not revertible (a transient command stores no before-state) or was
    already reverted.
    """
    service, seams, owns, store = _service(request, for_apply=True)
    try:
        change = store.get_change(body.change_id)
        if change is None or change["issue_id"] != issue_id:
            raise HTTPException(
                status_code=404,
                detail=f"change {body.change_id} not found for issue {issue_id}",
            )
        try:
            await service.revert(body.change_id)
        except WriterRequired as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except FixError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    finally:
        await _close(seams, owns)
    updated = store.get_change(body.change_id)
    return {
        "change": _change_dict(updated) if updated is not None else None,
        "verification": _verification_dict(service, issue_id),
    }


__all__ = ["router"]
