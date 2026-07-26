"""Tests for the output-discipline primitives (``docs/MCP_SERVER.md`` section 3).

These are the rules that keep a tool result readable: caps, downsampling,
timestamp rendering, evidence trimming, the size guard and redaction. They are
pure functions, so they are tested here directly rather than inferred from a
tool's output.
"""

from __future__ import annotations

import json

import pytest

from netadmin.mcp import format as fmt

NOW = 1_900_000_000


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
def test_iso_is_utc_with_trailing_z() -> None:
    assert fmt.iso(0) == "1970-01-01T00:00:00Z"
    assert fmt.iso(1_894_017_600) == "2030-01-07T12:00:00Z"
    assert fmt.iso(None) is None


@pytest.mark.parametrize(
    "delta,expected",
    [
        (0, "just now"),
        (59, "just now"),
        (60, "1m"),
        (42 * 60, "42m"),
        (3600, "1h"),
        (3 * 3600 + 12 * 60, "3h 12m"),
        (4 * 86_400 + 2 * 3600, "4d 2h"),
        (5 * 86_400, "5d"),
    ],
)
def test_ago_uses_at_most_two_units(delta: int, expected: str) -> None:
    assert fmt.ago(NOW - delta, NOW) == expected


def test_ago_renders_a_future_timestamp_forwards() -> None:
    assert fmt.ago(NOW + 300, NOW) == "in 5m"


def test_duration_keeps_seconds_below_a_minute() -> None:
    assert fmt.duration(18) == "18s"
    assert fmt.duration(3 * 3600 + 20 * 60) == "3h 20m"
    assert fmt.duration(None) is None


def test_stamp_carries_both_forms() -> None:
    assert fmt.stamp(NOW - 86_400, NOW) == {"at": fmt.iso(NOW - 86_400), "ago": "1d"}


# --------------------------------------------------------------------------- #
# Caps
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [(None, 20), ("", 20), (0, 20), (-5, 20), ("all", 20), (7, 7), (999, 50), ("30", 30)],
)
def test_clamp_limit_never_raises_and_never_exceeds_the_hard_cap(raw, expected: int) -> None:
    assert fmt.clamp_limit(raw) == expected


def test_listing_reports_total_and_truncation() -> None:
    block = fmt.listing([{"i": n} for n in range(30)], 20)
    assert len(block["items"]) == 20
    assert block["total"] == 30
    assert block["truncated"] is True

    block = fmt.listing([{"i": 1}], 20)
    assert block["truncated"] is False


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #
def test_downsample_never_exceeds_the_budget_and_keeps_the_left_edge() -> None:
    points = [(1_000_000 + n * 60, float(n)) for n in range(1000)]
    folded = fmt.downsample(points, fmt.MAX_SERIES_POINTS)
    assert len(folded) <= fmt.MAX_SERIES_POINTS
    assert folded[0][0] == fmt.iso(1_000_000)
    assert all(isinstance(entry, list) and len(entry) == 2 for entry in folded)


def test_downsample_drops_none_values_rather_than_zero_filling() -> None:
    points = [(100, None), (160, 5.0), (220, None)]
    assert fmt.downsample(points, 96) == [[fmt.iso(160), 5.0]]


def test_series_block_reports_extremes_from_the_unfolded_input() -> None:
    # A single spike must survive downsampling in min/max even though the folded
    # points average it away -- that spike is usually the answer.
    points = [(1_000_000 + n * 60, 1.0) for n in range(500)]
    points[250] = (points[250][0], 99.0)
    block = fmt.series_block(points, tier="raw")
    assert block["max"] == 99.0
    assert block["min"] == 1.0
    assert block["sample_count"] == 500
    assert len(block["points"]) <= fmt.MAX_SERIES_POINTS
    assert max(value for _, value in block["points"]) < 99.0


def test_series_block_on_empty_input_is_honest() -> None:
    block = fmt.series_block([], tier="hourly")
    assert block["points"] == []
    assert block["min"] is None and block["avg"] is None and block["max"] is None


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def test_trim_evidence_keeps_scalars_and_summarises_nesting() -> None:
    trimmed = fmt.trim_evidence(
        {
            "rssi_p50": -78,
            "threshold": -72,
            "healthy": False,
            "samples": [1, 2, 3, 4],
            "confounders": {"a": 1, "b": 2},
        }
    )
    assert trimmed["rssi_p50"] == -78
    assert trimmed["healthy"] is False
    assert trimmed["samples"] == "[list of 4]"
    assert trimmed["confounders"] == "[object with 2 keys]"


def test_trim_evidence_tolerates_non_mappings() -> None:
    assert fmt.trim_evidence(None) == {}
    assert fmt.trim_evidence("not json") == {}


# --------------------------------------------------------------------------- #
# Whole-payload guards
# --------------------------------------------------------------------------- #
def test_lead_with_summary_hoists_and_backfills() -> None:
    ordered = fmt.lead_with_summary({"z": 1, "summary": "hi"})
    assert list(ordered)[0] == "summary"
    assert fmt.lead_with_summary({"z": 1})["summary"] == "No summary available."


def test_guard_size_trims_the_biggest_list_and_says_so() -> None:
    payload = {
        "summary": "big",
        "rows": [{"text": "x" * 200, "n": n} for n in range(400)],
        "scalar": 42,
    }
    guarded = fmt.guard_size(payload)
    assert len(json.dumps(guarded).encode()) <= fmt.MAX_RESPONSE_BYTES
    assert "trimmed" in guarded["note"]
    assert guarded["summary"] == "big"
    assert guarded["scalar"] == 42
    assert len(guarded["rows"]) < 400


def test_guard_size_leaves_a_small_payload_untouched() -> None:
    payload = {"summary": "small", "rows": [1, 2, 3]}
    assert fmt.guard_size(payload) is payload


def test_guard_size_falls_back_to_the_summary_when_nothing_can_be_cut() -> None:
    payload = {"summary": "huge string", "blob": "x" * (fmt.MAX_RESPONSE_BYTES + 10)}
    guarded = fmt.guard_size(payload)
    assert guarded == {"summary": "huge string", "note": guarded["note"]}


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def test_mask_macs_keeps_the_oui_and_drops_the_device_half() -> None:
    assert fmt.Redactor.mask_macs("aa:BB:cc:11:22:33") == "aa:bb:cc:xx:xx:xx"
    assert "de:ad:be:xx:xx:xx" in fmt.Redactor.mask_macs("client de:ad:be:ef:00:01 left")


def test_pseudonyms_are_stable_and_kind_aware() -> None:
    first = fmt.Redactor.pseudonym("Work Laptop", kind="client")
    assert first == fmt.Redactor.pseudonym("work laptop", kind="client")
    assert first.startswith("client-")
    assert fmt.Redactor.pseudonym("Office AP", kind="device").startswith("device-")


def test_redact_masks_names_and_macs_but_keeps_entity_ids() -> None:
    redactor = fmt.Redactor({"Work Laptop": "client-abcd"})
    out = redactor.redact(
        {
            "entity": {
                "entity_id": 48,
                "name": "Work Laptop",
                "type": "client",
                "native_id": "02:c1:00:00:0e:58",
            },
            "msg": "Work Laptop roamed from 02:a9:00:00:06:b0",
        }
    )
    assert out["entity"]["entity_id"] == 48
    assert out["entity"]["name"].startswith("client-")
    assert out["entity"]["native_id"] == "02:c1:00:xx:xx:xx"
    assert "Work Laptop" not in out["msg"]
    assert "02:a9:00:xx:xx:xx" in out["msg"]


def test_build_known_names_skips_names_too_short_to_replace_safely() -> None:
    rows = [
        {"name": "AP", "entity_type": "ap"},
        {"name": "Work Laptop", "entity_type": "client"},
        {"name": None, "entity_type": "client"},
    ]
    known = fmt.build_known_names(rows)
    assert "AP" not in known
    assert known["Work Laptop"].startswith("client-")
