"""The eleven MCP tools, as plain ``(Repository, params, now) -> dict`` functions.

Transport-agnostic by design (``docs/MCP_SERVER.md`` section 1): nothing here
imports the MCP SDK, so the whole tool surface is unit-testable against a temp
SQLite store with no protocol machinery in the way. :mod:`netadmin.mcp.server`
is the thin layer that binds these to stdio.

The tools are chosen around what a *history* store can answer and a live
controller cannot. There is no ``list_devices`` here and there never will be: if
Claude also has a live-controller MCP server loaded, the routing between them has
to be obvious, and it is only obvious if this server never offers a live-state
twin. Every description says so in the same words.

Read-only is structural, not conventional. This module calls only repository
*read* methods; the connection underneath is opened ``mode=ro`` with ``PRAGMA
query_only=ON``; and the imports are limited to :mod:`netadmin.store`,
:mod:`netadmin.sle`, :mod:`netadmin.analytics` and :mod:`netadmin.domain` -- the
fix and ingest layers, the only code in the project that can change a
controller, are not reachable from this process.

Output discipline (section 3) is enforced *centrally* in :func:`call_tool`, not
tool by tool: the summary-first ordering, the hard row-cap sweep, optional
redaction and the response-size guard all run over whatever a handler returned.
A handler that forgets a cap is corrected by the dispatcher rather than shipping
a 200-row payload.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from netadmin.analytics.offenders import CLIENT_ENTITY_TYPES, DEVICE_ENTITY_TYPES, rank_offenders
from netadmin.domain.types import EntityType
from netadmin.mcp import format as fmt
from netadmin.sle.scores import sle_scores
from netadmin.store import db as _db
from netadmin.store.repository import Repository

__all__ = [
    "TOOLS",
    "ToolSpec",
    "ToolError",
    "call_tool",
    "schema_gate",
    "redaction_enabled",
]

# --------------------------------------------------------------------------- #
# Shared vocabulary
# --------------------------------------------------------------------------- #

# The sentence that steers routing when a live-controller UniFi MCP server is
# loaded alongside this one. Every tool description carries it verbatim; changing
# the wording changes how reliably Claude picks the right server, so it lives in
# exactly one place.
_ROUTING = (
    "Answers from the local UnifiOptimizer history store; works even when the "
    "controller has forgotten or the daemon is down. Read-only: this tool never "
    "writes to the store and never touches the controller."
)

# Collector jobs whose poll_runs rows describe whether history was actually being
# recorded. Mirrors the job ids netadmin.ingest emits (kept as bare strings so
# this package never imports the ingest layer).
_COLLECTOR_JOBS: tuple[str, ...] = (
    "fast_device",
    "fast_sta",
    "fast_health",
    "events_catchup",
    "reports_5min",
    "detect_fast",
)

# Events that describe something *changing* rather than something merely
# happening: link transitions, WAN failover, radar-driven channel moves, firmware
# and adoption lifecycle. Keys the controller never sent are simply absent from
# the store, so an over-inclusive list costs nothing.
_CHANGE_EVENT_KEYS: tuple[str, ...] = (
    "EVT_SW_PortUp",
    "EVT_SW_PortDown",
    "EVT_SW_PoeOverload",
    "EVT_SW_StpPortBlocking",
    "EVT_GW_WANTransition",
    "EVT_AP_RadarDetected",
    "EVT_AP_Upgraded",
    "EVT_SW_Upgraded",
    "EVT_GW_Upgraded",
    "EVT_AP_Restarted",
    "EVT_SW_Restarted",
    "EVT_GW_Restarted",
    "EVT_AP_Adopted",
    "EVT_SW_Adopted",
    "EVT_AP_Configured",
)

# One client's connectivity story: joins, leaves, roams.
_CLIENT_EVENT_KEYS: tuple[str, ...] = (
    "EVT_WU_Connected",
    "EVT_WU_Disconnected",
    "EVT_LU_Disconnected",
    "EVT_WU_Roam",
    "EVT_WU_RoamRadio",
)

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_WINDOW_UNITS = {"m": 60, "h": 3600, "d": 86_400, "w": 604_800}

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# How far *before* an onset to look for a cause, and how far after to look for
# the cascade. Causes precede symptoms; the hour after catches what the fault
# then knocked over.
_ONSET_LOOKAHEAD_S = 3600


# --------------------------------------------------------------------------- #
# Errors: every one of these becomes a payload, never a protocol exception
# --------------------------------------------------------------------------- #
class ToolError(Exception):
    """A condition the *model* should read and act on, not a crash.

    An MCP tool that raises gives Claude an error string and no way forward. A
    tool that returns ``{"summary": "No issue 42 in the store; call
    netadmin_issues to list them."}`` gives it the next move. So every expected
    failure -- bad window, unknown id, ambiguous name -- is raised as one of these
    and converted to a payload by :func:`call_tool`.
    """

    error_code = "invalid_request"

    def __init__(self, summary: str, **extra: Any) -> None:
        super().__init__(summary)
        self.summary = summary
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"summary": self.summary, "error": self.error_code, **self.extra}


class EntityNotFound(ToolError):
    """No entity matched an ``entity`` argument."""

    error_code = "entity_not_found"


class AmbiguousEntity(ToolError):
    """More than one entity matched -- return the candidates, never a guess.

    Guessing which "iPhone" the user meant produces a confidently wrong answer
    about the wrong device, which is worse than no answer. The candidate list
    carries ``entity_id``s, and every tool accepts an ``entity_id``, so the
    follow-up call is unambiguous.
    """

    error_code = "ambiguous_entity"


# --------------------------------------------------------------------------- #
# Startup gate
# --------------------------------------------------------------------------- #
def schema_gate(repo: Repository) -> Optional[str]:
    """Guidance string when the store's schema does not match this build, else None.

    A version mismatch is not a mysterious SQL error, it is one of two ordinary
    situations with two different fixes, and saying which is the whole value of
    this check: an *older* database wants ``netadmin`` run once to migrate it, a
    *newer* one wants the package upgraded. Emitted on stderr at startup and
    returned from every tool call, because a stdio server's stderr is somewhere
    the user may never look.
    """
    expected = _db.latest_migration_version()
    try:
        found = _db.schema_version(repo.connection)
    except sqlite3.Error as exc:  # pragma: no cover - corrupt file, not a state
        return f"Cannot read the history store's schema version: {exc}"
    if found == expected:
        return None
    if found < expected:
        return (
            f"The history store is at schema version {found}, this build expects "
            f"{expected}. Run `netadmin` once against it to apply the pending "
            "migrations, then restart this MCP server."
        )
    return (
        f"The history store is at schema version {found}, newer than the {expected} "
        "this build understands. Upgrade with `pip install -U unifioptimizer` and "
        "restart this MCP server."
    )


def redaction_enabled() -> bool:
    """True when ``NETADMIN_MCP_REDACT`` asks for MAC/hostname masking."""
    return str(os.environ.get("NETADMIN_MCP_REDACT", "")).strip().lower() in _TRUTHY


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _window_seconds(text: str) -> int:
    match = _WINDOW_RE.match(str(text))
    if not match:
        raise ToolError(
            f"Could not read {text!r} as a window. Use a count and a unit, "
            'like "24h", "7d" or "30d", or pass explicit start/end timestamps.'
        )
    return int(match.group(1)) * _WINDOW_UNITS[match.group(2).lower()]


def _parse_ts(value: Any, field: str) -> int:
    """Epoch seconds from an int, a numeric string, or an ISO-8601 timestamp."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ToolError(
            f"Could not read {field}={value!r} as a time. Use ISO-8601 "
            '("2026-07-21T14:00:00Z") or epoch seconds.'
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def parse_window(params: Mapping[str, Any], now: int, *, default: str = "24h") -> tuple[int, int]:
    """Resolve ``window`` / ``start`` / ``end`` into a half-open ``[start, end)``.

    Explicit ``start``/``end`` win over ``window`` so a follow-up question can
    re-examine exactly the span a previous answer named. A lone ``start`` runs to
    now; a lone ``end`` runs back one default window from it.
    """
    start_raw = params.get("start")
    end_raw = params.get("end")
    if start_raw or end_raw:
        end_ts = _parse_ts(end_raw, "end") if end_raw else now
        if start_raw:
            start_ts = _parse_ts(start_raw, "start")
        else:
            start_ts = end_ts - _window_seconds(params.get("window") or default)
        if start_ts >= end_ts:
            raise ToolError("The window is empty: start must be before end.")
        return start_ts, end_ts
    span = _window_seconds(params.get("window") or default)
    return now - span, now


def resolve_entity(repo: Repository, ref: Any, *, field: str = "entity") -> sqlite3.Row:
    """Resolve an entity_id, MAC or name to exactly one entity row.

    Tried in order of decreasing certainty: numeric id, exact ``native_id`` or
    ``name`` match (case-insensitive), then substring on name, then substring on
    ``native_id``. The first stage that matches decides; a stage that matches
    more than once raises :class:`AmbiguousEntity` with the candidates rather
    than picking the lowest id, which would be a silent wrong answer.
    """
    if ref is None or str(ref).strip() == "":
        raise ToolError(f"{field} is required: pass an entity_id, a MAC, or a device name.")
    text = str(ref).strip()
    if re.fullmatch(r"\d+", text):
        row = repo.get_entity(int(text))
        if row is not None:
            return row

    rows = repo.list_entities()
    lowered = text.lower()

    def _pick(candidates: list[sqlite3.Row]) -> Optional[sqlite3.Row]:
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise AmbiguousEntity(
                f"{len(candidates)} entities match {text!r}. Re-run with one of "
                "these entity_ids.",
                candidates=[_entity_ref(row) for row in candidates[: fmt.MAX_LIMIT]],
            )
        return None

    exact = [
        row
        for row in rows
        if str(row["native_id"]).lower() == lowered
        or (row["name"] and str(row["name"]).lower() == lowered)
    ]
    picked = _pick(exact)
    if picked is not None:
        return picked

    by_name = [row for row in rows if row["name"] and lowered in str(row["name"]).lower()]
    picked = _pick(by_name)
    if picked is not None:
        return picked

    by_native = [row for row in rows if lowered in str(row["native_id"]).lower()]
    picked = _pick(by_native)
    if picked is not None:
        return picked

    hint = (
        " The history store has no entities yet: run `netadmin` to start collecting."
        if not rows
        else ""
    )
    raise EntityNotFound(f"No entity matches {text!r}.{hint}")


# --------------------------------------------------------------------------- #
# Row -> payload helpers
# --------------------------------------------------------------------------- #
def _entity_ref(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    """Compact ``{entity_id, name, type, native_id}``; ``name`` falls back to MAC."""
    if row is None:
        return None
    name = row["name"]
    native_id = row["native_id"]
    return {
        "entity_id": int(row["entity_id"]),
        "name": name if name else native_id,
        "type": row["entity_type"],
        "native_id": native_id,
    }


def _entity_map(
    repo: Repository, rows: Sequence[sqlite3.Row], column: str = "entity_id"
) -> dict[int, sqlite3.Row]:
    """Batch-resolve a row set's ``entity_id`` column in one query, never N."""
    ids = [row[column] for row in rows if row[column] is not None]
    return repo.entities_by_ids(ids) if ids else {}


def _issue_brief(
    row: sqlite3.Row,
    entities: Mapping[int, sqlite3.Row],
    now: int,
    *,
    headline: bool = False,
) -> dict[str, Any]:
    """One issue as the model should read it: identity, state, age, owner.

    ``headline=True`` is for the *subject* of a response (the issue a drill-down
    is about) and adds the human ``ago``; a row inside a list stays bare, so a
    fifty-issue payload does not restate arithmetic fifty times.
    """
    entity_id = row["entity_id"]
    first_seen_ts = int(row["first_seen_ts"])
    resolved_ts = row["resolved_ts"]
    brief: dict[str, Any] = {
        "issue_id": int(row["id"]),
        "title": row["title"],
        "detector": row["detector_key"],
        "severity": row["severity"],
        "state": row["state"],
        "entity": _entity_ref(entities.get(int(entity_id))) if entity_id is not None else None,
        "first_seen": fmt.stamp(first_seen_ts, now) if headline else fmt.iso(first_seen_ts),
        "last_seen": fmt.iso(row["last_seen_ts"]),
        "occurrences": int(row["occurrences"]),
    }
    if resolved_ts is not None:
        brief["resolved"] = fmt.iso(resolved_ts)
        brief["lasted"] = fmt.duration(int(resolved_ts) - first_seen_ts)
    if row["fix_state"]:
        brief["fix_state"] = row["fix_state"]
    return brief


def _issue_event_brief(row: sqlite3.Row) -> dict[str, Any]:
    return {"at": fmt.iso(row["ts"]), "kind": row["kind"]}


def _event_brief(row: sqlite3.Row, entities: Mapping[int, sqlite3.Row]) -> dict[str, Any]:
    entity_id = row["entity_id"]
    return {
        "at": fmt.iso(row["ts"]),
        "key": row["key"],
        "entity": _entity_ref(entities.get(int(entity_id))) if entity_id is not None else None,
        "msg": row["msg"],
    }


def _change_brief(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "change_id": int(row["id"]),
        "at": fmt.iso(row["ts"]),
        "action": row["action"],
        "status": row["status"],
        "issue_id": row["issue_id"],
        "entity_id": row["entity_id"],
        "reverted_at": fmt.iso(row["reverted_ts"]),
    }


def _state_change_brief(row: sqlite3.Row, entities: Mapping[int, sqlite3.Row]) -> dict[str, Any]:
    entity_id = row["entity_id"]
    return {
        "at": fmt.iso(row["ts"]),
        "attr": row["attr"],
        "from": row["old_value"],
        "to": row["new_value"],
        "entity": _entity_ref(entities.get(int(entity_id))) if entity_id is not None else None,
    }


def _severity_counts(rows: Sequence[sqlite3.Row]) -> dict[str, int]:
    counts = {"p1": 0, "p2": 0, "p3": 0}
    for row in rows:
        key = str(row["severity"])
        if key in counts:
            counts[key] += 1
    return counts


def _score_pct(score: Optional[float]) -> Optional[int]:
    """A 0-1 SLE score as whole percent, the unit operators actually speak."""
    return None if score is None else int(round(score * 100))


def _read_series(
    repo: Repository, series_id: int, start_ts: int, end_ts: int
) -> tuple[str, list[tuple[int, Optional[float]]]]:
    """Points for a window from the tier that fits ~96 buckets, with fallback.

    Tier choice is arithmetic, not preference: raw samples land about every 60 s,
    hourly buckets every 3600 s, so the finest tier that can express the window in
    :data:`~netadmin.mcp.format.MAX_SERIES_POINTS` buckets is the right one. The
    remaining order is a fallback chain because retention prunes raw at 30 days
    and hourly at 18 months -- asking for the ideal tier and finding it empty must
    degrade to the tier that still holds the data, not to "no data".
    """
    span = max(1, end_ts - start_ts)
    if span <= fmt.MAX_SERIES_POINTS * 60:
        order = ("raw", "hourly", "daily")
    elif span <= fmt.MAX_SERIES_POINTS * 3600:
        order = ("hourly", "raw", "daily")
    else:
        order = ("daily", "hourly", "raw")

    for tier in order:
        if tier == "raw":
            rows = repo.read_raw(series_id, start_ts, end_ts)
        else:
            rows = repo.read_rollup(series_id, tier, start_ts, end_ts)
        if rows:
            return tier, [(int(r["ts"]), r["value"]) for r in rows]
    return order[0], []


def _baseline_map(repo: Repository, series_id: int) -> dict[str, float]:
    """``{stat: value}`` for a series' whole-history ('all') baseline bucket."""
    return {str(r["stat"]): float(r["value"]) for r in repo.get_baselines(series_id, "all")}


def _entity_metrics(repo: Repository, entity_id: int) -> list[str]:
    """Metric names that actually have data for an entity, from its last samples."""
    return sorted({str(s["metric"]) for s in repo.latest_samples(entity_id)})


# --------------------------------------------------------------------------- #
# 1. netadmin_overview
# --------------------------------------------------------------------------- #
def overview(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    start_ts, end_ts = parse_window(params, now)
    limit = fmt.clamp_limit(params.get("limit"))

    open_issues = repo.list_issues(open_only=True)
    open_incidents = repo.list_incidents(open_only=True)
    entities = _entity_map(repo, open_issues)
    incident_counts = repo.incident_member_counts([int(r["id"]) for r in open_incidents])
    genuine_incidents = [
        r
        for r in open_incidents
        if Repository.is_genuine_incident(incident_counts.get(int(r["id"]), 0))
    ]
    grouped_issue_count = sum(incident_counts.get(int(r["id"]), 0) for r in genuine_incidents)

    report = sle_scores(repo, start_ts, end_ts)
    span = end_ts - start_ts
    prior = sle_scores(repo, start_ts - span, start_ts)
    headline = _score_pct(report.headline)
    prior_headline = _score_pct(prior.headline)
    delta = None if headline is None or prior_headline is None else headline - prior_headline

    collectors = _collector_health(repo, start_ts, end_ts)
    counts = _severity_counts(open_issues)

    if not open_issues and headline is None and not collectors["jobs"]:
        summary = (
            "No data yet: this history store has no issues, no SLE minutes and no "
            "collector runs in the window. Run `netadmin` to start collecting."
        )
    else:
        # Honest split (Gitea #21): "N open incidents" used to count every
        # incident-of-one as an incident (11 rows for 1 real group + 10 solo
        # issues); this instead says how many issues are genuinely grouped.
        if not genuine_incidents:
            incident_clause = "no correlated incidents (every issue stands alone)"
        elif len(genuine_incidents) == 1:
            incident_clause = f"1 incident grouping {grouped_issue_count} of them"
        else:
            incident_clause = (
                f"{len(genuine_incidents)} incidents grouping {grouped_issue_count} of them"
            )
        first = (
            f"{len(open_issues)} open issue(s) "
            f"(P1 {counts['p1']}, P2 {counts['p2']}, P3 {counts['p3']}); {incident_clause}."
        )
        if headline is None:
            second = "No SLE minutes recorded in this window, so there is no health score."
        elif delta is None:
            second = f"Health is {headline}% over the window, with no prior window to compare."
        else:
            direction = "up" if delta > 0 else "down" if delta < 0 else "level"
            second = (
                f"Health is {headline}% over the window, {direction} "
                f"{abs(delta)} point(s) versus the previous {fmt.duration(span)}."
            )
        summary = f"{first} {second}"

    return {
        "summary": summary,
        "window": _window_block(start_ts, end_ts, now),
        "open_issues": fmt.listing(
            [_issue_brief(row, entities, now) for row in open_issues[:limit]],
            limit,
            total=len(open_issues),
        ),
        "open_incidents": fmt.listing(
            [_incident_brief(row) for row in open_incidents[:limit]],
            limit,
            total=len(open_incidents),
        ),
        "sle": {
            "headline_pct": headline,
            "prior_headline_pct": prior_headline,
            "delta_pct": delta,
            "per_sle_pct": {
                name: _score_pct(score.score)
                for name, score in report.sles.items()
                if score.score is not None
            },
        },
        "collector_health": collectors,
    }


def _collector_health(repo: Repository, start_ts: int, end_ts: int) -> dict[str, Any]:
    """Per-job success rate over the window, plus the newest run seen.

    A history answer is only as trustworthy as the collection behind it, so the
    entry-point tool always says whether the recorder was actually running. Jobs
    with no rows at all are omitted rather than reported as 0 % -- absent is not
    the same as failing, and a site that never ran a given probe should not read
    as a broken one.
    """
    jobs: list[dict[str, Any]] = []
    newest: Optional[int] = None
    for job in _COLLECTOR_JOBS:
        runs = repo.read_poll_runs(job, start_ts, end_ts)
        if not runs:
            continue
        ok = sum(1 for row in runs if row["ok"])
        last_ts = max(int(row["ts"]) for row in runs)
        newest = last_ts if newest is None else max(newest, last_ts)
        jobs.append(
            {
                "job": job,
                "runs": len(runs),
                "ok": ok,
                "success_pct": int(round(100 * ok / len(runs))),
                "last_run": fmt.iso(last_ts),
            }
        )
    return {"jobs": jobs, "last_run": fmt.iso(newest)}


def _window_block(start_ts: int, end_ts: int, now: int) -> dict[str, Any]:
    return {
        "start": fmt.iso(start_ts),
        "end": fmt.iso(end_ts),
        "length": fmt.duration(end_ts - start_ts),
        "now": fmt.iso(now),
    }


# --------------------------------------------------------------------------- #
# 2. netadmin_when_did_this_start  (the flagship)
# --------------------------------------------------------------------------- #
def when_did_this_start(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    issue = _require_issue(repo, params.get("issue"))
    limit = fmt.clamp_limit(params.get("limit"))
    lookback = _window_seconds(params.get("window") or "24h")

    trail = repo.list_issue_events(int(issue["id"]))
    detected = [row for row in trail if row["kind"] == "detected"]
    onset = int(detected[0]["ts"]) if detected else int(issue["first_seen_ts"])

    entity_row = (
        repo.get_entity(int(issue["entity_id"])) if issue["entity_id"] is not None else None
    )
    owner = {int(entity_row["entity_id"]): entity_row} if entity_row is not None else {}

    search_start = onset - lookback
    search_end = onset + _ONSET_LOOKAHEAD_S

    state_rows = repo.list_state_changes(search_start, search_end, limit=fmt.MAX_LIMIT)
    state_entities = _entity_map(repo, state_rows)
    fix_rows = [row for row in repo.list_changes() if search_start <= int(row["ts"]) < search_end]
    event_rows = repo.query_events(
        since_ts=search_start,
        until_ts=search_end,
        keys=_CHANGE_EVENT_KEYS,
        limit=fmt.MAX_LIMIT,
    )
    event_entities = _entity_map(repo, event_rows)

    history = [
        row
        for row in repo.list_issue_history(str(issue["fingerprint"]))
        if int(row["id"]) != int(issue["id"])
    ]
    baseline = _baseline_comparison(repo, entity_row, now) if entity_row is not None else []

    ended = int(issue["resolved_ts"]) if issue["resolved_ts"] is not None else None
    lasted = fmt.duration((ended or now) - onset)
    still = "resolved after" if ended else "still open after"
    changed_count = len(state_rows) + len(fix_rows) + len(event_rows)
    prior_phrase = (
        f"The same fingerprint fired {len(history)} time(s) before this."
        if history
        else "This is the first time this fingerprint has fired."
    )
    summary = (
        f"{issue['title']} started {fmt.iso(onset)} ({fmt.ago(onset, now)}), "
        f"{still} {lasted}. {changed_count} change(s) and change-shaped event(s) "
        f"landed in the {fmt.duration(lookback)} before it. {prior_phrase}"
    )

    return {
        "summary": summary,
        "issue": _issue_brief(issue, owner, now, headline=True),
        "onset": fmt.stamp(onset, now),
        "onset_source": "issue_events.detected" if detected else "issues.first_seen_ts",
        "resolved": fmt.stamp(ended, now),
        "evidence": fmt.trim_evidence(_decode_json(issue["evidence"])),
        "baseline_vs_now": baseline,
        "changed_near_onset": {
            "searched": _window_block(search_start, search_end, now),
            "state_changes": fmt.listing(
                [_state_change_brief(row, state_entities) for row in state_rows[:limit]],
                limit,
                total=len(state_rows),
            ),
            "fixes": fmt.listing(
                [_change_brief(row) for row in fix_rows[:limit]], limit, total=len(fix_rows)
            ),
            "events": fmt.listing(
                [_event_brief(row, event_entities) for row in event_rows[:limit]],
                limit,
                total=len(event_rows),
            ),
        },
        "prior_occurrences": fmt.listing(
            [_issue_brief(row, {}, now) for row in history[:limit]], limit, total=len(history)
        ),
        "lifecycle": fmt.listing(
            [_issue_event_brief(row) for row in trail[:limit]], limit, total=len(trail)
        ),
    }


def _baseline_comparison(
    repo: Repository, entity_row: sqlite3.Row, now: int, *, max_metrics: int = 6
) -> list[dict[str, Any]]:
    """ "Is this abnormal for *this* device?" for each of an entity's metrics.

    Compares the last hour's mean against the stored EWMA baseline. The answer to
    "when did this start" is only useful next to "and what was normal before",
    and the baseline is the only honest source of normal: a global threshold
    calls a busy-by-design AP broken every evening.
    """
    out: list[dict[str, Any]] = []
    for metric in _entity_metrics(repo, int(entity_row["entity_id"]))[:max_metrics]:
        series_id = repo.get_series(int(entity_row["entity_id"]), metric)
        if series_id is None:
            continue
        baseline = _baseline_map(repo, series_id)
        if not baseline:
            continue
        recent = repo.read_raw(series_id, now - 3600, now)
        values = [float(r["value"]) for r in recent if r["value"] is not None]
        mean = round(sum(values) / len(values), 3) if values else None
        expected = baseline.get("ewma_mean")
        out.append(
            {
                "metric": metric,
                "recent_hour_avg": mean,
                "baseline_mean": None if expected is None else round(expected, 3),
                "baseline_p05": baseline.get("p05"),
                "baseline_p95": baseline.get("p95"),
                "delta": None if mean is None or expected is None else round(mean - expected, 3),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 3. netadmin_has_this_happened_before
# --------------------------------------------------------------------------- #
def has_this_happened_before(
    repo: Repository, params: Mapping[str, Any], now: int
) -> dict[str, Any]:
    limit = fmt.clamp_limit(params.get("limit"))
    fingerprint = params.get("fingerprint")
    anchor: Optional[sqlite3.Row] = None
    if not fingerprint:
        anchor = _require_issue(repo, params.get("issue"))
        fingerprint = str(anchor["fingerprint"])

    history = repo.list_issue_history(str(fingerprint))
    if not history:
        return {
            "summary": (
                f"No issue with fingerprint {fingerprint} has ever been recorded in "
                "this history store."
            ),
            "fingerprint": fingerprint,
            "occurrences": fmt.listing([], limit),
        }

    occurrences: list[dict[str, Any]] = []
    durations: list[int] = []
    for row in history[:limit]:
        brief = _issue_brief(row, {}, now)
        fixes = [_change_brief(change) for change in repo.list_changes(issue_id=int(row["id"]))]
        brief["fixes_tried"] = fixes
        if row["resolved_ts"] is not None:
            durations.append(int(row["resolved_ts"]) - int(row["first_seen_ts"]))
        occurrences.append(brief)

    latest_resolved = next((row for row in history if row["resolved_ts"] is not None), None)
    trail = (
        [_issue_event_brief(row) for row in repo.list_issue_events(int(latest_resolved["id"]))]
        if latest_resolved is not None
        else []
    )
    applied = [
        change
        for row in history
        for change in repo.list_changes(issue_id=int(row["id"]))
        if change["status"] == "applied"
    ]

    oldest = min(int(row["first_seen_ts"]) for row in history)
    typical = fmt.duration(int(sum(durations) / len(durations))) if durations else None
    typical_phrase = (
        f" A typical occurrence lasted {typical}." if typical else " None has resolved yet."
    )
    fix_phrase = (
        f" {len(applied)} fix(es) were applied across those occurrences."
        if applied
        else " No fix was ever applied to it."
    )
    summary = (
        f"This fingerprint has fired {len(history)} time(s) since {fmt.iso(oldest)} "
        f"({fmt.ago(oldest, now)}).{typical_phrase}{fix_phrase}"
    )

    return {
        "summary": summary,
        "fingerprint": fingerprint,
        "anchor_issue_id": int(anchor["id"]) if anchor is not None else None,
        "first_ever": fmt.stamp(oldest, now),
        "typical_duration": typical,
        "occurrences": fmt.listing(occurrences, limit, total=len(history)),
        "last_resolved_lifecycle": fmt.listing(trail, limit),
    }


# --------------------------------------------------------------------------- #
# 4. netadmin_issues
# --------------------------------------------------------------------------- #
def issues(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    limit = fmt.clamp_limit(params.get("limit"))
    if params.get("issue") not in (None, ""):
        return _issue_detail(repo, params, now, limit)

    entity_id: Optional[int] = None
    if params.get("entity") not in (None, ""):
        entity_id = int(resolve_entity(repo, params.get("entity"))["entity_id"])

    open_only = _as_bool(params.get("open_only"), default=True)
    rows = repo.list_issues(
        state=params.get("state") or None,
        severity=params.get("severity") or None,
        entity_id=entity_id,
        open_only=open_only and not params.get("state"),
    )
    entities = _entity_map(repo, rows)
    counts = _severity_counts(rows)
    scope = "open" if open_only and not params.get("state") else "matching"
    if not rows:
        summary = f"No {scope} issues recorded in this history store for that filter."
    else:
        newest = max(int(row["last_seen_ts"]) for row in rows)
        summary = (
            f"{len(rows)} {scope} issue(s): P1 {counts['p1']}, P2 {counts['p2']}, "
            f"P3 {counts['p3']}. The most recent was last seen {fmt.ago(newest, now)}."
        )
    return {
        "summary": summary,
        "severity_counts": counts,
        "issues": fmt.listing(
            [_issue_brief(row, entities, now) for row in rows[:limit]], limit, total=len(rows)
        ),
    }


def _issue_detail(
    repo: Repository, params: Mapping[str, Any], now: int, limit: int
) -> dict[str, Any]:
    issue = _require_issue(repo, params.get("issue"))
    issue_id = int(issue["id"])
    entity_row = (
        repo.get_entity(int(issue["entity_id"])) if issue["entity_id"] is not None else None
    )
    entities = {int(entity_row["entity_id"]): entity_row} if entity_row is not None else {}

    trail = repo.list_issue_events(issue_id)
    investigations = repo.list_investigations(issue_id)
    changes = repo.list_changes(issue_id=issue_id)
    incident_id = repo.incident_id_for_issue(issue_id)

    resolved_ts = issue["resolved_ts"]
    age = fmt.duration(
        (int(resolved_ts) if resolved_ts is not None else now) - int(issue["first_seen_ts"])
    )
    state_phrase = f"resolved after {age}" if resolved_ts is not None else f"open for {age}"
    summary = (
        f"{issue['title']} ({issue['severity'].upper()}, {issue['state']}) is "
        f"{state_phrase}, with {len(changes)} fix(es) tried and "
        f"{len(investigations)} investigation(s)."
    )
    return {
        "summary": summary,
        "issue": _issue_brief(issue, entities, now, headline=True),
        "incident_id": incident_id,
        "evidence": fmt.trim_evidence(_decode_json(issue["evidence"])),
        "lifecycle": fmt.listing(
            [_issue_event_brief(row) for row in trail[:limit]], limit, total=len(trail)
        ),
        "fixes": fmt.listing(
            [_change_brief(row) for row in changes[:limit]], limit, total=len(changes)
        ),
        "investigations": fmt.listing(
            [
                {
                    "investigation_id": int(row["id"]),
                    "at": fmt.iso(row["ts"]),
                    "provider": row["provider"],
                    "status": row["status"],
                    "answered": bool(row["response_md"]),
                }
                for row in investigations[:limit]
            ],
            limit,
            total=len(investigations),
        ),
    }


# --------------------------------------------------------------------------- #
# 5. netadmin_incidents
# --------------------------------------------------------------------------- #
def _incident_brief(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "incident_id": int(row["id"]),
        "title": row["title"],
        "severity": row["severity"],
        "state": row["state"],
        "root_issue_id": int(row["root_issue_id"]),
        "first_seen": fmt.iso(row["first_seen_ts"]),
        "last_seen": fmt.iso(row["last_seen_ts"]),
        "resolved": fmt.iso(row["resolved_ts"]),
    }


def incidents(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    limit = fmt.clamp_limit(params.get("limit"))
    if params.get("incident") not in (None, ""):
        return _incident_detail(repo, params, now, limit)

    open_only = _as_bool(params.get("open_only"), default=True)
    # Genuine groups (2+ members) by default (Gitea #21) -- the same predicate
    # the REST API and the store share, so this tool's narration cannot drift
    # from what the UI shows. include_singletons=true restores the engine's
    # uniform one-row-per-root projection.
    include_singletons = _as_bool(params.get("include_singletons"), default=False)
    rows = repo.list_incidents(open_only=open_only, genuine_only=not include_singletons)
    if not rows:
        scope = "open " if open_only else ""
        kind = "" if include_singletons else "genuine "
        return {
            "summary": f"No {scope}{kind}incidents recorded in this history store.",
            "incidents": fmt.listing([], limit),
        }
    counts = repo.incident_member_counts([int(row["id"]) for row in rows])
    briefs = []
    for row in rows[:limit]:
        brief = _incident_brief(row)
        brief["member_count"] = counts.get(int(row["id"]), 0)
        briefs.append(brief)
    newest = max(int(row["last_seen_ts"]) for row in rows)
    if include_singletons:
        summary = (
            f"{len(rows)} {'open ' if open_only else ''}incident(s), each grouping a root "
            f"cause with its symptoms. The most recent was last seen {fmt.ago(newest, now)}."
        )
    else:
        # The honest split: how many issues are actually grouped, versus every
        # open issue that stands alone and never shows up here at all.
        grouped_issues = sum(counts.values())
        noun = "incident" if len(rows) == 1 else "incidents"
        base = f"{len(rows)} {noun} grouping {grouped_issues} issue(s)"
        if open_only:
            standalone = len(repo.list_issues(open_only=True)) - grouped_issues
            base = f"{base}; {standalone} standalone open issue(s)"
        summary = f"{base}. The most recent group was last seen {fmt.ago(newest, now)}."
    return {
        "summary": summary,
        "incidents": fmt.listing(briefs, limit, total=len(rows)),
    }


def _incident_detail(
    repo: Repository, params: Mapping[str, Any], now: int, limit: int
) -> dict[str, Any]:
    raw = params.get("incident")
    try:
        incident_id = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"incident must be a numeric incident_id, got {raw!r}.") from None
    incident = repo.get_incident(incident_id)
    if incident is None:
        raise ToolError(
            f"No incident {incident_id} in this history store. Call netadmin_incidents "
            "with no arguments to list them.",
        )
    members = repo.list_incident_members(incident_id)
    issue_rows = {int(m["issue_id"]): repo.get_issue(int(m["issue_id"])) for m in members}
    entities = _entity_map(repo, [row for row in issue_rows.values() if row is not None])

    detailed: list[dict[str, Any]] = []
    for member in members[:limit]:
        row = issue_rows.get(int(member["issue_id"]))
        entry: dict[str, Any] = {
            "role": member["role"],
            "rule": member["rule"],
            "rationale": member["rationale"],
        }
        if row is not None:
            entry.update(_issue_brief(row, entities, now))
        else:
            entry["issue_id"] = int(member["issue_id"])
        detailed.append(entry)

    roots = sum(1 for m in members if m["role"] == "root")
    summary = (
        f"{incident['title']} ({incident['severity'].upper()}, {incident['state']}) groups "
        f"{len(members)} issue(s): {roots} root cause and {len(members) - roots} symptom(s)."
    )
    return {
        "summary": summary,
        "incident": _incident_brief(incident),
        "narrative": incident["summary"],
        "members": fmt.listing(detailed, limit, total=len(members)),
    }


# --------------------------------------------------------------------------- #
# 6. netadmin_sle_trend
# --------------------------------------------------------------------------- #
_BUCKET_SECONDS = {"hour": 3600, "day": 86_400}


def sle_trend(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    start_ts, end_ts = parse_window(params, now, default="7d")
    limit = fmt.clamp_limit(params.get("limit"))
    sle_filter = params.get("sle") or None

    bucket = str(params.get("bucket") or "").lower()
    if bucket not in _BUCKET_SECONDS:
        # Auto: hourly detail up to four days, daily beyond, so the returned
        # bucket count always lands inside the row cap instead of being clipped.
        bucket = "hour" if (end_ts - start_ts) <= 4 * 86_400 else "day"
    size = _BUCKET_SECONDS[bucket]

    rows = repo.query_sle_minutes(start_ts, end_ts, group_by=("bucket_ts", "sle", "classifier"))
    if sle_filter:
        rows = [row for row in rows if row["sle"] == sle_filter]

    folded: dict[int, dict[str, float]] = {}
    for row in rows:
        bucket_ts = int(row["bucket_ts"])
        slot = bucket_ts - (bucket_ts % size)
        minutes = float(row["minutes"] or 0.0)
        cell = folded.setdefault(slot, {"total": 0.0, "ok": 0.0})
        cell["total"] += minutes
        if row["classifier"] == "ok":
            cell["ok"] += minutes

    series = [
        {
            "bucket": fmt.iso(slot),
            "score_pct": _score_pct(cell["ok"] / cell["total"]) if cell["total"] else None,
            "fail_minutes": round(cell["total"] - cell["ok"], 2),
            "total_minutes": round(cell["total"], 2),
        }
        for slot, cell in sorted(folded.items())
    ]

    report = sle_scores(repo, start_ts, end_ts)
    if not series:
        return {
            "summary": (
                "No SLE minutes recorded in this window, so there is no trend to report. "
                "That usually means the daemon was not running."
            ),
            "window": _window_block(start_ts, end_ts, now),
            "bucket": bucket,
            "buckets": fmt.listing([], limit),
        }

    direction, delta = _trend_direction([entry["score_pct"] for entry in series])
    headline = _score_pct(report.headline)
    worst = min(
        (entry for entry in series if entry["score_pct"] is not None),
        key=lambda entry: entry["score_pct"],
        default=None,
    )
    worst_phrase = (
        f" The worst {bucket} was {worst['bucket']} at {worst['score_pct']}%." if worst else ""
    )
    summary = (
        f"Health over the window is {headline}% and {direction} "
        f"({delta:+d} point(s) between the first and second half), "
        f"measured in {len(series)} {bucket} bucket(s).{worst_phrase}"
    )

    return {
        "summary": summary,
        "window": _window_block(start_ts, end_ts, now),
        "bucket": bucket,
        "direction": direction,
        "half_over_half_delta_pct": delta,
        "headline_pct": headline,
        "per_sle": {
            name: {
                "score_pct": _score_pct(score.score),
                "fail_minutes": round(score.fail_minutes, 2),
            }
            for name, score in report.sles.items()
            if score.score is not None
        },
        "buckets": fmt.listing(series, limit, total=len(series)),
    }


def _trend_direction(scores: Sequence[Optional[int]]) -> tuple[str, int]:
    """Compare the mean of the window's two halves; +-2 points is "flat".

    A two-point dead band keeps the answer from flipping between "improving" and
    "worsening" on sampling noise, which is exactly the failure that makes a
    trend readout untrustworthy.
    """
    usable = [value for value in scores if value is not None]
    if len(usable) < 2:
        return "flat", 0
    mid = len(usable) // 2
    first = sum(usable[:mid]) / max(1, mid)
    second = sum(usable[mid:]) / max(1, len(usable) - mid)
    delta = int(round(second - first))
    if delta >= 2:
        return "improving", delta
    if delta <= -2:
        return "worsening", delta
    return "flat", delta


# --------------------------------------------------------------------------- #
# 7. netadmin_what_changed
# --------------------------------------------------------------------------- #
def what_changed(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    start_ts, end_ts = parse_window(params, now, default="7d")
    limit = fmt.clamp_limit(params.get("limit"))

    entity_id: Optional[int] = None
    entity: Optional[dict[str, Any]] = None
    if params.get("entity") not in (None, ""):
        row = resolve_entity(repo, params.get("entity"))
        entity_id = int(row["entity_id"])
        entity = _entity_ref(row)

    state_rows = repo.list_state_changes(
        start_ts, end_ts, entity_id=entity_id, limit=fmt.MAX_LIMIT * 4
    )
    fix_rows = [
        row for row in repo.list_changes(entity_id=entity_id) if start_ts <= int(row["ts"]) < end_ts
    ]
    event_rows = repo.query_events(
        since_ts=start_ts,
        until_ts=end_ts,
        keys=_CHANGE_EVENT_KEYS,
        entity_id=entity_id,
        limit=fmt.MAX_LIMIT * 4,
    )
    entities = repo.entities_by_ids(
        [row["entity_id"] for row in state_rows if row["entity_id"] is not None]
        + [row["entity_id"] for row in event_rows if row["entity_id"] is not None]
    )

    timeline: list[dict[str, Any]] = []
    for row in state_rows:
        entry = _state_change_brief(row, entities)
        entry["kind"] = "state"
        timeline.append(entry)
    for row in fix_rows:
        entry = _change_brief(row)
        entry["kind"] = "fix"
        timeline.append(entry)
    for row in event_rows:
        entry = _event_brief(row, entities)
        entry["kind"] = "event"
        timeline.append(entry)
    timeline.sort(key=lambda entry: entry["at"] or "", reverse=True)

    scope = f" on {entity['name']}" if entity else ""
    if not timeline:
        summary = (
            f"Nothing changed{scope} in this window: no firmware, channel or link-state "
            "changes, no applied fixes, and no change-shaped controller events."
        )
    else:
        summary = (
            f"{len(timeline)} change(s){scope} in this window: {len(state_rows)} "
            f"state change(s), {len(fix_rows)} applied/reverted fix(es), and "
            f"{len(event_rows)} controller event(s). Newest first."
        )

    return {
        "summary": summary,
        "window": _window_block(start_ts, end_ts, now),
        "entity": entity,
        "counts": {
            "state_changes": len(state_rows),
            "fixes": len(fix_rows),
            "events": len(event_rows),
        },
        "timeline": fmt.listing(timeline[:limit], limit, total=len(timeline)),
    }


# --------------------------------------------------------------------------- #
# 8. netadmin_worst_offenders
# --------------------------------------------------------------------------- #
def worst_offenders(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    """Rank a surface by client cost, and report device downtime beside it.

    ``fail_minutes`` is client-axis only and is what the ranking is built from;
    ``down_minutes`` is the device's own offline time (``null`` where that axis
    was never measured) and is deliberately absent from the score. An assistant
    reading this must be able to say "this AP cost clients 655 minutes" and "this
    AP was itself down for 55 minutes" without ever adding the two -- they are
    different units over different populations, and a downed AP's client cost is
    already counted against whichever AP its clients moved to (Gitea #38).

    ``clients_in_window`` is the denominator the client-minute figures are quoted
    against, so a total can be read as a share of a watched population rather
    than of the whole site.
    """
    start_ts, end_ts = parse_window(params, now, default="7d")
    limit = fmt.clamp_limit(params.get("limit"), default=10)
    surface = str(params.get("surface") or "devices").lower()
    if surface not in ("devices", "clients"):
        raise ToolError('surface must be "devices" or "clients".')
    types = DEVICE_ENTITY_TYPES if surface == "devices" else CLIENT_ENTITY_TYPES

    clients_in_window = repo.sle_measured_client_count(start_ts, end_ts)
    ranked = rank_offenders(repo, types, start_ts, end_ts, top_n=limit)
    if not ranked:
        return {
            "summary": (
                f"No {surface} carried any measurable burden in this window: no attributed "
                "failed client-minutes, no open issues, no disconnect or roam events, "
                "no downtime."
            ),
            "window": _window_block(start_ts, end_ts, now),
            "surface": surface,
            "clients_in_window": clients_in_window,
            "offenders": fmt.listing([], limit),
        }

    entities = repo.entities_by_ids([score.entity_id for score in ranked])
    rows = []
    for score in ranked:
        entry = score.as_dict()
        entry["entity"] = _entity_ref(entities.get(score.entity_id))
        entry["score"] = round(entry["score"], 2)
        entry["fail_minutes"] = round(entry["fail_minutes"], 2)
        if entry["down_minutes"] is not None:
            entry["down_minutes"] = round(entry["down_minutes"], 2)
        entry["components"] = {k: round(v, 2) for k, v in entry["components"].items()}
        rows.append(entry)

    top = rows[0]
    name = top["entity"]["name"] if top["entity"] else f"entity {top['entity_id']}"
    # The claim is now literally true: fail_minutes is client-axis only, so
    # "client-minute(s)" names the unit correctly. Downtime is a second sentence,
    # never an addend, and stays silent where it was never measured.
    summary = (
        f"{name} tops the {surface} ranking with {top['fail_minutes']} attributed failed "
        f"client-minute(s) out of {clients_in_window} client(s) judged in this window, "
        f"{top['issue_counts']['total']} open issue(s) and "
        f"{top['event_count']} disconnect/roam event(s)."
    )
    # Only when there is downtime to report. A measured zero is already in the
    # entry's `down_minutes` field; narrating "offline for 0.0 minute(s)" would
    # spend a sentence saying nothing and read as a hedge.
    if top["down_minutes"]:
        summary += (
            f" It was itself offline for {top['down_minutes']} minute(s) -- device time, "
            "counted separately and never added to client-minutes, because the clients of a "
            "downed device spend their bad minutes on whatever they moved to."
        )
    return {
        "summary": summary,
        "window": _window_block(start_ts, end_ts, now),
        "surface": surface,
        "clients_in_window": clients_in_window,
        "offenders": fmt.listing(rows, limit, total=len(rows)),
    }


# --------------------------------------------------------------------------- #
# 9. netadmin_metric_history
# --------------------------------------------------------------------------- #
def metric_history(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    start_ts, end_ts = parse_window(params, now, default="7d")
    entity_row = resolve_entity(repo, params.get("entity"))
    entity_id = int(entity_row["entity_id"])
    entity = _entity_ref(entity_row)

    metric = params.get("metric")
    available = _entity_metrics(repo, entity_id)
    if not metric:
        raise ToolError(
            f"metric is required. {entity['name']} has data for: "
            f"{', '.join(available) if available else 'no metrics yet'}.",
            available_metrics=available,
        )
    series_id = repo.get_series(entity_id, str(metric))
    if series_id is None:
        return {
            "summary": (
                f"{entity['name']} has no {metric!r} series in the history store. "
                f"It does have: {', '.join(available) if available else 'no metrics yet'}."
            ),
            "entity": entity,
            "metric": metric,
            "available_metrics": available,
        }

    tier, points = _read_series(repo, series_id, start_ts, end_ts)
    block = fmt.series_block(points, tier=tier)
    baseline = _baseline_map(repo, series_id)

    if not points:
        summary = (
            f"No {metric} samples stored for {entity['name']} in this window. "
            "Retention may have pruned it, or the collector was not running."
        )
    else:
        expected = baseline.get("ewma_mean")
        drift = (
            f" The baseline mean is {round(expected, 3)}, so the window average is "
            f"{round(block['avg'] - expected, 3):+}."
            if expected is not None and block["avg"] is not None
            else ""
        )
        summary = (
            f"{metric} on {entity['name']} averaged {block['avg']} "
            f"(min {block['min']}, max {block['max']}) over the window, "
            f"served from the {tier} tier.{drift}"
        )

    return {
        "summary": summary,
        "window": _window_block(start_ts, end_ts, now),
        "entity": entity,
        "metric": metric,
        "series": block,
        "baseline": {stat: round(value, 3) for stat, value in baseline.items()},
        "available_metrics": available,
    }


# --------------------------------------------------------------------------- #
# 10. netadmin_events_around
# --------------------------------------------------------------------------- #
# How many events to pull before grouping. Well above the row cap because the
# groups are the answer; the raw rows are only there to be counted.
_EVENT_SCAN_LIMIT = 500


def events_around(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    limit = fmt.clamp_limit(params.get("limit"))
    radius = _window_seconds(params.get("radius") or "30m")

    if params.get("issue") not in (None, ""):
        issue = _require_issue(repo, params.get("issue"))
        anchor = int(issue["first_seen_ts"])
        anchor_label = f"the onset of issue {int(issue['id'])}"
    elif params.get("at") not in (None, ""):
        anchor = _parse_ts(params.get("at"), "at")
        anchor_label = fmt.iso(anchor) or "the given time"
    else:
        raise ToolError("Pass either `at` (a timestamp) or `issue` (an issue_id) to anchor on.")

    entity_id: Optional[int] = None
    if params.get("entity") not in (None, ""):
        entity_id = int(resolve_entity(repo, params.get("entity"))["entity_id"])

    start_ts, end_ts = anchor - radius, anchor + radius
    rows = repo.query_events(
        since_ts=start_ts, until_ts=end_ts, entity_id=entity_id, limit=_EVENT_SCAN_LIMIT
    )
    entities = _entity_map(repo, rows)

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["key"]), []).append(row)
    groups = [
        {
            "key": key,
            "count": len(members),
            "first": fmt.iso(min(int(row["ts"]) for row in members)),
            "last": fmt.iso(max(int(row["ts"]) for row in members)),
            "exemplars": [_event_brief(row, entities) for row in members[:3]],
        }
        for key, members in sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    ]

    if not rows:
        summary = (
            f"No events within {fmt.duration(radius)} of {anchor_label}. "
            "Nothing else was breaking at the same time, at least nothing the "
            "controller reported."
        )
    else:
        top = groups[0]
        summary = (
            f"{len(rows)} event(s) in {len(groups)} kind(s) within "
            f"{fmt.duration(radius)} of {anchor_label}. The loudest was "
            f"{top['key']} ({top['count']})."
        )
        if len(rows) >= _EVENT_SCAN_LIMIT:
            summary += f" Scan capped at {_EVENT_SCAN_LIMIT} events; narrow the radius."

    return {
        "summary": summary,
        "anchor": fmt.stamp(anchor, now),
        "radius": fmt.duration(radius),
        "window": _window_block(start_ts, end_ts, now),
        "event_count": len(rows),
        "groups": fmt.listing(groups[:limit], limit, total=len(groups)),
    }


# --------------------------------------------------------------------------- #
# 11. netadmin_client_experience
# --------------------------------------------------------------------------- #
def client_experience(repo: Repository, params: Mapping[str, Any], now: int) -> dict[str, Any]:
    start_ts, end_ts = parse_window(params, now, default="7d")
    limit = fmt.clamp_limit(params.get("limit"))
    entity_row = resolve_entity(repo, params.get("entity"))
    entity_id = int(entity_row["entity_id"])
    entity = _entity_ref(entity_row)

    rows = repo.query_sle_minutes(start_ts, end_ts, group_by=("entity_id", "sle", "classifier"))
    mine = [
        row for row in rows if row["entity_id"] is not None and int(row["entity_id"]) == entity_id
    ]
    per_sle: dict[str, dict[str, float]] = {}
    for row in mine:
        cell = per_sle.setdefault(str(row["sle"]), {"total": 0.0, "ok": 0.0, "fail": 0.0})
        minutes = float(row["minutes"] or 0.0)
        cell["total"] += minutes
        if row["classifier"] == "ok":
            cell["ok"] += minutes
        else:
            cell["fail"] += minutes
    sle_block = {
        name: {
            "score_pct": _score_pct(cell["ok"] / cell["total"]) if cell["total"] else None,
            "fail_minutes": round(cell["fail"], 2),
            "total_minutes": round(cell["total"], 2),
        }
        for name, cell in sorted(per_sle.items())
    }
    failures = sorted(
        (
            {
                "sle": str(row["sle"]),
                "classifier": str(row["classifier"]),
                "minutes": round(float(row["minutes"] or 0.0), 2),
            }
            for row in mine
            if row["classifier"] != "ok"
        ),
        key=lambda entry: -entry["minutes"],
    )

    event_rows = repo.query_events(
        since_ts=start_ts,
        until_ts=end_ts,
        entity_id=entity_id,
        keys=_CLIENT_EVENT_KEYS,
        limit=_EVENT_SCAN_LIMIT,
    )
    event_counts: dict[str, int] = {}
    for row in event_rows:
        event_counts[str(row["key"])] = event_counts.get(str(row["key"]), 0) + 1

    ap_rows = [
        row
        for row in repo.state_history(entity_id, "ap_mac", limit=fmt.MAX_LIMIT * 2)
        if start_ts <= int(row["ts"]) < end_ts
    ]
    ap_history = [
        {
            "at": fmt.iso(row["ts"]),
            "from": _ap_label(repo, row["old_value"]),
            "to": _ap_label(repo, row["new_value"]),
        }
        for row in ap_rows
    ]

    rssi_series_id = repo.get_series(entity_id, "rssi")
    if rssi_series_id is None:
        rssi = None
    else:
        tier, points = _read_series(repo, rssi_series_id, start_ts, end_ts)
        rssi = fmt.series_block(points, tier=tier)

    total_fail = round(sum(cell["fail"] for cell in per_sle.values()), 2)
    roams = sum(count for key, count in event_counts.items() if "Roam" in key)
    drops = sum(count for key, count in event_counts.items() if "Disconnected" in key)
    if not mine and not event_rows and rssi is None:
        summary = (
            f"No recorded experience for {entity['name']} in this window: no SLE minutes, "
            "no connectivity events and no RSSI samples."
        )
    else:
        rssi_phrase = (
            f" RSSI averaged {rssi['avg']} dBm." if rssi and rssi["avg"] is not None else ""
        )
        summary = (
            f"{entity['name']} logged {total_fail} failed client-minute(s), {drops} "
            f"disconnect(s) and {roams} roam(s) across "
            f"{len(ap_history)} AP change(s) in this window.{rssi_phrase}"
        )

    return {
        "summary": summary,
        "window": _window_block(start_ts, end_ts, now),
        "entity": entity,
        "sle": sle_block,
        "top_failures": fmt.listing(failures[:limit], limit, total=len(failures)),
        "connectivity_events": event_counts,
        "ap_history": fmt.listing(ap_history[:limit], limit, total=len(ap_history)),
        "rssi": rssi,
    }


def _ap_label(repo: Repository, mac: Optional[str]) -> Optional[str]:
    """An AP MAC from ``state_changes.ap_mac`` rendered as its name when known."""
    if not mac:
        return None
    row = repo.find_entity(EntityType.AP, str(mac))
    if row is None:
        return str(mac)
    return str(row["name"] or row["native_id"])


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #
def _require_issue(repo: Repository, raw: Any) -> sqlite3.Row:
    if raw in (None, ""):
        raise ToolError("issue is required: pass an issue_id. Call netadmin_issues to list them.")
    try:
        issue_id = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"issue must be a numeric issue_id, got {raw!r}.") from None
    row = repo.get_issue(issue_id)
    if row is None:
        raise ToolError(
            f"No issue {issue_id} in this history store. Call netadmin_issues to list "
            "the ones that exist.",
        )
    return row


def _decode_json(raw: Any) -> Any:
    """Decode a JSON TEXT column, degrading to ``{}`` rather than raising."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


# --------------------------------------------------------------------------- #
# Tool registry: name -> description + JSON schema + handler
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolSpec:
    """One tool as the SDK needs it, plus the handler :func:`call_tool` runs."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Repository, Mapping[str, Any], int], dict[str, Any]]


_WINDOW_PROP = {
    "type": "string",
    "description": 'Window like "24h", "7d", "30d". Ignored when start/end are given.',
}
_START_PROP = {"type": "string", "description": "ISO-8601 or epoch-seconds window start."}
_END_PROP = {"type": "string", "description": "ISO-8601 or epoch-seconds window end."}
_LIMIT_PROP = {"type": "integer", "description": "Max rows per list (default 20, hard max 50)."}
_ENTITY_PROP = {
    "type": "string",
    "description": "Entity id, MAC, or device/client name. Ambiguous names return candidates.",
}


def _schema(properties: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _windowed(extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    props = {"window": _WINDOW_PROP, "start": _START_PROP, "end": _END_PROP, "limit": _LIMIT_PROP}
    props.update(extra or {})
    return props


_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="netadmin_overview",
        description=(
            "History: start here. Open issues and incidents, the SLE health score and "
            "how it moved against the previous window, and whether the collector was "
            f"actually running. {_ROUTING}"
        ),
        input_schema=_schema(_windowed()),
        handler=overview,
    ),
    ToolSpec(
        name="netadmin_when_did_this_start",
        description=(
            "History: when did this issue begin, what was normal before it, what changed "
            "just before onset, and has it happened before. The question a live "
            f"controller cannot answer. {_ROUTING}"
        ),
        input_schema=_schema(
            {
                "issue": {"type": "integer", "description": "issue_id to explain."},
                "window": {
                    "type": "string",
                    "description": 'How far back before onset to hunt for a cause. Default "24h".',
                },
                "limit": _LIMIT_PROP,
            },
            required=["issue"],
        ),
        handler=when_did_this_start,
    ),
    ToolSpec(
        name="netadmin_has_this_happened_before",
        description=(
            "History: every past occurrence of an issue's fingerprint, how long each "
            f"lasted, and which fixes were applied to them. {_ROUTING}"
        ),
        input_schema=_schema(
            {
                "issue": {"type": "integer", "description": "issue_id whose fingerprint to trace."},
                "fingerprint": {
                    "type": "string",
                    "description": "Fingerprint to trace directly, instead of an issue_id.",
                },
                "limit": _LIMIT_PROP,
            }
        ),
        handler=has_this_happened_before,
    ),
    ToolSpec(
        name="netadmin_issues",
        description=(
            "History: list tracked issues, or one issue in full with its lifecycle trail, "
            f"evidence, investigations and the fixes tried on it. {_ROUTING}"
        ),
        input_schema=_schema(
            {
                "issue": {"type": "integer", "description": "issue_id for full detail."},
                "state": {
                    "type": "string",
                    "description": "pending | active | resolving | resolved.",
                },
                "severity": {"type": "string", "description": "p1 | p2 | p3."},
                "entity": _ENTITY_PROP,
                "open_only": {
                    "type": "boolean",
                    "description": "List only non-resolved issues. Default true.",
                },
                "limit": _LIMIT_PROP,
            }
        ),
        handler=issues,
    ),
    ToolSpec(
        name="netadmin_incidents",
        description=(
            "History: genuine correlated incidents (2+ issues grouped under one root "
            "cause), each with the symptom issues it explains. List them, or open one to "
            f"see its members. {_ROUTING}"
        ),
        input_schema=_schema(
            {
                "incident": {"type": "integer", "description": "incident_id for full detail."},
                "open_only": {
                    "type": "boolean",
                    "description": "List only non-resolved incidents. Default true.",
                },
                "include_singletons": {
                    "type": "boolean",
                    "description": (
                        "Also list issues the engine could not group with anything else "
                        "(one-member incidents-of-one). Default false: only genuine 2+ "
                        "member groups are listed, so this tool never inflates the count."
                    ),
                },
                "limit": _LIMIT_PROP,
            }
        ),
        handler=incidents,
    ),
    ToolSpec(
        name="netadmin_sle_trend",
        description=(
            "History: is it getting worse? Per-bucket SLE score and failed client-minutes "
            f"across a window, with the direction of travel. {_ROUTING}"
        ),
        input_schema=_schema(
            _windowed(
                {
                    "bucket": {"type": "string", "description": "hour | day. Default: automatic."},
                    "sle": {
                        "type": "string",
                        "description": "coverage | roaming | capacity | connect | wan | infra.",
                    },
                }
            )
        ),
        handler=sle_trend,
    ),
    ToolSpec(
        name="netadmin_what_changed",
        description=(
            "History: one merged timeline of firmware, channel and link-state changes, "
            f"applied or reverted fixes, and change-shaped controller events. {_ROUTING}"
        ),
        input_schema=_schema(_windowed({"entity": _ENTITY_PROP})),
        handler=what_changed,
    ),
    ToolSpec(
        name="netadmin_worst_offenders",
        description=(
            "History: which devices or clients caused the most grief in a window, ranked "
            "by attributed failed client-minutes, open issues and event churn. Device "
            "downtime is reported per entry but is never part of the ranking. "
            f"{_ROUTING}"
        ),
        input_schema=_schema(
            _windowed(
                {
                    "surface": {
                        "type": "string",
                        "description": "devices | clients. Default devices.",
                    }
                }
            )
        ),
        handler=worst_offenders,
    ),
    ToolSpec(
        name="netadmin_metric_history",
        description=(
            "History: one entity's metric over time, downsampled to a readable series, "
            f"next to that series' learned baseline. {_ROUTING}"
        ),
        input_schema=_schema(
            _windowed(
                {
                    "entity": _ENTITY_PROP,
                    "metric": {
                        "type": "string",
                        "description": "Metric name, e.g. rssi, cu_total, wan_latency.",
                    },
                }
            ),
            required=["entity", "metric"],
        ),
        handler=metric_history,
    ),
    ToolSpec(
        name="netadmin_events_around",
        description=(
            "History: what else broke at the same time. Events near a timestamp or an "
            f"issue's onset, grouped by kind with exemplars. {_ROUTING}"
        ),
        input_schema=_schema(
            {
                "at": {"type": "string", "description": "ISO-8601 or epoch-seconds anchor time."},
                "issue": {"type": "integer", "description": "Anchor on this issue's onset."},
                "radius": {
                    "type": "string",
                    "description": 'How far either side of the anchor. Default "30m".',
                },
                "entity": _ENTITY_PROP,
                "limit": _LIMIT_PROP,
            }
        ),
        handler=events_around,
    ),
    ToolSpec(
        name="netadmin_client_experience",
        description=(
            "History: one client's story. SLE breakdown, disconnects and roams, which APs "
            f"it moved between, and its RSSI trend. {_ROUTING}"
        ),
        input_schema=_schema(_windowed({"entity": _ENTITY_PROP}), required=["entity"]),
        handler=client_experience,
    ),
)

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


# --------------------------------------------------------------------------- #
# Central dispatch: this is where output discipline is enforced
# --------------------------------------------------------------------------- #
def call_tool(
    repo: Repository,
    name: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Run one tool and return its finished payload. Never raises.

    Everything a caller could get wrong -- unknown tool, bad window, missing id,
    ambiguous name, a store whose schema does not match -- comes back as a payload
    with a ``summary`` telling the model what to do next, because an MCP exception
    is a dead end and a sentence is a next step. Only the finishing passes run
    unconditionally: hard row caps, optional redaction, summary-first ordering,
    and the size guard.
    """
    now = int(time.time()) if now is None else int(now)
    args = dict(params or {})

    spec = TOOLS.get(name)
    if spec is None:
        return fmt.lead_with_summary(
            {
                "summary": f"No tool named {name!r} on this server.",
                "error": "unknown_tool",
                "available_tools": sorted(TOOLS),
            }
        )

    gate = schema_gate(repo)
    if gate is not None:
        return fmt.lead_with_summary({"summary": gate, "error": "schema_mismatch"})

    try:
        payload = spec.handler(repo, args, now)
    except ToolError as exc:
        payload = exc.payload()
    except sqlite3.Error as exc:
        payload = {
            "summary": f"The history store could not answer that: {exc}",
            "error": "store_error",
        }
    return _finalize(repo, payload)


def _finalize(repo: Repository, payload: dict[str, Any]) -> dict[str, Any]:
    """Row caps, redaction, summary-first, size guard -- in that order.

    Order matters. Caps run first so redaction and the size guard work on the
    payload the caller will actually see; redaction runs before the size guard so
    a trimmed payload is still redacted; the summary is hoisted last so it
    survives whatever the guard removed.
    """
    capped = _cap_rows(payload)
    if redaction_enabled():
        redactor = fmt.Redactor(fmt.build_known_names(repo.list_entities()))
        capped = redactor.redact(capped)
    return fmt.guard_size(fmt.lead_with_summary(capped))


def _cap_rows(node: Any) -> Any:
    """Clip every row list in the tree to the hard cap, wherever it came from.

    A defensive sweep, not the primary mechanism: handlers already cap through
    :func:`netadmin.mcp.format.listing`. This exists so a future tool that builds
    a list by hand cannot ship an unbounded one. Lists of *rows* (dicts) and of
    names (strings) are capped; lists of lists are left alone, because that is
    what a downsampled series is and it has its own, larger budget.
    """
    if isinstance(node, dict):
        return {key: _cap_rows(value) for key, value in node.items()}
    if isinstance(node, list):
        if node and all(isinstance(item, (dict, str)) for item in node):
            node = node[: fmt.MAX_LIMIT]
        return [_cap_rows(item) for item in node]
    return node
