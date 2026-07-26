"""Inventory router: devices and clients with per-entity rollups (section 12).

The device/client pages live "at last" in the issue-centric model (section 12):
the surface leads with issues and SLE, and inventory is where an operator drills
into one box's state history, current metrics, and open/resolved issues.

* ``GET /api/inventory/devices`` — aps / switches / gateways, each with its
  current state (firmware, up/down...), current metric values, and open-issue
  counts, so the list can badge severity without a second call.
* ``GET /api/inventory/devices/{id}`` — one device in full: meta, state-change
  history, child ports/radios (each with their own current metrics), and both
  open and resolved issues.
* ``GET /api/inventory/clients`` + ``/{id}`` — the same shape for clients, with
  the client's recent events (roams, disconnects) as its journey.

Read-only; all rollups come back through :class:`Repository` helpers (the joins
live in the store, section 4). ``async`` because the connection is loop-bound.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Sequence

from fastapi import APIRouter, HTTPException, Query, Request

from netadmin.analytics.offenders import (
    CLIENT_ENTITY_TYPES,
    DEVICE_ENTITY_TYPES,
    load_offender_weights,
    rank_offenders,
)
from netadmin.domain.types import EntityType
from netadmin.server.serialize import decode_json, entity_ref, entity_ref_map, get_store
from netadmin.store.repository import Repository

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# The offender leaderboards live at ``/api/devices/offenders`` and
# ``/api/clients/offenders`` (section 17), one prefix up from ``/api/inventory``,
# so they ride a second router mounted alongside this one.
offenders_router = APIRouter(prefix="/api", tags=["offenders"])

# Offender-window guard rails: default look-back and the hard cap that keeps a
# stray query from scanning the whole retained history.
_DEFAULT_OFFENDER_WINDOW_S = 86_400  # 24 h
_MAX_OFFENDER_WINDOW_S = 400 * 86_400  # ~13 months (daily rollups are kept forever)
_DEFAULT_OFFENDER_TOP_N = 10
_MAX_OFFENDER_TOP_N = 100

# The entity types the "devices" surface covers (clients are their own surface).
_DEVICE_TYPES = (EntityType.AP, EntityType.SWITCH, EntityType.GATEWAY)

# Structural children shown on a device detail: its ports and radios. A client
# roamed onto an AP is parented to it too, but it is not part of the device's
# hardware and belongs on the clients surface, so it is excluded here.
_CHILD_TYPES = {EntityType.PORT.value, EntityType.RADIO.value}

# Recent events shown on a client's journey / a device's activity.
_JOURNEY_LIMIT = 100


def _base(row: sqlite3.Row) -> dict[str, Any]:
    """The common identity block for an entity row (name falls back to MAC)."""
    name = row["name"]
    return {
        "entity_id": int(row["entity_id"]),
        "native_id": row["native_id"],
        "name": name if name else row["native_id"],
        "type": row["entity_type"],
        "model": row["model"],
        "parent_id": row["parent_id"],
        "first_seen_ts": row["first_seen_ts"],
        "last_seen_ts": row["last_seen_ts"],
        "meta": decode_json(row["meta"], {}),
    }


def _rollup(
    row: sqlite3.Row,
    counts: dict[int, dict[str, int]],
    states: dict[int, dict[str, Any]],
    samples: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """One entity's list-row rollup: identity + current state + metrics + issues.

    States, metrics, and issue counts all arrive pre-batched (one query each over
    the whole id set); the rollup is a pure map lookup so a large site never fans
    a list scan into 2N synchronous, loop-blocking reads.
    """
    eid = int(row["entity_id"])
    data = _base(row)
    data["state"] = states.get(eid, {})
    data["metrics"] = samples.get(eid, [])
    data["issue_counts"] = counts.get(eid, {"p1": 0, "p2": 0, "p3": 0, "total": 0})
    return data


def _issue_row(row: sqlite3.Row) -> dict[str, Any]:
    """Serialise an ``issues`` row for a detail page (evidence decoded)."""
    data = dict(row)
    data["evidence"] = decode_json(data.get("evidence"), {})
    return data


def _detail(store: Repository, row: sqlite3.Row) -> dict[str, Any]:
    """Shared device/client detail: meta, state history, children, issues."""
    eid = int(row["entity_id"])
    data = _base(row)
    data["state"] = store.current_states(eid)
    data["metrics"] = store.latest_samples(eid)

    history = store.state_history(eid, limit=200)
    data["state_changes"] = [dict(h) for h in history]

    child_rows = [c for c in store.children(eid) if c["entity_type"] in _CHILD_TYPES]
    child_ids = [int(c["entity_id"]) for c in child_rows]
    child_states = store.current_states_bulk(child_ids)
    child_samples = store.latest_samples_bulk(child_ids)
    data["children"] = [
        {
            **_base(c),
            "state": child_states.get(int(c["entity_id"]), {}),
            "metrics": child_samples.get(int(c["entity_id"]), []),
        }
        for c in child_rows
    ]

    issues = store.list_issues(entity_id=eid)
    data["issues_open"] = [_issue_row(i) for i in issues if i["state"] != "resolved"]
    data["issues_resolved"] = [_issue_row(i) for i in issues if i["state"] == "resolved"]
    return data


@router.get("/devices")
async def list_devices(request: Request) -> dict[str, Any]:
    """All infrastructure devices (aps / switches / gateways) with rollups."""
    store = get_store(request)
    counts = store.open_issue_counts()
    rows: list[sqlite3.Row] = []
    for etype in _DEVICE_TYPES:
        rows.extend(store.list_entities(etype))
    ids = [int(r["entity_id"]) for r in rows]
    states = store.current_states_bulk(ids)
    samples = store.latest_samples_bulk(ids)
    devices = [_rollup(row, counts, states, samples) for row in rows]
    return {"devices": devices, "count": len(devices)}


@router.get("/devices/{entity_id}")
async def get_device(request: Request, entity_id: int) -> dict[str, Any]:
    """One device in full. 404 if unknown or not an infrastructure device."""
    store = get_store(request)
    row = store.get_entity(entity_id)
    if row is None or row["entity_type"] not in {t.value for t in _DEVICE_TYPES}:
        raise HTTPException(status_code=404, detail=f"device {entity_id} not found")
    return {"device": _detail(store, row)}


@router.get("/clients")
async def list_clients(request: Request) -> dict[str, Any]:
    """All clients with rollups (current state + metrics + open-issue counts)."""
    store = get_store(request)
    counts = store.open_issue_counts()
    rows = list(store.list_entities(EntityType.CLIENT))
    ids = [int(r["entity_id"]) for r in rows]
    states = store.current_states_bulk(ids)
    samples = store.latest_samples_bulk(ids)
    clients = [_rollup(row, counts, states, samples) for row in rows]
    return {"clients": clients, "count": len(clients)}


@router.get("/clients/{entity_id}")
async def get_client(request: Request, entity_id: int) -> dict[str, Any]:
    """One client in full, plus its recent events as a journey. 404 if unknown."""
    store = get_store(request)
    row = store.get_entity(entity_id)
    if row is None or row["entity_type"] != EntityType.CLIENT.value:
        raise HTTPException(status_code=404, detail=f"client {entity_id} not found")

    detail = _detail(store, row)
    events = store.query_events(entity_id=entity_id, limit=_JOURNEY_LIMIT)
    ref_ids = {int(e["related_entity_id"]) for e in events if e["related_entity_id"] is not None}
    refs = entity_ref_map(store, ref_ids)
    detail["journey"] = [
        {
            "id": int(e["id"]),
            "ts": int(e["ts"]),
            "key": e["key"],
            "msg": e["msg"],
            "related_entity": (
                refs.get(int(e["related_entity_id"]))
                if e["related_entity_id"] is not None
                else None
            ),
            "data": decode_json(e["data"], {}),
        }
        for e in events
    ]
    detail["current_ap"] = (
        entity_ref(store.get_entity(row["parent_id"])) if row["parent_id"] else None
    )
    return {"client": detail}


def _offenders_response(
    request: Request, entity_types: Sequence[str], window_s: int, top_n: int
) -> dict[str, Any]:
    """Rank offenders of ``entity_types`` and resolve the ranked ids to names.

    Shared by both offender endpoints: clamp the window to ``[now - window, now]``,
    call the pure ranking (:func:`rank_offenders`) over the store, then batch-
    resolve the ranked entity ids to compact name refs (section 12: the UI shows
    names, not numeric ids). Read-only; the ranking is three repository GROUP BYs.
    """
    store = get_store(request)
    settings = getattr(request.app.state, "settings", None)
    now = int(time.time())
    start_ts = now - window_s

    scores = rank_offenders(
        store,
        entity_types,
        start_ts,
        now,
        top_n=top_n,
        settings=settings,
    )
    refs = entity_ref_map(store, [s.entity_id for s in scores])
    offenders = [{**s.as_dict(), "entity": refs.get(s.entity_id)} for s in scores]
    return {
        "start_ts": start_ts,
        "end_ts": now,
        "window_s": window_s,
        "weights": load_offender_weights(settings),
        "count": len(offenders),
        "offenders": offenders,
    }


@offenders_router.get("/devices/offenders")
async def device_offenders(
    request: Request,
    window_s: int = Query(default=_DEFAULT_OFFENDER_WINDOW_S, ge=1, le=_MAX_OFFENDER_WINDOW_S),
    top_n: int = Query(default=_DEFAULT_OFFENDER_TOP_N, ge=1, le=_MAX_OFFENDER_TOP_N),
) -> dict[str, Any]:
    """Top problem devices (ap / switch / gateway) by composite burden (section 17).

    Ranked by failed SLE client-minutes attributed to the device, its open issues
    weighted by severity, and its disconnect/roam event volume over the last
    ``window_s`` seconds (default 24 h). ``top_n`` caps the leaderboard. Each entry
    carries the weighted ``score``, the raw per-channel components, and the device's
    resolved name.
    """
    return _offenders_response(request, DEVICE_ENTITY_TYPES, window_s, top_n)


@offenders_router.get("/clients/offenders")
async def client_offenders(
    request: Request,
    window_s: int = Query(default=_DEFAULT_OFFENDER_WINDOW_S, ge=1, le=_MAX_OFFENDER_WINDOW_S),
    top_n: int = Query(default=_DEFAULT_OFFENDER_TOP_N, ge=1, le=_MAX_OFFENDER_TOP_N),
) -> dict[str, Any]:
    """Top problem clients by composite burden (section 17).

    Same composite as :func:`device_offenders`, ranked over client entities:
    dominated in practice by disconnect/roam churn and the client's own open
    issues, since failed SLE minutes attribute to infrastructure, not clients.
    """
    return _offenders_response(request, CLIENT_ENTITY_TYPES, window_s, top_n)


__all__ = ["router", "offenders_router"]
