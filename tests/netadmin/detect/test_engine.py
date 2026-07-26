"""DetectorEngine: cadence routing, the firewall, UNKNOWN vs clear, ceilings.

Every test drives findings into a *real* IssueEngine on a temp store (via
``support.build_stack``), so the framework's contract with the issue lifecycle is
exercised end-to-end, not mocked.
"""

from __future__ import annotations

from netadmin.detect.catalog import build_catalog
from netadmin.detect.engine import UNKNOWN, DetectorEngine, DetectorResult
from netadmin.domain.types import Cadence, IssueState, Severity
from netadmin.issues.engine import fingerprint
from netadmin.issues.models import EngineConfig
from netadmin.store.repository import Repository
from tests.netadmin.detect.support import (
    StubDetector,
    build_stack,
    entry,
    make_finding,
    seed_device,
)

NOW = 3_000_000


def _boom(ctx):
    raise RuntimeError("detector exploded")


def _open_issue(repo: Repository, detector_key: str):
    rows = [r for r in repo.list_issues(open_only=True) if r["detector_key"] == detector_key]
    return rows[0] if rows else None


# ---------------------------------------------------------------------- #
# Cadence routing
# ---------------------------------------------------------------------- #
def test_pass_runs_only_its_own_cadence(repo: Repository) -> None:
    fast = StubDetector("t.fast", Cadence.FAST, lambda ctx: [])
    window = StubDetector("t.window", Cadence.WINDOW, lambda ctx: [])
    daily = StubDetector("t.daily", Cadence.DAILY, lambda ctx: [])
    catalog = build_catalog([entry(fast), entry(window), entry(daily)])
    stack = build_stack(repo, catalog=catalog)

    stack.detector_engine.run_fast(NOW)
    assert (fast.calls, window.calls, daily.calls) == (1, 0, 0)

    stack.detector_engine.run_window(NOW)
    assert (fast.calls, window.calls, daily.calls) == (1, 1, 0)

    stack.detector_engine.run_daily(NOW)
    assert (fast.calls, window.calls, daily.calls) == (1, 1, 1)


def test_pass_records_a_poll_run_per_cadence(repo: Repository) -> None:
    catalog = build_catalog([entry(StubDetector("t.fast", Cadence.FAST, lambda ctx: []))])
    build_stack(repo, catalog=catalog).detector_engine.run_fast(NOW)
    rows = repo.read_poll_runs("detect_fast", NOW - 1, NOW + 1)
    assert len(rows) == 1
    assert rows[0]["ok"] == 1
    assert rows[0]["error"] is None


# ---------------------------------------------------------------------- #
# Findings -> issues
# ---------------------------------------------------------------------- #
def test_finding_opens_an_issue(repo: Repository) -> None:
    det = StubDetector("t.d", Cadence.FAST, lambda ctx: [make_finding("t.d")])
    result = build_stack(repo, catalog=build_catalog([entry(det)])).detector_engine.run_fast(NOW)
    assert [f.detector_key for f in result.findings] == ["t.d"]
    assert _open_issue(repo, "t.d") is not None


def test_severity_ceiling_clamps_finding(repo: Repository) -> None:
    # detector emits P1; catalog ceiling is P3 -> issue must be stored as P3.
    det = StubDetector(
        "t.loud", Cadence.FAST, lambda ctx: [make_finding("t.loud", severity=Severity.P1)]
    )
    catalog = build_catalog([entry(det, ceiling=Severity.P3)])
    result = build_stack(repo, catalog=catalog).detector_engine.run_fast(NOW)
    assert result.findings[0].severity is Severity.P3
    assert _open_issue(repo, "t.loud")["severity"] == "p3"


# ---------------------------------------------------------------------- #
# Firewall
# ---------------------------------------------------------------------- #
def test_broken_detector_is_isolated(repo: Repository) -> None:
    bad = StubDetector("t.bad", Cadence.FAST, _boom)
    good = StubDetector("t.good", Cadence.FAST, lambda ctx: [make_finding("t.good")])
    catalog = build_catalog([entry(bad), entry(good)])
    result = build_stack(repo, catalog=catalog).detector_engine.run_fast(NOW)

    assert result.ok is True  # the pass survived
    assert result.failed_detectors == ["t.bad"]
    assert result.evaluated == 1  # only the good detector counts as evaluated
    assert _open_issue(repo, "t.good") is not None  # good detector's finding still landed


def test_firewall_failure_annotates_the_poll_run(repo: Repository) -> None:
    catalog = build_catalog([entry(StubDetector("t.bad", Cadence.FAST, _boom))])
    build_stack(repo, catalog=catalog).detector_engine.run_fast(NOW)
    row = repo.read_poll_runs("detect_fast", NOW - 1, NOW + 1)[0]
    assert row["ok"] == 1  # the pass itself completed
    assert "1 detector(s) failed" in row["error"]


# ---------------------------------------------------------------------- #
# UNKNOWN vs clear — the crux
# ---------------------------------------------------------------------- #
def test_unknown_advances_nothing_but_empty_list_clears(repo: Repository) -> None:
    holder: dict = {"r": [make_finding("t.d")]}
    det = StubDetector("t.d", Cadence.FAST, lambda ctx: holder["r"])
    stack = build_stack(
        repo,
        catalog=build_catalog([entry(det)]),
        issue_config=EngineConfig(default_m=3, default_k=6),
    )
    engine = stack.detector_engine

    # Drive pending -> active with three consecutive fires.
    for _ in range(3):
        engine.run_fast(NOW)
    issue = _open_issue(repo, "t.d")
    assert issue["state"] == IssueState.ACTIVE.value
    assert issue["clear_streak"] == 0

    # UNKNOWN: the detector could not evaluate -> advance NOTHING.
    holder["r"] = UNKNOWN
    result = engine.run_fast(NOW)
    assert result.unknown_detectors == ["t.d"]
    assert result.cleared == []
    after_unknown = _open_issue(repo, "t.d")
    assert after_unknown["state"] == IssueState.ACTIVE.value
    assert after_unknown["clear_streak"] == 0  # frozen, not advanced

    # Empty list: the detector looked and found nothing -> a clear evaluation.
    holder["r"] = []
    result = engine.run_fast(NOW)
    fp = fingerprint(make_finding("t.d"))
    assert result.cleared == [fp]
    after_clear = _open_issue(repo, "t.d")
    assert after_clear["state"] == IssueState.RESOLVING.value
    assert after_clear["clear_streak"] == 1  # advanced


def test_per_entity_unknown_freezes_that_issue_instead_of_clearing(repo: Repository) -> None:
    # The crux of Finding 1: a detector that iterates entities and skips one for
    # thin per-series samples (Wi-Fi power-save) must FREEZE that entity's open
    # issue, not let the engine clear it by absence and flap the issue.
    entity_id = seed_device(repo, native_id="dev-a", last_seen_ts=NOW)
    holder: dict = {"r": [make_finding("t.d", native_id="dev-a", entity_id=entity_id)]}
    det = StubDetector("t.d", Cadence.FAST, lambda ctx: holder["r"])
    stack = build_stack(
        repo,
        catalog=build_catalog([entry(det)]),
        issue_config=EngineConfig(default_m=1, default_k=6),  # active on first fire
    )
    engine = stack.detector_engine

    engine.run_fast(NOW)  # fire -> active
    assert _open_issue(repo, "t.d")["state"] == IssueState.ACTIVE.value

    # The detector evaluated the pass but could not judge THIS entity (thin
    # samples): it returns the entity in unknown_entities. Run more cycles than
    # K=6 — a clear-by-absence would have resolved the issue; a freeze must not.
    fp = fingerprint(make_finding("t.d", native_id="dev-a", entity_id=entity_id))
    holder["r"] = DetectorResult(findings=[], unknown_entities={entity_id})
    for _ in range(8):
        result = engine.run_fast(NOW)
        assert result.frozen == [fp]
        assert result.cleared == []
    frozen = _open_issue(repo, "t.d")
    assert frozen["state"] == IssueState.ACTIVE.value  # still broken, never resolved
    assert frozen["clear_streak"] == 0  # frozen, not advanced

    # Once the detector can actually look again and finds nothing, it clears.
    holder["r"] = []
    result = engine.run_fast(NOW)
    assert result.cleared == [fp]
    assert result.frozen == []
    assert _open_issue(repo, "t.d")["state"] == IssueState.RESOLVING.value
    assert _open_issue(repo, "t.d")["clear_streak"] == 1


def test_unknown_entity_freeze_is_scoped_to_that_entity(repo: Repository) -> None:
    # One detector, two entities: entity A is UNKNOWN this cycle, entity B is not
    # re-fired. A must freeze; B must clear. (Both fingerprints differ by native_id.)
    id_a = seed_device(repo, native_id="dev-a", last_seen_ts=NOW)
    id_b = seed_device(repo, native_id="dev-b", last_seen_ts=NOW)
    a = make_finding("t.d", native_id="dev-a", entity_id=id_a)
    b = make_finding("t.d", native_id="dev-b", entity_id=id_b)
    holder: dict = {"r": [a, b]}
    det = StubDetector("t.d", Cadence.FAST, lambda ctx: holder["r"])
    stack = build_stack(
        repo,
        catalog=build_catalog([entry(det)]),
        issue_config=EngineConfig(default_m=1, default_k=6),
    )
    engine = stack.detector_engine
    engine.run_fast(NOW)  # both -> active

    holder["r"] = DetectorResult(findings=[], unknown_entities={id_a})
    result = engine.run_fast(NOW)
    assert result.frozen == [fingerprint(a)]
    assert result.cleared == [fingerprint(b)]


def test_detector_result_of_is_a_plain_list_when_nothing_unknown() -> None:
    # The common path keeps list semantics; a synthetic (None-id) entity cannot
    # freeze anything and is dropped.
    findings = [make_finding("t.d")]
    assert DetectorResult.of(findings, set()) == findings
    assert DetectorResult.of(findings, {None}) == findings
    result = DetectorResult.of(findings, {7})
    assert isinstance(result, DetectorResult)
    assert result.unknown_entities == {7}


def test_unknown_pass_produces_no_transitions(repo: Repository) -> None:
    det = StubDetector("t.d", Cadence.FAST, lambda ctx: UNKNOWN)
    result = build_stack(repo, catalog=build_catalog([entry(det)])).detector_engine.run_fast(NOW)
    assert result.transitions == []
    assert result.findings == []
    assert result.unknown_detectors == ["t.d"]


# ---------------------------------------------------------------------- #
# Clear inference is scoped to the detector's own cadence + key
# ---------------------------------------------------------------------- #
def test_fast_clear_never_touches_a_window_issue(repo: Repository) -> None:
    fast_holder: dict = {"r": [make_finding("t.fast", native_id="fp")]}
    win_holder: dict = {"r": [make_finding("t.win", native_id="wp")]}
    fast = StubDetector("t.fast", Cadence.FAST, lambda ctx: fast_holder["r"])
    window = StubDetector("t.win", Cadence.WINDOW, lambda ctx: win_holder["r"])
    stack = build_stack(
        repo,
        catalog=build_catalog([entry(fast), entry(window)]),
        issue_config=EngineConfig(default_m=1, default_k=6),  # M=1: active on first fire
    )
    engine = stack.detector_engine

    engine.run_fast(NOW)  # fast issue -> active
    engine.run_window(NOW)  # window issue -> active
    assert _open_issue(repo, "t.fast")["state"] == IssueState.ACTIVE.value
    assert _open_issue(repo, "t.win")["state"] == IssueState.ACTIVE.value

    # A fast pass that clears its own detector must leave the window issue alone.
    fast_holder["r"] = []
    result = engine.run_fast(NOW)
    assert result.cleared == [fingerprint(make_finding("t.fast", native_id="fp"))]
    assert _open_issue(repo, "t.fast")["state"] == IssueState.RESOLVING.value
    win_issue = _open_issue(repo, "t.win")
    assert win_issue["state"] == IssueState.ACTIVE.value
    assert win_issue["clear_streak"] == 0


def test_empty_pass_with_no_issues_is_a_noop(repo: Repository) -> None:
    det = StubDetector("t.d", Cadence.FAST, lambda ctx: [])
    result = build_stack(repo, catalog=build_catalog([entry(det)])).detector_engine.run_fast(NOW)
    assert result.findings == []
    assert result.cleared == []
    assert result.transitions == []
    assert result.ok is True


# ---------------------------------------------------------------------- #
# Construction
# ---------------------------------------------------------------------- #
def test_engine_defaults_to_the_shipped_catalog(repo: Repository) -> None:
    from netadmin.detect.catalog import DEFAULT_CATALOG
    from netadmin.issues.engine import IssueEngine
    from netadmin.issues.store_repository import StoreIssueRepository
    from tests.netadmin.detect.support import FakeBaselines

    engine = DetectorEngine(
        repo=repo,
        issue_engine=IssueEngine(StoreIssueRepository(repo)),
        baselines=FakeBaselines(),
    )
    assert engine._catalog is DEFAULT_CATALOG
