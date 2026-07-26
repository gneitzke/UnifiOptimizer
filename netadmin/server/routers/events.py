"""Events router: ``GET /api/events`` — the normalized event feed for the UI.

Powers the dashboard's live ticker and an entity's journey timeline. Reads
through :meth:`Repository.query_events` (newest-first, hard-capped) and resolves
each event's subject + related entity to names, so the ticker shows "roam:
iPhone → ap-office", not two numeric ids. Read-only; no SQL here (section 4).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from netadmin.server.serialize import decode_json, entity_ref_map, get_store

router = APIRouter(prefix="/api", tags=["events"])

_MAX_LIMIT = 1_000
_DEFAULT_LIMIT = 200


@router.get("/events")
async def list_events(
    request: Request,
    since_ts: Optional[int] = Query(default=None, ge=0),
    keys: Optional[str] = Query(default=None, description="comma-separated event keys"),
    entity_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> dict[str, Any]:
    """Recent events, newest first, filtered by time / key set / entity.

    ``keys`` is a comma-separated list (``EVT_WU_Roam,EVT_SW_PoeOverload``); an
    empty or missing value matches all keys. The ``data`` JSON blob is decoded to
    an object and both the subject and related entity are resolved to name refs.
    """
    store = get_store(request)
    key_list = [k.strip() for k in keys.split(",")] if keys else None
    key_list = [k for k in (key_list or []) if k] or None

    rows = store.query_events(since_ts=since_ts, keys=key_list, entity_id=entity_id, limit=limit)

    ref_ids: set[int] = set()
    for r in rows:
        for col in ("entity_id", "related_entity_id"):
            if r[col] is not None:
                ref_ids.add(int(r[col]))
    refs = entity_ref_map(store, ref_ids)

    events = []
    for r in rows:
        eid = r["entity_id"]
        rid = r["related_entity_id"]
        events.append(
            {
                "id": int(r["id"]),
                "ts": int(r["ts"]),
                "key": r["key"],
                "msg": r["msg"],
                "native_id": r["native_id"],
                "entity": refs.get(int(eid)) if eid is not None else None,
                "related_entity": refs.get(int(rid)) if rid is not None else None,
                "data": decode_json(r["data"], {}),
            }
        )
    return {"events": events, "count": len(events)}


__all__ = ["router"]
