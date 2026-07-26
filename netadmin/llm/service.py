"""Investigation orchestration: dossier → provider → persistence → trail.

The one place the CLI (`netadmin investigate`) and the API (`POST
/api/issues/{id}/investigate`) share, so neither re-implements the lifecycle:

1. build the dossier (:mod:`netadmin.llm.dossier`),
2. persist a ``pending`` ``investigations`` row and write an ``investigated``
   ``issue_events`` entry through the issue engine (so it fans out on the
   WebSocket exactly like any other transition),
3. run the chosen provider — inline for the non-blocking ``manual`` provider,
   deferred for the blocking network providers so the API can run them off the
   event loop,
4. attach the response and flip the row to ``answered`` when one arrives.

The persistence steps only ever touch our own database; nothing here mutates the
controller. The provider split (``start`` / ``complete``) exists precisely so the
async API keeps every SQLite call on the loop thread and runs only the network
call in a thread executor (the store connection is loop-bound, section 3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from netadmin.detect.catalog import DEFAULT_CATALOG, Catalog
from netadmin.issues.engine import IssueEngine
from netadmin.llm.dossier import build_dossier
from netadmin.llm.provider import build_provider
from netadmin.store.repository import Repository

__all__ = [
    "InvestigationOutcome",
    "PreparedInvestigation",
    "start_investigation",
    "complete_investigation",
    "run_investigation",
    "import_response",
]


@dataclass
class InvestigationOutcome:
    """The persisted result of one investigation, for the CLI/API to report."""

    investigation_id: int
    issue_id: int
    provider: str
    status: str  # pending | answered
    dossier_md: str
    response_md: Optional[str] = None
    dossier_path: Optional[str] = None  # manual provider's written file


@dataclass
class PreparedInvestigation:
    """A started (``pending``) investigation plus the deferred provider call.

    ``run`` is ``None`` when the investigation is already terminal for now — the
    manual provider (dossier written, awaiting import). For a blocking provider,
    ``run`` performs the network call; the caller runs it inline (CLI) or in a
    thread executor (API), then passes its result to :func:`complete_investigation`.
    """

    outcome: InvestigationOutcome
    run: Optional[Callable[[], Optional[str]]]


def _now(now: Optional[int]) -> int:
    return int(time.time()) if now is None else int(now)


def start_investigation(
    repo: Repository,
    engine: IssueEngine,
    issue_id: int,
    provider_name: str,
    *,
    now: Optional[int] = None,
    catalog: Catalog = DEFAULT_CATALOG,
    base_dir: Optional[Path] = None,
    trigger: Optional[str] = None,
) -> PreparedInvestigation:
    """Build the dossier, persist a pending row, and prepare the provider call.

    Raises ``KeyError`` if the issue is unknown, or
    :class:`~netadmin.llm.provider.ProviderUnavailableError` if the requested
    provider cannot run — both *before* any row is written.

    ``trigger`` labels *what asked for this* on the emitted ``investigated``
    event (``"auto"`` for the unattended path, section 21). It is omitted from
    the detail entirely when ``None``, so a human-clicked investigation's event
    payload is byte-identical to what it was before auto-investigation existed.
    """
    if repo.get_issue(issue_id) is None:
        raise KeyError(f"issue {issue_id} not found")

    ts = _now(now)
    # Resolve the provider first so an unavailable one fails cleanly with no
    # orphaned pending row.
    provider = build_provider(provider_name, issue_id=issue_id, ts=ts, base_dir=base_dir)
    dossier = build_dossier(issue_id, repo, catalog=catalog, now=ts)

    investigation_id = repo.insert_investigation(
        issue_id=issue_id, provider=provider.name, dossier_md=dossier, status="pending", ts=ts
    )
    detail: dict[str, object] = {
        "provider": provider.name,
        "status": "pending",
        "investigation_id": investigation_id,
    }
    if trigger is not None:
        detail["trigger"] = trigger
    engine.investigated(issue_id, ts, detail=detail)
    outcome = InvestigationOutcome(
        investigation_id=investigation_id,
        issue_id=issue_id,
        provider=provider.name,
        status="pending",
        dossier_md=dossier,
    )

    if not getattr(provider, "blocking", False):
        # Non-blocking (manual): the only work is a local file write — do it now.
        provider.investigate(dossier)
        path = getattr(provider, "output_path", None)
        outcome.dossier_path = str(path) if path is not None else None
        return PreparedInvestigation(outcome=outcome, run=None)

    return PreparedInvestigation(outcome=outcome, run=lambda: provider.investigate(dossier))


def complete_investigation(
    repo: Repository,
    engine: IssueEngine,
    outcome: InvestigationOutcome,
    response_md: str,
    *,
    now: Optional[int] = None,
    imported: bool = False,
    trigger: Optional[str] = None,
) -> InvestigationOutcome:
    """Attach a response, flip the row to ``answered``, and record the event.

    ``trigger`` carries the same label as :func:`start_investigation` so both
    halves of one unattended investigation are attributable in the trail.
    """
    ts = _now(now)
    repo.attach_investigation_response(outcome.investigation_id, response_md, status="answered")
    detail: dict[str, object] = {
        "provider": outcome.provider,
        "status": "answered",
        "investigation_id": outcome.investigation_id,
        "imported": imported,
    }
    if trigger is not None:
        detail["trigger"] = trigger
    engine.investigated(outcome.issue_id, ts, detail=detail)
    return replace(outcome, status="answered", response_md=response_md)


def run_investigation(
    repo: Repository,
    engine: IssueEngine,
    issue_id: int,
    provider_name: str,
    *,
    now: Optional[int] = None,
    catalog: Catalog = DEFAULT_CATALOG,
    base_dir: Optional[Path] = None,
) -> InvestigationOutcome:
    """Synchronous start-to-finish investigation (the CLI path).

    Runs a blocking provider inline. For the async API, call
    :func:`start_investigation` then run ``prepared.run`` in a thread executor and
    finish with :func:`complete_investigation`.
    """
    prepared = start_investigation(
        repo, engine, issue_id, provider_name, now=now, catalog=catalog, base_dir=base_dir
    )
    if prepared.run is None:
        return prepared.outcome
    text = prepared.run()
    if text is None:
        return prepared.outcome
    return complete_investigation(repo, engine, prepared.outcome, text, now=now)


def import_response(
    repo: Repository,
    engine: IssueEngine,
    issue_id: int,
    response_md: str,
    *,
    now: Optional[int] = None,
) -> InvestigationOutcome:
    """Attach an externally-produced response to an issue's investigation.

    Attaches to the newest still-``pending`` investigation for the issue (the
    manual round-trip). If none is pending, records a standalone ``answered``
    manual investigation so the response is never lost. Raises ``KeyError`` for an
    unknown issue.
    """
    if repo.get_issue(issue_id) is None:
        raise KeyError(f"issue {issue_id} not found")
    ts = _now(now)

    # list_investigations is ordered oldest-first; the last pending row is newest.
    pending = [r for r in repo.list_investigations(issue_id) if r["status"] == "pending"]
    if pending:
        target = pending[-1]
        outcome = InvestigationOutcome(
            investigation_id=int(target["id"]),
            issue_id=issue_id,
            provider=str(target["provider"]),
            status="pending",
            dossier_md=str(target["dossier_md"] or ""),
        )
        return complete_investigation(repo, engine, outcome, response_md, now=ts, imported=True)

    investigation_id = repo.insert_investigation(
        issue_id=issue_id,
        provider="manual",
        dossier_md="",
        status="answered",
        ts=ts,
        response_md=response_md,
    )
    engine.investigated(
        issue_id,
        ts,
        detail={
            "provider": "manual",
            "status": "answered",
            "investigation_id": investigation_id,
            "imported": True,
        },
    )
    return InvestigationOutcome(
        investigation_id=investigation_id,
        issue_id=issue_id,
        provider="manual",
        status="answered",
        dossier_md="",
        response_md=response_md,
    )
