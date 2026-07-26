"""Changes router: ``GET /api/changes`` — the config change ledger (with revert).

Every config write the fix engine applies is recorded in ``changes`` with the
before/after state for revert (ARCHITECTURE.md 12, section 9). This read surface
lists the ledger newest-first, decodes the before/after JSON, and resolves the
touched entity to a name ref. Read-only; the apply/revert *writes* land in Phase 4
(act). No SQL here (section 4).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from netadmin.server.serialize import decode_json, entity_ref_map, get_store

router = APIRouter(prefix="/api", tags=["changes"])


@router.get("/changes")
async def list_changes(
    request: Request,
    issue_id: Optional[int] = Query(default=None, ge=1),
    entity_id: Optional[int] = Query(default=None, ge=1),
) -> dict[str, Any]:
    """The change ledger, newest first, optionally scoped to an issue or entity.

    Each row carries the decoded ``before``/``after`` state, its ``status``
    (applied | reverted | failed), the ``reverted_ts`` when reverted, and the
    touched entity resolved to a name ref.
    """
    store = get_store(request)
    rows = store.list_changes(issue_id=issue_id, entity_id=entity_id)

    refs = entity_ref_map(store, [r["entity_id"] for r in rows])
    changes = []
    for r in rows:
        eid = r["entity_id"]
        changes.append(
            {
                "id": int(r["id"]),
                "ts": int(r["ts"]),
                "issue_id": r["issue_id"],
                "action": r["action"],
                "status": r["status"],
                "reverted_ts": r["reverted_ts"],
                "entity": refs.get(int(eid)) if eid is not None else None,
                "before": decode_json(r["before_json"], {}),
                "after": decode_json(r["after_json"], {}),
            }
        )
    return {"changes": changes, "count": len(changes)}


__all__ = ["router"]
