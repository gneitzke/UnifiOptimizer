"""Unit tests for the CVSS severity ladder and its impact-refined mapping."""

from __future__ import annotations

from netadmin.report import severity as sev


def test_mapping_uses_measured_impact_to_split_the_top() -> None:
    # P1: Critical when it burned failed minutes, High when it did not (yet).
    assert sev.to_cvss("p1", fail_minutes=120.0) == sev.CRITICAL
    assert sev.to_cvss("p1", fail_minutes=0.0) == sev.HIGH
    # P2: High with impact, Medium without.
    assert sev.to_cvss("p2", fail_minutes=5.0) == sev.HIGH
    assert sev.to_cvss("p2", fail_minutes=0.0) == sev.MEDIUM
    # P3 is always Low; unknown/None is Info.
    assert sev.to_cvss("p3", fail_minutes=999.0) == sev.LOW
    assert sev.to_cvss(None) == sev.INFO


def test_environmental_defaults_to_info_without_a_graver_issue() -> None:
    assert sev.to_cvss(None, environmental=True) == sev.INFO


def test_rank_orders_critical_first() -> None:
    ranks = [sev.cvss_rank(x) for x in sev.CVSS_ORDER]
    assert ranks == sorted(ranks)
    assert sev.cvss_rank(sev.CRITICAL) < sev.cvss_rank(sev.INFO)
    assert sev.cvss_rank("nonsense") == len(sev.CVSS_ORDER)


def test_rubric_covers_every_level_with_a_colour() -> None:
    rubric = sev.severity_rubric()
    assert [r["level"] for r in rubric] == list(sev.CVSS_ORDER)
    for row in rubric:
        assert row["color_light"].startswith("#")
        assert row["color_dark"].startswith("#")
        assert row["label"] and row["meaning"] and row["netadmin_source"]
