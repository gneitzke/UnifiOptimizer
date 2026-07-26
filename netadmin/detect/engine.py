"""``DetectorEngine`` — cadence-tiered detector orchestration (section 6).

The engine turns a catalog of detectors into issue-lifecycle transitions. Each
pass, for one cadence tier:

1. builds a fresh :class:`~netadmin.detect.context.DetectorContext` at ``now``;
2. runs every registered detector of that tier behind a **per-detector firewall**
   (one broken detector never stops the pass — its failure is logged and counted);
3. splits results into three verdicts and routes them into the existing
   :class:`~netadmin.issues.engine.IssueEngine` in a single ``process_cycle``:

   * **findings** (a returned ``list[Finding]``) — FIRE, clamped to the catalog's
     severity ceiling;
   * **clear** — a detector that returned a list *without* re-firing an open issue
     of its own has implicitly cleared it (Prometheus-style absence-of-finding);
   * **UNKNOWN** (the :data:`UNKNOWN` sentinel) — the detector could not evaluate
     (a coverage gap): it advances **nothing**, in either direction, and is
     distinct from an empty list, which *is* a clear evaluation;
4. records the pass to ``poll_runs`` as ``job='detect_fast|window|daily'`` for
   observability, even if it raised.

The clear/UNKNOWN distinction is the crux: an empty ``[]`` says "I looked, the
problem is gone" (advance the clear streak); ``UNKNOWN`` says "I could not look"
(freeze). The engine derives clears by absence — it lists the open issues owned by
each detector and clears the ones the detector did not re-fire this cycle — and
skips that derivation entirely for a detector that returned ``UNKNOWN``.

``run`` takes an injected ``now`` (never the wall clock) so passes are
deterministic and testable; the exported :func:`schedule_detection` wires the
three tiers onto an APScheduler instance but is **not** started here — the
integration layer owns the daemon lifecycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, List, Optional, Union

from netadmin.detect.context import DetectorContext
from netadmin.domain.entities import Finding, Timestamp
from netadmin.domain.types import Cadence, Severity
from netadmin.issues.engine import IssueEngine, fingerprint
from netadmin.issues.models import Transition
from netadmin.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from netadmin.detect.baseline import Baselines
    from netadmin.detect.catalog import Catalog, CatalogEntry
    from netadmin.store.repository import Repository

_log = get_logger("detect.engine")

# A detector whose window has under this fraction of expected live coverage must
# return :data:`UNKNOWN` instead of a verdict (sections 4 & 6). Exposed for
# detectors to compare against so the 0.5 line lives in exactly one place.
COVERAGE_MIN = 0.5


class _Unknown:
    """The UNKNOWN verdict: a detector that could not evaluate this cycle.

    A single shared sentinel compared by identity (``result is UNKNOWN``). It is
    deliberately *not* falsy-magic — the engine never truth-tests it, so it can
    never be confused with an empty findings list (which is a real clear).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNKNOWN"


UNKNOWN = _Unknown()


@dataclass
class DetectorResult:
    """A detector verdict carrying fired findings **and** per-entity UNKNOWNs.

    The whole-detector :data:`UNKNOWN` sentinel says "I could not evaluate this
    cycle at all" (a job-global coverage gap). This finer verdict lets an
    entity-iterating detector say "I evaluated the pass, but *these specific
    entities* had too few samples of their own to judge" — a per-series gap that
    must **freeze** those entities' open issues, not clear them by absence.

    ``unknown_entities`` is a set of ``entity_id`` values the detector skipped for
    want of samples. The engine translates each into the fingerprint(s) of that
    entity's open issues under this detector and routes them to
    :meth:`IssueEngine.process_cycle`'s ``unknown=`` (advance nothing) instead of
    ``cleared=`` — so a genuinely-broken entity that merely went quiet (Wi-Fi
    power-save, a sparse poll) keeps its issue and its "still broken, day N"
    continuity rather than spuriously resolving and re-detecting.

    Construct via :meth:`of`, which returns a plain ``list[Finding]`` when nothing
    is unknown so the common path keeps the simplest possible return type.
    """

    findings: list[Finding] = field(default_factory=list)
    unknown_entities: set[int] = field(default_factory=set)

    @classmethod
    def of(cls, findings: List[Finding], unknown_entities: Any) -> "EvalResult":
        """Package ``findings`` with per-entity UNKNOWNs.

        Returns a bare ``list`` when ``unknown_entities`` is empty (the overwhelming
        common case) so detectors and their tests keep list semantics; otherwise a
        :class:`DetectorResult`. ``None`` entity ids are dropped — a synthetic
        entity has no open issue to freeze.
        """
        unknown = {eid for eid in unknown_entities if eid is not None}
        if not unknown:
            return list(findings)
        return cls(findings=list(findings), unknown_entities=unknown)


# What a detector's ``evaluate`` may return: a (possibly empty) list of findings,
# the UNKNOWN sentinel, or a :class:`DetectorResult` (findings + per-entity gaps).
EvalResult = Union[List[Finding], _Unknown, DetectorResult]

_SEVERITY_RANK: dict[Severity, int] = {Severity.P1: 3, Severity.P2: 2, Severity.P3: 1}


def _clamp_severity(severity: Severity, ceiling: Severity) -> Severity:
    """Clamp ``severity`` down to ``ceiling`` (a catalog invariant, section 6)."""
    if _SEVERITY_RANK[severity] > _SEVERITY_RANK[ceiling]:
        return ceiling
    return severity


@dataclass
class PassResult:
    """The outcome of one cadence pass — returned for tests and observability."""

    cadence: Cadence
    ts: Timestamp
    findings: list[Finding] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    frozen: list[str] = field(default_factory=list)
    unknown_detectors: list[str] = field(default_factory=list)
    failed_detectors: list[str] = field(default_factory=list)
    evaluated: int = 0
    ok: bool = True

    @property
    def job(self) -> str:
        return _pass_job(self.cadence)


@dataclass
class EngineRunConfig:
    """Cadence tunables, all wall-clock. :meth:`DetectorEngine.run` takes an
    explicit ``now`` and ignores these; they are read only by
    :func:`schedule_detection` when it registers the three tiers' triggers.
    """

    fast_interval_s: int = 60  # FAST tier: every collector fast cadence
    window_interval_s: int = 900  # WINDOW tier: 15 minutes
    daily_hour: int = 3  # DAILY tier: UTC hour for config audits
    stagger_s: float = 5.0  # first-run offset so tiers do not all fire at once


def _pass_job(cadence: Cadence) -> str:
    """``poll_runs.job`` id for a detection pass of ``cadence``."""
    return f"detect_{cadence.value}"


class DetectorEngine:
    """Runs registered detectors by cadence tier and drives the issue engine.

    Construct once at the composition root. ``baselines`` is injected (built via
    :meth:`Baselines.for_repository` in :func:`build_detector_engine`) so the
    engine stays decoupled from the baseline implementation and unit-testable with
    a fake. The ``catalog`` defaults to the shipped
    :data:`~netadmin.detect.catalog.DEFAULT_CATALOG`.
    """

    def __init__(
        self,
        *,
        repo: "Repository",
        issue_engine: IssueEngine,
        baselines: "Baselines",
        catalog: Optional["Catalog"] = None,
        settings: Any = None,
        site_id: Optional[str] = None,
        config: Optional[EngineRunConfig] = None,
    ) -> None:
        self._repo = repo
        self._issue_engine = issue_engine
        self._baselines = baselines
        if catalog is None:
            from netadmin.detect.catalog import DEFAULT_CATALOG

            catalog = DEFAULT_CATALOG
        self._catalog = catalog
        self._settings = settings
        self._site_id = site_id if site_id is not None else getattr(repo, "site_id", "default")
        self.config = config or EngineRunConfig()

    # ------------------------------------------------------------------ #
    # Context
    # ------------------------------------------------------------------ #
    def build_context(self, now: Timestamp) -> DetectorContext:
        """A fresh per-pass context at ``now`` (baselines/settings injected)."""
        return DetectorContext(
            repo=self._repo,
            baselines=self._baselines,
            now_ts=now,
            site_id=self._site_id,
            settings=self._settings,
        )

    # ------------------------------------------------------------------ #
    # Passes
    # ------------------------------------------------------------------ #
    def run(self, cadence: Cadence, now: Timestamp) -> PassResult:
        """Run every detector of ``cadence`` and apply one issue-engine cycle.

        Always records a ``poll_runs`` row for the pass — including when the whole
        pass raises (``ok=False``) — so the detection engine's own liveness is
        queryable the same way collector jobs are.
        """
        started = time.monotonic()
        ok = True
        findings: list[Finding] = []
        cleared: set[str] = set()
        frozen: set[str] = set()
        unknown_detectors: list[str] = []
        failed_detectors: list[str] = []
        transitions: list[Transition] = []
        evaluated = 0
        try:
            ctx = self.build_context(now)
            entries = self._catalog.by_cadence(cadence)
            open_by_key = self._open_issues_by_detector()
            for entry in entries:
                verdict = self._evaluate(entry, ctx, cadence, failed_detectors)
                if verdict is _SKIP:
                    continue
                evaluated += 1
                if verdict is UNKNOWN:
                    unknown_detectors.append(entry.key)
                    continue  # advance nothing: a gap is not a clear
                result_findings, unknown_entities = _split_result(verdict)
                fired = self._collect_findings(entry, result_findings, findings)
                self._collect_clears(
                    entry.key, fired, unknown_entities, open_by_key, cleared, frozen
                )
            transitions = self._issue_engine.process_cycle(
                now, findings=findings, cleared=sorted(cleared), unknown=sorted(frozen)
            )
        except Exception:  # noqa: BLE001 - a pass never crashes the caller
            ok = False
            _log.exception("detection %s pass failed", cadence.value)
        duration_ms = int((time.monotonic() - started) * 1000)
        self._record_pass(
            cadence, now, ok=ok, failed=len(failed_detectors), duration_ms=duration_ms
        )
        return PassResult(
            cadence=cadence,
            ts=now,
            findings=findings,
            transitions=transitions,
            cleared=sorted(cleared),
            frozen=sorted(frozen),
            unknown_detectors=unknown_detectors,
            failed_detectors=failed_detectors,
            evaluated=evaluated,
            ok=ok,
        )

    def run_fast(self, now: Timestamp) -> PassResult:
        return self.run(Cadence.FAST, now)

    def run_window(self, now: Timestamp) -> PassResult:
        return self.run(Cadence.WINDOW, now)

    def run_daily(self, now: Timestamp) -> PassResult:
        return self.run(Cadence.DAILY, now)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _evaluate(
        self,
        entry: "CatalogEntry",
        ctx: DetectorContext,
        cadence: Cadence,
        failed: list[str],
    ) -> Any:
        """Run one detector behind the firewall. Returns its result, ``UNKNOWN``,
        or the private :data:`_SKIP` marker when it raised (already counted)."""
        try:
            return entry.detector.evaluate(ctx)
        except Exception:  # noqa: BLE001 - isolation is the whole point
            failed.append(entry.key)
            _log.warning(
                "detector %s raised during %s pass; isolated and skipped",
                entry.key,
                cadence.value,
                exc_info=True,
            )
            return _SKIP

    def _collect_findings(
        self, entry: "CatalogEntry", result: List[Finding], sink: list[Finding]
    ) -> set[str]:
        """Append ``result`` (severity-clamped) to ``sink``; return fired fingerprints.

        Two normalisations happen here, at the one seam every finding crosses on
        its way into the issue engine:

        * severity is clamped down to the catalog's ceiling (a catalog invariant);
        * the detector's ``confounders_checked`` audit trail (section 6) is folded
          into the persisted ``evidence`` under ``"confounders_checked"``. The issue
          engine persists only ``evidence``, so folding it here is what carries the
          false-positive-trap trail onto the stored issue (and the section-10
          dossier) without the pure issue engine having to know the concept.

        Neither touches ``dims``, so the fingerprint is unchanged by both.
        """
        fired: set[str] = set()
        for finding in result:
            clamped = _clamp_severity(finding.severity, entry.severity_ceiling)
            evidence = finding.evidence
            if finding.confounders_checked and "confounders_checked" not in evidence:
                evidence = {
                    **evidence,
                    "confounders_checked": list(finding.confounders_checked),
                }
            if clamped is not finding.severity or evidence is not finding.evidence:
                finding = replace(finding, severity=clamped, evidence=evidence)
            sink.append(finding)
            fired.add(fingerprint(finding))
        return fired

    def _collect_clears(
        self,
        detector_key: str,
        fired: set[str],
        unknown_entities: set[int],
        open_by_key: dict[str, list],
        cleared: set[str],
        frozen: set[str],
    ) -> None:
        """Route each open issue this detector owns but did *not* re-fire.

        An open issue whose entity the detector flagged UNKNOWN this cycle (too few
        samples of its own to judge) is **frozen** — added to ``frozen`` so it
        advances nothing — instead of cleared by absence. Everything else the
        detector did not re-fire is a genuine clear.

        Only issues of ``detector_key`` are considered, so a FAST pass never
        touches WINDOW/DAILY issues, and a detector that returned the whole-pass
        ``UNKNOWN`` (never reaching here) never clears its own issues.
        """
        for issue in open_by_key.get(detector_key, ()):  # type: ignore[union-attr]
            if issue.fingerprint in fired:
                continue
            if issue.entity_id is not None and issue.entity_id in unknown_entities:
                frozen.add(issue.fingerprint)  # per-entity gap: freeze, do not clear
                continue
            cleared.add(issue.fingerprint)

    def _open_issues_by_detector(self) -> dict[str, list]:
        grouped: dict[str, list] = {}
        for issue in self._issue_engine.repo.list_open_issues():
            grouped.setdefault(issue.detector_key, []).append(issue)
        return grouped

    def _record_pass(
        self,
        cadence: Cadence,
        now: Timestamp,
        *,
        ok: bool,
        failed: int,
        duration_ms: Optional[int],
    ) -> None:
        error = f"{failed} detector(s) failed" if failed else None
        try:
            self._repo.record_poll_run(
                job=_pass_job(cadence),
                ok=ok,
                ts=now,
                duration_ms=duration_ms,
                error=error,
            )
        except Exception:  # noqa: BLE001 - accounting must not break the pass
            _log.exception("failed to record poll_run for %s pass", cadence.value)


# Private marker for "detector raised, skip it" — distinct from UNKNOWN, which is
# a real (no-op) verdict the detector chose.
class _Skip:
    __slots__ = ()


_SKIP = _Skip()


def _split_result(verdict: Any) -> tuple[List[Finding], set[int]]:
    """Normalise a non-UNKNOWN verdict into ``(findings, unknown_entity_ids)``.

    A plain ``list`` (the common case) carries no per-entity gaps; a
    :class:`DetectorResult` carries both. Called only after the ``UNKNOWN`` and
    ``_SKIP`` sentinels are handled, so ``verdict`` is always one of these two.
    """
    if isinstance(verdict, DetectorResult):
        return verdict.findings, verdict.unknown_entities
    return verdict, set()


# ---------------------------------------------------------------------- #
# Composition-root helpers (exported; NOT started here — Integrate wires them)
# ---------------------------------------------------------------------- #
def build_detector_engine(
    repo: "Repository",
    issue_engine: IssueEngine,
    *,
    settings: Any = None,
    catalog: Optional["Catalog"] = None,
    config: Optional[EngineRunConfig] = None,
    baselines: Optional["Baselines"] = None,
) -> DetectorEngine:
    """Assemble a :class:`DetectorEngine` with real baselines built from ``repo``.

    The one place the concrete :class:`~netadmin.detect.baseline.Baselines` is
    imported and constructed — unless the composition root already built one it
    wants shared (the daemon builds a single :class:`Baselines` and hands the same
    instance to the baseline-update job, the SLE minutes job, and here, so all
    three read and write the one ``baselines`` table through one repository). Pass
    ``baselines`` to reuse it; omit it to have this construct its own. Nothing here
    starts a scheduler or touches the loop.
    """
    if baselines is None:
        from netadmin.detect.baseline import Baselines

        baselines = Baselines.for_repository(repo)
    site_id = getattr(settings, "site_id", None) or getattr(repo, "site_id", "default")
    return DetectorEngine(
        repo=repo,
        issue_engine=issue_engine,
        baselines=baselines,
        catalog=catalog,
        settings=settings,
        site_id=site_id,
        config=config,
    )


def schedule_detection(
    engine: DetectorEngine,
    *,
    scheduler: Any = None,
    poll: Any = None,
    daily_hour: Optional[int] = None,
    now: Any = None,
) -> Any:
    """Register the three cadence tiers on an APScheduler ``AsyncIOScheduler``.

    FAST runs at the collector fast cadence, WINDOW every 15 minutes, DAILY on a
    UTC-hour cron. Jobs are ``max_instances=1`` + ``coalesce=True`` with staggered
    first runs so the tiers do not fire together. Each job calls the matching
    ``run_*`` with a freshly sampled UTC ``now``.

    This is wiring only — it is **not** started, and the integration layer decides
    the threading model (the store connection is single-threaded, so passes run on
    the loop thread unless Integrate arranges otherwise). Import of APScheduler is
    deferred so importing this module never requires it.
    """
    from datetime import datetime, timedelta, timezone

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = scheduler or AsyncIOScheduler(timezone=timezone.utc)
    base = now or datetime.now(timezone.utc)
    cfg = engine.config
    fast_s = (
        int(getattr(poll, "device_s", cfg.fast_interval_s))
        if poll is not None
        else cfg.fast_interval_s
    )
    hour = cfg.daily_hour if daily_hour is None else daily_hour

    async def _fast() -> None:
        engine.run_fast(_utcnow_ts())

    async def _window() -> None:
        engine.run_window(_utcnow_ts())

    async def _daily() -> None:
        engine.run_daily(_utcnow_ts())

    sched.add_job(
        _fast,
        "interval",
        seconds=fast_s,
        id=_pass_job(Cadence.FAST),
        name=_pass_job(Cadence.FAST),
        max_instances=1,
        coalesce=True,
        next_run_time=base + timedelta(seconds=cfg.stagger_s),
        replace_existing=True,
    )
    sched.add_job(
        _window,
        "interval",
        seconds=cfg.window_interval_s,
        id=_pass_job(Cadence.WINDOW),
        name=_pass_job(Cadence.WINDOW),
        max_instances=1,
        coalesce=True,
        next_run_time=base + timedelta(seconds=cfg.stagger_s * 2),
        replace_existing=True,
    )
    sched.add_job(
        _daily,
        CronTrigger(hour=hour, minute=0, timezone=timezone.utc),
        id=_pass_job(Cadence.DAILY),
        name=_pass_job(Cadence.DAILY),
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return sched


def _utcnow_ts() -> int:
    """Epoch-second UTC now, for scheduled passes."""
    from datetime import datetime, timezone

    return int(datetime.now(timezone.utc).timestamp())


__all__ = [
    "COVERAGE_MIN",
    "UNKNOWN",
    "DetectorResult",
    "EvalResult",
    "PassResult",
    "EngineRunConfig",
    "DetectorEngine",
    "build_detector_engine",
    "schedule_detection",
]
