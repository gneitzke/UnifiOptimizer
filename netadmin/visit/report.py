"""Render a :class:`~netadmin.visit.runner.VisitReport` to disk / the console.

Three surfaces, one report:

* :func:`render_html` — a single self-contained HTML file (inline CSS, no external
  requests, both light and dark themes) an admin can hand a client after a visit.
  It follows ``docs/DESIGN_FOUNDATION.md``: one accent, severity as tinted text +
  a shape glyph (never colour alone), tabular numerics, no gauges/gradients/glow.
* :func:`render_json` — the report's ``to_dict()`` as pretty JSON, for piping into
  other tools or re-rendering later.
* :func:`console_summary` — a compact, readable terminal summary for the CLI.

Nothing here re-derives analysis; it only formats what the runner already
computed. Kept deliberately separate from the daemon's React UI so the offline
report has no build step and no dependencies.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any, Optional

from netadmin.visit.runner import VisitReport

_SLE_ORDER = ("coverage", "capacity", "connect", "roaming", "wan", "infra")
_SLE_LABELS = {
    "coverage": "Coverage",
    "capacity": "Capacity",
    "connect": "Connectivity",
    "roaming": "Roaming",
    "wan": "WAN",
    "infra": "Infrastructure",
}
_SEV_LABEL = {"p1": "P1 critical", "p2": "P2 major", "p3": "P3 minor"}
_SEV_GLYPH = {"p1": "◉", "p2": "▲", "p3": "●"}  # octagon-ish / triangle / circle


# --------------------------------------------------------------------------- #
# Formatting helpers (shared by HTML + console)
# --------------------------------------------------------------------------- #
def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_duration(seconds: int) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    rem_m = m % 60
    if h < 24:
        return f"{h}h {rem_m}m" if rem_m else f"{h}h"
    d = h // 24
    rem_h = h % 24
    return f"{d}d {rem_h}h" if rem_h else f"{d}d"


def _score100(score: Optional[float]) -> Optional[int]:
    if score is None:
        return None
    try:
        return round(float(score) * 100)
    except (TypeError, ValueError):
        return None


def _band(score100: Optional[int]) -> str:
    if score100 is None:
        return "none"
    if score100 >= 90:
        return "good"
    if score100 >= 75:
        return "fair"
    return "poor"


def _humanize(key: str) -> str:
    acronyms = {"wan", "dns", "dhcp", "isp", "rssi", "ap", "poe", "sfp", "stp", "cci"}
    words = []
    for w in str(key).replace(".", "_").split("_"):
        if not w:
            continue
        words.append(w.upper() if w.lower() in acronyms else w.capitalize())
    return " ".join(words)


def _entity_label(entity: Optional[dict[str, Any]]) -> str:
    if not entity:
        return "—"
    return str(entity.get("name") or entity.get("native_id") or f"#{entity.get('entity_id')}")


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def render_json(report: VisitReport) -> str:
    """The report as pretty, sorted JSON."""
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# Console
# --------------------------------------------------------------------------- #
def console_summary(report: VisitReport) -> str:
    """A compact, human-readable terminal summary (no colour codes)."""
    lines: list[str] = []
    host = report.controller_host or "controller"
    lines.append(f"Tech visit — {host} (site {report.site_id})")
    lines.append(
        f"  window: {_fmt_ts(report.window_start_ts)} → {_fmt_ts(report.window_end_ts)}"
        f"  ({report.lookback_days}d lookback)"
    )
    lines.append(f"  run in {_fmt_duration(report.duration_s)}")

    headline = _score100(report.headline_score)
    lines.append("")
    lines.append(f"Network health: {headline if headline is not None else '—'}/100")
    sles = report.sles.get("sles", {}) if isinstance(report.sles, dict) else {}
    ordered = [k for k in _SLE_ORDER if k in sles] + [k for k in sles if k not in _SLE_ORDER]
    for key in ordered:
        s = sles[key]
        sc = _score100(s.get("score"))
        label = _SLE_LABELS.get(key, key.capitalize())
        val = f"{sc}/100" if sc is not None else "no data"
        lines.append(f"  {label:<15} {val}")

    c = report.issue_counts
    lines.append("")
    lines.append(
        f"Issues: {c.get('open', 0)} open "
        f"(P1 {c.get('p1', 0)} / P2 {c.get('p2', 0)} / P3 {c.get('p3', 0)})"
    )
    for issue in report.issues[:20]:
        if str(issue.get("state")) == "resolved":
            continue
        sev = str(issue.get("severity", ""))
        glyph = _SEV_GLYPH.get(sev, "•")
        owner = _entity_label(issue.get("entity"))
        lines.append(f"  {glyph} [{sev.upper()}] {issue.get('title', '')}  —  {owner}")

    if report.caveats:
        lines.append("")
        lines.append("Caveats:")
        for cav in report.caveats:
            lines.append(f"  • {cav}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
def render_html(report: VisitReport) -> str:
    """A single self-contained HTML document for the visit report."""
    body = "\n".join(
        [
            _html_header(report),
            _html_health(report),
            _html_issues(report),
            _html_topology(report),
            _html_coverage(report),
            _html_caveats(report),
            _html_footer(report),
        ]
    )
    host = html.escape(report.controller_host or "network")
    return _HTML_SHELL.format(title=f"Tech visit — {host}", css=_CSS, body=body)


def _html_header(report: VisitReport) -> str:
    host = html.escape(report.controller_host or "network")
    return f"""
<header class="head">
  <div class="eyebrow">Tech visit report</div>
  <h1>{host}</h1>
  <div class="sub">
    Site <span class="mono">{html.escape(report.site_id)}</span> ·
    {_fmt_ts(report.window_start_ts)} &rarr; {_fmt_ts(report.window_end_ts)} ·
    {report.lookback_days}-day lookback · run in {_fmt_duration(report.duration_s)}
  </div>
</header>"""


def _html_health(report: VisitReport) -> str:
    headline = _score100(report.headline_score)
    band = _band(headline)
    sles = report.sles.get("sles", {}) if isinstance(report.sles, dict) else {}
    ordered = [k for k in _SLE_ORDER if k in sles] + [k for k in sles if k not in _SLE_ORDER]

    cards = []
    for key in ordered:
        s = sles[key]
        sc = _score100(s.get("score"))
        b = _band(sc)
        label = html.escape(_SLE_LABELS.get(key, key.capitalize()))
        val = f"{sc}" if sc is not None else "&mdash;"
        sub = "no data" if sc is None else f"{s.get('fail_minutes', 0):.0f} fail min"
        offenders = ""
        tops = [o for o in s.get("top_offenders", []) if o.get("entity")]
        if tops and sc is not None and sc < 100:
            names = ", ".join(html.escape(_entity_label(o["entity"])) for o in tops[:2])
            offenders = f'<div class="sle-off">on {names}</div>'
        cards.append(
            f"""
    <div class="sle-card">
      <div class="sle-label">{label}</div>
      <div class="sle-score band-{b}">{val}</div>
      <div class="sle-sub">{sub}</div>
      {offenders}
    </div>"""
        )

    headline_txt = f"{headline}" if headline is not None else "&mdash;"
    band_word = {"good": "Healthy", "fair": "Fair", "poor": "Degraded", "none": "No data"}[band]
    return f"""
<section class="sec">
  <div class="headline">
    <div>
      <div class="k-label">Network health</div>
      <div class="headline-num band-{band}">{headline_txt}<span class="of">/100</span></div>
      <div class="headline-band"><span class="dot band-{band}"></span>{band_word}</div>
    </div>
  </div>
  <div class="sle-grid">{''.join(cards)}</div>
</section>"""


def _html_issues(report: VisitReport) -> str:
    open_issues = [i for i in report.issues if str(i.get("state")) != "resolved"]
    if not open_issues:
        return """
<section class="sec">
  <h2>Issues</h2>
  <div class="empty">No open issues detected in this window.</div>
</section>"""

    rows = []
    for issue in open_issues:
        sev = str(issue.get("severity", ""))
        glyph = _SEV_GLYPH.get(sev, "•")
        owner = html.escape(_entity_label(issue.get("entity")))
        title = html.escape(str(issue.get("title", "")))
        detector = html.escape(_humanize(str(issue.get("detector_key", ""))))
        evidence = _evidence_summary(issue.get("evidence"))
        confounders = issue.get("confounders") or []
        conf_html = ""
        if confounders:
            chips = "".join(
                f'<span class="chip">{html.escape(_humanize(c))}</span>' for c in confounders
            )
            conf_html = f'<div class="conf">Confounders checked: {chips}</div>'
        rows.append(
            f"""
    <div class="issue">
      <div class="issue-glyph sev-{sev}" aria-hidden="true">{glyph}</div>
      <div class="issue-body">
        <div class="issue-title">{title}</div>
        <div class="issue-meta">
          <span class="sev-{sev} sev-tag">{html.escape(_SEV_LABEL.get(sev, sev.upper()))}</span>
          · {detector} · <span class="mono">{owner}</span>
        </div>
        {evidence}
        {conf_html}
      </div>
    </div>"""
        )
    return f"""
<section class="sec">
  <h2>Issues <span class="count">{len(open_issues)}</span></h2>
  <div class="issues">{''.join(rows)}</div>
</section>"""


def _evidence_summary(evidence: Any) -> str:
    if not isinstance(evidence, dict) or not evidence:
        return ""
    items = []
    for k, v in evidence.items():
        if k == "confounders_checked":
            continue
        if isinstance(v, (dict, list)):
            continue
        items.append(
            f'<span class="ev"><span class="ev-k">{html.escape(_humanize(k))}</span>'
            f'<span class="ev-v">{html.escape(str(v))}</span></span>'
        )
        if len(items) >= 6:
            break
    if not items:
        return ""
    return f'<div class="evidence">{"".join(items)}</div>'


def _html_topology(report: VisitReport) -> str:
    topo = report.topology or {}
    by_type = topo.get("by_type", {})
    counts = "".join(
        f'<span class="stat"><span class="stat-n">{v}</span>'
        f'<span class="stat-l">{html.escape(_humanize(k))}</span></span>'
        for k, v in by_type.items()
    )
    devices = topo.get("devices", [])
    dev_rows = "".join(
        f"""
    <tr>
      <td>{html.escape(_entity_label(d))}</td>
      <td class="mono">{html.escape(_humanize(str(d.get('type', ''))))}</td>
      <td class="mono">{html.escape(str(d.get('model') or '—'))}</td>
      <td class="mono">{html.escape(str(d.get('native_id') or '—'))}</td>
    </tr>"""
        for d in devices
    )
    dev_table = (
        f"""
  <table class="tbl">
    <thead><tr><th>Device</th><th>Type</th><th>Model</th><th>MAC</th></tr></thead>
    <tbody>{dev_rows}</tbody>
  </table>"""
        if devices
        else '<div class="empty">No infrastructure devices discovered.</div>'
    )
    return f"""
<section class="sec">
  <h2>Topology</h2>
  <div class="stats">{counts}</div>
  {dev_table}
</section>"""


def _html_coverage(report: VisitReport) -> str:
    coverage = report.coverage or []
    if not coverage:
        return ""
    rows = []
    for c in coverage:
        live = c.get("live")
        backfill = c.get("backfill")
        rows.append(
            f"""
    <tr>
      <td class="mono">{html.escape(_humanize(str(c.get('job', ''))))}</td>
      <td class="num">{_pct(live)}</td>
      <td class="num">{_pct(backfill)}</td>
      <td class="num">{_pct(c.get('total'))}</td>
    </tr>"""
        )
    return f"""
<section class="sec">
  <h2>Data coverage</h2>
  <p class="note">Live is what a poller actually collected in-window; backfill is
  reconstructed from controller history (coarser, weighted down by detectors).</p>
  <table class="tbl">
    <thead><tr><th>Job</th><th class="num">Live</th><th class="num">Backfill</th>
    <th class="num">Total</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</section>"""


def _pct(frac: Any) -> str:
    if frac is None:
        return "&mdash;"
    try:
        return f"{round(float(frac) * 100)}%"
    except (TypeError, ValueError):
        return "&mdash;"


def _html_caveats(report: VisitReport) -> str:
    if not report.caveats:
        return ""
    items = "".join(f"<li>{html.escape(c)}</li>" for c in report.caveats)
    return f"""
<section class="sec">
  <h2>Caveats</h2>
  <ul class="caveats">{items}</ul>
</section>"""


def _html_footer(report: VisitReport) -> str:
    return f"""
<footer class="foot">
  Generated by netadmin · {_fmt_ts(report.finished_ts)}
</footer>"""


# --------------------------------------------------------------------------- #
# Static assets (inline; no external requests — offline-safe)
# --------------------------------------------------------------------------- #
_HTML_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<main class="wrap">
{body}
</main>
</body>
</html>
"""

_CSS = """
:root {
  --canvas:#F5F5F7; --surface:#FFFFFF; --hairline:#E5E5EA;
  --fg:#1D1D1F; --fg2:#55555A; --fg3:#6E6E73;
  --accent:#0066CC;
  --p1:#D70015; --p2:#C93400; --p3:#B25000; --healthy:#1E7A34; --neutral:#6E6E73;
}
@media (prefers-color-scheme: dark) {
  :root {
    --canvas:#161618; --surface:#1F1F21; --hairline:rgba(255,255,255,0.10);
    --fg:#F5F5F7; --fg2:#A1A1A6; --fg3:#8E8E93;
    --accent:#64A8FF;
    --p1:#FF6961; --p2:#FFB340; --p3:#FFD426; --healthy:#30DB5B; --neutral:#8E8E93;
  }
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--canvas); color:var(--fg);
  font-family:InterVariable,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-optical-sizing:auto; -webkit-font-smoothing:antialiased;
  font-size:14px; line-height:1.5;
}
.mono { font-variant-numeric:tabular-nums; font-family:ui-monospace,"Geist Mono",monospace; }
.num { text-align:right; font-variant-numeric:tabular-nums lining-nums; }
.wrap { max-width:860px; margin:0 auto; padding:32px 24px 64px; }
.head { padding-bottom:20px; border-bottom:1px solid var(--hairline); margin-bottom:24px; }
.eyebrow { font-size:12px; font-weight:600; letter-spacing:0.04em; text-transform:uppercase;
  color:var(--fg3); }
h1 { font-size:26px; line-height:1.2; font-weight:600; letter-spacing:-0.015em; margin:6px 0 8px; }
.sub { font-size:13px; color:var(--fg2); }
.sec { background:var(--surface); border:1px solid var(--hairline); border-radius:12px;
  padding:20px; margin-bottom:16px; }
h2 { font-size:17px; font-weight:600; letter-spacing:-0.01em; margin:0 0 12px; display:flex;
  align-items:center; gap:8px; }
.count { font-size:12px; font-weight:600; color:var(--fg3); background:var(--canvas);
  border-radius:999px; padding:1px 8px; }
.k-label { font-size:13px; font-weight:500; color:var(--fg2); }
.headline { margin-bottom:16px; }
.headline-num { font-size:44px; font-weight:600; letter-spacing:-0.02em;
  font-variant-numeric:tabular-nums; line-height:1.1; }
.headline-num .of { font-size:18px; color:var(--fg3); font-weight:400; margin-left:2px; }
.headline-band { font-size:13px; color:var(--fg2); display:flex; align-items:center; gap:6px; }
.dot { width:9px; height:9px; border-radius:50%; display:inline-block; background:var(--neutral); }
.band-good { color:var(--healthy); } .dot.band-good { background:var(--healthy); }
.band-fair { color:var(--p3); } .dot.band-fair { background:var(--p3); }
.band-poor { color:var(--p2); } .dot.band-poor { background:var(--p2); }
.band-none { color:var(--fg3); }
.sle-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }
.sle-card { border:1px solid var(--hairline); border-radius:10px; padding:12px; }
.sle-label { font-size:13px; font-weight:500; color:var(--fg2); }
.sle-score { font-size:28px; font-weight:600; font-variant-numeric:tabular-nums; line-height:1.2; }
.sle-sub { font-size:12px; color:var(--fg3); }
.sle-off { font-size:12px; color:var(--fg2); margin-top:4px; }
.issues { display:flex; flex-direction:column; gap:2px; }
.issue { display:flex; gap:12px; padding:12px 0; border-top:1px solid var(--hairline); }
.issue:first-child { border-top:none; }
.issue-glyph { font-size:15px; line-height:1.5; width:16px; text-align:center; flex:none; }
.issue-body { flex:1; min-width:0; }
.issue-title { font-weight:500; }
.issue-meta { font-size:13px; color:var(--fg2); margin-top:2px; }
.sev-tag { font-weight:600; }
.sev-p1 { color:var(--p1); } .sev-p2 { color:var(--p2); } .sev-p3 { color:var(--p3); }
.evidence { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.ev { display:inline-flex; align-items:baseline; gap:5px; background:var(--canvas);
  border-radius:6px; padding:2px 8px; font-size:12px; }
.ev-k { color:var(--fg3); } .ev-v { font-variant-numeric:tabular-nums; font-weight:500; }
.conf { font-size:12px; color:var(--fg3); margin-top:8px; display:flex; flex-wrap:wrap;
  gap:6px; align-items:center; }
.chip { background:var(--canvas); border-radius:6px; padding:1px 7px; color:var(--fg2); }
.stats { display:flex; flex-wrap:wrap; gap:20px; margin-bottom:16px; }
.stat { display:flex; flex-direction:column; }
.stat-n { font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; }
.stat-l { font-size:12px; color:var(--fg3); }
.tbl { width:100%; border-collapse:collapse; font-size:13px; }
.tbl th { text-align:left; font-weight:500; color:var(--fg3); font-size:12px;
  padding:6px 8px; border-bottom:1px solid var(--hairline); }
.tbl th.num { text-align:right; }
.tbl td { padding:8px; border-bottom:1px solid var(--hairline); }
.tbl tr:last-child td { border-bottom:none; }
.note { font-size:12px; color:var(--fg3); margin:0 0 12px; }
.empty { font-size:13px; color:var(--fg3); padding:8px 0; }
.caveats { margin:0; padding-left:18px; color:var(--fg2); font-size:13px; }
.caveats li { margin-bottom:6px; }
.foot { text-align:center; font-size:12px; color:var(--fg3); padding-top:16px; }
"""


__all__ = ["render_html", "render_json", "console_summary"]
