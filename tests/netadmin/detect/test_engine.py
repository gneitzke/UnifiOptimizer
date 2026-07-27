"""DetectorEngine: cadence routing, the firewall, UNKNOWN vs clear, ceilings.

Every test drives findings into a *real* IssueEngine on a temp store (via
``support.build_stack``), so the framework's contract with the issue lifecycle is
exercised end-to-end, not mocked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from netadmin.detect.catalog import build_catalog
from netadmin.detect.engine import (
    DEFAULT_CLIENT_ABSENT_AFTER_S,
    UNKNOWN,
    DetectorEngine,
    DetectorResult,
)
from netadmin.domain.types import Cadence, EntityType, IssueState, Severity
from netadmin.issues.engine import fingerprint
from netadmin.issues.models import EngineConfig
from netadmin.store.repository import Repository
from tests.netadmin.detect.support import (
    StubDetector,
    build_stack,
    entry,
    make_finding,
    seed_client,
    seed_device,
)

NOW = 3_000_000


def _boom(ctx):
    raise RuntimeError("detector exploded")


def _open_issue(repo: Repository, detector_key: str):
    rows = [r for r in repo.list_issues(open_only=True) if r["detector_key"] == detector_key]
    return rows[0] if rows else None


def _resolved_issue_id(repo: Repository, detector_key: str) -> int:
    rows = [
        r
        for r in repo.list_issues(state=IssueState.RESOLVED.value)
        if r["detector_key"] == detector_key
    ]
    return int(rows[0]["id"])


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
# Departure — a client that left the network (Gitea #43)
# ---------------------------------------------------------------------- #
def _client_stack(repo: Repository, holder: dict, *, settings=None) -> DetectorEngine:
    det = StubDetector("t.client", Cadence.WINDOW, lambda ctx: holder["r"])
    return build_stack(
        repo,
        catalog=build_catalog([entry(det)]),
        issue_config=EngineConfig(default_m=1, default_k=2),
        settings=settings,
    ).detector_engine


def test_departed_client_clears_the_issue_a_detector_can_only_freeze(repo: Repository) -> None:
    # The live defect: the client leaves, every client-scoped detector reports it
    # as unknown for want of samples, and the issue sits active for ever. Absence
    # of the client itself outranks that freeze and resolves the issue.
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    engine = _client_stack(repo, holder)

    engine.run_window(NOW)  # fire -> active (M=1)
    assert _open_issue(repo, "t.client")["state"] == IssueState.ACTIVE.value

    # The client is gone: no samples, so the detector says "cannot judge", and the
    # entity's own last sighting is now older than the absence threshold.
    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    later = NOW + DEFAULT_CLIENT_ABSENT_AFTER_S + 60
    result = engine.run_window(later)
    assert result.absent == [fingerprint(finding)]
    assert result.frozen == []
    resolving = _open_issue(repo, "t.client")
    assert resolving["state"] == IssueState.RESOLVING.value
    assert resolving["clear_streak"] == 1

    # K=2: the second absent pass resolves it, and the trail says why.
    engine.run_window(later + 900)
    assert _open_issue(repo, "t.client") is None
    events = repo.list_issue_events(_resolved_issue_id(repo, "t.client"))
    reasons = {e["kind"]: json.loads(e["detail"] or "{}").get("reason") for e in events}
    assert reasons["resolving"] == "entity_absent"
    assert reasons["resolved"] == "entity_absent"


def test_client_seen_within_the_threshold_still_freezes(repo: Repository) -> None:
    # A client that is here but quiet (power-save, a sparse poll) is not gone: the
    # per-entity freeze must still win, or every dozing phone loses its issue.
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    engine = _client_stack(repo, holder)
    engine.run_window(NOW)

    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    result = engine.run_window(NOW + DEFAULT_CLIENT_ABSENT_AFTER_S - 60)
    assert result.absent == []
    assert result.frozen == [fingerprint(finding)]
    assert _open_issue(repo, "t.client")["clear_streak"] == 0


def test_a_stale_ap_is_never_treated_as_departed(repo: Repository) -> None:
    # APs and switches have their own down semantics (infra.device_down plus the
    # inhibition it triggers). Clearing their issues by absence would resolve the
    # whole site's findings the moment the controller stopped reporting them.
    ap_id = seed_device(repo, native_id="ap-a", entity_type=EntityType.AP, last_seen_ts=NOW)
    finding = make_finding("t.client", native_id="ap-a", entity_type=EntityType.AP, entity_id=ap_id)
    holder: dict = {"r": [finding]}
    engine = _client_stack(repo, holder)
    engine.run_window(NOW)

    holder["r"] = DetectorResult(findings=[], unknown_entities={ap_id})
    result = engine.run_window(NOW + 10 * DEFAULT_CLIENT_ABSENT_AFTER_S)
    assert result.absent == []
    assert result.frozen == [fingerprint(finding)]


def test_absent_pending_issue_is_discarded(repo: Repository) -> None:
    # The orphan-pending gap: an unconfirmed row whose client left had no path to
    # removal, because only a clear discards it and absence never produced one.
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    det = StubDetector("t.client", Cadence.WINDOW, lambda ctx: holder["r"])
    engine = build_stack(
        repo,
        catalog=build_catalog([entry(det)]),
        issue_config=EngineConfig(default_m=3, default_k=6),  # never confirmed
    ).detector_engine

    engine.run_window(NOW)
    assert _open_issue(repo, "t.client")["state"] == IssueState.PENDING.value

    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    engine.run_window(NOW + DEFAULT_CLIENT_ABSENT_AFTER_S + 60)
    assert repo.list_issues() == []


def test_absence_threshold_is_configurable(repo: Repository) -> None:
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    settings = SimpleNamespace(thresholds={"engine": {"client_absent_after_s": 300}}, poll=None)
    engine = _client_stack(repo, holder, settings=settings)
    engine.run_window(NOW)

    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    assert engine.run_window(NOW + 240).absent == []  # inside the configured window
    assert engine.run_window(NOW + 600).absent == [fingerprint(finding)]


def test_a_garbage_threshold_falls_back_to_the_default(repo: Repository) -> None:
    # Tunables never raise (the DetectorContext contract): a mistyped setting
    # must not take the detection pass down with it.
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    settings = SimpleNamespace(thresholds={"engine": {"client_absent_after_s": "soon"}}, poll=None)
    engine = _client_stack(repo, holder, settings=settings)
    engine.run_window(NOW)

    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    assert engine.run_window(NOW + DEFAULT_CLIENT_ABSENT_AFTER_S - 60).absent == []
    assert engine.run_window(NOW + DEFAULT_CLIENT_ABSENT_AFTER_S + 60).absent == [
        fingerprint(finding)
    ]


def test_absence_can_be_switched_off(repo: Repository) -> None:
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    settings = SimpleNamespace(thresholds={"engine": {"client_absent_after_s": 0}}, poll=None)
    engine = _client_stack(repo, holder, settings=settings)
    engine.run_window(NOW)

    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    result = engine.run_window(NOW + 30 * DEFAULT_CLIENT_ABSENT_AFTER_S)
    assert result.absent == []
    assert result.frozen == [fingerprint(finding)]


def test_ap_outage_does_not_mass_resolve_its_clients_issues(repo: Repository) -> None:
    """A power cut takes an AP down; all its clients vanish at once.

    Every one of those clients is absent by the letter of the rule, so without
    inhibition the site would silently resolve every client issue under that AP
    while the operator is still holding a torch. ``infra.device_down`` freezes the
    lot, and they come back the moment the AP does.
    """
    ap_id = seed_device(repo, native_id="ap-a", entity_type=EntityType.AP, last_seen_ts=NOW)
    clients = [
        seed_client(repo, native_id=f"client-{n}", last_seen_ts=NOW, parent_id=ap_id)
        for n in range(3)
    ]
    findings = [
        make_finding(
            "t.client", native_id=f"client-{n}", entity_type=EntityType.CLIENT, entity_id=cid
        )
        for n, cid in enumerate(clients)
    ]
    down = make_finding(
        "infra.device_down", native_id="ap-a", entity_type=EntityType.AP, entity_id=ap_id
    )

    client_holder: dict = {"r": list(findings)}
    down_holder: dict = {"r": []}
    catalog = build_catalog(
        [
            entry(StubDetector("t.client", Cadence.WINDOW, lambda ctx: client_holder["r"])),
            entry(StubDetector("infra.device_down", Cadence.WINDOW, lambda ctx: down_holder["r"])),
        ]
    )
    engine = build_stack(
        repo,
        catalog=catalog,
        issue_config=EngineConfig(default_m=1, default_k=2),
    ).detector_engine

    engine.run_window(NOW)  # three client issues -> active
    assert len({fingerprint(f) for f in findings}) == 3

    # The AP dies. Its clients disappear with it, and stay gone well past K.
    down_holder["r"] = [down]
    client_holder["r"] = DetectorResult(findings=[], unknown_entities=set(clients))
    outage = NOW + DEFAULT_CLIENT_ABSENT_AFTER_S + 60
    for cycle in range(6):
        result = engine.run_window(outage + cycle * 900)
        assert sorted(result.absent) == sorted(fingerprint(f) for f in findings)

    rows = [r for r in repo.list_issues(open_only=True) if r["detector_key"] == "t.client"]
    assert len(rows) == 3, "no client issue resolved while its AP was down"
    assert {r["state"] for r in rows} == {IssueState.ACTIVE.value}
    assert {r["clear_streak"] for r in rows} == {0}, "inhibition froze the streak"


def test_returning_client_snaps_its_issue_back(repo: Repository) -> None:
    cid = seed_client(repo, native_id="client-a", last_seen_ts=NOW)
    finding = make_finding(
        "t.client", native_id="client-a", entity_type=EntityType.CLIENT, entity_id=cid
    )
    holder: dict = {"r": [finding]}
    engine = _client_stack(repo, holder)
    engine.run_window(NOW)

    holder["r"] = DetectorResult(findings=[], unknown_entities={cid})
    gone = NOW + DEFAULT_CLIENT_ABSENT_AFTER_S + 60
    engine.run_window(gone)
    assert _open_issue(repo, "t.client")["state"] == IssueState.RESOLVING.value

    # It comes back and the condition is still there: same row, streak reset.
    back = gone + 900
    seed_client(repo, native_id="client-a", last_seen_ts=back)
    holder["r"] = [finding]
    engine.run_window(back)
    row = _open_issue(repo, "t.client")
    assert row["state"] == IssueState.ACTIVE.value
    assert row["clear_streak"] == 0
    assert row["first_seen_ts"] == NOW, "continuity: the same issue, not a fresh one"


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
