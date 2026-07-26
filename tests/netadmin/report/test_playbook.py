"""Unit tests for the catalog-playbook-backed finding guidance."""

from __future__ import annotations

from netadmin.report.playbook import finding_guidance


def test_registered_detector_returns_playbook_text() -> None:
    g = finding_guidance("wifi.mesh_uplink")
    assert g.has_playbook is True
    assert g.signature  # the diagnostic signature is present
    assert g.recommendation  # a concrete fix, from the playbook fix_guidance
    assert "mesh" in g.recommendation.lower() or "backhaul" in g.recommendation.lower()
    assert g.root_cause == g.signature


def test_correlated_symptoms_prefix_root_cause() -> None:
    g = finding_guidance("wifi.mesh_uplink", correlated_symptoms=3)
    assert g.root_cause.startswith("Root cause of 3 correlated symptoms")


def test_unknown_detector_gives_honest_named_advisory_not_vague_filler() -> None:
    g = finding_guidance("made.up_detector")
    assert g.has_playbook is False
    # The advisory is specific: it names the exact detector, never empty/vague.
    assert "made.up_detector" in g.recommendation
    assert "made.up_detector" in g.root_cause
    assert g.recommendation.strip() != ""
