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
