"""Incidents router: the correlation surface (docs/ARCHITECTURE.md section 17).

An **incident** groups the confirmed open issues that share one root cause: one
member is the *root* (the thing to fix), the rest are *symptoms* that clear when
it clears. The correlation engine writes one incident row per ultimate root,
uniformly -- including a one-member "incident-of-one" for an issue it could not
attribute anywhere, which is load-bearing bookkeeping (it is what lets that
issue keep its identity if it later gains a symptom). "Incident" is reserved as
a presentation-tier word for a *genuine* group of 2+ members
(:meth:`~netadmin.store.repository.Repository.is_genuine_incident`, Gitea #21);
this router reads the engine's uniform rows and applies that filter:

* ``GET /api/incidents`` — genuine incidents only by default (severity-ranked,
  each with its root, a member count, and the plain-language summary): this is
  what the dashboard's "Active incidents" card leads with. Pass
  ``include_singletons=true`` to restore the uniform projection (every
  incident-of-one included too) -- the dashboard's "Needs attention" card uses
  this for its honest, all-open-work triage view.
* ``GET /api/incidents/{id}`` — the whole story: the root at top, the symptoms
  each with the correlation ``rule`` + human ``rationale`` that linked them, and
  two hooks pointing at the root issue — the ONE recommended fix (the root's fix
  plan) and the investigation entry point (narrate the incident by investigating
  its root). Issue lifecycle is untouched; incidents are a read-only projection.

Read-only; every value comes back through :class:`Repository` query methods (the
SQL lives in the store, section 4). ``async`` because the connection is loop-bound.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from netadmin.domain.types import Severity
from netadmin.issues.engine import IssueEngine
from netadmin.issues.suppression import row_is_suppressed
from netadmin.server.serialize import decode_json, entity_ref_map, get_store
from netadmin.store.repository import Repository

router = APIRouter(prefix="/api", tags=["incidents"])

# p1 is most severe. Lower rank sorts first.
_SEVERITY_RANK: dict[str, int] = {
    Severity.P1.value: 0,
    Severity.P2.value: 1,
    Severity.P3.value: 2,
}


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, len(_SEVERITY_RANK))


def _engine(request: Request, store: Repository) -> IssueEngine:
    """The shared issue engine, or an ephemeral one bound to this store.

    Mirrors :func:`netadmin.server.routers.issues._engine`: the running daemon's
    lifespan builds one engine (with the WebSocket broadcaster registered) and the
    bulk suppress/unsuppress routes reuse it so each member's ``suppressed`` event
    fans out on ``/ws``; router tests that never enter the lifespan get a throwaway
    engine bound to the same store, which still writes ``issue_events`` correctly.
    """
    engine = request.app.state.issue_engine
    if engine is not None:
        return engine
    from netadmin.issues.store_repository import StoreIssueRepository

    return IssueEngine(StoreIssueRepository(store))


class IncidentSuppressBody(BaseModel):
    """Body for ``POST /api/incidents/{id}/suppress``: park the whole incident's
    attention claim, optionally until ``until_ts`` (omit / null = until
    unsuppressed). Same shape and semantics as the per-issue suppress body."""

    until_ts: Optional[int] = Field(
        default=None, ge=0, description="epoch seconds to suppress until; null = indefinite"
    )


def _issue_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Serialise an ``issues`` row, decoding the JSON evidence blob."""
    data = dict(row)
    data["evidence"] = decode_json(data.get("evidence"), {})
    return data


def _root_ref(
    root_issue_id: int,
    issues_by_id: dict[int, sqlite3.Row],
    entity_refs: dict[int, dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """A compact card for an incident's root: the fix target the operator cares
    about. ``None`` only if the root issue vanished mid-pass (should not happen —
    the correlation engine keys the incident on a live root)."""
    row = issues_by_id.get(root_issue_id)
    if row is None:
        return None
    eid = row["entity_id"]
    return {
        "issue_id": int(row["id"]),
        "detector_key": row["detector_key"],
        "title": row["title"],
        "severity": row["severity"],
        "state": row["state"],
        "entity": entity_refs.get(int(eid)) if eid is not None else None,
    }


@router.get("/incidents")
async def list_incidents(
    request: Request,
    include_resolved: bool = Query(default=False),
    include_singletons: bool = Query(default=False),
) -> dict[str, Any]:
    """Genuine incidents, most-severe first (ties broken by most-recently-seen).

    Each card carries the root's fix-target ref, the member count (root included),
    and the correlation-generated summary line. ``include_resolved=true`` also
    returns resolved incidents (newest last), for history views. ``include_
    singletons=true`` restores the engine's uniform one-row-per-root projection
    (every incident-of-one included too); the default is genuine groups only
    (2+ members) -- see :meth:`Repository.is_genuine_incident`.
    """
    store = get_store(request)
    incidents = store.list_incidents(
        open_only=not include_resolved, genuine_only=not include_singletons
    )

    # Resolve every root issue + every root entity in two batched reads, not N.
    all_issues = {int(r["id"]): r for r in store.list_issues()}
    root_entity_ids = [
        all_issues[int(i["root_issue_id"])]["entity_id"]
        for i in incidents
        if int(i["root_issue_id"]) in all_issues
    ]
    entity_refs = entity_ref_map(store, root_entity_ids)
    counts = store.incident_member_counts([int(i["id"]) for i in incidents])

    # An incident is suppressed for attention purposes only when ALL its members
    # are (Gitea #49): a suppressed root with a live symptom keeps the incident in
    # the "Needs attention" surfaces, because the symptom is an unanswered ask. A
    # fully-suppressed incident drops out here, server-side (the compact list
    # payload can't carry member states for the client to derive it), and the
    # count of dropped incidents is disclosed so the shrink is never silent.
    now = int(time.time())

    def _all_members_suppressed(incident_id: int) -> bool:
        members = store.list_incident_members(incident_id)
        rows = [all_issues.get(int(m["issue_id"])) for m in members]
        rows = [r for r in rows if r is not None]
        return bool(rows) and all(row_is_suppressed(r, now) for r in rows)

    items = []
    suppressed_excluded = 0
    for inc in incidents:
        if not include_resolved and _all_members_suppressed(int(inc["id"])):
            suppressed_excluded += 1
            continue
        incident = dict(inc)
        member_count = counts.get(int(inc["id"]), 0)
        incident["member_count"] = member_count
        incident["symptom_count"] = max(0, member_count - 1)
        incident["root"] = _root_ref(int(inc["root_issue_id"]), all_issues, entity_refs)
        items.append(incident)

    # Severity-ranked (p1 first), then most-recently-seen; resolved incidents sink
    # below open ones so an "include_resolved" view still leads with live work.
    items.sort(
        key=lambda i: (
            0 if i["state"] != "resolved" else 1,
            _severity_rank(i["severity"]),
            -int(i["last_seen_ts"]),
        )
    )
    return {
        "incidents": items,
        "count": len(items),
        "suppressed_excluded": suppressed_excluded,
    }


@router.get("/incidents/{incident_id}")
async def get_incident(request: Request, incident_id: int) -> dict[str, Any]:
    """One incident in full: root at top, symptoms grouped, one recommended fix.

    Returns the root issue (full read model + resolved entity), the symptom issues
    each with the correlation ``rule`` + ``rationale`` that attributed them, and
    the two hooks pointing at the root issue: ``recommended_fix`` (fetch the root's
    fix plan — the incident's single fix is the root's fix) and ``investigation``
    (investigate the root to narrate the whole story). 404 if unknown.
    """
    store = get_store(request)
    row = store.get_incident(incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")

    members = store.list_incident_members(incident_id)  # root first, then symptoms
    member_issue_ids = [int(m["issue_id"]) for m in members]
    issues_by_id = {
        int(r["id"]): r for r in store.list_issues() if int(r["id"]) in set(member_issue_ids)
    }
    entity_ids = [issues_by_id[iid]["entity_id"] for iid in member_issue_ids if iid in issues_by_id]
    entity_refs = entity_ref_map(store, entity_ids)

    def _member(m: sqlite3.Row) -> Optional[dict[str, Any]]:
        issue_row = issues_by_id.get(int(m["issue_id"]))
        if issue_row is None:
            return None
        eid = issue_row["entity_id"]
        return {
            "issue": _issue_dict(issue_row),
            "entity": entity_refs.get(int(eid)) if eid is not None else None,
            "role": m["role"],
            "rule": m["rule"],
            "rationale": m["rationale"],
        }

    root_member: Optional[dict[str, Any]] = None
    symptoms: list[dict[str, Any]] = []
    for m in members:
        built = _member(m)
        if built is None:
            continue
        if m["role"] == "root":
            root_member = built
        else:
            symptoms.append(built)

    incident = dict(row)
    incident["member_count"] = len(members)
    incident["symptom_count"] = sum(1 for m in members if m["role"] != "root")

    root_issue_id = int(row["root_issue_id"])
    root_issue = issues_by_id.get(root_issue_id)
    return {
        "incident": incident,
        "root": root_member,
        "symptoms": symptoms,
        # The ONE recommended fix is the root's; the UI fetches its plan on demand.
        "recommended_fix": {
            "issue_id": root_issue_id,
            "detector_key": root_issue["detector_key"] if root_issue is not None else None,
            "fix_state": root_issue["fix_state"] if root_issue is not None else None,
        },
        # Investigate the root to narrate the whole story (section 10 + 17).
        "investigation": {"issue_id": root_issue_id},
    }


@router.post("/incidents/{incident_id}/suppress")
async def suppress_incident(
    request: Request, incident_id: int, body: IncidentSuppressBody
) -> dict[str, Any]:
    """Suppress a whole incident in one action: the root and every symptom (Gitea
    #50). Each member is suppressed *individually* — its own ``suppressed`` event,
    stamped ``source="incident"`` so the trail distinguishes a bulk mute from a
    per-issue one — because suppression lives on the issue row, not the incident
    projection. Measured impact is untouched, exactly as for the per-issue route:
    this parks attention (counts, alerts, HA sensors), never a measured number.

    Token-gated as a mutation (fans out on the WebSocket via the engine). 404 if
    the incident is unknown. Idempotent per member: re-suppressing an already-muted
    member simply restamps it.
    """
    store = get_store(request)
    if store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    engine = _engine(request, store)
    now = int(time.time())
    count = 0
    for member in store.list_incident_members(incident_id):
        transition = engine.suppress(
            int(member["issue_id"]), now, until_ts=body.until_ts, source="incident"
        )
        if transition is not None:
            count += 1
    return {"incident_id": incident_id, "count": count}


@router.post("/incidents/{incident_id}/unsuppress")
async def unsuppress_incident(request: Request, incident_id: int) -> dict[str, Any]:
    """Lift a bulk incident suppression: unsuppress the root and every symptom,
    each writing its own ``unsuppressed`` event (Gitea #50). Mirrors
    :func:`suppress_incident`. 404 if the incident is unknown."""
    store = get_store(request)
    if store.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    engine = _engine(request, store)
    now = int(time.time())
    count = 0
    for member in store.list_incident_members(incident_id):
        transition = engine.unsuppress(int(member["issue_id"]), now)
        if transition is not None:
            count += 1
    return {"incident_id": incident_id, "count": count}


__all__ = ["router"]
