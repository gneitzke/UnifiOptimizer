"""The dossier builder — the real value of the LLM investigator (section 10).

Deterministic detectors find and track; the *dossier* is what lets a model (or a
human) explain and correlate. :func:`build_dossier` compiles a single Markdown
document for one issue: its lifecycle trail, evidence rendered as compact tables
with units, the confounders the detector already ruled out, related open/recent
issues on the same entity and its parent/children, bucketed metric windows around
``first_seen`` (small stat tables, never raw dumps), site/topology context, the
detector's playbook entry from the catalog, and a closing STRUCTURED QUESTIONS
section with response-format instructions so answers parse loosely.

Provider-independent by design: the same document feeds the manual, copilot, and
anthropic providers. It is a *read-only* view over the repository — it never
writes, and never touches the controller.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from netadmin.detect.catalog import DEFAULT_CATALOG, Catalog, Playbook
from netadmin.domain.entities import entity_display_label
from netadmin.store.repository import Repository

__all__ = ["build_dossier", "build_incident_dossier", "parse_answers"]

# How many hours either side of first_seen the metric windows cover, and the cap
# on distinct metrics / hourly buckets so the dossier stays compact.
_WINDOW_HOURS = 3
_MAX_METRICS = 4
_MAX_BUCKETS = 12
_HOUR = 3600

# Evidence keys that are surfaced in their own sections, not the evidence table.
_EVIDENCE_SKIP = frozenset({"confounders_checked", "series_hints"})

# Per-detector metric preference for the "windows around first_seen" section:
# which series to chart first when the entity owns several. Mirrors the web's
# metricHints so the dossier and the UI agree on what matters per detector.
_DETECTOR_METRICS: dict[str, tuple[str, ...]] = {
    "wired.bad_cable": ("rx_errors", "tx_errors"),
    "wired.duplex_mismatch": ("rx_errors",),
    "wired.uplink_saturation": ("tx_bytes", "rx_bytes"),
    "wired.poe_budget": ("total_used_power",),
    "wired.sfp_degraded": ("sfp_rxpower", "sfp_txpower", "sfp_temperature", "sfp_current"),
    "infra.device_overheating": ("temp", "fan_level"),
    "wifi.airtime_saturation": ("cu_total", "cu_self_rx", "cu_self_tx"),
    "wifi.sticky_client": ("rssi", "tx_retries"),
    "wifi.tx_power_loud": ("tx_power",),
    "client.flaky": ("rssi", "satisfaction"),
    "wan.isp_degraded": ("wan_latency", "wan_drops"),
    "wan.latency_shift": ("wan_latency",),
    "wan.dns_slow": ("dns_latency_ms",),
    "wan.bufferbloat": ("gw_rtt_ms",),
    "wan.flapping": ("wan_drops",),
}

# Cheap unit inference for evidence values whose key encodes the unit.
_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_per_min", "/min"),
    ("_per_s", "/s"),
    ("_ms", "ms"),
    ("_mbps", "Mbps"),
    ("_bps", "bps"),
    ("_pct", "%"),
    ("_percent", "%"),
    ("_dbm", "dBm"),
    ("_watts", "W"),
    ("_bytes", "B"),
)


def _iso(ts: Optional[int]) -> str:
    """Epoch seconds → ISO-8601 UTC (``…Z``); ``—`` for ``None``."""
    if ts is None:
        return "—"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_duration(seconds: int) -> str:
    """A compact human duration (``2d 3h``, ``14m``, ``38s``)."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    parts: list[str] = []
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:  # drop the minutes term once we're into days
        parts.append(f"{minutes}m")
    return " ".join(parts) or f"{seconds}s"


def _num(value: Any) -> str:
    """Render a metric value tersely (ints bare, floats compactly).

    Whole-valued floats render as bare integers. Fractional floats use 3
    significant figures *below* 1000, but from 1000 up ``.3g`` would collapse to
    scientific notation and drop the magnitude a reader needs (``1234.5`` →
    ``1.23e+03``); those render with one decimal so the integer part is never lost.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        if abs(value) >= 1000:
            return f"{value:.1f}"
        return f"{value:.3g}"
    return str(value)


def _cell(value: Any) -> str:
    """Escape a value for a Markdown table cell (pipes, newlines)."""
    text = value if isinstance(value, str) else _num(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """A GitHub-flavoured Markdown table. Empty ``rows`` → empty string."""
    if not rows:
        return ""
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def _evidence_unit(key: str, meta_unit: Optional[str] = None) -> str:
    if meta_unit:
        return meta_unit
    for suffix, unit in _UNIT_SUFFIXES:
        if key.endswith(suffix):
            return unit
    return ""


def _entity_label(row: Optional[sqlite3.Row]) -> str:
    if row is None:
        return "unknown"
    name = row["name"]
    return str(name) if name else str(row["native_id"])


def _qualified(repo: Repository, row: Optional[sqlite3.Row]) -> str:
    """:func:`_entity_label` with the parent resolved: ``"Loft / wifi0"``.

    A dossier is read by a model that has to say *which* AP is at fault, and
    every AP calls its radios ``wifi0``/``wifi1`` (Gitea #44). One indexed lookup
    per dossier, on a document that is built on demand.
    """
    if row is None:
        return "unknown"
    parent_id = row["parent_id"]
    parent = repo.get_entity(int(parent_id)) if parent_id is not None else None
    return entity_display_label(
        _entity_label(row),
        str(row["entity_type"]),
        _entity_label(parent) if parent is not None else None,
    )


def _humanize(token: str) -> str:
    return token.replace("_", " ").replace(".", " · ").strip().capitalize()


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #


def _section_header(
    repo: Repository, issue: sqlite3.Row, entity: Optional[sqlite3.Row], now: int
) -> str:
    open_ = issue["state"] != "resolved"
    if open_:
        span = _fmt_duration(now - int(issue["first_seen_ts"]))
        life = f"ongoing {span}"
    else:
        resolved = issue["resolved_ts"] or issue["last_seen_ts"]
        span = _fmt_duration(int(resolved) - int(issue["first_seen_ts"]))
        life = f"lasted {span}"

    entity_ref = (
        "network-wide"
        if entity is None
        else f"{_qualified(repo, entity)} ({entity['entity_type']}, {entity['native_id']})"
    )

    lines = [
        f"# Investigation dossier — issue #{issue['id']}",
        "",
        f"**{issue['title']}**",
        "",
        _table(
            ["Field", "Value"],
            [
                ["Detector", f"`{issue['detector_key']}`"],
                ["Severity", str(issue["severity"]).upper()],
                ["State", str(issue["state"])],
                ["Entity", entity_ref],
                ["First seen", _iso(issue["first_seen_ts"])],
                ["Last seen", _iso(issue["last_seen_ts"])],
                ["Lifetime", life],
                ["Occurrences", str(issue["occurrences"])],
                ["Fix state", str(issue["fix_state"]) if issue["fix_state"] else "—"],
                ["Fingerprint", f"`{issue['fingerprint']}`"],
            ],
        ),
    ]
    return "\n".join(lines)


def _section_lifecycle(events: Sequence[sqlite3.Row]) -> str:
    rows = []
    for ev in events:
        detail = _decode(ev["detail"])
        detail_txt = (
            ", ".join(f"{k}={_num(v)}" for k, v in sorted(detail.items())) if detail else "—"
        )
        rows.append([_iso(ev["ts"]), str(ev["kind"]), detail_txt])
    table = _table(["When", "Event", "Detail"], rows)
    body = table or "_No lifecycle events recorded._"
    return f"## Lifecycle trail\n\n{body}"


def _section_evidence(evidence: dict[str, Any]) -> str:
    rows = []
    for key in sorted(evidence):
        if key in _EVIDENCE_SKIP:
            continue
        value = evidence[key]
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
            unit = ""
        else:
            rendered = _num(value)
            unit = _evidence_unit(key)
        rows.append([key, rendered, unit or "—"])
    table = _table(["Metric", "Value", "Unit"], rows)
    body = table or "_The detector recorded no numeric evidence for this issue._"
    return f"## Evidence\n\n{body}"


def _section_confounders(confounders: Sequence[str], playbook: Optional[Playbook]) -> str:
    lines = ["## Confounders ruled out", ""]
    if confounders:
        lines.append("The detector tested and rejected these false-positive traps:")
        lines.append("")
        lines.extend(f"- {_humanize(c)}" for c in confounders)
    else:
        lines.append("_The detector recorded no ruled-out confounders for this finding._")
    if playbook and playbook.confounders:
        lines.append("")
        lines.append(f"**Traps this class of problem is known for:** {playbook.confounders}")
    return "\n".join(lines)


def _issue_rows(issues: Sequence[sqlite3.Row], exclude_id: int) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for it in issues:
        if int(it["id"]) == exclude_id:
            continue
        rows.append(
            [
                f"#{it['id']}",
                f"`{it['detector_key']}`",
                str(it["severity"]).upper(),
                str(it["state"]),
                _iso(it["first_seen_ts"]),
                str(it["title"]),
            ]
        )
    return rows


def _section_related(repo: Repository, issue: sqlite3.Row, entity: Optional[sqlite3.Row]) -> str:
    lines = ["## Related issues"]
    if entity is None:
        lines.append("")
        lines.append("_This issue is network-wide; it has no owning entity to correlate against._")
        return "\n".join(lines)

    headers = ["Issue", "Detector", "Sev", "State", "First seen", "Title"]
    eid = int(entity["entity_id"])
    any_related = False

    same = _issue_rows(repo.list_issues(entity_id=eid), int(issue["id"]))
    lines.append("")
    lines.append(f"### On {_qualified(repo, entity)} (this entity)")
    lines.append("")
    lines.append(_table(headers, same) if same else "_No other issues on this entity._")
    any_related = any_related or bool(same)

    parent_id = entity["parent_id"]
    if parent_id is not None:
        parent = repo.get_entity(int(parent_id))
        parent_rows = _issue_rows(repo.list_issues(entity_id=int(parent_id)), int(issue["id"]))
        lines.append("")
        lines.append(f"### On parent {_entity_label(parent)}")
        lines.append("")
        lines.append(_table(headers, parent_rows) if parent_rows else "_No issues on the parent._")
        any_related = any_related or bool(parent_rows)

    child_rows: list[list[Any]] = []
    child_ids = [int(child["entity_id"]) for child in repo.children(eid)]
    # One query for every child's issues instead of one per child (the N+1). The
    # buckets preserve list_issues' newest-first order, so the rendered table is
    # byte-identical to the per-child version.
    issues_by_child = repo.list_issues_for_entities(child_ids)
    for cid in child_ids:
        child_rows.extend(_issue_rows(issues_by_child.get(cid, []), int(issue["id"])))
    if child_rows:
        lines.append("")
        lines.append("### On children")
        lines.append("")
        lines.append(_table(headers, child_rows))
        any_related = True

    if not any_related:
        lines.append("")
        lines.append("_No related issues on this entity, its parent, or its children._")
    return "\n".join(lines)


def _select_metrics(repo: Repository, entity_id: int, detector_key: str) -> list[tuple[str, str]]:
    """Metrics to window, as ``(metric, unit)``, detector-preferred first."""
    samples = repo.latest_samples(entity_id)
    available: dict[str, str] = {s["metric"]: (s["unit"] or "") for s in samples}
    if not available:
        return []
    preferred = [m for m in _DETECTOR_METRICS.get(detector_key, ()) if m in available]
    rest = sorted(m for m in available if m not in preferred)
    ordered = preferred + rest
    return [(m, available[m]) for m in ordered[:_MAX_METRICS]]


def _bucket_window(rows: Sequence[dict[str, Any]]) -> list[list[Any]]:
    """Bucket ``[{ts, value}, …]`` into hourly count/min/mean/max stat rows."""
    buckets: dict[int, list[float]] = {}
    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        bucket = int(row["ts"]) - (int(row["ts"]) % _HOUR)
        buckets.setdefault(bucket, []).append(float(value))
    out: list[list[Any]] = []
    for bucket in sorted(buckets)[:_MAX_BUCKETS]:
        vals = buckets[bucket]
        n = len(vals)
        mean = sum(vals) / n
        out.append([_iso(bucket), str(n), _num(min(vals)), _num(mean), _num(max(vals))])
    return out


def _section_metric_windows(
    repo: Repository, entity: Optional[sqlite3.Row], detector_key: str, first_seen: int, now: int
) -> str:
    lines = ["## Metric windows around first seen", ""]
    span = f"±{_WINDOW_HOURS} h around {_iso(first_seen)}"
    if entity is None:
        lines.append("_This issue is network-wide; per-entity metric windows do not apply._")
        return "\n".join(lines)

    metrics = _select_metrics(repo, int(entity["entity_id"]), detector_key)
    if not metrics:
        lines.append(f"_No metric series recorded for {_qualified(repo, entity)} ({span})._")
        return "\n".join(lines)

    lines.append(f"Bucketed hourly stats, {span} (windows, not raw samples).")
    start = first_seen - _WINDOW_HOURS * _HOUR
    end = first_seen + _WINDOW_HOURS * _HOUR
    for metric, unit in metrics:
        series_id = repo.get_series(int(entity["entity_id"]), metric)
        if series_id is None:
            continue
        window = repo.read_window(series_id, start, end, now=now)
        stat_rows = _bucket_window(window.rows)
        unit_label = f" ({unit})" if unit else ""
        lines.append("")
        lines.append(f"### {metric}{unit_label} — tier: {window.tier}")
        lines.append("")
        lines.append(
            _table(["Hour (UTC)", "n", "min", "mean", "max"], stat_rows)
            if stat_rows
            else "_No samples in the window._"
        )
    return "\n".join(lines)


def _section_site_context(repo: Repository) -> str:
    entities = repo.list_entities()
    counts: dict[str, int] = {}
    for row in entities:
        counts[str(row["entity_type"])] = counts.get(str(row["entity_type"]), 0) + 1

    lines = ["## Site context", ""]
    summary = ", ".join(f"{counts[t]} {t}" for t in sorted(counts)) or "no entities inventoried"
    lines.append(f"Inventory: {summary}.")

    device_rows: list[list[Any]] = []
    for row in entities:
        etype = str(row["entity_type"])
        if etype in ("ap", "switch", "gateway"):
            children = repo.children(int(row["entity_id"]))
            device_rows.append(
                [_entity_label(row), etype, str(row["model"] or "—"), str(len(children))]
            )
    if device_rows:
        lines.append("")
        lines.append(_table(["Device", "Type", "Model", "Children"], device_rows))

    if counts.get("gateway", 0) == 0:
        lines.append("")
        lines.append(
            "> **Gateway-less site:** no gateway in inventory. WAN latency/loss/DNS "
            "detectors run against controller health and local probes only — treat "
            "WAN attribution as best-effort."
        )
    return "\n".join(lines)


def _section_playbook(detector_key: str, playbook: Optional[Playbook]) -> str:
    lines = [f"## Detector playbook — `{detector_key}`", ""]
    if playbook is None:
        lines.append("_No playbook entry is registered for this detector._")
        return "\n".join(lines)
    lines.append(f"- **Signature:** {playbook.signature}")
    if playbook.confounders:
        lines.append(f"- **Confounders to rule out:** {playbook.confounders}")
    if playbook.fix_guidance:
        lines.append(f"- **Fix guidance:** {playbook.fix_guidance}")
    return "\n".join(lines)


_STRUCTURED_QUESTIONS = """## STRUCTURED QUESTIONS

Answer as a network admin who remembers this issue's history. Respond in
**Markdown**, beginning with a `## Answers` heading, and use these `### `
subheadings verbatim so the response can be parsed loosely:

### Root cause
The single most likely root cause, given the evidence above and the confounders
already ruled out. State what the dossier does *not* yet prove.

### Evidence to collect next
The one or two additional signals that would confirm or refute that root cause.

### Recommended fix and risk
The one change you would make and its risk. This tool never applies changes
automatically — recommend, do not act.

### Confidence
`low` / `medium` / `high`, with one sentence of justification."""


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def build_dossier(
    issue_id: int,
    repo: Repository,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
    now: Optional[int] = None,
) -> str:
    """Compile the full Markdown investigation dossier for one issue.

    Raises ``KeyError`` if the issue does not exist. ``now`` (epoch seconds)
    anchors the "ongoing N" lifetime and the metric-window retention tiering; it
    defaults to the issue's ``last_seen_ts`` so a dossier built for a resolved
    issue is reproducible.
    """
    issue = repo.get_issue(issue_id)
    if issue is None:
        raise KeyError(f"issue {issue_id} not found")

    now = int(issue["last_seen_ts"]) if now is None else int(now)
    entity_id = issue["entity_id"]
    entity = repo.get_entity(int(entity_id)) if entity_id is not None else None
    events = repo.list_issue_events(issue_id)
    evidence = _decode(issue["evidence"])
    confounders = evidence.get("confounders_checked", []) if isinstance(evidence, dict) else []
    detector_key = str(issue["detector_key"])
    playbook = _playbook_for(catalog, detector_key)

    sections = [
        _section_header(repo, issue, entity, now),
        _section_lifecycle(events),
        _section_evidence(evidence if isinstance(evidence, dict) else {}),
        _section_confounders([str(c) for c in confounders], playbook),
        _section_related(repo, issue, entity),
        _section_metric_windows(repo, entity, detector_key, int(issue["first_seen_ts"]), now),
        _section_site_context(repo),
        _section_playbook(detector_key, playbook),
        _STRUCTURED_QUESTIONS,
    ]
    return "\n\n".join(sections).rstrip() + "\n"


def build_incident_dossier(
    incident_id: int,
    repo: Repository,
    *,
    catalog: Catalog = DEFAULT_CATALOG,
    now: Optional[int] = None,
) -> str:
    """Compile a dossier that narrates a whole **incident** (section 17), not one
    issue: the root cause up front, the symptoms it explains with the correlation
    rationale that linked each, then the root's full issue dossier.

    Cheap and provider-independent: it reuses :func:`build_dossier` on the root
    issue (the thing to investigate and fix) and prepends the incident story built
    from the already-stored ``incident_members`` (role + rationale). Clustering is
    never LLM-driven — this only *describes* the deterministic grouping for a model
    to reason about the root cause. Raises ``KeyError`` if the incident is unknown.
    """
    incident = repo.get_incident(incident_id)
    if incident is None:
        raise KeyError(f"incident {incident_id} not found")

    members = repo.list_incident_members(incident_id)
    issue_ids = [int(m["issue_id"]) for m in members]
    issues_by_id = {int(r["id"]): r for r in repo.list_issues() if int(r["id"]) in set(issue_ids)}

    symptom_rows: list[list[Any]] = []
    for m in members:
        if m["role"] == "root":
            continue
        issue = issues_by_id.get(int(m["issue_id"]))
        title = issue["title"] if issue is not None else f"issue {int(m['issue_id'])}"
        symptom_rows.append([issue["detector_key"] if issue else "?", title, m["rationale"]])

    lines = [
        f"# Incident: {incident['title']}",
        "",
        str(incident["summary"] or "").strip() or "_(no summary)_",
        "",
        f"- **Severity (max of members):** {str(incident['severity']).upper()}",
        f"- **Members:** {len(members)} (1 root + {len(symptom_rows)} symptom(s))",
        f"- **First seen:** {_iso(int(incident['first_seen_ts']))}",
    ]
    if symptom_rows:
        lines += [
            "",
            "## Symptoms attributed to this root",
            "",
            _table(["Detector", "Symptom", "Why it is attributed"], symptom_rows),
        ]
    lines += [
        "",
        "## Root cause",
        "",
        "The full investigation dossier for the root issue follows. Fixing the "
        "root is expected to clear the symptoms above.",
        "",
    ]
    root_id = int(incident["root_issue_id"])
    root_dossier = build_dossier(root_id, repo, catalog=catalog, now=now)
    return "\n".join(lines).rstrip() + "\n\n" + root_dossier


def parse_answers(response_md: str) -> dict[str, str]:
    """Loosely parse an investigator response into ``{heading: body}``.

    Looks for the ``## Answers`` block and splits it on ``### `` subheadings. Any
    text before the first subheading is stored under ``"_preamble"``. Returns an
    empty dict when no ``## Answers`` heading is present — the raw response is
    always the source of truth; this is a convenience for structured display.
    """
    lines = response_md.splitlines()
    start: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## answers"):
            start = i + 1
            break
    if start is None:
        return {}

    out: dict[str, str] = {}
    current = "_preamble"
    buffer: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            break  # left the Answers block
        if stripped.startswith("### "):
            if buffer:
                text = "\n".join(buffer).strip()
                if text:
                    out[current] = text
                buffer = []
            current = stripped[4:].strip()
            continue
        buffer.append(line)
    if buffer:
        text = "\n".join(buffer).strip()
        if text:
            out[current] = text
    return out


def _playbook_for(catalog: Catalog, detector_key: str) -> Optional[Playbook]:
    try:
        return catalog.get(detector_key).playbook
    except KeyError:
        return None


def _decode(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
