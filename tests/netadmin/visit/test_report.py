"""Rendering a :class:`VisitReport` to HTML / JSON / console."""

from __future__ import annotations

import json

from netadmin.visit.report import console_summary, render_html, render_json
from netadmin.visit.runner import run_visit

from .conftest import NOW


def _report(fake, store, settings):
    return run_visit(settings, endpoints=fake, store=store, now=NOW, lookback_days=2)


def test_render_json_roundtrips(fake_controller, visit_store, visit_settings):
    report = _report(fake_controller, visit_store, visit_settings)
    payload = json.loads(render_json(report))
    assert payload["site_id"] == "default"
    assert payload["issue_counts"]["open"] >= 2
    assert "sles" in payload["sles"]


def test_render_html_is_self_contained(fake_controller, visit_store, visit_settings):
    report = _report(fake_controller, visit_store, visit_settings)
    html_doc = render_html(report)
    assert html_doc.startswith("<!doctype html>")
    # Self-contained: no external stylesheet, script, font, or image request.
    lowered = html_doc.lower()
    assert "http://" not in lowered and "https://" not in lowered
    assert "<link" not in lowered and "<script" not in lowered
    # Both themes styled.
    assert "prefers-color-scheme: dark" in html_doc
    # The findings and the health headline made it into the document.
    assert "Channel-plan" in html_doc or "channel_plan" in html_doc.lower()
    assert "Network health" in html_doc


def test_console_summary_is_readable(fake_controller, visit_store, visit_settings):
    report = _report(fake_controller, visit_store, visit_settings)
    text = console_summary(report)
    assert "Tech visit" in text
    assert "Network health" in text
    assert "Issues:" in text
    # No raw HTML leaks into the terminal summary.
    assert "<" not in text


def test_html_escapes_untrusted_text(fake_controller, visit_store, visit_settings):
    report = _report(fake_controller, visit_store, visit_settings)
    # Inject a hostile title as if a controller had named a device with markup.
    report.issues[0]["title"] = "<script>alert(1)</script>"
    report.issues[0]["state"] = "active"
    html_doc = render_html(report)
    assert "<script>alert(1)</script>" not in html_doc
    assert "&lt;script&gt;" in html_doc


# --------------------------------------------------------------------------- #
# the header must describe the window actually analysed
# --------------------------------------------------------------------------- #
def _capped_report(fake, store, settings):
    """A visit whose requested lookback exceeds the SLE sweep cap.

    ``_MAX_SLE_SWEEP_S`` clamps the analysed window to the most recent 3 days, and
    the report is built with ``window_start_ts=sle_start`` — the clamped value —
    while ``lookback_days`` keeps the larger number that was asked for.
    """
    return run_visit(settings, endpoints=fake, store=store, now=NOW, lookback_days=7)


def test_header_does_not_claim_a_window_it_did_not_analyse(
    fake_controller, visit_store, visit_settings
):
    """A 7-day lookback capped to 3 days must not print "7-day lookback".

    The console line and the HTML header both render the real window next to
    ``lookback_days``, so a capped run advertised a span it never looked at: the
    reader compares two reports of the same network, sees scores 24 points apart,
    and has nothing to tell them the windows differed. The caveat says the sweep
    was capped; the header must not contradict it.
    """
    report = _capped_report(fake_controller, visit_store, visit_settings)
    analysed_days = round((report.window_end_ts - report.window_start_ts) / 86_400)
    assert analysed_days < report.lookback_days, "fixture must actually trip the cap"

    summary = console_summary(report)
    html_doc = render_html(report)

    # Neither surface may present the requested lookback as if it described the
    # window that was analysed...
    assert f"{report.lookback_days}d lookback)" not in summary
    assert f"{report.lookback_days}-day lookback ·" not in html_doc
    assert f"{analysed_days}-day" in html_doc
    assert f"{analysed_days}d" in summary

    # ...and the request must still be disclosed, not quietly dropped. Asserting
    # only the absence of the old string lets a fix that deletes the whole
    # parenthetical pass: the header would then read "3d lookback" and the reader
    # would never learn a 7-day window was asked for.
    assert f"{report.lookback_days}d lookback requested" in summary
    assert f"{report.lookback_days}-day lookback requested" in html_doc


def test_uncapped_window_wording_is_unchanged(fake_controller, visit_store, visit_settings):
    """The common case must read exactly as it always did.

    The capped/uncapped branch is a boolean, so an inverted or hardcoded
    ``window_was_capped`` would decorate *every* ordinary report with a
    contradictory "(2-day lookback requested)". Nothing else pins that.
    """
    report = run_visit(
        visit_settings, endpoints=fake_controller, store=visit_store, now=NOW, lookback_days=2
    )
    assert not report.window_was_capped
    assert report.window_days == report.lookback_days == 2

    assert "(2d lookback)" in console_summary(report)
    assert "2-day lookback ·" in render_html(report)
    assert "requested" not in console_summary(report)
    assert "lookback requested" not in render_html(report)


def test_derived_window_facts_are_serialised(fake_controller, visit_store, visit_settings):
    """They are properties, and ``asdict`` copies fields only.

    Left unserialised, every machine consumer -- ``render_json``, the on-demand
    API, the web /visit page -- keeps seeing only the requested lookback, which
    is the whole defect. The JSON export is where that silently regresses.
    """
    report = _capped_report(fake_controller, visit_store, visit_settings)
    payload = json.loads(render_json(report))
    assert payload["window_days"] == report.window_days
    assert payload["window_was_capped"] is True
    assert payload["lookback_days"] == report.lookback_days


def test_window_was_capped_compares_seconds_not_rounded_days():
    """A part-day shortfall still counts as capped.

    Comparing the floored ``window_days`` against ``lookback_days`` would call a
    6.5-day analysis of a 7-day request "capped" only by luck of rounding, and a
    3.5-day one not capped at all -- silently suppressing the disclosure.
    """
    from netadmin.visit.runner import VisitReport

    end = 1_800_000_000
    half = VisitReport(
        started_ts=0,
        finished_ts=0,
        window_start_ts=end - int(6.5 * 86_400),
        window_end_ts=end,
        site_id="default",
        lookback_days=7,
        controller_host=None,
        headline_score=None,
    )
    assert half.window_was_capped is True
    exact = VisitReport(
        started_ts=0,
        finished_ts=0,
        window_start_ts=end - 7 * 86_400,
        window_end_ts=end,
        site_id="default",
        lookback_days=7,
        controller_host=None,
        headline_score=None,
    )
    assert exact.window_was_capped is False
