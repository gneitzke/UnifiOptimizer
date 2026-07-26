"""Issues router: the core surface — list, detail, and operator actions.

The issue list and detail are the heart of the UI (ARCHITECTURE.md 12). This
router serves:

* ``GET /api/issues`` — list, filterable by ``state`` / ``severity`` /
  ``entity_id``, with each issue's owning entity resolved to a name ref.
* ``GET /api/issues/{id}`` — one issue, its full ``issue_events`` lifecycle trail
  (section 7), its decoded evidence, and the confounders the detector checked
  (folded into evidence at detection time, surfaced here as a first-class list).
* ``POST /api/issues/{id}/ack`` and ``/snooze`` — the two operator mutations. They
  go **through the issue engine** so each writes an ``issue_events`` row and fans
  out on the WebSocket exactly like an engine-driven transition; they mutate only
  our own database (never the controller), setting mute flags without touching
  evaluation (section 7).

Reads call only :class:`Repository` query methods; the two writes call the engine,
which owns persistence through the store. No SQL here (section 4). Handlers are
``async``: the SQLite connection is loop-bound (section 3).
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from netadmin.domain.types import IssueState, Severity
from netadmin.issues.engine import IssueEngine
from netadmin.llm import service as investigations
from netadmin.llm.provider import ProviderError, ProviderUnavailableError, available_providers
from netadmin.server.auth import extract_bearer, token_matches
from netadmin.server.serialize import decode_json, entity_ref_map, get_store
from netadmin.store.repository import Repository

router = APIRouter(prefix="/api", tags=["issues"])


def _engine(request: Request, store: Repository) -> IssueEngine:
    """The shared issue engine, or an ephemeral one bound to this store.

    In the running daemon the lifespan builds one engine (with the WebSocket
    broadcaster registered as a callback) and stores it on the app; ack/snooze
    reuse it so their events fan out on ``/ws``. In router tests that never enter
    the lifespan the engine is absent, so we bind a throwaway one to the same
    store — the mutation still writes ``issue_events`` correctly, just without a
    live socket to notify.
    """
    engine = request.app.state.issue_engine
    if engine is not None:
        return engine
    from netadmin.issues.store_repository import StoreIssueRepository

    return IssueEngine(StoreIssueRepository(store))


def _issue_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Serialise an ``issues`` row, decoding the JSON evidence blob."""
    data = dict(row)
    data["evidence"] = decode_json(data.get("evidence"), {})
    return data


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Serialise an ``issue_events`` row, decoding the JSON detail blob."""
    data = dict(row)
    data["detail"] = decode_json(data.get("detail"), {})
    return data


class SnoozeBody(BaseModel):
    """Body for ``POST /api/issues/{id}/snooze``: mute until this epoch second."""

    until_ts: int = Field(..., ge=0, description="epoch seconds to snooze until")


class InvestigateBody(BaseModel):
    """Body for ``POST /api/issues/{id}/investigate``: which provider to run."""

    provider: str = Field("manual", description="manual | copilot | anthropic")


class ImportResponseBody(BaseModel):
    """Body for ``.../investigations/import``: the completed response markdown."""

    text: str = Field(..., min_length=1, description="the finished investigation response")


def _investigation_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    """Serialise an ``investigations`` row (all columns, JSON-safe as-is)."""
    return None if row is None else dict(row)


@router.get("/issues")
async def list_issues(
    request: Request,
    state: Optional[IssueState] = None,
    severity: Optional[Severity] = None,
    entity_id: Optional[int] = None,
) -> dict[str, Any]:
    """List issues, most-recently-seen first, optionally filtered.

    ``state`` / ``severity`` are enum-validated by FastAPI (bad value -> 422).
    Each issue's owning entity is resolved to a ``{entity_id, name, type, ...}``
    ref so the list can show names, not numeric ids.
    """
    store = get_store(request)
    rows = store.list_issues(
        state=state.value if state else None,
        severity=severity.value if severity else None,
        entity_id=entity_id,
    )
    refs = entity_ref_map(store, [r["entity_id"] for r in rows])
    # Incident membership (section 17) is a join on the read model, never a stored
    # column — so issue lifecycle stays untouched. One batched query for the set.
    incidents = store.incident_brief_for_issues([r["id"] for r in rows])
    issues = []
    for r in rows:
        item = _issue_dict(r)
        eid = r["entity_id"]
        item["entity"] = refs.get(int(eid)) if eid is not None else None
        inc = incidents.get(int(r["id"]))
        item["incident_id"] = int(inc["incident_id"]) if inc is not None else None
        item["incident_role"] = inc["incident_role"] if inc is not None else None
        issues.append(item)
    return {"issues": issues, "count": len(issues)}


@router.get("/issues/{issue_id}")
async def get_issue(request: Request, issue_id: int) -> dict[str, Any]:
    """One issue plus its lifecycle trail, evidence, and confounders. 404 if unknown."""
    store = get_store(request)
    row = store.get_issue(issue_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    issue = _issue_dict(row)
    evidence = issue["evidence"]
    confounders = evidence.get("confounders_checked", []) if isinstance(evidence, dict) else []
    events = store.list_issue_events(issue_id)
    eid = row["entity_id"]
    entity = None
    if eid is not None:
        entity = entity_ref_map(store, [eid]).get(int(eid))
    # The incident this issue is part of (section 17), for the "Part of:" link on
    # the detail page. A join on the read model — issue lifecycle is untouched.
    inc = store.incident_brief_for_issues([issue_id]).get(issue_id)
    incident = (
        {
            "id": int(inc["incident_id"]),
            "role": inc["incident_role"],
            "title": inc["incident_title"],
            "severity": inc["incident_severity"],
        }
        if inc is not None
        else None
    )
    issue["incident_id"] = incident["id"] if incident else None
    issue["incident_role"] = incident["role"] if incident else None
    return {
        "issue": issue,
        "entity": entity,
        "evidence": evidence,
        "confounders": list(confounders),
        "events": [_event_dict(e) for e in events],
        "incident": incident,
    }


@router.post("/issues/{issue_id}/ack")
async def ack_issue(request: Request, issue_id: int) -> dict[str, Any]:
    """Acknowledge an issue (mutes notifications only). 404 if unknown."""
    store = get_store(request)
    engine = _engine(request, store)
    transition = engine.ack(issue_id, int(time.time()))
    if transition is None:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    return {"issue": _issue_dict(store.get_issue(issue_id))}


@router.post("/issues/{issue_id}/snooze")
async def snooze_issue(request: Request, issue_id: int, body: SnoozeBody) -> dict[str, Any]:
    """Snooze notifications until ``until_ts`` (evaluation untouched). 404 if unknown."""
    store = get_store(request)
    engine = _engine(request, store)
    transition = engine.snooze(issue_id, body.until_ts, int(time.time()))
    if transition is None:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    return {"issue": _issue_dict(store.get_issue(issue_id))}


# --------------------------------------------------------------------------- #
# LLM investigator (ARCHITECTURE.md section 10)
#
# The dossier builder + providers live in ``netadmin.llm``; this router only
# exposes them. Every path here writes solely to our own database and never
# mutates the controller. The provider network call (copilot/anthropic) runs in a
# thread executor so the loop-bound SQLite connection is only ever touched on the
# event-loop thread (section 3); the manual provider does local file IO inline.
# --------------------------------------------------------------------------- #


def _require_token_for_provider(request: Request, provider: str) -> None:
    """401 unless ``provider`` is ``manual`` or the bearer token matches.

    The investigate route is left OPEN in :class:`~netadmin.server.auth.
    ApiTokenAuthMiddleware` because the token decision depends on the request
    body, which the middleware never parses. This is where that decision
    actually happens: ``manual`` only writes a local markdown dossier (no
    network call, no cost) and never needs the token; ``copilot`` and
    ``anthropic`` do, checked exactly like the middleware would -- an unset
    token means open access (the same posture as every other route), a
    configured one requires a matching ``Authorization: Bearer`` header,
    compared constant-time via :func:`token_matches`.
    """
    if provider == "manual":
        return
    configured = request.app.state.settings.api_token
    if configured is None:
        return
    supplied = extract_bearer(request.headers.get("authorization"))
    if not token_matches(supplied, configured):
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/issues/investigate/providers")
async def list_investigation_providers() -> dict[str, Any]:
    """Which investigator providers are usable here (the UI picker reads this).

    A two-segment path so it never collides with ``/issues/{issue_id}`` (int
    converter). Reports availability only — never the value of any API key.
    """
    return {"providers": available_providers()}


@router.get("/issues/{issue_id}/investigations")
async def list_issue_investigations(request: Request, issue_id: int) -> dict[str, Any]:
    """List an issue's investigations, oldest-first (the rendered thread). 404 if unknown."""
    store = get_store(request)
    if store.get_issue(issue_id) is None:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    rows = store.list_investigations(issue_id)
    return {"investigations": [_investigation_dict(r) for r in rows], "count": len(rows)}


@router.post("/issues/{issue_id}/investigate")
async def investigate_issue(
    request: Request, issue_id: int, body: InvestigateBody
) -> dict[str, Any]:
    """Compile the dossier and run the chosen provider.

    ``manual`` writes the dossier and returns it *pending* (import the answer
    later); ``copilot`` / ``anthropic`` run in a thread executor and attach the
    answer. 404 for an unknown issue, 400 for an unavailable provider, 502 for a
    provider runtime failure.

    This route is OPEN in the auth middleware (it cannot see ``body.provider``
    before the body is parsed), so the token is enforced right here, first: 401
    for ``copilot``/``anthropic`` without a valid token when one is configured;
    ``manual`` never needs it (see :func:`_require_token_for_provider`).
    """
    _require_token_for_provider(request, body.provider)
    store = get_store(request)
    engine = _engine(request, store)
    try:
        prepared = investigations.start_investigation(store, engine, issue_id, body.provider)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    outcome = prepared.outcome
    if prepared.run is not None:
        try:
            text = await run_in_threadpool(prepared.run)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        if text is not None:
            outcome = investigations.complete_investigation(store, engine, outcome, text)

    return {"investigation": _investigation_dict(store.get_investigation(outcome.investigation_id))}


@router.post("/issues/{issue_id}/investigations/import")
async def import_issue_investigation(
    request: Request, issue_id: int, body: ImportResponseBody
) -> dict[str, Any]:
    """Attach a completed response to the issue's pending investigation. 404 if unknown."""
    store = get_store(request)
    engine = _engine(request, store)
    try:
        outcome = investigations.import_response(store, engine, issue_id, body.text)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"issue {issue_id} not found")
    return {"investigation": _investigation_dict(store.get_investigation(outcome.investigation_id))}


__all__ = ["router"]
