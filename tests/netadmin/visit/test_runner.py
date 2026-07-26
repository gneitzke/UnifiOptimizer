"""End-to-end tech-visit runs against a fake, read-only controller.

The whole pipeline (inventory -> rogue scan -> event catch-up -> backfill ->
baselines -> SLE -> detectors -> report) runs against :class:`FakeController`.
No live controller is ever contacted; every controller method the runner calls is
a GET returning canned models.
"""

from __future__ import annotations

from netadmin.visit.runner import STEP_ORDER, VisitReport, VisitStep, run_visit

from .conftest import AP_MAC, CLIENT_FLAKY, NOW


def _run(fake, store, **kw) -> VisitReport:
    return run_visit(
        kw.pop("settings"),
        endpoints=fake,
        store=store,
        now=NOW,
        lookback_days=kw.pop("lookback_days", 2),
        **kw,
    )


def test_visit_produces_report_with_expected_issues(fake_controller, visit_store, visit_settings):
    report = _run(fake_controller, visit_store, settings=visit_settings)

    assert isinstance(report, VisitReport)
    # Inventory synced: the AP, gateway, two radios, and two clients exist.
    assert report.topology["entity_count"] >= 5
    assert report.topology["by_type"].get("ap") == 1
    assert report.topology["by_type"].get("client") == 2

    # The off-grid 2.4 GHz channel fires wifi.channel_plan (deterministic from the
    # single current snapshot).
    detectors = {i["detector_key"] for i in report.issues}
    assert "wifi.channel_plan" in detectors

    # The pathological disconnect storm fires client.flaky, attributed to the AP.
    flaky = [i for i in report.issues if i["detector_key"] == "client.flaky"]
    assert flaky, "expected a client.flaky issue from the disconnect storm"
    assert flaky[0]["entity"]["native_id"] == CLIENT_FLAKY

    assert report.issue_counts["open"] >= 2


def test_visit_reads_our_own_ssids(fake_controller, visit_store, visit_settings):
    """The inventory step reads rest/wlanconf, so wifi.rogue_ap knows our SSIDs."""
    _run(fake_controller, visit_store, settings=visit_settings)
    assert "rest_wlanconf" in fake_controller.calls
    wlans = visit_store.list_entities("wlan")
    assert [row["name"] for row in wlans] == ["HomeNet"]


def test_visit_never_mutates_controller(fake_controller, visit_store, visit_settings):
    _run(fake_controller, visit_store, settings=visit_settings)
    # Every recorded call is from the read set; a mutating verb would betray the
    # rule. ``rest_wlanconf`` is a GET of the WLAN config -- the facade exposes no
    # PUT/POST against that route, which is what a write would need.
    assert fake_controller.calls, "the visit made no controller calls"
    for call in fake_controller.calls:
        name = call.split(":", 1)[0]
        assert name.startswith(("stat_", "rest_")), f"non-read call {call!r}"


def test_visit_steps_all_ok(fake_controller, visit_store, visit_settings):
    report = _run(fake_controller, visit_store, settings=visit_settings)
    step_ids = [s["id"] for s in report.steps]
    assert step_ids == [sid for sid, _ in STEP_ORDER]
    for step in report.steps:
        assert step["status"] in ("ok", "skipped"), f"{step['id']} was {step['status']}"


def test_visit_reports_sle_scores(fake_controller, visit_store, visit_settings):
    report = _run(fake_controller, visit_store, settings=visit_settings)
    sles = report.sles["sles"]
    # Every canonical SLE is represented (score may be null where there was no data).
    assert {"coverage", "capacity", "connect", "roaming", "wan", "infra"} <= set(sles)
    # The clients moved backfilled traffic, so coverage has exposed minutes.
    assert sles["coverage"]["total_minutes"] > 0


def test_visit_progress_callback_streams_steps(fake_controller, visit_store, visit_settings):
    seen: list[tuple[str, str]] = []

    def progress(step: VisitStep) -> None:
        seen.append((step.id, step.status))

    run_visit(
        visit_settings,
        endpoints=fake_controller,
        store=visit_store,
        now=NOW,
        lookback_days=2,
        progress=progress,
    )
    # Each step reports at least a running then a terminal status.
    running = {sid for sid, status in seen if status == "running"}
    assert running == {sid for sid, _ in STEP_ORDER}
    terminal = {sid for sid, status in seen if status in ("ok", "failed", "skipped")}
    assert terminal == {sid for sid, _ in STEP_ORDER}


def test_visit_coverage_caveat_present(fake_controller, visit_store, visit_settings):
    report = _run(fake_controller, visit_store, settings=visit_settings)
    # A one-shot visit has thin live coverage; that limitation is stated, not hidden.
    assert any("Live poll coverage" in c or "one live sample" in c for c in report.caveats)


def test_visit_opens_its_own_store_when_none_given(fake_controller, visit_settings):
    # No injected store: the runner opens and closes a throwaway visit DB itself.
    report = run_visit(visit_settings, endpoints=fake_controller, now=NOW, lookback_days=2)
    assert report.db_path is not None
    assert report.topology["entity_count"] >= 5
