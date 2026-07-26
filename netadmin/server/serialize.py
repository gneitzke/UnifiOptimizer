"""Shared JSON serialization + entity-name resolution for the API routers.

The routers are thin (ARCHITECTURE.md 12): they call :class:`Repository`
read methods and serialise rows. Several of them decode the same JSON blobs and
resolve the same ``entity_id -> name`` shape, so that logic lives here once
rather than being copy-pasted per router. Nothing here touches SQL (section 4);
it consumes rows the repository already returned.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Optional

from fastapi import HTTPException, Request

from netadmin.store.repository import Repository

__all__ = [
    "get_store",
    "decode_json",
    "entity_ref",
    "entity_ref_map",
]


def get_store(request: Request) -> Repository:
    """The process store, or a 503 when the daemon has not opened it yet."""
    store = request.app.state.store
    if store is None:
        raise HTTPException(status_code=503, detail="store not ready")
    return store


def decode_json(raw: Any, default: Any) -> Any:
    """Decode a JSON TEXT column, falling back to ``default`` on null/garbage."""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def entity_ref(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    """A compact ``{entity_id, name, type, native_id, model}`` reference, or None.

    The UI shows an entity's name, not its numeric id, everywhere it references
    one (issue owner, event subject, SLE offender). ``name`` falls back to the
    ``native_id`` (a MAC) when the controller never gave the entity a name.
    """
    if row is None:
        return None
    name = row["name"]
    native_id = row["native_id"]
    return {
        "entity_id": int(row["entity_id"]),
        "name": name if name else native_id,
        "type": row["entity_type"],
        "native_id": native_id,
        "model": row["model"],
    }


def entity_ref_map(store: Repository, ids: Iterable[Optional[int]]) -> dict[int, dict[str, Any]]:
    """Batch-resolve entity ids to their compact references in one query.

    Callers pass every id a payload references (issue owners, event subjects,
    offender ids); this resolves them all through one ``entities_by_ids`` join so
    a list endpoint does not do N per-row lookups.
    """
    wanted = [int(i) for i in ids if i is not None]
    if not wanted:
        return {}
    rows = store.entities_by_ids(wanted)
    return {eid: entity_ref(row) for eid, row in rows.items() if entity_ref(row) is not None}
