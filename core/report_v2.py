"""UniFi Network Analysis Report V2
===================================
Premium consultant-grade network analysis report.
Self-contained HTML5 — no external dependencies.
"""

import os
import math
import html as _html
from datetime import datetime


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _esc(text):
    """HTML-escape text safely."""
    return _html.escape(str(text)) if text else ""


def _score_color(score):
    """Map health score to color."""
    if score >= 90:
        return "#34a853"
    if score >= 75:
        return "#2196f3"
    if score >= 60:
        return "#fbbc04"
    if score >= 40:
        return "#ea8600"
    return "#ea4335"


def _fmt(n):
    """Format number with commas."""
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{int(n):,}" if n else "0"


def _priority_color(priority):
    if priority == "critical":
        return "#ea4335"
    if priority == "important":
        return "#fbbc04"
    return "#2196f3"


def _priority_label(priority):
    if priority == "critical":
        return "Critical"
    if priority == "important":
        return "Important"
    return "Recommended"


# ---------------------------------------------------------------------------
# CSS — plain string, no f-string (braces are literal)
# ---------------------------------------------------------------------------

def _css():
    return """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    background: #0b1426;
    color: #e8eaed;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
}
.container { max-width: 1120px; margin: 0 auto; padding: 0 24px 48px; }

/* Header */
.report-header {
    background: linear-gradient(180deg, #111d33 0%, #0e1829 100%);
    border-bottom: 2px solid #006fff;
    padding: 20px 32px;
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 32px;
}
.report-header h1 { font-size: 20px; font-weight: 600; letter-spacing: -0.3px; }
.report-header h1 span { color: #006fff; }
.report-header .meta { color: #9aa0a6; font-size: 13px; text-align: right; }

/* Hero */
.hero {
    display: flex; align-items: center; gap: 40px;
    background: #111d33; border-radius: 16px;
    padding: 36px 40px; margin-bottom: 28px;
    border: 1px solid #1e2d4a;
}
.ring-container { flex-shrink: 0; position: relative; width: 180px; height: 180px; }
.ring-score { font-size: 42px; font-weight: 700; fill: #e8eaed; }
.ring-grade { font-size: 18px; font-weight: 600; fill: #9aa0a6; }
.ring-label { font-size: 11px; fill: #5f6368; }
.ring-progress { transition: stroke-dashoffset 1.8s cubic-bezier(0.4, 0, 0.2, 1); }
.hero-content { flex: 1; min-width: 0; }
.stat-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.stat-card {
    background: #162540; border-radius: 10px; padding: 14px 16px;
    border: 1px solid #1e2d4a;
}
.stat-card .label { font-size: 11px; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.stat-card .value { font-size: 20px; font-weight: 700; }
.stat-card .detail { font-size: 12px; color: #9aa0a6; margin-top: 2px; }
.narrative { font-size: 14px; color: #9aa0a6; line-height: 1.6; }

/* Section titles */
.section-title {
    font-size: 16px; font-weight: 600; margin: 28px 0 16px;
    display: flex; align-items: center; gap: 10px;
}
.section-title .icon { font-size: 18px; }

/* Topology */
.topology-wrap {
    background: #111d33; border-radius: 12px; padding: 24px;
    margin-bottom: 28px; border: 1px solid #1e2d4a; overflow-x: auto;
}
.topology-svg { width: 100%; height: auto; display: block; }
.topo-node-rect { fill: #162540; stroke: #1e2d4a; stroke-width: 1.5; }
.topo-node-rect:hover { fill: #1a2f50; stroke: #006fff; }
.topo-ap-circle { fill: #162540; stroke-width: 2; }
.topo-ap-circle:hover { fill: #1a2f50; }
.topo-mesh-circle { fill: #162540; stroke-width: 2; stroke-dasharray: 6 3; }
.topo-edge { stroke: #1e2d4a; stroke-width: 1.2; }
.topo-edge-mesh { stroke: #006fff; stroke-width: 1.2; stroke-dasharray: 6 4; opacity: 0.6; }
.topo-label { fill: #e8eaed; font-size: 11px; text-anchor: middle; font-weight: 500; }
.topo-sublabel { fill: #9aa0a6; font-size: 9.5px; text-anchor: middle; }
.topo-badge {
    font-size: 9px; font-weight: 700; text-anchor: middle; fill: #0b1426;
}
.topo-rssi-label { fill: #006fff; font-size: 9px; text-anchor: middle; font-weight: 500; }
.legend-row { display: flex; gap: 20px; margin-top: 12px; justify-content: center; }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #9aa0a6; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

/* Actions */
.actions-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 28px; }
.action-card {
    background: #111d33; border-radius: 10px; padding: 16px 20px;
    border: 1px solid #1e2d4a; display: flex; gap: 16px; align-items: flex-start;
    border-left: 4px solid #2196f3;
}
.action-card.critical { border-left-color: #ea4335; }
.action-card.important { border-left-color: #fbbc04; }
.action-num {
    width: 28px; height: 28px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 13px; font-weight: 700;
    flex-shrink: 0; color: #0b1426;
}
.action-body { flex: 1; min-width: 0; }
.action-title { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.action-detail { font-size: 13px; color: #9aa0a6; line-height: 1.5; }
.action-meta { font-size: 11px; color: #5f6368; margin-top: 6px; display: flex; gap: 16px; }
.action-badge {
    font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
    text-transform: uppercase; letter-spacing: 0.5px;
}
.show-more-btn {
    background: #162540; border: 1px solid #1e2d4a; color: #9aa0a6;
    padding: 8px 20px; border-radius: 8px; cursor: pointer; font-size: 13px;
    text-align: center; width: 100%;
}
.show-more-btn:hover { background: #1a2f50; color: #e8eaed; }

/* Tabs */
.tabs-container { margin-bottom: 28px; }
.tab-nav {
    display: flex; gap: 0; border-bottom: 2px solid #1e2d4a; margin-bottom: 20px;
}
.tab-btn {
    background: none; border: none; color: #5f6368; padding: 10px 20px;
    font-size: 13px; font-weight: 500; cursor: pointer;
    border-bottom: 2px solid transparent; margin-bottom: -2px;
    transition: all 0.2s;
}
.tab-btn:hover { color: #9aa0a6; }
.tab-btn.active { color: #006fff; border-bottom-color: #006fff; }
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Cards within tabs */
.panel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.panel-card {
    background: #111d33; border-radius: 10px; padding: 20px;
    border: 1px solid #1e2d4a;
}
.panel-card.full { grid-column: 1 / -1; }
.panel-card h3 { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: #e8eaed; }
.panel-card h3 .count { color: #5f6368; font-weight: 400; }

/* Bar chart */
.bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.bar-label { width: 80px; font-size: 12px; color: #9aa0a6; text-align: right; flex-shrink: 0; }
.bar-track { flex: 1; height: 20px; background: #1e2d4a; border-radius: 4px; overflow: hidden; position: relative; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.5s ease; min-width: 2px; }
.bar-value { width: 50px; font-size: 12px; color: #e8eaed; font-weight: 500; }

/* SVG donut */
.donut-container { text-align: center; }
.donut-legend { margin-top: 8px; }
.donut-legend-item { display: inline-flex; align-items: center; gap: 5px; margin: 2px 8px; font-size: 11px; color: #9aa0a6; }
.donut-legend-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }

/* Matrix/table */
.matrix-table {
    width: 100%; border-collapse: collapse; font-size: 12px;
}
.matrix-table th {
    text-align: left; padding: 8px 10px; color: #5f6368;
    border-bottom: 1px solid #1e2d4a; font-weight: 500; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.3px;
}
.matrix-table td {
    padding: 7px 10px; border-bottom: 1px solid #1e2d4a15;
}
.matrix-table tr:hover td { background: #162540; }
.pwr-high { color: #ea4335; font-weight: 600; }
.pwr-medium { color: #34a853; }
.pwr-low { color: #2196f3; }
.pwr-auto { color: #5f6368; }
.feat-yes { color: #34a853; font-weight: 600; }
.feat-no { color: #ea4335; }
.feat-partial { color: #fbbc04; }

/* Port grid */
.port-grid { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.port-cell {
    width: 36px; height: 36px; border-radius: 4px; display: flex;
    align-items: center; justify-content: center; font-size: 10px; font-weight: 600;
    cursor: default; position: relative;
}
.port-cell.healthy { background: #34a85320; color: #34a853; border: 1px solid #34a85340; }
.port-cell.slow { background: #fbbc0420; color: #fbbc04; border: 1px solid #fbbc0440; }
.port-cell.drops { background: #ea433520; color: #ea4335; border: 1px solid #ea433540; }
.port-cell.inactive { background: #1e2d4a40; color: #5f6368; border: 1px solid #1e2d4a; }
.port-cell.ap { box-shadow: 0 0 0 2px #006fff60; }
.port-cell[title]:hover::after {
    content: attr(title); position: absolute; bottom: 110%; left: 50%;
    transform: translateX(-50%); background: #0b1426; border: 1px solid #1e2d4a;
    color: #e8eaed; padding: 4px 8px; border-radius: 4px; font-size: 10px;
    white-space: nowrap; z-index: 10; pointer-events: none;
}

/* Client table */
.client-table { width: 100%; font-size: 12px; }
.client-table th { text-align: left; padding: 6px 8px; color: #5f6368; font-weight: 500; border-bottom: 1px solid #1e2d4a; font-size: 11px; }
.client-table td { padding: 6px 8px; border-bottom: 1px solid #1e2d4a15; }
.behavior-badge {
    font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 500;
}
.behavior-stable { background: #34a85320; color: #34a853; }
.behavior-roamer { background: #2196f320; color: #2196f3; }
.behavior-flapping { background: #ea433520; color: #ea4335; }
.behavior-sticky { background: #fbbc0420; color: #fbbc04; }

/* Swim lane */
.swim-lane-wrap { margin-top: 12px; }
.swim-lane-title { font-size: 12px; font-weight: 600; margin-bottom: 6px; }
.swim-lane-subtitle { font-size: 11px; color: #9aa0a6; margin-bottom: 8px; }

/* Executive */
.executive {
    background: #111d33; border-radius: 12px; padding: 28px 32px;
    border: 1px solid #1e2d4a; margin-top: 28px;
}
.executive h2 { font-size: 16px; margin-bottom: 16px; }
.executive p { color: #9aa0a6; line-height: 1.7; margin-bottom: 12px; font-size: 14px; }
.print-btn {
    background: #162540; border: 1px solid #1e2d4a; color: #9aa0a6;
    padding: 8px 20px; border-radius: 6px; cursor: pointer; font-size: 13px;
    margin-top: 8px;
}
.print-btn:hover { background: #1a2f50; color: #e8eaed; }

/* Print */
@media print {
    body { background: #fff; color: #222; }
    .report-header, .topology-wrap, .tabs-container, .actions-list { display: none; }
    .hero { background: #f5f5f5; border: 1px solid #ddd; }
    .executive { background: #fff; border: 1px solid #ddd; page-break-inside: avoid; }
    .executive p { color: #333; }
    .print-btn { display: none; }
    .stat-card { background: #f5f5f5; border: 1px solid #ddd; }
}

/* Responsive */
@media (max-width: 768px) {
    .hero { flex-direction: column; padding: 24px; gap: 20px; }
    .stat-cards { grid-template-columns: repeat(2, 1fr); }
    .panel-grid { grid-template-columns: 1fr; }
    .tab-btn { padding: 8px 12px; font-size: 12px; }
}
"""


# ---------------------------------------------------------------------------
# JavaScript — plain string
# ---------------------------------------------------------------------------

def _js():
    return """
document.addEventListener('DOMContentLoaded', function() {
    // Animate health ring
    var ring = document.querySelector('.ring-progress');
    if (ring) {
        setTimeout(function() {
            ring.style.strokeDashoffset = ring.dataset.target;
        }, 200);
    }
    // Show first tab
    var firstTab = document.querySelector('.tab-content');
    if (firstTab) firstTab.classList.add('active');
    var firstBtn = document.querySelector('.tab-btn');
    if (firstBtn) firstBtn.classList.add('active');
});
function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(function(el) {
        el.classList.remove('active');
    });
    document.querySelectorAll('.tab-btn').forEach(function(el) {
        el.classList.remove('active');
    });
    var tab = document.getElementById('tab-' + tabId);
    if (tab) tab.classList.add('active');
    if (btn) btn.classList.add('active');
}
function toggleMore(id) {
    var el = document.getElementById(id);
    if (!el) return;
    var showing = el.style.display !== 'none';
    el.style.display = showing ? 'none' : 'block';
    var btn = el.previousElementSibling;
    if (btn && btn.classList.contains('show-more-btn')) {
        btn.textContent = showing ? 'Show more ▾' : 'Show less ▴';
    }
}
"""


# ---------------------------------------------------------------------------
# SVG Generators
# ---------------------------------------------------------------------------

def _svg_ring(score, size=180):
    """Animated health ring with score in center."""
    r = 72
    circumference = 2 * math.pi * r
    target_offset = circumference * (1 - score / 100)
    color = _score_color(score)
    grade = "A+" if score >= 95 else "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"

    return f"""<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none"
    stroke="#1e2d4a" stroke-width="10"/>
  <circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none"
    stroke="{color}" stroke-width="10" stroke-linecap="round"
    stroke-dasharray="{circumference:.1f}"
    stroke-dashoffset="{circumference:.1f}"
    data-target="{target_offset:.1f}"
    transform="rotate(-90 {size/2} {size/2})"
    class="ring-progress"
    style="filter: drop-shadow(0 0 6px {color}40);"/>
  <text x="{size/2}" y="{size/2 - 8}" class="ring-score" text-anchor="middle">{score}</text>
  <text x="{size/2}" y="{size/2 + 14}" class="ring-grade" text-anchor="middle">{grade}</text>
  <text x="{size/2}" y="{size/2 + 30}" class="ring-label" text-anchor="middle">HEALTH SCORE</text>
</svg>"""


def _svg_donut(segments, size=140):
    """SVG donut chart. segments = [(value, color, label), ...]."""
    total = sum(s[0] for s in segments if s[0] > 0)
    if total == 0:
        return ""
    # Using r ≈ 15.915 so circumference = 100 (percentage-based dasharray)
    r = 15.91549430918954
    vb = 42
    cx, cy = vb / 2, vb / 2

    arcs = []
    offset = 25  # start at 12 o'clock
    for value, color, label in segments:
        if value <= 0:
            continue
        pct = (value / total) * 100
        arcs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
            f'stroke="{color}" stroke-width="7" '
            f'stroke-dasharray="{pct:.2f} {100 - pct:.2f}" '
            f'stroke-dashoffset="{offset:.2f}"/>'
        )
        offset -= pct

    # Legend
    legend_items = []
    for value, color, label in segments:
        if value <= 0:
            continue
        pct = value / total * 100
        legend_items.append(
            f'<span class="donut-legend-item">'
            f'<span class="donut-legend-dot" style="background:{color}"></span>'
            f'{_esc(label)} ({value}, {pct:.0f}%)</span>'
        )

    return (
        f'<div class="donut-container">'
        f'<svg viewBox="0 0 {vb} {vb}" width="{size}" height="{size}">{"".join(arcs)}</svg>'
        f'<div class="donut-legend">{"".join(legend_items)}</div></div>'
    )


def _svg_hbar(items, max_val=None):
    """Horizontal bar chart. items = [(label, value, color), ...]."""
    if not items:
        return ""
    if max_val is None:
        max_val = max(v for _, v, _ in items) or 1

    rows = []
    for label, value, color in items:
        pct = min(100, (value / max_val) * 100) if max_val > 0 else 0
        rows.append(
            f'<div class="bar-row">'
            f'<span class="bar-label">{_esc(label)}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>'
            f'<span class="bar-value">{_fmt(value)}</span>'
            f'</div>'
        )
    return "\n".join(rows)


def _svg_swim_lane(client_name, client_data, width=700):
    """Client journey swim lane visualization."""
    ap_path = client_data.get("ap_path", [])
    visited_aps = client_data.get("visited_aps", [])
    if not ap_path or not visited_aps:
        return ""

    behavior = client_data.get("behavior", "unknown")
    roams = client_data.get("roam_count", 0)
    daily = client_data.get("daily_roam_rate", 0)

    # Limit APs shown to most visited (top 6)
    visited_aps = visited_aps[:6]
    row_h = 24
    label_w = 85
    h = len(visited_aps) * row_h + 30

    # Time range
    times = [e.get("ts", 0) for e in ap_path if e.get("ts")]
    if not times:
        return ""
    t_min, t_max = min(times), max(times)
    t_span = t_max - t_min or 1

    ap_colors = ["#006fff", "#34a853", "#fbbc04", "#ea8600", "#9c27b0", "#00bcd4", "#ff5722"]

    parts = [f'<svg viewBox="0 0 {width} {h}" width="100%" preserveAspectRatio="xMinYMin meet" style="max-width:{width}px">']

    # Grid lines and AP labels
    for i, ap in enumerate(visited_aps):
        y = i * row_h + 20
        parts.append(f'<text x="2" y="{y + 4}" fill="#9aa0a6" font-size="9.5" font-family="sans-serif">{_esc(ap[:12])}</text>')
        parts.append(f'<line x1="{label_w}" y1="{y - 6}" x2="{width}" y2="{y - 6}" stroke="#1e2d4a" stroke-width="0.5"/>')

    # Build segments from path
    for i, event in enumerate(ap_path):
        to_ap = event.get("to_ap", "")
        if to_ap not in visited_aps:
            continue
        row = visited_aps.index(to_ap)
        t_start = event.get("ts", 0)
        t_end = ap_path[i + 1].get("ts", t_max) if i + 1 < len(ap_path) else t_max

        x1 = label_w + (t_start - t_min) / t_span * (width - label_w - 10)
        x2 = label_w + (t_end - t_min) / t_span * (width - label_w - 10)
        y = row * row_h + 12
        color = ap_colors[row % len(ap_colors)]
        w = max(2, x2 - x1)
        parts.append(f'<rect x="{x1:.1f}" y="{y}" width="{w:.1f}" height="{row_h - 6}" rx="2" fill="{color}" opacity="0.65"/>')

    parts.append("</svg>")

    bclass = "behavior-flapping" if behavior == "flapping" else "behavior-roamer" if "roam" in behavior else "behavior-sticky" if behavior == "sticky" else "behavior-stable"

    return (
        f'<div class="swim-lane-wrap">'
        f'<div class="swim-lane-title">{_esc(client_name)} '
        f'<span class="behavior-badge {bclass}">{_esc(behavior)}</span></div>'
        f'<div class="swim-lane-subtitle">{roams} roams ({daily:.0f}/day) across {len(visited_aps)} APs</div>'
        f'{"".join(parts)}</div>'
    )


# ---------------------------------------------------------------------------
# Section: Header
# ---------------------------------------------------------------------------

def _header(site_name, analysis_data):
    now = datetime.now().strftime("%B %d, %Y at %H:%M")
    lookback = analysis_data.get("lookback_days", 3)
    return (
        f'<div class="report-header">'
        f'<h1><span>UniFi</span> Network Analysis</h1>'
        f'<div class="meta">Site: {_esc(site_name)}<br>{now}<br>{lookback}-day lookback</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Section: Hero Dashboard
# ---------------------------------------------------------------------------

def _hero(analysis_data, recommendations):
    hs = analysis_data.get("health_score", {})
    score = hs.get("score", 0) or 0

    # AP counts
    ap_analysis = analysis_data.get("ap_analysis", {})
    total_aps = ap_analysis.get("total_aps", 0)
    mesh_aps = ap_analysis.get("mesh_aps", [])
    wired_aps = ap_analysis.get("wired_aps", [])
    mesh_count = len(mesh_aps) if isinstance(mesh_aps, list) else 0
    wired_ap_count = len(wired_aps) if isinstance(wired_aps, list) else 0

    # Client counts
    ca = analysis_data.get("client_analysis", {})
    total_clients = ca.get("total_clients", 0)
    wired_clients = sum(1 for c in ca.get("clients", []) if c.get("is_wired"))
    wireless_clients = total_clients - wired_clients

    # Top issue
    issues = analysis_data.get("health_analysis", {}).get("issues", [])
    critical = [i for i in issues if i.get("severity") in ("high", "critical")]
    top_issue_text = critical[0].get("message", "No critical issues")[:60] if critical else "No critical issues"

    # Action count
    action_count = len(_group_recs(recommendations, analysis_data))

    # Narrative
    narrative_parts = [f"{total_aps} APs serving {wireless_clients} wireless clients."]
    if critical:
        narrative_parts.append(critical[0].get("message", "")[:80] + ".")
    bs = analysis_data.get("band_steering_analysis", {})
    stuck_2g = bs.get("dual_band_clients_on_2ghz", 0)
    if stuck_2g > 5:
        narrative_parts.append(f"{stuck_2g} capable clients stuck on 2.4GHz.")
    if action_count:
        narrative_parts.append(f"{action_count} actionable fixes identified.")
    narrative = " ".join(narrative_parts)

    ring = _svg_ring(score)

    return (
        f'<div class="hero">'
        f'<div class="ring-container">{ring}</div>'
        f'<div class="hero-content">'
        f'<div class="stat-cards">'
        # Card 1: APs
        f'<div class="stat-card">'
        f'<div class="label">Access Points</div>'
        f'<div class="value">{total_aps}</div>'
        f'<div class="detail">{wired_ap_count} wired · {mesh_count} mesh</div></div>'
        # Card 2: Clients
        f'<div class="stat-card">'
        f'<div class="label">Clients</div>'
        f'<div class="value">{wireless_clients}</div>'
        f'<div class="detail">{wired_clients} wired</div></div>'
        # Card 3: Top Issue
        f'<div class="stat-card">'
        f'<div class="label">Top Issue</div>'
        f'<div class="value" style="font-size:13px;line-height:1.3;margin-top:2px">{_esc(top_issue_text)}</div></div>'
        # Card 4: Actions
        f'<div class="stat-card">'
        f'<div class="label">Actions</div>'
        f'<div class="value">{action_count}</div>'
        f'<div class="detail">optimization{"s" if action_count != 1 else ""} available</div></div>'
        f'</div>'
        f'<div class="narrative">{_esc(narrative)}</div>'
        f'</div></div>'
    )


# ---------------------------------------------------------------------------
# Section: Network Topology
# ---------------------------------------------------------------------------

def _topology(analysis_data):
    devices = analysis_data.get("devices", [])
    ap_details = analysis_data.get("ap_analysis", {}).get("ap_details", [])
    health_issues = analysis_data.get("health_analysis", {}).get("issues", [])

    if not devices:
        return ""

    # Build lookups
    ap_info_by_name = {ap.get("name", ""): ap for ap in ap_details}
    issue_devices = set()
    for issue in health_issues:
        if issue.get("severity") in ("high", "critical"):
            issue_devices.add(issue.get("device", ""))

    # Classify devices
    switches = [d for d in devices if d.get("type") == "usw"]
    aps = [d for d in devices if d.get("type") == "uap"]
    mac_to_dev = {d.get("mac", ""): d for d in devices}

    # Enrich APs with analysis data
    for ap in aps:
        name = ap.get("name", "")
        info = ap_info_by_name.get(name, {})
        ap["_is_mesh"] = info.get("is_mesh", False)
        ap["_clients"] = info.get("client_count", 0)
        ap["_rssi"] = info.get("uplink_rssi")
        ap["_has_issue"] = name in issue_devices

    wired_aps = [a for a in aps if not a.get("_is_mesh")]
    mesh_aps = [a for a in aps if a.get("_is_mesh")]

    # Layout computation
    svg_w = 960
    has_mesh = len(mesh_aps) > 0
    svg_h = 310 if has_mesh else 210

    # Level positions
    sw_y = 55
    wap_y = 165
    map_y = 265

    # Position switches
    positions = {}
    if switches:
        sp = svg_w / (len(switches) + 1)
        for i, sw in enumerate(switches):
            positions[sw.get("mac", "")] = (sp * (i + 1), sw_y)

    # Position wired APs
    if wired_aps:
        sp = svg_w / (len(wired_aps) + 1)
        for i, ap in enumerate(wired_aps):
            positions[ap.get("mac", "")] = (sp * (i + 1), wap_y)

    # Position mesh APs
    if mesh_aps:
        sp = svg_w / (len(mesh_aps) + 1)
        for i, ap in enumerate(mesh_aps):
            positions[ap.get("mac", "")] = (sp * (i + 1), map_y)

    svg = [f'<svg viewBox="0 0 {svg_w} {svg_h}" class="topology-svg" xmlns="http://www.w3.org/2000/svg">']

    # Draw edges (behind nodes)
    for dev in switches + aps:
        child_mac = dev.get("mac", "")
        uplink = dev.get("uplink", {}) or {}
        parent_mac = uplink.get("uplink_mac", "")
        if not parent_mac or child_mac not in positions or parent_mac not in positions:
            continue
        cx, cy = positions[child_mac]
        px, py = positions[parent_mac]
        is_mesh = dev.get("_is_mesh", False)
        edge_class = "topo-edge-mesh" if is_mesh else "topo-edge"
        # Offset: connect from parent bottom to child top
        py_off = py + (25 if dev.get("type") != "uap" else 30)
        cy_off = cy - (25 if dev.get("type") == "usw" else 28)
        svg.append(f'<line x1="{px:.0f}" y1="{py_off}" x2="{cx:.0f}" y2="{cy_off}" class="{edge_class}"/>')
        if is_mesh and dev.get("_rssi"):
            mx = (px + cx) / 2
            my = (py_off + cy_off) / 2
            svg.append(f'<text x="{mx:.0f}" y="{my:.0f}" class="topo-rssi-label">{dev["_rssi"]} dBm</text>')

    # Draw switch nodes
    for sw in switches:
        mac = sw.get("mac", "")
        if mac not in positions:
            continue
        x, y = positions[mac]
        name = sw.get("name", "Switch")[:18]
        port_count = len([p for p in sw.get("port_table", []) if p.get("speed", 0) > 0]) if sw.get("port_table") else "?"
        sw_color = "#ea4335" if name in issue_devices else "#1e2d4a"
        svg.append(
            f'<g transform="translate({x:.0f},{y:.0f})">'
            f'<rect x="-58" y="-24" width="116" height="48" rx="6" class="topo-node-rect" style="stroke:{sw_color}"/>'
            f'<text y="-4" class="topo-label">{_esc(name[:16])}</text>'
            f'<text y="12" class="topo-sublabel">Switch</text>'
            f'</g>'
        )

    # Draw AP nodes
    for ap in wired_aps + mesh_aps:
        mac = ap.get("mac", "")
        if mac not in positions:
            continue
        x, y = positions[mac]
        name = ap.get("name", "AP")[:14]
        clients = ap.get("_clients", 0)
        is_mesh = ap.get("_is_mesh", False)
        has_issue = ap.get("_has_issue", False)

        stroke = "#ea4335" if has_issue else "#006fff" if is_mesh else "#34a853"
        circle_class = "topo-mesh-circle" if is_mesh else "topo-ap-circle"

        svg.append(
            f'<g transform="translate({x:.0f},{y:.0f})">'
            f'<circle r="28" class="{circle_class}" style="stroke:{stroke}"/>'
            f'<text y="-5" class="topo-label">{_esc(name)}</text>'
            f'<text y="9" class="topo-sublabel">{clients} client{"s" if clients != 1 else ""}</text>'
        )
        # Client count badge
        if clients > 0:
            badge_color = "#ea4335" if has_issue else "#006fff" if is_mesh else "#34a853"
            svg.append(
                f'<circle cx="20" cy="-20" r="9" fill="{badge_color}"/>'
                f'<text x="20" y="-17" class="topo-badge">{clients}</text>'
            )
        svg.append("</g>")

    svg.append("</svg>")

    return (
        f'<div class="section-title"><span class="icon">🗺</span> Network Topology</div>'
        f'<div class="topology-wrap">{"".join(svg)}'
        f'<div class="legend-row">'
        f'<span class="legend-item"><span class="legend-dot" style="background:#34a853"></span>Wired AP</span>'
        f'<span class="legend-item"><span class="legend-dot" style="background:#006fff;border:1px dashed #006fff"></span>Mesh AP</span>'
        f'<span class="legend-item"><span class="legend-dot" style="background:#ea4335"></span>Issue detected</span>'
        f'</div></div>'
    )


# ---------------------------------------------------------------------------
# Recommendation grouping
# ---------------------------------------------------------------------------

_GROUP_META = {
    "mesh_power": ("Optimize Mesh AP Power", "important", "MEDIUM power improves mesh link stability and reduces co-channel interference"),
    "power_optimization": ("Reduce Transmit Power", "important", "Lower power reduces interference and helps clients roam to closer APs"),
    "band_steering": ("Enable Band Steering", "important", "Move capable clients to faster 5GHz/6GHz bands automatically"),
    "band_steering_critical": ("Enable Band Steering", "important", "Clients stuck on 2.4GHz need band steering to use faster bands"),
    "min_rssi_disabled": ("Enable Minimum RSSI", "recommended", "Force weak clients to roam to a closer AP for better performance"),
    "channel_width": ("Optimize Channel Widths", "recommended", "Match channel width to environment for best throughput"),
}


def _group_recs(recommendations, analysis_data):
    """Group recommendations into actionable cards."""
    actions = []

    # Critical health issues first
    issues = analysis_data.get("health_analysis", {}).get("issues", [])
    for issue in issues:
        if issue.get("severity") in ("high", "critical"):
            actions.append({
                "priority": "critical",
                "title": issue.get("device", "Network") + ": " + (issue.get("type", "issue")).replace("_", " ").title(),
                "detail": issue.get("message", ""),
                "action": issue.get("recommendation", ""),
                "count": 1,
                "devices": [issue.get("device", "")],
            })

    # Group recs by type
    groups = {}
    for rec in recommendations:
        rtype = rec.get("type", "other")
        groups.setdefault(rtype, []).append(rec)

    for rtype in ["band_steering", "band_steering_critical", "power_optimization",
                   "mesh_power", "min_rssi_disabled", "channel_width"]:
        recs = groups.pop(rtype, [])
        if not recs:
            continue
        meta = _GROUP_META.get(rtype, (rtype.replace("_", " ").title(), "recommended", ""))
        devs = list(set(
            r.get("ap", {}).get("name", "") or r.get("device", "")
            for r in recs
        ))
        devs = [d for d in devs if d]

        # Merge band_steering and band_steering_critical
        if rtype == "band_steering_critical" and actions:
            for a in actions:
                if "Band Steering" in a.get("title", ""):
                    bs = analysis_data.get("band_steering_analysis", {})
                    stuck = bs.get("dual_band_clients_on_2ghz", 0)
                    a["detail"] += f" {stuck} clients stuck on 2.4GHz."
                    a["count"] += len(recs)
                    break
            else:
                pass  # fall through to add
            continue

        actions.append({
            "priority": meta[1],
            "title": meta[0],
            "detail": f"{meta[2]}. Affects: {', '.join(devs[:6])}{'...' if len(devs) > 6 else ''}." if devs else meta[2] + ".",
            "action": recs[0].get("recommendation", ""),
            "count": len(recs),
            "devices": devs,
        })

    # Remaining groups
    for rtype, recs in groups.items():
        meta = _GROUP_META.get(rtype, (rtype.replace("_", " ").title(), "recommended", ""))
        actions.append({
            "priority": meta[1] if isinstance(meta, tuple) else "recommended",
            "title": meta[0] if isinstance(meta, tuple) else rtype.replace("_", " ").title(),
            "detail": f"{len(recs)} recommendation{'s' if len(recs) > 1 else ''}.",
            "count": len(recs),
            "devices": [],
        })

    return actions


# ---------------------------------------------------------------------------
# Section: Top Actions
# ---------------------------------------------------------------------------

def _actions(recommendations, analysis_data):
    actions = _group_recs(recommendations, analysis_data)
    if not actions:
        return ""

    visible = actions[:5]
    hidden = actions[5:]

    cards = []
    for i, action in enumerate(visible):
        pri = action.get("priority", "recommended")
        color = _priority_color(pri)
        cards.append(
            f'<div class="action-card {pri}">'
            f'<div class="action-num" style="background:{color}">{i + 1}</div>'
            f'<div class="action-body">'
            f'<div class="action-title">{_esc(action["title"])}</div>'
            f'<div class="action-detail">{_esc(action.get("detail", ""))}</div>'
            f'<div class="action-meta">'
            f'<span class="action-badge" style="background:{color}20;color:{color}">{_priority_label(pri)}</span>'
            f'<span>{action.get("count", 1)} change{"s" if action.get("count", 1) > 1 else ""}</span>'
            f'</div></div></div>'
        )

    hidden_html = ""
    if hidden:
        hidden_cards = []
        for i, action in enumerate(hidden):
            pri = action.get("priority", "recommended")
            color = _priority_color(pri)
            hidden_cards.append(
                f'<div class="action-card {pri}">'
                f'<div class="action-num" style="background:{color}">{i + 6}</div>'
                f'<div class="action-body">'
                f'<div class="action-title">{_esc(action["title"])}</div>'
                f'<div class="action-detail">{_esc(action.get("detail", ""))}</div>'
                f'</div></div>'
            )
        hidden_html = (
            f'<button class="show-more-btn" onclick="toggleMore(\'more-actions\')">Show {len(hidden)} more ▾</button>'
            f'<div id="more-actions" style="display:none">{"".join(hidden_cards)}</div>'
        )

    return (
        f'<div class="section-title"><span class="icon">🎯</span> Top Actions</div>'
        f'<div class="actions-list">{"".join(cards)}{hidden_html}</div>'
    )


# ---------------------------------------------------------------------------
# Deep Dive: RF & Airtime
# ---------------------------------------------------------------------------

def _rf_panel(analysis_data):
    sd = analysis_data.get("client_analysis", {}).get("signal_distribution", {})
    caps = analysis_data.get("client_capabilities", {}).get("capability_distribution", {})
    ap_details = analysis_data.get("ap_analysis", {}).get("ap_details", [])

    # Signal distribution bar chart
    sig_items = [
        ("Excellent", sd.get("excellent", 0), "#34a853"),
        ("Good", sd.get("good", 0), "#2196f3"),
        ("Fair", sd.get("fair", 0), "#fbbc04"),
        ("Poor", sd.get("poor", 0), "#ea8600"),
        ("Critical", sd.get("critical", 0), "#ea4335"),
    ]
    total_sig = sum(v for _, v, _ in sig_items)
    sig_chart = _svg_hbar(sig_items, max_val=total_sig) if total_sig else "<span style='color:#5f6368'>No wireless clients</span>"

    # Band distribution from client channels
    clients = analysis_data.get("client_analysis", {}).get("clients", [])
    band_24 = sum(1 for c in clients if not c.get("is_wired") and 0 < (c.get("channel") or 0) <= 14)
    band_5 = sum(1 for c in clients if not c.get("is_wired") and 14 < (c.get("channel") or 0) < 200)
    band_6 = sum(1 for c in clients if not c.get("is_wired") and (c.get("channel") or 0) >= 200)
    band_donut = _svg_donut([
        (band_24, "#fbbc04", "2.4 GHz"),
        (band_5, "#2196f3", "5 GHz"),
        (band_6, "#9c27b0", "6 GHz"),
    ], size=130)

    # Capability donut
    cap_donut = _svg_donut([
        (caps.get("802.11ax", 0), "#34a853", "WiFi 6"),
        (caps.get("802.11ac", 0), "#2196f3", "WiFi 5"),
        (caps.get("802.11n", 0), "#fbbc04", "WiFi 4"),
        (caps.get("legacy", 0), "#ea4335", "Legacy"),
    ], size=130)

    # Power/Channel matrix
    matrix_rows = []
    for ap in ap_details:
        name = ap.get("name", "?")[:16]
        is_mesh = ap.get("is_mesh", False)
        row_cells = [f'<td>{_esc(name)}{"  ◌" if is_mesh else ""}</td>']
        for band in ["2.4GHz", "5GHz", "6GHz"]:
            radio = ap.get("radios", {}).get(band)
            if radio:
                pwr_mode = radio.get("tx_power_mode", "?")
                ch = radio.get("channel", 0)
                pwr_class = f"pwr-{pwr_mode}" if pwr_mode in ("high", "medium", "low", "auto") else ""
                row_cells.append(f'<td class="{pwr_class}">ch{ch} · {pwr_mode}</td>')
            else:
                row_cells.append('<td style="color:#5f6368">—</td>')
        matrix_rows.append(f'<tr>{"".join(row_cells)}</tr>')

    matrix_html = (
        '<table class="matrix-table">'
        '<tr><th>AP</th><th>2.4 GHz</th><th>5 GHz</th><th>6 GHz</th></tr>'
        + "".join(matrix_rows) +
        '</table>'
    )

    return (
        f'<div class="panel-grid">'
        # Signal distribution
        f'<div class="panel-card">'
        f'<h3>Signal Quality <span class="count">({total_sig} wireless)</span></h3>'
        f'{sig_chart}</div>'
        # Band distribution
        f'<div class="panel-card">'
        f'<h3>Band Distribution</h3>'
        f'{band_donut}</div>'
        # Capability
        f'<div class="panel-card">'
        f'<h3>Client Capabilities</h3>'
        f'{cap_donut}</div>'
        # Power/Channel matrix
        f'<div class="panel-card">'
        f'<h3>Channel &amp; Power Map</h3>'
        f'{matrix_html}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Deep Dive: Clients
# ---------------------------------------------------------------------------

def _clients_panel(analysis_data):
    ca = analysis_data.get("client_analysis", {})
    journeys = analysis_data.get("client_journeys", {})

    # Problem clients — only those with issues
    all_clients = ca.get("clients", [])
    problem_clients = [
        c for c in all_clients
        if not c.get("is_wired") and (
            c.get("health_score", 100) < 70
            or c.get("roam_count", 0) > 20
            or c.get("disconnect_count", 0) > 5
        )
    ]
    problem_clients.sort(key=lambda c: c.get("health_score", 100))
    problem_clients = problem_clients[:15]

    if problem_clients:
        rows = []
        for c in problem_clients:
            score = c.get("health_score", 0)
            color = _score_color(score)
            rssi = c.get("rssi", 0)
            rows.append(
                f'<tr>'
                f'<td>{_esc(c.get("hostname", "?")[:20])}</td>'
                f'<td>{_esc(c.get("ap_name", "?"))}</td>'
                f'<td>{rssi} dBm</td>'
                f'<td style="color:{color};font-weight:600">{score} ({c.get("grade", "?")})</td>'
                f'<td>{c.get("roam_count", 0)} roams</td>'
                f'<td>{c.get("disconnect_count", 0)} disc</td>'
                f'</tr>'
            )
        client_table = (
            '<table class="client-table">'
            '<tr><th>Client</th><th>AP</th><th>Signal</th><th>Health</th><th>Roaming</th><th>Disconnects</th></tr>'
            + "".join(rows) +
            '</table>'
        )
    else:
        client_table = '<div style="color:#34a853;padding:12px">All wireless clients are healthy.</div>'

    # Client journeys — top 3 issues
    journey_html = ""
    top_issues = journeys.get("top_issues", [])
    journey_clients = journeys.get("clients", {})
    for issue in top_issues[:3]:
        client_name = issue.get("client", "?")
        # Find MAC for this client
        client_mac = None
        for mac, jdata in journey_clients.items():
            if jdata.get("hostname") == client_name or client_name in str(jdata):
                client_mac = mac
                break
        if not client_mac:
            # Try matching by name in the client analysis
            for c in all_clients:
                if c.get("hostname") == client_name:
                    client_mac = c.get("mac")
                    break
        if client_mac and client_mac in journey_clients:
            jdata = journey_clients[client_mac]
            lane = _svg_swim_lane(client_name, jdata)
            if lane:
                journey_html += lane

    if not journey_html:
        journey_html = '<div style="color:#5f6368;padding:8px">No significant roaming issues detected.</div>'

    total_tracked = journeys.get("total_tracked", 0)

    return (
        f'<div class="panel-grid">'
        f'<div class="panel-card full">'
        f'<h3>Problem Clients <span class="count">({len(problem_clients)} of {ca.get("total_clients", 0)})</span></h3>'
        f'{client_table}</div>'
        f'<div class="panel-card full">'
        f'<h3>Client Journeys <span class="count">({total_tracked} tracked)</span></h3>'
        f'{journey_html}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Deep Dive: Infrastructure
# ---------------------------------------------------------------------------

def _infra_panel(analysis_data):
    sw_analysis = analysis_data.get("switch_analysis", {})
    switches = sw_analysis.get("switches", [])
    if not switches:
        return '<div class="panel-card"><p style="color:#5f6368">No switches detected.</p></div>'

    cards = []
    for sw in switches:
        name = sw.get("name", "Switch")
        model = sw.get("model", "")
        ports = sw.get("ports", [])
        issues = sw.get("issues", []) if isinstance(sw.get("issues"), list) else []

        # Build port grid
        port_cells = []
        for p in ports:
            idx = p.get("port_idx", "?")
            speed = p.get("speed", 0)
            is_ap = p.get("is_ap", False)
            rx_drop = p.get("rx_dropped", 0) or 0
            tx_drop = p.get("tx_dropped", 0) or 0
            has_drops = (rx_drop + tx_drop) > 100

            if speed == 0:
                css = "inactive"
                tip = f"Port {idx}: No link"
            elif speed < 100:
                css = "slow"
                tip = f"Port {idx}: {speed}Mbps"
            elif speed < 1000:
                css = "slow"
                tip = f"Port {idx}: {speed}Mbps (slow)"
            elif has_drops:
                css = "drops"
                tip = f"Port {idx}: {speed}Mbps, {_fmt(rx_drop + tx_drop)} drops"
            else:
                css = "healthy"
                tip = f"Port {idx}: {speed}Mbps"

            pname = p.get("name", "")
            if pname and pname != f"Port {idx}":
                tip += f" — {pname}"

            if is_ap:
                css += " ap"
                tip += " [AP]"

            port_cells.append(f'<div class="port-cell {css}" title="{_esc(tip)}">{idx}</div>')

        # PoE info
        poe_html = ""
        poe = sw.get("total_max_power")
        poe_used = sw.get("system_stats", {}).get("output_power") if sw.get("system_stats") else None
        if poe and poe > 0:
            used = poe_used or 0
            poe_html = f'<div style="font-size:12px;color:#9aa0a6;margin-top:6px">PoE: {used:.0f}W / {poe}W</div>'

        # Issues
        issue_html = ""
        sw_issues = [i for i in sw_analysis.get("issues", []) if i.get("switch") == name or name in str(i)]
        if sw_issues:
            issue_items = []
            for si in sw_issues[:5]:
                issue_items.append(f'<div style="font-size:11px;color:#ea8600;margin:2px 0">{_esc(str(si.get("message", si.get("issue", "?")))[:80])}</div>')
            issue_html = f'<div style="margin-top:8px">{"".join(issue_items)}</div>'

        active_ports = sum(1 for p in ports if (p.get("speed", 0) or 0) > 0)
        total_ports = len(ports)

        cards.append(
            f'<div class="panel-card">'
            f'<h3>{_esc(name)} <span class="count">{model}</span></h3>'
            f'<div style="font-size:12px;color:#9aa0a6;margin-bottom:8px">{active_ports}/{total_ports} ports active</div>'
            f'<div class="port-grid">{"".join(port_cells)}</div>'
            f'{poe_html}{issue_html}'
            f'</div>'
        )

    # Port health legend
    legend = (
        '<div style="display:flex;gap:16px;margin-top:4px;font-size:11px;color:#5f6368">'
        '<span>🟢 1Gbps</span><span>🟡 Slow</span><span>🔴 Drops</span><span>⚪ Inactive</span>'
        '<span style="color:#006fff">◎ AP port</span>'
        '</div>'
    )

    return (
        f'<div class="panel-grid">{"".join(cards)}'
        f'<div class="panel-card full" style="padding:12px 20px">{legend}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Deep Dive: Configuration
# ---------------------------------------------------------------------------

def _config_panel(analysis_data):
    ap_details = analysis_data.get("ap_analysis", {}).get("ap_details", [])
    bs = analysis_data.get("band_steering_analysis", {})
    mr = analysis_data.get("min_rssi_analysis", {})
    fr = analysis_data.get("fast_roaming_analysis", {})

    # Feature matrix: AP × features
    bs_enabled = bs.get("band_steering_enabled", {})
    # Min RSSI: build per-AP lookup
    mr_by_ap = {}
    for radio_info in mr.get("radios_without_min_rssi", []):
        ap_name = radio_info.get("device", "")
        mr_by_ap.setdefault(ap_name, []).append(False)
    for radio_info in mr.get("radios_with_min_rssi", []) if isinstance(mr.get("radios_with_min_rssi"), list) else []:
        ap_name = radio_info.get("device", "")
        mr_by_ap.setdefault(ap_name, []).append(True)

    rows = []
    for ap in ap_details:
        name = ap.get("name", "?")
        has_bs = bs_enabled.get(name, False)
        has_mr = any(mr_by_ap.get(name, []))
        is_mesh = ap.get("is_mesh", False)

        bs_cell = '<td class="feat-yes">✓</td>' if has_bs else '<td class="feat-no">✗</td>'
        mr_cell = '<td class="feat-yes">✓</td>' if has_mr else '<td class="feat-no">✗</td>'
        mesh_cell = '<td class="feat-yes">Mesh</td>' if is_mesh else '<td style="color:#5f6368">Wired</td>'

        rows.append(f'<tr><td>{_esc(name[:18])}</td>{mesh_cell}{bs_cell}{mr_cell}</tr>')

    ap_matrix = (
        '<table class="matrix-table">'
        '<tr><th>AP</th><th>Uplink</th><th>Band Steering</th><th>Min RSSI</th></tr>'
        + "".join(rows) +
        '</table>'
    )

    # Roaming features (per-WLAN)
    roaming = fr.get("roaming_features", {})
    r11r = roaming.get("802.11r", {})
    r11k = roaming.get("802.11k", {})
    r11v = roaming.get("802.11v", {})
    wlan_count = fr.get("wlan_count", 0) or (r11r.get("enabled_count", 0) + r11r.get("disabled_count", 0))

    roaming_rows = [
        f'<tr><td>802.11r (Fast Transition)</td>'
        f'<td class="{"feat-yes" if r11r.get("enabled_count") else "feat-no"}">'
        f'{r11r.get("enabled_count", 0)}/{wlan_count} WLANs</td></tr>',
        f'<tr><td>802.11k (Neighbor Reports)</td>'
        f'<td class="{"feat-yes" if r11k.get("enabled_count") else "feat-no"}">'
        f'{r11k.get("enabled_count", 0)}/{wlan_count} WLANs</td></tr>',
        f'<tr><td>802.11v (BSS Transition)</td>'
        f'<td class="{"feat-yes" if r11v.get("enabled_count") else "feat-no"}">'
        f'{r11v.get("enabled_count", 0)}/{wlan_count} WLANs</td></tr>',
    ]
    roaming_table = (
        '<table class="matrix-table">'
        '<tr><th>Feature</th><th>Status</th></tr>'
        + "".join(roaming_rows) +
        '</table>'
    )

    # VLAN/Security
    health_cats = analysis_data.get("health_analysis", {}).get("categories", {})
    vlan = health_cats.get("vlan_segmentation", {})
    vlan_status = vlan.get("status", "unknown")
    vlan_color = "#34a853" if vlan_status == "healthy" else "#fbbc04" if vlan_status == "warning" else "#ea4335"
    vlan_issues = vlan.get("issues", [])
    vlan_html = f'<div style="color:{vlan_color};font-size:13px;font-weight:500">{vlan_status.title()}</div>'
    if vlan_issues:
        for vi in vlan_issues[:2]:
            vlan_html += f'<div style="font-size:12px;color:#9aa0a6;margin-top:4px">{_esc(vi.get("message", ""))[:80]}</div>'

    return (
        f'<div class="panel-grid">'
        f'<div class="panel-card">'
        f'<h3>AP Configuration</h3>'
        f'{ap_matrix}</div>'
        f'<div class="panel-card">'
        f'<h3>Roaming Features</h3>'
        f'{roaming_table}</div>'
        f'<div class="panel-card">'
        f'<h3>Network Segmentation</h3>'
        f'{vlan_html}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Deep Dive Tabs Wrapper
# ---------------------------------------------------------------------------

def _tabs(analysis_data):
    return (
        f'<div class="section-title"><span class="icon">🔍</span> Deep Dive</div>'
        f'<div class="tabs-container">'
        f'<div class="tab-nav">'
        f'<button class="tab-btn active" onclick="switchTab(\'rf\', this)">RF &amp; Airtime</button>'
        f'<button class="tab-btn" onclick="switchTab(\'clients\', this)">Clients</button>'
        f'<button class="tab-btn" onclick="switchTab(\'infra\', this)">Infrastructure</button>'
        f'<button class="tab-btn" onclick="switchTab(\'config\', this)">Configuration</button>'
        f'</div>'
        f'<div id="tab-rf" class="tab-content active">{_rf_panel(analysis_data)}</div>'
        f'<div id="tab-clients" class="tab-content">{_clients_panel(analysis_data)}</div>'
        f'<div id="tab-infra" class="tab-content">{_infra_panel(analysis_data)}</div>'
        f'<div id="tab-config" class="tab-content">{_config_panel(analysis_data)}</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Section: Executive Summary
# ---------------------------------------------------------------------------

def _executive(analysis_data, recommendations, site_name):
    hs = analysis_data.get("health_score", {})
    score = hs.get("score", 0) or 0
    grade = hs.get("grade", "?")
    ap_count = analysis_data.get("ap_analysis", {}).get("total_aps", 0)
    mesh_aps = analysis_data.get("ap_analysis", {}).get("mesh_aps", [])
    wired_aps = analysis_data.get("ap_analysis", {}).get("wired_aps", [])
    mesh_count = len(mesh_aps) if isinstance(mesh_aps, list) else 0
    wired_ap_count = len(wired_aps) if isinstance(wired_aps, list) else 0

    ca = analysis_data.get("client_analysis", {})
    total_clients = ca.get("total_clients", 0)
    wired_clients = sum(1 for c in ca.get("clients", []) if c.get("is_wired"))
    wireless = total_clients - wired_clients

    # Paragraph 1: Overview
    p1 = (
        f"This report analyzes the {_esc(site_name)} network, consisting of "
        f"{ap_count} access points ({wired_ap_count} wired, {mesh_count} mesh) "
        f"serving {wireless} wireless and {wired_clients} wired clients. "
        f"The network scores {score}/100 (Grade {grade})."
    )

    # Paragraph 2: Key findings
    findings = []
    issues = analysis_data.get("health_analysis", {}).get("issues", [])
    for issue in issues:
        if issue.get("severity") in ("high", "critical"):
            findings.append(issue.get("message", ""))
    bs = analysis_data.get("band_steering_analysis", {})
    stuck = bs.get("dual_band_clients_on_2ghz", 0)
    if stuck > 5:
        findings.append(
            f"{stuck} dual-band capable clients are connected to the slower 2.4GHz "
            f"band due to band steering being disabled."
        )
    weak = ca.get("weak_signal", [])
    if len(weak) > 2:
        findings.append(f"{len(weak)} clients have weak signal strength below -70 dBm.")

    p2 = " ".join(findings) if findings else "No significant issues were identified."

    # Paragraph 3: Recommendations summary
    actions = _group_recs(recommendations, analysis_data)
    if actions:
        action_descs = [a["title"].lower() for a in actions[:5]]
        p3 = (
            f"{len(actions)} optimization group{'s' if len(actions) > 1 else ''} identified: "
            f"{', '.join(action_descs)}. "
            f"These changes would improve client roaming, reduce interference, "
            f"and increase overall throughput."
        )
    else:
        p3 = "No optimization opportunities were identified. The network is well-configured."

    return (
        f'<div class="executive" id="section-executive">'
        f'<h2>Executive Summary</h2>'
        f'<p>{p1}</p>'
        f'<p>{p2}</p>'
        f'<p>{p3}</p>'
        f'<button class="print-btn" onclick="window.print()">🖨 Print Report</button>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Main Generator
# ---------------------------------------------------------------------------

def generate_v2_report(analysis_data, recommendations, site_name, output_dir="reports"):
    """Generate premium HTML report. Returns (report_path, share_path)."""
    # Prefer typed recommendations from analysis_data (have 'type' field for grouping)
    typed_recs = analysis_data.get("recommendations", [])
    if typed_recs and isinstance(typed_recs, list) and typed_recs[0].get("type"):
        recommendations = typed_recs

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unifi_report_{site_name}_{timestamp}.html"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    parts = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<title>UniFi Network Report — {_esc(site_name)}</title>',
        '<style>', _css(), '</style>',
        '</head>',
        '<body>',
        _header(site_name, analysis_data),
        '<div class="container">',
        _hero(analysis_data, recommendations),
        _topology(analysis_data),
        _actions(recommendations, analysis_data),
        _tabs(analysis_data),
        _executive(analysis_data, recommendations, site_name),
        '</div>',
        '<script>', _js(), '</script>',
        '</body></html>',
    ]

    html = "\n".join(parts)

    with open(filepath, "w") as f:
        f.write(html)

    # V2 is fully self-contained (no CDN deps) — same file is shareable
    return filepath, filepath
