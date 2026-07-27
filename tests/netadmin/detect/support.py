"""Shared builders for the detector-framework tests (not collected by pytest).

Assembles the real production seam — a temp-DB :class:`Repository`, the
:class:`StoreIssueRepository` adapter, a real :class:`IssueEngine`, and the
:class:`DetectorEngine` — so framework behaviour (cadence routing, UNKNOWN vs
clear, the firewall, infra detectors) is exercised end-to-end rather than against
a mock. Baselines are faked (infra detectors never read them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from netadmin.detect.catalog import Catalog, CatalogEntry
from netadmin.detect.engine import DetectorEngine, EngineRunConfig
from netadmin.domain.entities import Entity, Finding
from netadmin.domain.types import Cadence, EntityType, Severity
from netadmin.issues.engine import IssueEngine
from netadmin.issues.models import EngineConfig
from netadmin.issues.store_repository import StoreIssueRepository
from netadmin.store.repository import Repository


class FakeBaselines:
    """A no-op baselines stub: every band is cold. Infra detectors ignore it."""

    def band(self, series_id: int, *, bucket: Optional[str] = None):  # noqa: D401
        return None

    def update_from_recent(self, now_ts: int) -> int:
        return 0


@dataclass
class Stack:
    """The wired stack a test drives."""

    repo: Repository
    issue_engine: IssueEngine
    detector_engine: DetectorEngine


def build_stack(
    repo: Repository,
    *,
    catalog: Optional[Catalog] = None,
    settings: Any = None,
    issue_config: Optional[EngineConfig] = None,
    run_config: Optional[EngineRunConfig] = None,
) -> Stack:
    """Wire a temp-DB repository into the full detection -> issue path."""
    issue_engine = IssueEngine(StoreIssueRepository(repo), config=issue_config or EngineConfig())
    detector_engine = DetectorEngine(
        repo=repo,
        issue_engine=issue_engine,
        baselines=FakeBaselines(),
        catalog=catalog,
        settings=settings,
        config=run_config,
    )
    return Stack(repo=repo, issue_engine=issue_engine, detector_engine=detector_engine)


class StubDetector:
    """A programmable detector for engine-behaviour tests.

    ``result_fn(ctx)`` returns a ``list[Finding]``, the ``UNKNOWN`` sentinel, or
    raises (to exercise the firewall). ``calls`` counts invocations so cadence
    routing can be asserted.
    """

    def __init__(
        self,
        key: str,
        cadence: Cadence,
        result_fn: Callable[[Any], Any],
        *,
        scope: EntityType = EntityType.PORT,
    ) -> None:
        self.key = key
        self.cadence = cadence
        self.scope = scope
        self._result_fn = result_fn
        self.calls = 0

    def evaluate(self, ctx: Any) -> Any:
        self.calls += 1
        return self._result_fn(ctx)


def entry(
    detector: Any,
    *,
    ceiling: Severity = Severity.P1,
    title: str = "stub",
) -> CatalogEntry:
    return CatalogEntry(detector=detector, severity_ceiling=ceiling, title_template=title)


def make_finding(
    key: str,
    *,
    native_id: str = "aa:bb:cc:00:00:01",
    entity_type: EntityType = EntityType.PORT,
    entity_id: Optional[int] = None,
    severity: Severity = Severity.P2,
    title: str = "stub finding",
    dims: Optional[dict[str, str]] = None,
    evidence: Optional[dict[str, Any]] = None,
) -> Finding:
    entity = Entity(
        entity_type=entity_type,
        native_id=native_id,
        site_id="default",
        entity_id=entity_id,
    )
    return Finding(
        detector_key=key,
        entity=entity,
        severity=severity,
        title=title,
        dims=dims or {},
        evidence=evidence or {},
    )


def seed_device(
    repo: Repository,
    *,
    native_id: str,
    entity_type: EntityType = EntityType.SWITCH,
    name: Optional[str] = None,
    state: Optional[str] = None,
    last_seen_ts: int,
    parent_id: Optional[int] = None,
    meta: Optional[dict[str, Any]] = None,
) -> int:
    """Upsert a device entity, optionally recording its ``state``. Returns the id."""
    eid = repo.upsert_entity(
        Entity(
            entity_type=entity_type,
            native_id=native_id,
            site_id="default",
            name=name,
            parent_id=parent_id,
            meta=meta or {},
        ),
        ts=last_seen_ts,
    )
    if state is not None:
        repo.record_state_change(eid, "state", state, ts=last_seen_ts)
    return eid


def seed_client(
    repo: Repository,
    *,
    native_id: str,
    last_seen_ts: int,
    parent_id: Optional[int] = None,
    name: Optional[str] = None,
) -> int:
    """Upsert a client entity last sighted at ``last_seen_ts``. Returns the id.

    ``last_seen_ts`` is the whole point: the detection engine reads client
    departure from it, so a test sets it to whenever the client was last on the
    air rather than to "now".
    """
    return repo.upsert_entity(
        Entity(
            entity_type=EntityType.CLIENT,
            native_id=native_id,
            site_id="default",
            name=name,
            parent_id=parent_id,
        ),
        ts=last_seen_ts,
    )


def seed_coverage(
    repo: Repository,
    *,
    job: str = "fast_device",
    now: int,
    window_s: int = 600,
    interval_s: int = 60,
    ok: bool = True,
) -> int:
    """Seed evenly-spaced live poll_runs across ``[now-window_s, now]``.

    Returns the number of rows written. With ``interval_s=60`` over 600 s this is
    ~10 rows, i.e. full coverage; pass ``ok=False`` to seed failures instead.
    """
    count = 0
    ts = now - window_s + interval_s
    while ts <= now:
        repo.record_poll_run(job=job, ok=ok, ts=ts)
        count += 1
        ts += interval_s
    return count
