"""Unit tests for the report's severity ladder (a static rename of P1/P2/P3)."""

from __future__ import annotations

from netadmin.report import severity as sev


def test_mapping_is_a_fixed_rename_of_netadmin_severity() -> None:
    # A static 1:1 rename -- never conditioned on measured impact (Gitea #22:
    # the old impact-refined mapping let the same P1 render as either word).
    assert sev.to_severity_label("p1") == sev.CRITICAL
    assert sev.to_severity_label("p1", environmental=False) == sev.CRITICAL
    assert sev.to_severity_label("p2") == sev.HIGH
    assert sev.to_severity_label("p3") == sev.LOW
    assert sev.to_severity_label(None) == sev.INFO


def test_environmental_is_always_info_regardless_of_p_level() -> None:
    # The aggregated neighbour/channel-plan finding is context, not a fault --
    # Info regardless of how severe the underlying issues were.
    assert sev.to_severity_label(None, environmental=True) == sev.INFO
    assert sev.to_severity_label("p1", environmental=True) == sev.INFO
    assert sev.to_severity_label("p2", environmental=True) == sev.INFO


def test_rank_orders_critical_first() -> None:
    ranks = [sev.severity_rank(x) for x in sev.SEVERITY_ORDER]
    assert ranks == sorted(ranks)
    assert sev.severity_rank(sev.CRITICAL) < sev.severity_rank(sev.INFO)
    assert sev.severity_rank("nonsense") == len(sev.SEVERITY_ORDER)


def test_rubric_covers_every_level_with_a_colour() -> None:
    rubric = sev.severity_rubric()
    assert [r["level"] for r in rubric] == list(sev.SEVERITY_ORDER)
    for row in rubric:
        assert row["color_light"].startswith("#")
        assert row["color_dark"].startswith("#")
        assert row["label"] and row["meaning"] and row["netadmin_source"]
