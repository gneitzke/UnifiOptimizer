"""Per-tool logic tests, called directly with no protocol in the way.

The tools are plain ``(Repository, params, now) -> dict`` functions precisely so
they can be tested like this: against the deterministic demo store, at its fixed
anchor timestamp, with no SDK, no stdio and no event loop. What is asserted is
the *answer*, not the plumbing -- that ``when_did_this_start`` finds the onset the
issue trail recorded, that ``sle_trend`` folds 5-minute buckets into the right
day, that an ambiguous name comes back as candidates instead of a guess.
"""

from __future__ import annotations

from typing import Any

import pytest

from netadmin.mcp import format as fmt
from netadmin.mcp import tools
from netadmin.store.repository import Repository

from .conftest import DEMO_NOW


def _call(repo: Repository, name: str, **params: Any) -> dict[str, Any]:
    return tools.call_tool(repo, name, params, now=DEMO_NOW)


def _first_open_issue_id(repo: Repository) -> int:
    return int(repo.list_issues(open_only=True)[0]["id"])


# --------------------------------------------------------------------------- #
# Argument plumbing shared by every tool
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "window,seconds",
    [("30m", 1800), ("24h", 86_400), ("7d", 604_800), ("30d", 2_592_000), ("2w", 1_209_600)],
)
def test_parse_window_accepts_the_documented_shorthand(window: str, seconds: int) -> None:
    start, end = tools.parse_window({"window": window}, DEMO_NOW)
    assert end == DEMO_NOW
    assert end - start == seconds


def test_parse_window_prefers_explicit_iso_bounds() -> None:
    start, end = tools.parse_window(
        {"window": "7d", "start": "2030-01-01T00:00:00Z", "end": "2030-01-02T00:00:00Z"},
        DEMO_NOW,
    )
    assert (fmt.iso(start), fmt.iso(end)) == ("2030-01-01T00:00:00Z", "2030-01-02T00:00:00Z")


def test_parse_window_rejects_nonsense_with_an_actionable_message() -> None:
    with pytest.raises(tools.ToolError) as excinfo:
        tools.parse_window({"window": "last tuesday"}, DEMO_NOW)
    assert '"24h"' in str(excinfo.value)


def test_parse_window_rejects_an_inverted_range() -> None:
    with pytest.raises(tools.ToolError):
        tools.parse_window({"start": DEMO_NOW, "end": DEMO_NOW - 10}, DEMO_NOW)


def test_bad_window_reaches_the_caller_as_a_payload_not_an_exception(
    demo_repo: Repository,
) -> None:
    result = _call(demo_repo, "netadmin_overview", window="soon")
    assert result["error"] == "invalid_request"
    assert list(result)[0] == "summary"


def test_unknown_tool_lists_the_real_ones(demo_repo: Repository) -> None:
    result = tools.call_tool(demo_repo, "netadmin_reboot_everything", {})
    assert result["error"] == "unknown_tool"
    assert "netadmin_overview" in result["available_tools"]


# --------------------------------------------------------------------------- #
# Entity resolution
# --------------------------------------------------------------------------- #
def test_entity_resolves_by_id_mac_and_name(demo_repo: Repository) -> None:
    client = demo_repo.list_entities("client")[0]
    by_id = tools.resolve_entity(demo_repo, str(client["entity_id"]))
    by_mac = tools.resolve_entity(demo_repo, client["native_id"])
    by_name = tools.resolve_entity(demo_repo, client["name"])
    assert by_id["entity_id"] == by_mac["entity_id"] == by_name["entity_id"]


def test_entity_name_match_is_case_insensitive(demo_repo: Repository) -> None:
    client = demo_repo.list_entities("client")[0]
    resolved = tools.resolve_entity(demo_repo, str(client["name"]).upper())
    assert resolved["entity_id"] == client["entity_id"]


def test_ambiguous_entity_returns_candidates_never_a_guess(demo_repo: Repository) -> None:
    # "Port " prefixes many switch ports in the demo network.
    with pytest.raises(tools.AmbiguousEntity) as excinfo:
        tools.resolve_entity(demo_repo, "Port")
    payload = excinfo.value.payload()
    assert payload["error"] == "ambiguous_entity"
    assert len(payload["candidates"]) > 1
    assert all("entity_id" in candidate for candidate in payload["candidates"])


def test_unknown_entity_is_reported_not_guessed(demo_repo: Repository) -> None:
    with pytest.raises(tools.EntityNotFound):
        tools.resolve_entity(demo_repo, "definitely-not-a-device")


# --------------------------------------------------------------------------- #
# 1. netadmin_overview
# --------------------------------------------------------------------------- #
def test_overview_leads_with_counts_health_and_collector_state(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_overview", window="24h")
    assert result["open_issues"]["total"] == len(demo_repo.list_issues(open_only=True))
    assert result["open_incidents"]["total"] == len(demo_repo.list_incidents(open_only=True))
    assert 0 <= result["sle"]["headline_pct"] <= 100
    assert result["sle"]["delta_pct"] == (
        result["sle"]["headline_pct"] - result["sle"]["prior_headline_pct"]
    )
    jobs = {job["job"] for job in result["collector_health"]["jobs"]}
    assert "fast_device" in jobs
    assert result["summary"].count(".") <= 3


def test_overview_gives_the_honest_incident_split_not_a_raw_count(
    demo_repo: Repository,
) -> None:
    """Gitea #21: the summary must not narrate every incident-of-one as an
    "incident" (the demo has 1 genuine group of 4 out of 11 incident rows)."""
    result = _call(demo_repo, "netadmin_overview", window="24h")
    genuine = demo_repo.list_incidents(open_only=True, genuine_only=True)
    assert len(genuine) == 1  # the demo's Back Porch mesh cluster
    assert "1 incident grouping" in result["summary"]
    assert "open incident(s)" not in result["summary"]


def test_overview_compares_against_the_immediately_prior_window(
    demo_repo: Repository,
) -> None:
    """The trend term must be the *previous* window of the same length."""
    from netadmin.sle.scores import sle_scores

    result = _call(demo_repo, "netadmin_overview", window="24h")
    prior = sle_scores(demo_repo, DEMO_NOW - 2 * 86_400, DEMO_NOW - 86_400)
    assert result["sle"]["prior_headline_pct"] == int(round(prior.headline * 100))


# --------------------------------------------------------------------------- #
# 2. netadmin_when_did_this_start  (flagship)
# --------------------------------------------------------------------------- #
def test_when_did_this_start_uses_the_detected_event_as_onset(demo_repo: Repository) -> None:
    issue_id = _first_open_issue_id(demo_repo)
    detected = [row for row in demo_repo.list_issue_events(issue_id) if row["kind"] == "detected"]
    result = _call(demo_repo, "netadmin_when_did_this_start", issue=issue_id)
    assert result["onset"]["at"] == fmt.iso(int(detected[0]["ts"]))
    assert result["onset_source"] == "issue_events.detected"


def test_when_did_this_start_searches_before_onset_and_an_hour_after(
    demo_repo: Repository,
) -> None:
    issue_id = _first_open_issue_id(demo_repo)
    result = _call(demo_repo, "netadmin_when_did_this_start", issue=issue_id, window="24h")
    searched = result["changed_near_onset"]["searched"]
    assert searched["length"] == "1d 1h"
    for section in ("state_changes", "fixes", "events"):
        assert set(result["changed_near_onset"][section]) == {"items", "total", "truncated"}


def test_when_did_this_start_trims_evidence_to_scalars(demo_repo: Repository) -> None:
    issue_id = _first_open_issue_id(demo_repo)
    result = _call(demo_repo, "netadmin_when_did_this_start", issue=issue_id)
    assert all(
        value is None or isinstance(value, (bool, int, float, str))
        for value in result["evidence"].values()
    )


def test_when_did_this_start_excludes_the_anchor_from_prior_occurrences(
    demo_repo: Repository,
) -> None:
    issue_id = _first_open_issue_id(demo_repo)
    result = _call(demo_repo, "netadmin_when_did_this_start", issue=issue_id)
    assert all(row["issue_id"] != issue_id for row in result["prior_occurrences"]["items"])


def test_when_did_this_start_needs_a_real_issue(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_when_did_this_start", issue=999_999)
    assert "netadmin_issues" in result["summary"]
    assert result["error"] == "invalid_request"


# --------------------------------------------------------------------------- #
# 3. netadmin_has_this_happened_before
# --------------------------------------------------------------------------- #
def test_recurrence_is_traced_by_fingerprint(demo_repo: Repository) -> None:
    issue_id = _first_open_issue_id(demo_repo)
    fingerprint = str(demo_repo.get_issue(issue_id)["fingerprint"])
    by_issue = _call(demo_repo, "netadmin_has_this_happened_before", issue=issue_id)
    by_fingerprint = _call(demo_repo, "netadmin_has_this_happened_before", fingerprint=fingerprint)
    assert by_issue["fingerprint"] == fingerprint
    assert by_issue["occurrences"]["total"] == by_fingerprint["occurrences"]["total"]
    assert by_fingerprint["anchor_issue_id"] is None


def test_recurrence_carries_the_fixes_tried_on_each_occurrence(
    demo_repo: Repository,
) -> None:
    fixed = next(
        row for row in demo_repo.list_issues() if demo_repo.list_changes(issue_id=int(row["id"]))
    )
    result = _call(demo_repo, "netadmin_has_this_happened_before", issue=int(fixed["id"]))
    assert any(entry["fixes_tried"] for entry in result["occurrences"]["items"])


def test_an_unseen_fingerprint_says_so_plainly(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_has_this_happened_before", fingerprint="deadbeef")
    assert "has ever been recorded" in result["summary"]
    assert result["occurrences"]["items"] == []


# --------------------------------------------------------------------------- #
# 4. netadmin_issues
# --------------------------------------------------------------------------- #
def test_issues_lists_open_by_default(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_issues")
    assert result["issues"]["total"] == len(demo_repo.list_issues(open_only=True))
    assert all(row["state"] != "resolved" for row in result["issues"]["items"])


def test_issues_can_include_resolved(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_issues", open_only=False, limit=50)
    assert result["issues"]["total"] == len(demo_repo.list_issues())


def test_issues_filters_by_severity_and_entity(demo_repo: Repository) -> None:
    by_severity = _call(demo_repo, "netadmin_issues", severity="p1")
    assert all(row["severity"] == "p1" for row in by_severity["issues"]["items"])

    owner_id = next(
        int(row["entity_id"])
        for row in demo_repo.list_issues(open_only=True)
        if row["entity_id"] is not None
    )
    by_entity = _call(demo_repo, "netadmin_issues", entity=str(owner_id))
    assert by_entity["issues"]["total"] >= 1
    assert all(row["entity"]["entity_id"] == owner_id for row in by_entity["issues"]["items"])


def test_issue_detail_carries_lifecycle_fixes_and_investigations(
    demo_repo: Repository,
) -> None:
    investigated = next(
        row for row in demo_repo.list_issues() if demo_repo.list_investigations(int(row["id"]))
    )
    result = _call(demo_repo, "netadmin_issues", issue=int(investigated["id"]))
    assert result["issue"]["issue_id"] == int(investigated["id"])
    assert result["lifecycle"]["total"] >= 1
    assert result["investigations"]["total"] >= 1
    assert "provider" in result["investigations"]["items"][0]


# --------------------------------------------------------------------------- #
# 5. netadmin_incidents
# --------------------------------------------------------------------------- #
def test_incidents_list_defaults_to_genuine_groups_only(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_incidents")
    genuine = demo_repo.list_incidents(open_only=True, genuine_only=True)
    assert result["incidents"]["total"] == len(genuine)
    assert all(row["member_count"] >= 2 for row in result["incidents"]["items"])
    assert "standalone" in result["summary"]


def test_incidents_list_include_singletons_restores_uniform_view(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_incidents", include_singletons=True)
    assert result["incidents"]["total"] == len(demo_repo.list_incidents(open_only=True))
    assert all(row["member_count"] >= 1 for row in result["incidents"]["items"])


def test_incident_detail_separates_root_from_symptoms(demo_repo: Repository) -> None:
    incident_id = int(demo_repo.list_incidents(open_only=True, genuine_only=True)[0]["id"])
    result = _call(demo_repo, "netadmin_incidents", incident=incident_id)
    roles = [member["role"] for member in result["members"]["items"]]
    assert roles[0] == "root"
    assert all("title" in member for member in result["members"]["items"])


def test_unknown_incident_points_back_at_the_list_tool(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_incidents", incident=999_999)
    assert "netadmin_incidents" in result["summary"]


# --------------------------------------------------------------------------- #
# 6. netadmin_sle_trend
# --------------------------------------------------------------------------- #
def test_sle_trend_auto_buckets_by_window_length(demo_repo: Repository) -> None:
    assert _call(demo_repo, "netadmin_sle_trend", window="24h")["bucket"] == "hour"
    assert _call(demo_repo, "netadmin_sle_trend", window="7d")["bucket"] == "day"


def test_sle_trend_bucket_scores_are_percentages_with_fail_minutes(
    demo_repo: Repository,
) -> None:
    result = _call(demo_repo, "netadmin_sle_trend", window="7d")
    assert result["direction"] in {"improving", "worsening", "flat"}
    for bucket in result["buckets"]["items"]:
        assert 0 <= bucket["score_pct"] <= 100
        assert bucket["fail_minutes"] >= 0
        assert bucket["fail_minutes"] <= bucket["total_minutes"]


def test_sle_trend_can_be_narrowed_to_one_sle(demo_repo: Repository) -> None:
    everything = _call(demo_repo, "netadmin_sle_trend", window="7d")
    coverage = _call(demo_repo, "netadmin_sle_trend", window="7d", sle="coverage")
    day = coverage["buckets"]["items"][0]
    assert day["total_minutes"] < everything["buckets"]["items"][0]["total_minutes"]


@pytest.mark.parametrize(
    "scores,expected",
    [
        ([90, 90, 95, 95], "improving"),
        ([95, 95, 90, 90], "worsening"),
        ([90, 91, 90, 91], "flat"),
        ([90], "flat"),
    ],
)
def test_trend_direction_has_a_dead_band(scores: list[int], expected: str) -> None:
    assert tools._trend_direction(scores)[0] == expected


# --------------------------------------------------------------------------- #
# 7. netadmin_what_changed
# --------------------------------------------------------------------------- #
def test_what_changed_merges_three_sources_newest_first(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_what_changed", window="7d", limit=50)
    kinds = {entry["kind"] for entry in result["timeline"]["items"]}
    assert kinds <= {"state", "fix", "event"}
    stamps = [entry["at"] for entry in result["timeline"]["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert result["counts"]["state_changes"] >= 1


def test_what_changed_scoped_to_one_entity_only_shows_that_entity(
    demo_repo: Repository,
) -> None:
    radio = next(
        row
        for row in demo_repo.list_entities("radio")
        if demo_repo.list_state_changes(DEMO_NOW - 604_800, DEMO_NOW, entity_id=row["entity_id"])
    )
    result = _call(demo_repo, "netadmin_what_changed", window="7d", entity=str(radio["entity_id"]))
    assert result["entity"]["entity_id"] == int(radio["entity_id"])
    for entry in result["timeline"]["items"]:
        if entry.get("entity"):
            assert entry["entity"]["entity_id"] == int(radio["entity_id"])


# --------------------------------------------------------------------------- #
# 8. netadmin_worst_offenders
# --------------------------------------------------------------------------- #
def test_worst_offenders_ranks_devices_by_burden(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_worst_offenders", window="7d")
    scores = [row["score"] for row in result["offenders"]["items"]]
    assert scores == sorted(scores, reverse=True)
    assert all(
        row["entity"]["type"] in {"ap", "switch", "gateway"} for row in result["offenders"]["items"]
    )
    assert set(result["offenders"]["items"][0]["components"]) == {"sle_minutes", "issues", "events"}


def test_worst_offenders_clients_surface_ranks_clients(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_worst_offenders", window="7d", surface="clients")
    assert all(row["entity"]["type"] == "client" for row in result["offenders"]["items"])


def test_worst_offenders_rejects_an_unknown_surface(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_worst_offenders", surface="switches")
    assert result["error"] == "invalid_request"


# --------------------------------------------------------------------------- #
# 9. netadmin_metric_history
# --------------------------------------------------------------------------- #
def test_metric_history_downsamples_and_names_its_tier(demo_repo: Repository) -> None:
    client = next(
        row
        for row in demo_repo.list_entities("client")
        if demo_repo.get_series(row["entity_id"], "rssi")
    )
    result = _call(
        demo_repo,
        "netadmin_metric_history",
        entity=str(client["entity_id"]),
        metric="rssi",
        window="7d",
    )
    series = result["series"]
    assert len(series["points"]) <= fmt.MAX_SERIES_POINTS
    assert series["tier"] in {"raw", "hourly", "daily"}
    assert series["min"] <= series["avg"] <= series["max"]
    assert all(len(point) == 2 and point[0].endswith("Z") for point in series["points"])


def test_metric_history_returns_the_learned_baseline(demo_repo: Repository) -> None:
    radio = next(
        row
        for row in demo_repo.list_entities("radio")
        if demo_repo.get_baselines(demo_repo.get_series(row["entity_id"], "cu_total") or -1, "all")
    )
    result = _call(
        demo_repo, "netadmin_metric_history", entity=str(radio["entity_id"]), metric="cu_total"
    )
    assert "ewma_mean" in result["baseline"]


def test_metric_history_on_an_unknown_metric_offers_the_real_ones(
    demo_repo: Repository,
) -> None:
    client = demo_repo.list_entities("client")[0]
    result = _call(
        demo_repo, "netadmin_metric_history", entity=str(client["entity_id"]), metric="nonsense"
    )
    assert "no 'nonsense' series" in result["summary"] or "has no" in result["summary"]
    assert result["available_metrics"]


# --------------------------------------------------------------------------- #
# 10. netadmin_events_around
# --------------------------------------------------------------------------- #
def test_events_around_groups_by_key_with_exemplars(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_events_around", at=fmt.iso(DEMO_NOW - 3600), radius="24h")
    groups = result["groups"]["items"]
    assert groups
    assert [group["count"] for group in groups] == sorted(
        (group["count"] for group in groups), reverse=True
    )
    assert all(len(group["exemplars"]) <= 3 for group in groups)


def test_events_around_can_anchor_on_an_issue_onset(demo_repo: Repository) -> None:
    issue_id = _first_open_issue_id(demo_repo)
    issue = demo_repo.get_issue(issue_id)
    result = _call(demo_repo, "netadmin_events_around", issue=issue_id, radius="1h")
    assert result["anchor"]["at"] == fmt.iso(int(issue["first_seen_ts"]))


def test_events_around_requires_an_anchor(demo_repo: Repository) -> None:
    result = _call(demo_repo, "netadmin_events_around")
    assert "at" in result["summary"] and "issue" in result["summary"]


# --------------------------------------------------------------------------- #
# 11. netadmin_client_experience
# --------------------------------------------------------------------------- #
def test_client_experience_tells_one_clients_story(demo_repo: Repository) -> None:
    client = next(
        row
        for row in demo_repo.list_entities("client")
        if demo_repo.state_history(row["entity_id"], "ap_mac", limit=5)
    )
    result = _call(
        demo_repo, "netadmin_client_experience", entity=str(client["entity_id"]), window="7d"
    )
    assert result["entity"]["type"] == "client"
    assert set(result["sle"]) <= {"coverage", "roaming", "capacity", "connect", "wan", "infra"}
    for cell in result["sle"].values():
        assert cell["fail_minutes"] <= cell["total_minutes"]
    assert result["ap_history"]["total"] >= 0


def test_client_experience_resolves_ap_macs_to_names(demo_repo: Repository) -> None:
    roamer = next(
        row
        for row in demo_repo.list_entities("client")
        if len(demo_repo.state_history(row["entity_id"], "ap_mac", limit=5)) > 1
    )
    result = _call(
        demo_repo, "netadmin_client_experience", entity=str(roamer["entity_id"]), window="7d"
    )
    labels = [entry["to"] for entry in result["ap_history"]["items"] if entry["to"]]
    ap_names = {str(row["name"]) for row in demo_repo.list_entities("ap")}
    assert any(label in ap_names for label in labels)
