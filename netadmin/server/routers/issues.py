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

from netadmin.detect.catalog import DEFAULT_CATALOG
from netadmin.domain.types import IssueState, Severity
from netadmin.issues.engine import IssueEngine
from netadmin.llm import service as investigations
from netadmin.llm.provider import ProviderError, ProviderUnavailableError, available_providers
from netadmin.logging import get_logger
from netadmin.server.auth import extract_bearer, token_matches
from netadmin.server.serialize import decode_json, entity_ref_map, get_store
from netadmin.store.repository import (
    SLE_ATTRIBUTED_ENTITY_TYPES,
    SLE_DEVICE_AXIS_ENTITY_TYPES,
    IssueImpactMinutes,
    Repository,
)

router = APIRouter(prefix="/api", tags=["issues"])
_log = get_logger("server.routers.issues")

# How far back an issue's impact figure looks. Matches the offenders leaderboard
# window (``netadmin.analytics.offenders``) so the two surfaces read the same
# recent grief, and bounds the ``sle_minutes`` scan to a day of buckets however
# old the issue itself is.
IMPACT_WINDOW_S = 86_400

# How far back the recurrence count looks (Gitea #39). A week covers a site's
# whole rhythm -- weekday and weekend, the evening peak, the one day someone
# works from the far bedroom -- so a condition that only bites under one of those
# still reads as recurring, while last month's flapping does not colour a row
# that has been steady since.
STREAK_RESET_WINDOW_S = 7 * 86_400

# Clear-streak resets before an issue is called recurring. Two, not one: a single
# bounce is the K-streak debounce doing its job, and calling that recurring would
# put the label on almost every open row. Two means the streak has been killed
# twice and the condition is genuinely oscillating.
RECURRING_MIN_RESETS = 2


def _engine(request: Request, store: Repository) -> IssueEngine:
    """The shared issue engine, or an ephemeral one bound to this store.

    In the running daemon the lifespan builds one engine (with the WebSocket
    broadcaster registered as a callback) and stores it on the app; ack/snooze
    reuse it so their events fan out on ``/ws``. In router tests that never enter
    the lifespan the engine is absent, so we bind a throwaway one to the same
    store — the mutation still writes ``issue_events`` correctly, just without a
    live socket to notify.

    The read paths use it too, for one thing only: ``cfg.k_for`` — the clear
    threshold an issue's ``clear_streak`` is counting towards. Reading it off the
    live engine is what keeps the number the UI shows equal to the number the
    state machine will actually act on, including any per-detector override.
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


def _lifecycle(row: sqlite3.Row, engine: IssueEngine, resets: dict[int, int]) -> dict[str, Any]:
    """What the row's lifecycle position actually means, for the UI (Gitea #39).

    Two facts the issues list could not previously show, both derived, neither
    stored:

    * ``clear_k`` — how many consecutive clean checks resolve this issue. The row
      already carries ``clear_streak``; without its denominator "Resolving" is a
      spinner with no end in sight, and half a list of them reads as stuck. Taken
      from the live engine's config, so a per-detector ``detector_k`` override is
      respected here for free rather than hard-coded to the default 6.
    * ``streak_resets_7d`` / ``recurring`` — how many times the clear streak has
      been killed in the last week, and whether that is enough to say so. This is
      the fact that separates an issue clearing for the first time from one on
      its ninety-eighth bounce, which until now rendered identically.

    Deliberately **not** a lifecycle state: recurrence is an adjective on being
    open, not a different place in the state machine, and every reset is already
    written down in ``issue_events``. A state would duplicate that record and
    could drift from it; a derived label also labels historical rows correctly
    the moment it ships, with no backfill.
    """
    n = int(resets.get(int(row["id"]), 0))
    return {
        "clear_k": engine.cfg.k_for(row["detector_key"]),
        "streak_resets_7d": n,
        "recurring": n >= RECURRING_MIN_RESETS,
    }


def _impact_basis(ref: Optional[dict[str, Any]]) -> Optional[str]:
    """Which side of the SLE ledger an issue's entity sits on, or None.

    ``"own"`` for a client (the failed minutes are its own), ``"attributed"``
    for the infrastructure a failed minute gets pinned on. None means the SLE
    engine records nothing against this kind of entity at all -- a port, a WLAN,
    the site-wide RF pseudo-entity, or an issue with no entity — so there is no
    fail-minute figure to quote. That is deliberately **not** the same answer as
    zero, and the payload keeps them apart.
    """
    if ref is None:
        return None
    etype = ref.get("type")
    if etype not in SLE_ATTRIBUTED_ENTITY_TYPES:
        return None
    return "own" if etype == "client" else "attributed"


def _overlaps(span: Optional[tuple[int, int]], start: int, end: int) -> bool:
    """Whether the SLE engine judged this axis anywhere in ``[start, end)``."""
    if span is None:
        return False
    lo, hi = span
    return not (start > hi or end <= lo)


def _unmeasured_impact(basis: Optional[str], window_s: int) -> dict[str, Any]:
    """The impact block with nothing on either axis — every figure explicitly null."""
    return {
        "window_s": window_s,
        "basis": basis,
        "measured": False,
        "client": {
            "measured": False,
            "clients": None,
            "fail_minutes": None,
            "clients_in_window": None,
        },
        "infra": {"measured": False, "down_minutes": None, "entity_type": None},
    }


def _impact(
    row: sqlite3.Row,
    ref: Optional[dict[str, Any]],
    impacts: dict[int, IssueImpactMinutes],
    spans: dict[str, Optional[tuple[int, int]]],
    clients_in_window: int,
    window_start: int,
    window_end: int,
) -> dict[str, Any]:
    """One issue's impact block: what it has cost, on two axes that never merge.

    **Two quantities, never added** (Gitea #36). ``client`` is what real clients
    lived through — how many of them, and for how many minutes, out of the
    clients the SLE engine judged in the window. ``infra`` is how long the AP,
    switch or gateway itself was down, which is device-time and was nobody's
    client minute. Summing the two produced minutes no client ever experienced;
    the shape here makes that sum impossible to write by accident, because
    there is no combined field to reach for.

    ``measured`` is the other half of the point, and it is per axis. An axis can
    carry no number for three honest reasons -- the issue's entity is one the
    engine never records that axis against (:func:`_impact_basis` for the client
    axis, :data:`SLE_DEVICE_AXIS_ENTITY_TYPES` for infra), the issue's open
    window does not reach into the impact window at all (resolved before it
    opened), or the engine produced no judgement on that axis in the overlap
    (fresh install, daemon was down, backfill still running). In every one of
    those cases the figures are **null**, never ``0.0``: a zero means "the
    window was judged and nothing failed", and a UI that cannot tell the two
    apart will report an unmeasured outage as harmless.
    """
    basis = _impact_basis(ref)
    window_s = window_end - window_start
    if basis is None:
        return _unmeasured_impact(basis, window_s)
    # The issue's own life, clipped to the impact window — the same clipping
    # Repository.issue_impact_minutes applies to the buckets it sums.
    start = max(int(row["first_seen_ts"]), window_start)
    resolved = row["resolved_ts"]
    end = min(int(resolved), window_end) if resolved is not None else window_end
    if start >= end:
        return _unmeasured_impact(basis, window_s)

    etype = ref.get("type") if ref else None
    client_measured = _overlaps(spans.get("client"), start, end)
    # A radio can be *attributed* client minutes but the infra SLE never walks a
    # radio's state timeline, so "down 0 min" would be a claim we never measured.
    infra_measured = etype in SLE_DEVICE_AXIS_ENTITY_TYPES and _overlaps(
        spans.get("infra"), start, end
    )
    if not (client_measured or infra_measured):
        return _unmeasured_impact(basis, window_s)

    cost = impacts.get(int(row["id"]), IssueImpactMinutes())
    return {
        "window_s": window_s,
        "basis": basis,
        "measured": True,
        "client": {
            "measured": client_measured,
            "clients": cost.clients if client_measured else None,
            "fail_minutes": round(cost.client_fail_minutes, 1) if client_measured else None,
            "clients_in_window": clients_in_window if client_measured else None,
        },
        "infra": {
            "measured": infra_measured,
            "down_minutes": round(cost.infra_down_minutes, 1) if infra_measured else None,
            "entity_type": etype if infra_measured else None,
        },
    }


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


def _presentation(
    detector_key: str, evidence: Any, confounders: list[str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Evidence display order/labels and narrated confounder sentences (Gitea #18).

    Resolves the detector's catalog :class:`~netadmin.detect.catalog.Playbook`
    presentation metadata against this issue's own evidence:

    * ``evidence_layout`` — the subset of evidence keys the playbook has a label
      for, in the playbook's narrative order. The detail page renders these
      first, in this order, then falls back to its generic renderer (still in
      the evidence dict's own -- now narrative -- insertion order) for anything
      not listed. An empty/missing list is not an error: the UI's generic path
      covers a detector with no authored layout.
    * ``confounder_notes`` — a one-sentence narration per confounder key the
      playbook knows how to explain, keyed by that confounder string. A key
      absent here falls back to its bare humanized label client-side.

    A detector with no registered playbook, or a note callable that raises on
    an evidence shape it didn't expect, degrades silently (logged, not raised)
    rather than breaking the issue page — this is presentation polish, never
    load-bearing for the underlying evidence/confounders the response also
    carries verbatim.
    """
    layout: list[dict[str, Any]] = []
    notes: dict[str, str] = {}
    if not isinstance(evidence, dict):
        return layout, notes
    try:
        playbook = DEFAULT_CATALOG.get(detector_key).playbook
    except KeyError:
        playbook = None
    if playbook is None:
        return layout, notes
    for f in playbook.evidence_fields:
        if f.key in evidence:
            layout.append(
                {
                    "key": f.key,
                    "label": f.label,
                    "unit": f.unit,
                    "percent": f.percent,
                    "duration": f.duration,
                }
            )
    for key in confounders:
        note_fn = playbook.confounder_notes.get(key)
        if note_fn is None:
            continue
        try:
            note = note_fn(evidence)
        except Exception:  # noqa: BLE001 - a bad note must never break the issue page
            _log.warning(
                "confounder note raised", extra={"detector_key": detector_key, "confounder": key}
            )
            note = None
        if note:
            notes[key] = note
    return layout, notes


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
    ref so the list can show names, not numeric ids, and each carries an
    ``impact`` block — how many clients lost how many minutes, and separately
    how long the device itself was down, over the last :data:`IMPACT_WINDOW_S`,
    or an explicit "not measured" (see :func:`_impact`). That block is the
    column the list is read by, so it is computed here rather than left to the
    client to guess at.

    Each also carries a ``lifecycle`` block (see :func:`_lifecycle`): the clear
    threshold its ``clear_streak`` is counting towards, and how often it has come
    back this week.
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
    window_end = int(time.time())
    window_start = window_end - IMPACT_WINDOW_S
    impacts = store.issue_impact_minutes(
        [r["id"] for r in rows], start_ts=window_start, end_ts=window_end
    )
    spans = store.sle_minutes_axis_spans(window_start, window_end)
    clients_in_window = store.sle_measured_client_count(window_start, window_end)
    engine = _engine(request, store)
    resets = store.issue_streak_reset_counts(
        [r["id"] for r in rows], since_ts=window_end - STREAK_RESET_WINDOW_S
    )
    issues = []
    for r in rows:
        item = _issue_dict(r)
        eid = r["entity_id"]
        item["entity"] = refs.get(int(eid)) if eid is not None else None
        item["impact"] = _impact(
            r, item["entity"], impacts, spans, clients_in_window, window_start, window_end
        )
        item["lifecycle"] = _lifecycle(r, engine, resets)
        inc = incidents.get(int(r["id"]))
        item["incident_id"] = int(inc["incident_id"]) if inc is not None else None
        item["incident_role"] = inc["incident_role"] if inc is not None else None
        # "incident_brief" — only when the group is genuine (Gitea #21) — lets the
        # Issues list render one group row for a real incident, inline, without a
        # second fetch: the member issues (root + symptoms) are already in this
        # same response, so the client groups them by `incident_brief.id`.
        member_count = int(inc["incident_member_count"]) if inc is not None else 0
        item["incident_brief"] = (
            {
                "id": int(inc["incident_id"]),
                "title": inc["incident_title"],
                "summary": inc["incident_summary"],
                "severity": inc["incident_severity"],
                "symptom_count": member_count - 1,
            }
            if inc is not None and Repository.is_genuine_incident(member_count)
            else None
        )
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
    # `symptom_count` lets the client decide whether to render the line at all:
    # a genuine incident-of-one (Gitea #21) has `symptom_count == 0`, and the
    # page shows nothing rather than a confusing self-link to its own incident.
    inc = store.incident_brief_for_issues([issue_id]).get(issue_id)
    incident = (
        {
            "id": int(inc["incident_id"]),
            "role": inc["incident_role"],
            "title": inc["incident_title"],
            "severity": inc["incident_severity"],
            "symptom_count": int(inc["incident_member_count"]) - 1,
        }
        if inc is not None
        else None
    )
    issue["incident_id"] = incident["id"] if incident else None
    issue["incident_role"] = incident["role"] if incident else None
    # Also inside the issue object, not just beside it: the list's issues carry
    # their own `entity`, and anything reading an issue from either endpoint
    # (the impact figure below, the client's shared IssueRow type) would
    # otherwise have to know which of the two shapes it was handed.
    issue["entity"] = entity
    window_end = int(time.time())
    window_start = window_end - IMPACT_WINDOW_S
    issue["impact"] = _impact(
        row,
        entity,
        store.issue_impact_minutes([issue_id], start_ts=window_start, end_ts=window_end),
        store.sle_minutes_axis_spans(window_start, window_end),
        store.sle_measured_client_count(window_start, window_end),
        window_start,
        window_end,
    )
    issue["lifecycle"] = _lifecycle(
        row,
        _engine(request, store),
        store.issue_streak_reset_counts([issue_id], since_ts=window_end - STREAK_RESET_WINDOW_S),
    )
    evidence_layout, confounder_notes = _presentation(issue["detector_key"], evidence, confounders)
    return {
        "issue": issue,
        "entity": entity,
        "evidence": evidence,
        "evidence_layout": evidence_layout,
        "confounders": list(confounders),
        "confounder_notes": confounder_notes,
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
