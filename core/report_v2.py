"""UniFi Network Analysis Report V2
===================================
Premium consultant-grade network analysis report.
Self-contained HTML5 — no external dependencies.
"""

import html as _html
import math
import os
import sys
from datetime import datetime

from core.trend_report import TREND_CSS, _trend_summary_card, _trend_tab_panel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from version import __version__  # noqa: E402

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _esc(text):
    """HTML-escape text safely."""
    return _html.escape(str(text)) if text else ""


def _rec_msg(rec):
    """Return display message from a recommendation dict.
    Handles both 'message' and 'reason' field names across different data sources.
    """
    if "reason" in rec and not rec.get("message"):
        return rec.get("reason", rec.get("recommendation", ""))
    return rec.get("message", rec.get("recommendation", ""))


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
.exec-inline { margin-top: 10px; padding: 10px 14px; background: #0d1a2e; border-radius: 8px; border: 1px solid #1e2d4a; }
.exec-findings { font-size: 13px; color: #e8eaed; line-height: 1.5; }
.exec-actions { font-size: 12px; color: #9aa0a6; margin-top: 4px; }
.print-btn {
    background: #162540; border: 1px solid #1e2d4a; color: #9aa0a6;
    padding: 8px 20px; border-radius: 6px; cursor: pointer; font-size: 13px;
}
.print-btn:hover { background: #1a2f50; color: #e8eaed; }

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
.behavior-flapping, .behavior-high-roam { background: #ea433520; color: #ea4335; }
.behavior-sticky { background: #fbbc0420; color: #fbbc04; }

/* Swim lane */
.swim-lane-wrap { margin-top: 12px; }
.swim-lane-title { font-size: 12px; font-weight: 600; margin-bottom: 6px; }
.swim-lane-subtitle { font-size: 11px; color: #9aa0a6; margin-bottom: 8px; }

/* Responsive */
@media print {
    body { background: #fff !important; color: #222 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .container { padding: 0 12px; }
    .report-header { background: #f5f5f5; border-bottom-color: #006fff; }
    .report-header h1, .report-header .meta { color: #222; }
    .report-header h1 span { color: #006fff; }
    .hero { background: #f5f5f5; border-color: #ddd; }
    .stat-card { background: #fff; border-color: #ddd; }
    .stat-card .label, .stat-card .detail { color: #666; }
    .stat-card .value { color: #222; }
    .exec-inline { background: #f9f9f9; border-color: #ddd; }
    .exec-findings { color: #222; }
    .exec-actions { color: #555; }
    .topology-wrap { background: #fff; border-color: #ddd; }
    .action-card { background: #fff; border-color: #ddd; }
    .action-detail, .action-meta { color: #555; }
    .panel-card { background: #fff; border-color: #ddd; }
    .panel-card h3 { color: #222; }
    .tab-content { display: block !important; page-break-inside: avoid; margin-bottom: 16px; }
    .tab-nav { display: none; }
    details { open: true; }
    details[open] { display: block; }
    details .detail-body { background: #f9f9f9; border-color: #ddd; }
    details .detail-body td, details .detail-body th { color: #333; }
    .print-btn { display: none !important; }
    .show-more-btn { display: none !important; }
    #more-actions { display: block !important; }
    .topo-node-rect { fill: #f5f5f5; stroke: #ccc; }
    .topo-label { fill: #222; }
    .topo-sublabel { fill: #666; }
    .topo-edge { stroke: #ccc; }
}

/* Expandable detail sections */
details { margin-top: 10px; }
details summary {
    cursor: pointer; font-size: 12px; color: #006fff; font-weight: 500;
    padding: 4px 0; user-select: none; list-style: none;
}
details summary::-webkit-details-marker { display: none; }
details summary::before { content: "▸ "; }
details[open] summary::before { content: "▾ "; }
details .detail-body {
    margin-top: 8px; padding: 10px 14px; background: #0d1a2e;
    border-radius: 6px; border: 1px solid #1e2d4a; font-size: 12px;
}
details .detail-body table { width: 100%; border-collapse: collapse; }
details .detail-body th {
    text-align: left; padding: 4px 8px; color: #5f6368; font-weight: 500;
    font-size: 11px; border-bottom: 1px solid #1e2d4a;
}
details .detail-body td { padding: 4px 8px; border-bottom: 1px solid #1e2d4a15; color: #9aa0a6; }
.action-devices { margin-top: 6px; font-size: 12px; color: #5f6368; }
.health-detail { margin-top: 12px; }
.health-issue {
    padding: 8px 12px; margin-bottom: 6px; border-radius: 6px;
    font-size: 12px; border-left: 3px solid;
}
.health-issue.high { background: #ea433510; border-color: #ea4335; }
.health-issue.medium { background: #fbbc0410; border-color: #fbbc04; }
.health-issue .issue-msg { color: #e8eaed; }
.health-issue .issue-impact { color: #9aa0a6; font-size: 11px; margin-top: 2px; }
.health-issue .issue-fix { color: #006fff; font-size: 11px; margin-top: 2px; }

/* Data quality banner */
.dq-banner {
    display: flex; align-items: flex-start; gap: 12px;
    border-radius: 10px; padding: 12px 20px; margin-bottom: 20px; border: 1px solid;
}
.dq-banner.warning { background: #fbbc0412; border-color: #fbbc0440; }
.dq-banner.critical { background: #ea433512; border-color: #ea433540; }
.dq-icon { font-size: 18px; flex-shrink: 0; line-height: 1.4; }
.dq-body { flex: 1; }
.dq-title { font-size: 13px; font-weight: 600; }
.dq-banner.warning .dq-title { color: #fbbc04; }
.dq-banner.critical .dq-title { color: #ea4335; }
.dq-detail { font-size: 11px; color: #9aa0a6; margin-top: 3px; line-height: 1.5; }

/* AI summary card */
.ai-summary {
    display: flex; align-items: flex-start; gap: 12px;
    border-radius: 10px; padding: 14px 20px; margin-bottom: 20px;
    border: 1px solid #2196f340; background: #2196f310;
}
.ai-summary-icon { font-size: 18px; flex-shrink: 0; line-height: 1.4; }
.ai-summary-body { flex: 1; }
.ai-summary-title { font-size: 12px; font-weight: 600; color: #2196f3;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
.ai-summary-text { font-size: 13px; color: #e8eaed; line-height: 1.6; }

/* Quick actions bar */
.quick-actions-bar {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    background: #111d33; border-radius: 10px; padding: 12px 20px;
    margin-bottom: 20px; border: 1px solid #1e2d4a;
}
.qa-label {
    font-size: 11px; font-weight: 600; color: #5f6368;
    text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0;
}
.qa-item {
    font-size: 13px; font-weight: 500; text-decoration: none;
    padding: 4px 12px; border-radius: 20px; border: 1px solid;
    transition: opacity 0.2s;
}
.qa-item:hover { opacity: 0.8; }
.qa-item.critical { color: #ea4335; border-color: #ea433540; background: #ea433510; }
.qa-item.important { color: #fbbc04; border-color: #fbbc0440; background: #fbbc0410; }
.qa-item.recommended { color: #2196f3; border-color: #2196f340; background: #2196f310; }

/* Needs attention rows */
.attn-row {
    display: flex; align-items: center; gap: 10px; padding: 6px 10px;
    background: #0d1a2e; border-radius: 6px; border: 1px solid #1e2d4a15;
}
.attn-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #e8eaed; }
.attn-ap { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #9aa0a6; font-size: 12px; }
.issue-pill {
    font-size: 10px; padding: 2px 7px; border-radius: 3px; white-space: nowrap; flex-shrink: 0;
}
.issue-critical { background: #ea433520; color: #ea4335; }
.issue-high { background: #ea433510; color: #ea8600; }
.issue-medium { background: #fbbc0415; color: #fbbc04; }
.issue-low { background: #1e2d4a; color: #5f6368; }

/* Band pills */
.band-pill {
    font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 600; white-space: nowrap;
}
.band-24 { background: #ea433520; color: #ea4335; }
.band-24-ok { background: #1e2d4a; color: #9aa0a6; }
.band-5 { background: #34a85320; color: #34a853; }
.band-6 { background: #2196f320; color: #2196f3; }

/* Responsive */
@media (max-width: 768px) {
    .hero { flex-direction: column; padding: 24px; gap: 20px; }
    .stat-cards { grid-template-columns: repeat(2, 1fr); }
    .panel-grid { grid-template-columns: 1fr; }
    .tab-btn { padding: 8px 12px; font-size: 12px; }
    .quick-actions-bar { gap: 8px; }
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
function printReport() {
    // Expand all <details> and hidden sections
    document.querySelectorAll('details').forEach(function(d) { d.open = true; });
    var more = document.getElementById('more-actions');
    if (more) more.style.display = 'block';
    // Show all tabs
    document.querySelectorAll('.tab-content').forEach(function(el) { el.classList.add('active'); });
    setTimeout(function() { window.print(); }, 200);
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
    grade = (
        "A+"
        if score >= 95
        else (
            "A"
            if score >= 90
            else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
        )
    )

    return f"""<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <circle cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none"
    stroke="#1e2d4a" stroke-width="10"/>
  <circle cx="{size / 2}" cy="{size / 2}" r="{r}" fill="none"
    stroke="{color}" stroke-width="10" stroke-linecap="round"
    stroke-dasharray="{circumference:.1f}"
    stroke-dashoffset="{circumference:.1f}"
    data-target="{target_offset:.1f}"
    transform="rotate(-90 {size / 2} {size / 2})"
    class="ring-progress"
    style="filter: drop-shadow(0 0 6px {color}40);"/>
  <text x="{size / 2}" y="{size / 2 - 8}" class="ring-score" text-anchor="middle">{score}</text>
  <text x="{size / 2}" y="{size / 2 + 14}" class="ring-grade" text-anchor="middle">{grade}</text>
  <text x="{size / 2}" y="{size / 2 + 30}" class="ring-label" text-anchor="middle">HEALTH SCORE</text>
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
            f"{_esc(label)} ({value}, {pct:.0f}%)</span>"
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
            f"</div>"
        )
    return "\n".join(rows)


def _svg_swim_lane(client_name, client_data, width=700):
    """Client journey swim lane visualization with time axis."""
    import time as _time_mod
    from datetime import datetime as _dt

    ap_path = client_data.get("ap_path", [])
    visited_aps = client_data.get("visited_aps", [])
    roams = client_data.get("roam_count", 0)
    daily = client_data.get("daily_roam_rate", 0)
    behavior = client_data.get("behavior", "unknown")
    has_sessions = client_data.get("has_session_data", False)
    session_count = client_data.get("session_count", 0)
    disconnects = client_data.get("disconnects", [])
    avg_sat = client_data.get("avg_satisfaction")

    bclass = _behavior_css_class(behavior)
    behavior_display = _behavior_display(behavior)

    # Subtitle with satisfaction if available
    sat_badge = ""
    if avg_sat is not None and isinstance(avg_sat, (int, float)):
        sat_color = "#ea4335" if avg_sat < 70 else "#fbbc04" if avg_sat < 85 else "#34a853"
        sat_badge = f' <span style="color:{sat_color};font-size:10px">WiFi {avg_sat:.0f}%</span>'

    subtitle = f"{roams} roams ({daily:.0f}/day) across {len(visited_aps)} APs"
    if session_count:
        subtitle += f" · {session_count} sessions"
    if disconnects:
        subtitle += f" · {len(disconnects)} disconnects"

    # If no path data, show summary card only
    if not ap_path or not visited_aps:
        if roams or session_count:
            return (
                f'<div class="swim-lane-wrap">'
                f'<div class="swim-lane-title">{_esc(client_name)} '
                f'<span class="behavior-badge {bclass}">{_esc(behavior_display)}</span>{sat_badge}</div>'
                f'<div class="swim-lane-subtitle">{subtitle}</div>'
                f'<div style="font-size:11px;color:#5f6368;padding:4px 0">No per-event path data available.</div></div>'
            )
        return ""

    path_count = len(ap_path)

    # Check if path data is stale (>7 days old) and we DON'T have session data
    times = [e.get("ts", 0) for e in ap_path if e.get("ts")]
    if not times:
        return ""
    t_min, t_max = min(times), max(times)
    now_ts = _time_mod.time()
    data_age_days = (now_ts - t_max) / 86400

    # If data is stale AND no session data, show summary card
    if data_age_days > 7 and not has_sessions:
        t_first = _dt.fromtimestamp(t_min)
        t_last = _dt.fromtimestamp(t_max)
        ap_visits = {}
        for e in ap_path:
            ap = e.get("to_ap", "")
            if ap:
                ap_visits[ap] = ap_visits.get(ap, 0) + 1
        ap_items = sorted(ap_visits.items(), key=lambda x: -x[1])
        ap_colors = ["#006fff", "#34a853", "#fbbc04", "#ea8600", "#9c27b0", "#00bcd4"]
        ap_bar = ""
        if ap_items:
            total = sum(v for _, v in ap_items)
            bars = []
            for i, (ap, cnt) in enumerate(ap_items[:6]):
                pct = cnt / total * 100
                color = ap_colors[i % len(ap_colors)]
                bars.append(
                    f'<div style="flex:{pct:.0f};background:{color};height:18px;border-radius:2px;'
                    f"min-width:20px;display:flex;align-items:center;justify-content:center;"
                    f'font-size:9px;color:#fff;white-space:nowrap;overflow:hidden" '
                    f'title="{_esc(ap)}: {cnt} roams ({pct:.0f}%)">{_esc(ap[:10])}</div>'
                )
            ap_bar = f'<div style="display:flex;gap:2px;margin:6px 0">{"".join(bars)}</div>'

        return (
            f'<div class="swim-lane-wrap">'
            f'<div class="swim-lane-title">{_esc(client_name)} '
            f'<span class="behavior-badge {bclass}">{_esc(behavior_display)}</span>{sat_badge}</div>'
            f'<div class="swim-lane-subtitle">{subtitle}</div>'
            f"{ap_bar}"
            f'<div style="font-size:10px;color:#5f6368;margin-top:2px">'
            f'Path sample: {t_first.strftime("%b %d")} – {t_last.strftime("%b %d %Y")} '
            f"({path_count} events — controller event buffer limit)</div></div>"
        )

    # --- Render full swim lane ---
    t_span = t_max - t_min or 1

    # Build AP visit counts for ordering (most-visited first)
    ap_visit_count = {}
    for e in ap_path:
        ap = e.get("to_ap", "")
        if ap:
            ap_visit_count[ap] = ap_visit_count.get(ap, 0) + 1
    # Order APs by visit count, limit to top 8
    ordered_aps = [ap for ap, _ in sorted(ap_visit_count.items(), key=lambda x: -x[1])[:8]]
    # Add any visited_aps not in path
    for ap in visited_aps:
        if ap not in ordered_aps and len(ordered_aps) < 8:
            ordered_aps.append(ap)

    row_h = 24
    label_w = 85
    time_axis_h = 18
    h = len(ordered_aps) * row_h + 30 + time_axis_h
    chart_w = width - label_w - 10

    ap_colors = [
        "#006fff",
        "#34a853",
        "#fbbc04",
        "#ea8600",
        "#9c27b0",
        "#00bcd4",
        "#ff5722",
        "#795548",
    ]

    parts = [
        f'<svg viewBox="0 0 {width} {h}" width="100%" preserveAspectRatio="xMinYMin meet" style="max-width:{width}px">'
    ]

    # Time axis labels
    n_ticks = min(8, max(2, int(t_span / 3600 / 4)))
    for i in range(n_ticks + 1):
        frac = i / n_ticks
        x = label_w + frac * chart_w
        tick_ts = t_min + frac * t_span
        dt = _dt.fromtimestamp(tick_ts)
        if t_span > 7 * 86400:
            label = dt.strftime("%b %d")
        elif t_span > 86400:
            label = dt.strftime("%b %d %H:%M")
        else:
            label = dt.strftime("%H:%M")
        parts.append(
            f'<text x="{x:.0f}" y="12" fill="#5f6368" font-size="8" '
            f'text-anchor="middle" font-family="sans-serif">{label}</text>'
        )

    # AP labels
    for i, ap in enumerate(ordered_aps):
        y = i * row_h + 20 + time_axis_h
        parts.append(
            f'<text x="2" y="{y + 4}" fill="#9aa0a6" font-size="9.5" font-family="sans-serif">{_esc(ap[:12])}</text>'
        )

    # Build segments from path
    for i, event in enumerate(ap_path):
        to_ap = event.get("to_ap", "")
        if to_ap not in ordered_aps:
            continue
        row = ordered_aps.index(to_ap)
        t_start = event.get("ts", 0)
        t_end = ap_path[i + 1].get("ts", t_max) if i + 1 < len(ap_path) else t_max

        x1 = label_w + (t_start - t_min) / t_span * chart_w
        x2 = label_w + (t_end - t_min) / t_span * chart_w
        y = row * row_h + 12 + time_axis_h
        color = ap_colors[row % len(ap_colors)]
        w = max(2, x2 - x1)
        # Satisfaction-based opacity if available
        sat = event.get("satisfaction")
        opacity = (
            "0.45" if (sat is not None and isinstance(sat, (int, float)) and sat < 70) else "0.65"
        )
        parts.append(
            f'<rect x="{x1:.1f}" y="{y}" width="{w:.1f}" height="{row_h - 6}" rx="2" fill="{color}" opacity="{opacity}"/>'
        )

    # Mark disconnects with red tick marks at bottom
    if disconnects:
        for disc in disconnects:
            disc_ts = disc.get("ts", 0)
            if disc_ts < t_min or disc_ts > t_max:
                continue
            x = label_w + (disc_ts - t_min) / t_span * chart_w
            y_bottom = len(ordered_aps) * row_h + 16 + time_axis_h
            parts.append(
                f'<line x1="{x:.1f}" y1="{time_axis_h + 14}" x2="{x:.1f}" y2="{y_bottom}" '
                f'stroke="#ea4335" stroke-width="1" opacity="0.5"/>'
            )

    parts.append("</svg>")

    # Data source note
    data_note = ""
    if has_sessions:
        t_first_dt = _dt.fromtimestamp(t_min).strftime("%b %d")
        t_last_dt = _dt.fromtimestamp(t_max).strftime("%b %d %Y")
        data_note = (
            f'<div style="font-size:10px;color:#5f6368;margin-top:2px">'
            f"{path_count} roam transitions · {t_first_dt} – {t_last_dt} · from session history</div>"
        )
    elif roams > path_count:
        data_note = (
            f'<div style="font-size:10px;color:#5f6368;margin-top:2px">'
            f"Showing {path_count} of {roams} roam events (controller event buffer limit)</div>"
        )

    return (
        f'<div class="swim-lane-wrap">'
        f'<div class="swim-lane-title">{_esc(client_name)} '
        f'<span class="behavior-badge {bclass}">{_esc(behavior_display)}</span>{sat_badge}</div>'
        f'<div class="swim-lane-subtitle">{subtitle}</div>'
        f'{"".join(parts)}{data_note}</div>'
    )


def _behavior_display(behavior):
    """Map internal behavior string to user-friendly label."""
    if behavior == "flapping":
        return "High Roam"
    return behavior.replace("_", " ").title()


def _behavior_css_class(behavior):
    if behavior == "flapping":
        return "behavior-high-roam"
    if "roam" in behavior:
        return "behavior-roamer"
    if behavior == "sticky":
        return "behavior-sticky"
    return "behavior-stable"


# ---------------------------------------------------------------------------
# SVG: Device Activity Timeline
# ---------------------------------------------------------------------------


def _svg_device_timeline(analysis_data, width=860):
    """Network event timeline using accurate hourly event data.
    Rows: Roaming, Restarts, DFS Radar. X-axis: hours, ending at today."""
    from datetime import datetime as _dt

    et = analysis_data.get("event_timeline", {})
    hours = et.get("hours", [])
    cats = et.get("categories", {})
    ap_events = et.get("ap_events", {})

    roaming = cats.get("roaming", [])
    restarts = cats.get("device_restart", [])
    dfs = cats.get("dfs_radar", [])
    offline = cats.get("device_offline", [])
    wifi_quality = cats.get("wifi_quality", [])

    if not hours or not roaming:
        return ""

    n_hours = len(hours)

    # Parse time range from hours array; extend to "now"
    try:
        t_first = _dt.strptime(hours[0], "%Y-%m-%d %H:%M")
        t_last = _dt.strptime(hours[-1], "%Y-%m-%d %H:%M")
    except (ValueError, IndexError):
        return ""

    now = _dt.now()
    # Use the actual data span — hourly arrays already extend to "now"
    total_span_h = n_hours

    # Bin into 6-hour blocks across the data range
    block_h = 6
    n_blocks = max(1, total_span_h // block_h + 1)

    # Aggregate hourly data into 6h blocks
    def bin_hourly(hourly_data):
        bins = [0] * n_blocks
        for i, val in enumerate(hourly_data):
            if i < n_hours and val:
                bi = i // block_h
                if 0 <= bi < n_blocks:
                    bins[bi] += val
        return bins

    # Build rows: one per event category
    rows = []

    roam_bins = bin_hourly(roaming)
    if sum(roam_bins):
        rows.append(("Roaming", roam_bins, "#006fff", sum(roaming), None))

    restart_bins = bin_hourly(restarts)
    restart_last_h = max((i for i, v in enumerate(restarts) if v > 0), default=0)
    restart_coverage = (restart_last_h / n_hours) if n_hours else 0
    if sum(restart_bins):
        rows.append(
            (
                "Restarts",
                restart_bins,
                "#ea4335",
                sum(restarts),
                restart_coverage if restart_coverage < 0.9 else None,
            )
        )

    dfs_bins = bin_hourly(dfs)
    dfs_last_h = max((i for i, v in enumerate(dfs) if v > 0), default=0)
    dfs_coverage = (dfs_last_h / n_hours) if n_hours else 0
    if sum(dfs_bins):
        rows.append(
            (
                "DFS Radar",
                dfs_bins,
                "#fbbc04",
                sum(dfs),
                dfs_coverage if dfs_coverage < 0.9 else None,
            )
        )

    offline_bins = bin_hourly(offline) if offline else []
    if offline_bins and sum(offline_bins):
        offline_last_h = max((i for i, v in enumerate(offline) if v > 0), default=0)
        offline_coverage = (offline_last_h / n_hours) if n_hours else 0
        rows.append(
            (
                "Went Offline",
                offline_bins,
                "#ff6d00",
                sum(offline),
                offline_coverage if offline_coverage < 0.9 else None,
            )
        )

    wq_bins = bin_hourly(wifi_quality) if wifi_quality else []
    if wq_bins and sum(wq_bins):
        rows.append(("WiFi Quality Dips", wq_bins, "#9c27b0", sum(wifi_quality), None))

    if not rows:
        return ""

    row_h = 28
    label_w = 100
    top_pad = 30
    h = len(rows) * row_h + top_pad + 20
    chart_w = width - label_w - 10

    parts = [
        f'<svg viewBox="0 0 {width} {h}" width="100%" preserveAspectRatio="xMinYMin meet" style="max-width:{width}px">'
    ]

    # X-axis labels (no grid lines)
    n_labels = min(8, n_blocks)
    label_every = max(1, n_blocks // n_labels)
    for i in range(0, n_blocks + 1, label_every):
        x = label_w + (i / n_blocks) * chart_w
        from datetime import timedelta

        block_dt = t_first + timedelta(hours=i * block_h)
        if block_dt > now:
            block_dt = now
        label = block_dt.strftime("%b %d")
        parts.append(
            f'<text x="{x:.0f}" y="{top_pad - 8}" fill="#5f6368" font-size="9" '
            f'text-anchor="middle" font-family="sans-serif">{label}</text>'
        )

    # "Today" marker
    today_x = label_w + chart_w
    parts.append(
        f'<text x="{today_x:.0f}" y="{top_pad - 8}" fill="#006fff" font-size="9" '
        f'text-anchor="end" font-family="sans-serif" font-weight="600">Today</text>'
    )
    parts.append(
        f'<line x1="{today_x:.0f}" y1="{top_pad}" x2="{today_x:.0f}" y2="{h - 10}" '
        f'stroke="#006fff" stroke-width="0.8" opacity="0.5"/>'
    )

    for row_idx, (row_label, bins, color, total, coverage) in enumerate(rows):
        y_center = top_pad + row_idx * row_h + row_h // 2

        parts.append(
            f'<text x="{label_w - 6}" y="{y_center + 3}" fill="#9aa0a6" '
            f'font-size="10" text-anchor="end" font-family="sans-serif">{_esc(row_label)}</text>'
        )

        # If partial coverage, shade the "no data" region
        if coverage is not None and coverage < 0.95:
            no_data_x = label_w + coverage * chart_w
            no_data_w = chart_w * (1 - coverage)
            parts.append(
                f'<rect x="{no_data_x:.1f}" y="{y_center - row_h / 2 + 2:.1f}" '
                f'width="{no_data_w:.1f}" height="{row_h - 4:.1f}" rx="1" '
                f'fill="#1e2d4a" opacity="0.3"/>'
            )
            parts.append(
                f'<text x="{no_data_x + no_data_w / 2:.1f}" y="{y_center + 3}" '
                f'fill="#5f6368" font-size="8" text-anchor="middle" '
                f'font-family="sans-serif">event log only</text>'
            )

        max_bin = max(bins) if bins else 1
        bw = max(2, chart_w / n_blocks)
        for bi, count in enumerate(bins):
            if count == 0:
                continue
            x = label_w + (bi / n_blocks) * chart_w
            intensity = min(1.0, count / max(max_bin, 1))
            opacity = 0.2 + intensity * 0.7
            bar_h = row_h - 4
            parts.append(
                f'<rect x="{x:.1f}" y="{y_center - bar_h / 2:.1f}" '
                f'width="{bw:.1f}" height="{bar_h:.1f}" rx="1" '
                f'fill="{color}" opacity="{opacity:.2f}"/>'
            )

    parts.append("</svg>")

    # Data source note
    stat_report_hours = et.get("stat_report_hours", 0)
    stat_report_roams = et.get("stat_report_roams", 0)
    daily_fill = et.get("daily_gap_fill", 0)
    data_note = ""
    if stat_report_hours:
        sources = []
        if daily_fill:
            sources.append(f"daily AP stats filled {_fmt(daily_fill)} roams")
        sources.append(f"hourly AP stats added {_fmt(stat_report_roams - daily_fill)} roams")
        data_note = (
            f'<div style="font-size:11px;color:#5f6368;margin-top:4px;text-align:center">'
            f'Combined: event log ({t_first.strftime("%b %d")} – {t_last.strftime("%b %d %Y")}) + '
            f'{" + ".join(sources)} through today</div>'
        )
    else:
        gap_days = int((now - t_last).total_seconds() / 86400)
        if gap_days > 1:
            data_note = (
                f'<div style="font-size:11px;color:#5f6368;margin-top:4px;text-align:center">'
                f'Event data covers {t_first.strftime("%b %d")} – {t_last.strftime("%b %d %Y")} · '
                f"{gap_days}-day gap to today (no recent events on controller)</div>"
            )

    legend_items = ['<span style="color:#006fff">■</span> Roaming']
    if sum(restart_bins):
        legend_items.append('<span style="color:#ea4335;margin-left:8px">■</span> Restarts')
    if sum(dfs_bins):
        legend_items.append('<span style="color:#fbbc04;margin-left:8px">■</span> DFS Radar')
    if offline_bins and sum(offline_bins):
        legend_items.append('<span style="color:#ff6d00;margin-left:8px">■</span> Went Offline')
    legend = (
        '<div style="display:flex;gap:14px;justify-content:center;margin-top:6px;font-size:11px;color:#5f6368">'
        + "".join(legend_items)
        + "</div>"
    )

    return (
        f'<div class="section-title"><span class="icon">📊</span> Event Timeline</div>'
        f'<div class="topology-wrap">'
        f'{"".join(parts)}{legend}{data_note}</div>'
    )


def _header(site_name, analysis_data):
    now = datetime.now().strftime("%B %d, %Y at %H:%M")
    lookback = analysis_data.get("lookback_days", 3)
    return (
        f'<div class="report-header">'
        f"<h1><span>UniFi</span> Network Analysis</h1>"
        f'<div class="meta">Site: {_esc(site_name)}<br>{now}<br>{lookback}-day lookback'
        f'<br><span style="color:#5f6368;font-size:11px">v{__version__}</span></div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Section: Data Quality Banner
# ---------------------------------------------------------------------------


def _ai_summary_card(ai_summary):
    """Render an AI-generated plain-English summary card. Returns '' when no summary."""
    if not ai_summary:
        return ""
    return (
        '<div class="ai-summary">'
        '<span class="ai-summary-icon">✦</span>'
        '<div class="ai-summary-body">'
        '<div class="ai-summary-title">AI Network Summary</div>'
        f'<div class="ai-summary-text">{_esc(ai_summary)}</div>'
        "</div></div>"
    )


def _data_quality_banner(analysis_data):
    """Warn when API errors caused incomplete analysis data."""
    api_errors = analysis_data.get("api_errors")
    if not api_errors or not isinstance(api_errors, dict):
        return ""
    total = api_errors.get("total_errors", 0)
    if not total:
        return ""
    is_critical = bool(api_errors.get("critical_errors"))
    failed = api_errors.get("failed_endpoints", [])
    level = "critical" if is_critical else "warning"
    icon = "✗" if is_critical else "⚠"
    if is_critical:
        title = f"{total} critical API error{'s' if total != 1 else ''} — analysis incomplete"
        subtitle = "Authentication or permission failure. Grade and recommendations may be missing."
    else:
        title = f"{total} API call{'s' if total != 1 else ''} failed — some data may be missing"
        subtitle = "Partial data: retry may resolve transient controller timeouts."
    endpoints_str = ""
    if failed:
        shown = [e.get("endpoint", str(e)) if isinstance(e, dict) else str(e) for e in failed[:5]]
        endpoints_str = "Failed endpoints: " + ", ".join(shown)
        if len(failed) > 5:
            endpoints_str += f" (+{len(failed) - 5} more)"
    detail = " · ".join(filter(None, [subtitle, endpoints_str]))
    return (
        f'<div class="dq-banner {level}">'
        f'<span class="dq-icon">{icon}</span>'
        f'<div class="dq-body">'
        f'<div class="dq-title">{_esc(title)}</div>'
        f'<div class="dq-detail">{_esc(detail)}</div>'
        f"</div></div>"
    )


# ---------------------------------------------------------------------------
# Section: Hero Dashboard
# ---------------------------------------------------------------------------


def _hero(analysis_data, recommendations, site_name="default"):
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
    top_issue_text = (
        critical[0].get("message", "No critical issues")[:60] if critical else "No critical issues"
    )

    # Action count
    actions = _group_recs(recommendations, analysis_data)
    action_count = len(actions)

    ring = _svg_ring(score)

    # Build executive summary (compact, inline)
    findings = []
    for issue in critical[:3]:
        findings.append(issue.get("message", ""))
    bs = analysis_data.get("band_steering_analysis", {})
    stuck_2g = bs.get("dual_band_clients_on_2ghz", 0)
    if stuck_2g > 5:
        findings.append(f"{stuck_2g} dual-band clients stuck on 2.4GHz.")
    weak = ca.get("weak_signal", [])
    if len(weak) > 2:
        findings.append(f"{len(weak)} clients below -70 dBm.")

    finding_text = " ".join(findings[:4]) if findings else "No significant issues detected."

    action_text = ""
    if actions:
        action_names = [a["title"].lower() for a in actions[:4]]
        action_text = (
            f"{action_count} fix{'es' if action_count > 1 else ''}: {', '.join(action_names)}."
        )

    exec_html = (
        f'<div class="exec-inline">'
        f'<div class="exec-findings">{_esc(finding_text)}</div>'
        f'{f"<div class=exec-actions>{_esc(action_text)}</div>" if action_text else ""}'
        f"</div>"
    )

    return (
        f'<div class="hero">'
        f'<div class="ring-container">{ring}</div>'
        f'<div class="hero-content">'
        f'<div class="stat-cards">'
        f'<div class="stat-card">'
        f'<div class="label">Access Points</div>'
        f'<div class="value">{total_aps}</div>'
        f'<div class="detail">{wired_ap_count} wired · {mesh_count} mesh</div></div>'
        f'<div class="stat-card">'
        f'<div class="label">Clients</div>'
        f'<div class="value">{wireless_clients}</div>'
        f'<div class="detail">{wired_clients} wired</div></div>'
        f'<div class="stat-card">'
        f'<div class="label">Top Issue</div>'
        f'<div class="value" style="font-size:13px;line-height:1.3;margin-top:2px">{_esc(top_issue_text)}</div></div>'
        f'<div class="stat-card">'
        f'<div class="label">Actions</div>'
        f'<div class="value">{action_count}</div>'
        f'<div class="detail">optimization{"s" if action_count != 1 else ""} available</div></div>'
        f"</div>"
        f"{exec_html}"
        f"</div></div>"
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

    mac_to_dev = {}
    for dev in devices:
        mac = dev.get("mac", "")
        name = dev.get("name", "")
        info = ap_info_by_name.get(name, {})
        dev["_is_mesh"] = info.get("is_mesh", False)
        dev["_clients"] = info.get("client_count", 0)
        dev["_rssi"] = info.get("uplink_rssi")
        dev["_has_issue"] = name in issue_devices
        mac_to_dev[mac] = dev

    # --- Build adjacency tree ---
    children_of = {}  # parent_mac -> [child_dev, ...]
    roots = []
    for dev in devices:
        mac = dev.get("mac", "")
        uplink = dev.get("uplink", {}) or {}
        parent_mac = uplink.get("uplink_mac", "")
        if parent_mac and parent_mac in mac_to_dev:
            children_of.setdefault(parent_mac, []).append(dev)
        else:
            roots.append(dev)

    # --- Assign DAG levels via BFS ---
    level_of = {}  # mac -> depth
    queue = [(r, 0) for r in roots]
    order = []
    visited = set()
    while queue:
        node, depth = queue.pop(0)
        mac = node.get("mac", "")
        if mac in visited:
            continue
        visited.add(mac)
        level_of[mac] = depth
        order.append(node)
        for child in children_of.get(mac, []):
            queue.append((child, depth + 1))

    max_depth = max(level_of.values()) if level_of else 0

    # --- Compute subtree widths for balanced layout ---
    node_width = 130  # horizontal space per node

    def subtree_width(mac):
        kids = children_of.get(mac, [])
        if not kids:
            return node_width
        return sum(subtree_width(k.get("mac", "")) for k in kids)

    # --- Assign X positions (centered over children) ---
    svg_w = max(960, subtree_width(roots[0].get("mac", "")) + 40) if roots else 960
    positions = {}  # mac -> (x, y)
    level_gap = 110

    def layout(node, x_start, x_end, depth):
        mac = node.get("mac", "")
        y = 55 + depth * level_gap
        cx = (x_start + x_end) / 2
        positions[mac] = (cx, y)
        kids = children_of.get(mac, [])
        if not kids:
            return
        # Distribute children proportionally across [x_start, x_end]
        total_w = sum(subtree_width(k.get("mac", "")) for k in kids)
        cursor = x_start
        for kid in kids:
            kid_w = subtree_width(kid.get("mac", ""))
            share = (
                (kid_w / total_w) * (x_end - x_start) if total_w else (x_end - x_start) / len(kids)
            )
            layout(kid, cursor, cursor + share, depth + 1)
            cursor += share

    if roots:
        padding = 40
        for i, root in enumerate(roots):
            if len(roots) == 1:
                layout(root, padding, svg_w - padding, 0)
            else:
                seg = (svg_w - 2 * padding) / len(roots)
                layout(root, padding + i * seg, padding + (i + 1) * seg, 0)

    svg_h = 55 + (max_depth + 1) * level_gap + 20

    svg = [
        f'<svg viewBox="0 0 {svg_w} {svg_h}" class="topology-svg" xmlns="http://www.w3.org/2000/svg">'
    ]

    # --- Draw edges (parent→child) ---
    for parent_mac, kids in children_of.items():
        if parent_mac not in positions:
            continue
        px, py = positions[parent_mac]
        for kid in kids:
            kid_mac = kid.get("mac", "")
            if kid_mac not in positions:
                continue
            cx, cy = positions[kid_mac]
            is_mesh = kid.get("_is_mesh", False)
            edge_class = "topo-edge-mesh" if is_mesh else "topo-edge"

            # Curved path for cleaner look
            mid_y = (py + cy) / 2
            svg.append(
                f'<path d="M {px:.0f} {py + 28:.0f} C {px:.0f} {mid_y:.0f}, '
                f'{cx:.0f} {mid_y:.0f}, {cx:.0f} {cy - 28:.0f}" '
                f'fill="none" class="{edge_class}"/>'
            )

            # RSSI label on mesh links
            if is_mesh and kid.get("_rssi"):
                mx = (px + cx) / 2
                my = mid_y - 6
                svg.append(
                    f'<text x="{mx:.0f}" y="{my:.0f}" class="topo-rssi-label">'
                    f'{kid["_rssi"]} dBm</text>'
                )

    # --- Draw nodes ---
    for dev in order:
        mac = dev.get("mac", "")
        if mac not in positions:
            continue
        x, y = positions[mac]
        name = dev.get("name", "?")[:16]
        dtype = dev.get("type", "")
        is_mesh = dev.get("_is_mesh", False)
        has_issue = dev.get("_has_issue", False)
        clients = dev.get("_clients", 0)

        if dtype == "usw":
            # Switch: rounded rectangle
            stroke = "#ea4335" if has_issue else "#1e2d4a"
            active_ports = (
                len([p for p in dev.get("port_table", []) if (p.get("speed", 0) or 0) > 0])
                if dev.get("port_table")
                else "?"
            )
            svg.append(
                f'<g transform="translate({x:.0f},{y:.0f})">'
                f'<rect x="-58" y="-24" width="116" height="48" rx="6" '
                f'class="topo-node-rect" style="stroke:{stroke}"/>'
                f'<text y="-4" class="topo-label">{_esc(name)}</text>'
                f'<text y="12" class="topo-sublabel">Switch</text>'
                f"</g>"
            )
        else:
            # AP: circle
            stroke = "#ea4335" if has_issue else "#006fff" if is_mesh else "#34a853"
            circle_class = "topo-mesh-circle" if is_mesh else "topo-ap-circle"
            sublabel = (
                f'{clients} client{"s" if clients != 1 else ""}'
                if clients
                else "mesh" if is_mesh else "0 clients"
            )

            svg.append(
                f'<g transform="translate({x:.0f},{y:.0f})">'
                f'<circle r="28" class="{circle_class}" style="stroke:{stroke}"/>'
                f'<text y="-5" class="topo-label">{_esc(name)}</text>'
                f'<text y="9" class="topo-sublabel">{sublabel}</text>'
            )
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
        f"</div></div>"
    )


# ---------------------------------------------------------------------------
# Recommendation grouping
# ---------------------------------------------------------------------------

_GROUP_META = {
    "mesh_power": (
        "Optimize Mesh AP Power",
        "important",
        "MEDIUM power improves mesh link stability and reduces co-channel interference",
    ),
    "power_optimization": (
        "Reduce Transmit Power",
        "important",
        "Lower power reduces interference and helps clients roam to closer APs",
    ),
    "band_steering": (
        "Enable Band Steering",
        "important",
        "Move capable clients to faster 5GHz/6GHz bands automatically",
    ),
    "band_steering_critical": (
        "Enable Band Steering",
        "important",
        "Clients stuck on 2.4GHz need band steering to use faster bands",
    ),
    "min_rssi_disabled": (
        "Enable Minimum RSSI",
        "recommended",
        "Force weak clients to roam to a closer AP for better performance",
    ),
    "channel_width": (
        "Optimize Channel Widths",
        "recommended",
        "Match channel width to environment for best throughput",
    ),
}


def _group_recs(recommendations, analysis_data):
    """Group recommendations into actionable cards."""
    actions = []

    # Critical health issues first
    issues = analysis_data.get("health_analysis", {}).get("issues", [])
    for issue in issues:
        if issue.get("severity") in ("high", "critical"):
            actions.append(
                {
                    "priority": "critical",
                    "title": issue.get("device", "Network")
                    + ": "
                    + (issue.get("type", "issue")).replace("_", " ").title(),
                    "detail": issue.get("message", ""),
                    "action": issue.get("recommendation", ""),
                    "count": 1,
                    "devices": [issue.get("device", "")],
                }
            )

    # Group recs by type
    groups = {}
    for rec in recommendations:
        rtype = rec.get("type", "other")
        groups.setdefault(rtype, []).append(rec)

    for rtype in [
        "band_steering",
        "band_steering_critical",
        "power_optimization",
        "mesh_power",
        "min_rssi_disabled",
        "channel_width",
    ]:
        recs = groups.pop(rtype, [])
        if not recs:
            continue
        meta = _GROUP_META.get(rtype, (rtype.replace("_", " ").title(), "recommended", ""))
        devs = list(set(r.get("ap", {}).get("name", "") or r.get("device", "") for r in recs))
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

        actions.append(
            {
                "priority": meta[1],
                "title": meta[0],
                "detail": (
                    f"{meta[2]}. Affects: {', '.join(devs[:6])}{'...' if len(devs) > 6 else ''}."
                    if devs
                    else meta[2] + "."
                ),
                "action": recs[0].get("recommendation", ""),
                "count": len(recs),
                "devices": devs,
                "recs": recs,
            }
        )

    # Remaining groups
    for rtype, recs in groups.items():
        meta = _GROUP_META.get(rtype, (rtype.replace("_", " ").title(), "recommended", ""))
        actions.append(
            {
                "priority": meta[1] if isinstance(meta, tuple) else "recommended",
                "title": meta[0] if isinstance(meta, tuple) else rtype.replace("_", " ").title(),
                "detail": f"{len(recs)} recommendation{'s' if len(recs) > 1 else ''}.",
                "count": len(recs),
                "devices": [],
            }
        )

    return actions


# ---------------------------------------------------------------------------
# Section: Top Actions
# ---------------------------------------------------------------------------


def _action_detail_html(action):
    """Build expandable detail for an action card."""
    recs = action.get("recs", [])
    if not recs and action.get("priority") == "critical":
        # Critical issues have action/recommendation text
        fix = action.get("action", "")
        if fix:
            return f'<details><summary>View details</summary><div class="detail-body"><p style="color:#9aa0a6">{_esc(fix)}</p></div></details>'
        return ""
    if not recs:
        return ""
    rows = []
    for r in recs:
        ap_name = r.get("ap", {}).get("name", r.get("device", "?"))
        band = r.get("band", "")
        msg = _rec_msg(r)
        rows.append(
            f'<tr><td style="color:#e8eaed;white-space:nowrap">{_esc(ap_name)}</td><td>{_esc(band)}</td><td>{_esc(msg)}</td></tr>'
        )
    return (
        f"<details><summary>View {len(recs)} individual changes</summary>"
        f'<div class="detail-body"><table>'
        f"<tr><th>Device</th><th>Band</th><th>Change</th></tr>"
        f'{"".join(rows)}</table></div></details>'
    )


def _quick_actions_card(analysis_data, recommendations):
    """Compact top-3 action summary bar inserted just below the hero."""
    actions = _group_recs(recommendations, analysis_data)
    if not actions:
        return ""
    items = []
    for i, action in enumerate(actions[:3]):
        pri = action.get("priority", "recommended")
        title = action["title"]
        recs = action.get("recs", [])
        affected = sum(r.get("affected_clients", 0) for r in recs) if recs else 0
        devices = action.get("devices", [])
        if affected:
            impact = f" \u2014 {affected} client{'s' if affected != 1 else ''}"
        elif devices:
            impact = f" \u2014 {len(devices)} AP{'s' if len(devices) != 1 else ''}"
        else:
            impact = ""
        items.append(
            f'<a class="qa-item {pri}" href="#action-{i}">'
            f"\u25cf {_esc(title)}{_esc(impact)}</a>"
        )
    return (
        f'<div class="quick-actions-bar">'
        f'<span class="qa-label">Top Actions</span>'
        f'{"".join(items)}'
        f"</div>"
    )


def _actions(recommendations, analysis_data):
    actions = _group_recs(recommendations, analysis_data)
    if not actions:
        return ""

    visible = actions[:5]
    hidden = actions[5:]

    def _impact_str(action):
        recs = action.get("recs", [])
        affected = sum(r.get("affected_clients", 0) for r in recs) if recs else 0
        devices = action.get("devices", [])
        if affected:
            return f"Affects {affected} client{'s' if affected != 1 else ''}"
        if devices:
            return f"{len(devices)} AP{'s' if len(devices) != 1 else ''}"
        return ""

    cards = []
    for i, action in enumerate(visible):
        pri = action.get("priority", "recommended")
        color = _priority_color(pri)
        detail_expand = _action_detail_html(action)
        impact = _impact_str(action)
        impact_html = f"<span>{_esc(impact)}</span>" if impact else ""
        cards.append(
            f'<div class="action-card {pri}" id="action-{i}">'
            f'<div class="action-num" style="background:{color}">{i + 1}</div>'
            f'<div class="action-body">'
            f'<div class="action-title">{_esc(action["title"])}</div>'
            f'<div class="action-detail">{_esc(action.get("detail", ""))}</div>'
            f'<div class="action-meta">'
            f'<span class="action-badge" style="background:{color}20;color:{color}">{_priority_label(pri)}</span>'
            f'<span>{action.get("count", 1)} change{"s" if action.get("count", 1) > 1 else ""}</span>'
            f"{impact_html}"
            f"</div>"
            f"{detail_expand}"
            f"</div></div>"
        )

    hidden_html = ""
    if hidden:
        hidden_cards = []
        for i, action in enumerate(hidden):
            pri = action.get("priority", "recommended")
            color = _priority_color(pri)
            detail_expand = _action_detail_html(action)
            impact = _impact_str(action)
            impact_html = f"<span>{_esc(impact)}</span>" if impact else ""
            hidden_cards.append(
                f'<div class="action-card {pri}" id="action-{i + 5}">'
                f'<div class="action-num" style="background:{color}">{i + 6}</div>'
                f'<div class="action-body">'
                f'<div class="action-title">{_esc(action["title"])}</div>'
                f'<div class="action-detail">{_esc(action.get("detail", ""))}</div>'
                f'<div class="action-meta">{impact_html}</div>'
                f"{detail_expand}"
                f"</div></div>"
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

_DFS_CHANNELS = set(range(52, 145))  # 5GHz DFS channels (52–144)


def _dfs_card(analysis_data):
    """Panel card showing DFS radar events per AP. Returns '' when no events."""
    dfs = analysis_data.get("dfs_analysis", {})
    if not dfs or not isinstance(dfs, dict):
        return ""
    total = dfs.get("total_events", 0)
    if not total:
        return ""

    events_by_ap = dfs.get("events_by_ap", {})
    affected_channels = sorted(dfs.get("affected_channels", []))
    severity = dfs.get("severity", "ok")
    title_color = "#ea4335" if severity == "high" else "#fbbc04"

    # Bar chart: AP → event count
    ap_items = sorted(events_by_ap.items(), key=lambda x: -len(x[1]))
    bar_data = []
    for ap_name, events in ap_items:
        n = len(events)
        color = "#ea4335" if n > 5 else "#fbbc04" if n > 2 else "#34a853"
        bar_data.append((ap_name[:20], n, color))
    bar_html = _svg_hbar(bar_data) if bar_data else ""

    # Affected channels row
    ch_pills = []
    for ch in affected_channels:
        is_dfs = ch in _DFS_CHANNELS
        style = (
            "background:#fbbc0420;color:#fbbc04" if is_dfs else "background:#34a85320;color:#34a853"
        )
        ch_pills.append(
            f'<span style="{style};padding:2px 7px;border-radius:3px;font-size:11px">ch{ch}</span>'
        )
    ch_row = (
        (
            f'<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:5px;align-items:center">'
            f'<span style="font-size:11px;color:#5f6368;margin-right:4px">Affected:</span>'
            f'{"".join(ch_pills)}'
            f'<span style="font-size:11px;color:#5f6368;margin-left:8px">'
            f"— switch to ch 36–48 or 149–165 to avoid DFS</span></div>"
        )
        if ch_pills
        else ""
    )

    return (
        f'<div class="panel-card full">'
        f'<h3 style="color:{title_color}">DFS Radar Events '
        f'<span class="count" style="color:{title_color}60">({total} event{"s" if total != 1 else ""}, '
        f'{len(events_by_ap)} AP{"s" if len(events_by_ap) != 1 else ""})</span></h3>'
        f'<div style="font-size:12px;color:#9aa0a6;margin-bottom:10px">'
        f"5 GHz DFS channels (52–144) are temporarily vacated when radar is detected. "
        f"Clients drop to 2.4 GHz until the channel clears.</div>"
        f"{bar_html}{ch_row}"
        f"</div>"
    )


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
    sig_chart = (
        _svg_hbar(sig_items, max_val=total_sig)
        if total_sig
        else "<span style='color:#5f6368'>No wireless clients</span>"
    )

    # Band distribution from client channels
    clients = analysis_data.get("client_analysis", {}).get("clients", [])
    band_24 = sum(1 for c in clients if not c.get("is_wired") and 0 < (c.get("channel") or 0) <= 14)
    band_5 = sum(1 for c in clients if not c.get("is_wired") and 14 < (c.get("channel") or 0) < 200)
    band_6 = sum(1 for c in clients if not c.get("is_wired") and (c.get("channel") or 0) >= 200)
    band_donut = _svg_donut(
        [
            (band_24, "#fbbc04", "2.4 GHz"),
            (band_5, "#2196f3", "5 GHz"),
            (band_6, "#9c27b0", "6 GHz"),
        ],
        size=130,
    )

    # Capability donut
    cap_donut = _svg_donut(
        [
            (caps.get("802.11ax", 0), "#34a853", "WiFi 6"),
            (caps.get("802.11ac", 0), "#2196f3", "WiFi 5"),
            (caps.get("802.11n", 0), "#fbbc04", "WiFi 4"),
            (caps.get("legacy", 0), "#ea4335", "Legacy"),
        ],
        size=130,
    )

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
                pwr_class = (
                    f"pwr-{pwr_mode}" if pwr_mode in ("high", "medium", "low", "auto") else ""
                )
                row_cells.append(f'<td class="{pwr_class}">ch{ch} · {pwr_mode}</td>')
            else:
                row_cells.append('<td style="color:#5f6368">—</td>')
        matrix_rows.append(f'<tr>{"".join(row_cells)}</tr>')

    matrix_html = (
        '<table class="matrix-table">'
        "<tr><th>AP</th><th>2.4 GHz</th><th>5 GHz</th><th>6 GHz</th></tr>"
        + "".join(matrix_rows)
        + "</table>"
    )

    # Airtime utilization
    airtime = analysis_data.get("airtime_analysis", {}).get("ap_utilization", {})
    airtime_items = []
    for key, data in sorted(airtime.items(), key=lambda x: -x[1].get("airtime_pct", 0)):
        pct = data.get("airtime_pct", 0)
        color = "#ea4335" if pct > 80 else "#fbbc04" if pct > 50 else "#34a853"
        airtime_items.append((key[:18], pct, color))
    airtime_chart = _svg_hbar(airtime_items[:10], max_val=100) if airtime_items else ""

    # Channel utilization from live device stats (more current than airtime analysis)
    devices = analysis_data.get("devices", [])
    cu_items = []
    for dev in devices:
        if dev.get("type") != "uap":
            continue
        name = dev.get("name", "?")
        for rs in dev.get("radio_table_stats", []):
            cu = rs.get("cu_total", 0)
            if cu and cu > 10:
                band = "2.4" if rs.get("radio") == "ng" else "5" if rs.get("radio") == "na" else "6"
                color = "#ea4335" if cu > 70 else "#fbbc04" if cu > 40 else "#34a853"
                cu_items.append((f"{name[:12]} {band}G", cu, color))
    cu_items.sort(key=lambda x: -x[1])
    cu_chart = _svg_hbar(cu_items[:8], max_val=100) if cu_items else ""

    # Per-AP detail expand (clients per AP)
    ap_client_detail = ""
    for ap in ap_details:
        ap_name = ap.get("name", "?")
        ap_clients = ap.get("clients", [])
        if not ap_clients:
            continue
        client_rows = []
        for c in sorted(ap_clients, key=lambda x: x.get("rssi", -100)):
            rssi = c.get("rssi", 0)
            if rssi and rssi > 0:
                rssi = -rssi
            hostname = c.get("hostname") or (c.get("mac") or "?")[:12]
            signal = c.get("signal", rssi)
            proto = c.get("radio_proto", "?")
            ch_w = c.get("channel_width", "?")
            client_rows.append(
                f'<tr><td style="color:#e8eaed">{_esc(hostname[:20])}</td>'
                f"<td>{signal} dBm</td><td>{proto}</td><td>{ch_w}MHz</td></tr>"
            )
        ap_client_detail += (
            f"<details><summary>{_esc(ap_name)} — {len(ap_clients)} clients</summary>"
            f'<div class="detail-body"><table>'
            f"<tr><th>Client</th><th>Signal</th><th>Proto</th><th>Width</th></tr>"
            f'{"".join(client_rows)}</table></div></details>'
        )

    return (
        f'<div class="panel-grid">'
        # Signal distribution
        f'<div class="panel-card">'
        f'<h3>Signal Quality <span class="count">({total_sig} wireless)</span></h3>'
        f"{sig_chart}</div>"
        # Band distribution
        f'<div class="panel-card">'
        f"<h3>Band Distribution</h3>"
        f"{band_donut}</div>"
        # Capability
        f'<div class="panel-card">'
        f"<h3>Client Capabilities</h3>"
        f"{cap_donut}</div>"
        # Airtime
        f'<div class="panel-card">'
        f'<h3>Airtime Utilization <span class="count">(%)</span></h3>'
        f'{airtime_chart if airtime_chart else "<span style=color:#5f6368>No airtime data</span>"}</div>'
        # Channel utilization
        f'{f"""<div class="panel-card"><h3>Channel Utilization <span class="count">(%)</span></h3>{cu_chart}</div>""" if cu_chart else ""}'
        # Power/Channel matrix
        f'<div class="panel-card full">'
        f"<h3>Channel &amp; Power Map</h3>"
        f"{matrix_html}</div>"
        # Per-AP client detail
        f'<div class="panel-card full">'
        f"<h3>Per-AP Client Detail</h3>"
        f'{ap_client_detail if ap_client_detail else "<span style=color:#5f6368>No client data</span>"}'
        f"</div>"
        f"{_dfs_card(analysis_data)}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Deep Dive: Clients
# ---------------------------------------------------------------------------


def _primary_issue(client):
    """Return (label, detail, severity) for the client's primary issue."""
    rssi = client.get("rssi", 0) or 0
    if rssi > 0:
        rssi = -rssi
    disconnect_count = client.get("disconnect_count", 0) or 0
    channel = client.get("channel", 0) or 0
    roam_count = client.get("roam_count", 0) or 0
    if rssi and rssi < -75:
        return ("Weak signal", f"{rssi} dBm", "critical")
    if disconnect_count > 5:
        return ("Frequent disconnects", f"{disconnect_count} events", "high")
    if 0 < channel <= 13:
        return ("On 2.4GHz", "band steering may help", "medium")
    if roam_count > 20:
        return ("Excessive roaming", f"{roam_count} roams", "medium")
    return ("Low health score", "", "low")


def _clients_panel(analysis_data):
    ca = analysis_data.get("client_analysis", {})
    journeys = analysis_data.get("client_journeys", {})

    # Problem clients — only those with issues
    all_clients = ca.get("clients", [])
    problem_clients = [
        c
        for c in all_clients
        if not c.get("is_wired")
        and (
            c.get("health_score", 100) < 70
            or c.get("roam_count", 0) > 20
            or c.get("disconnect_count", 0) > 5
        )
    ]
    problem_clients.sort(key=lambda c: c.get("health_score", 100))
    problem_clients = problem_clients[:15]

    # "Needs Attention" spotlight — worst 5 with health_score < 70
    attn_clients = [c for c in problem_clients if c.get("health_score", 100) < 70][:5]
    needs_attention_html = ""
    if attn_clients:
        attn_rows = []
        for c in attn_clients:
            score = c.get("health_score", 0)
            grade = c.get("grade", "?")
            color = _score_color(score)
            label, detail, severity = _primary_issue(c)
            issue_text = f"{label}: {detail}" if detail else label
            attn_rows.append(
                f'<div class="attn-row">'
                f'<span class="attn-name">{_esc(c.get("hostname", "?")[:24])}</span>'
                f'<span class="action-badge" style="background:{color}20;color:{color}">{_esc(grade)}</span>'
                f'<span style="color:{color};font-weight:600;width:28px;text-align:right;flex-shrink:0">{score}</span>'
                f'<span class="attn-ap">{_esc(c.get("ap_name", "?"))}</span>'
                f'<span class="issue-pill issue-{severity}">{_esc(issue_text)}</span>'
                f"</div>"
            )
        needs_attention_html = (
            f'<div style="margin-bottom:14px">'
            f'<div style="font-size:11px;font-weight:600;color:#9aa0a6;margin-bottom:8px;'
            f'text-transform:uppercase;letter-spacing:0.5px">Needs Attention</div>'
            f'<div style="display:flex;flex-direction:column;gap:5px">{"".join(attn_rows)}</div>'
            f"</div>"
        )

    def _band_pill(channel, health_score):
        ch = channel or 0
        if 0 < ch <= 13:
            css = "band-24" if health_score < 70 else "band-24-ok"
            return f'<span class="band-pill {css}">2.4 GHz</span>'
        if ch >= 36:
            return '<span class="band-pill band-5">5 GHz</span>'
        if ch > 13:
            return '<span class="band-pill band-6">6 GHz</span>'
        return '<span style="color:#5f6368;font-size:11px">—</span>'

    if problem_clients:
        rows = []
        for c in problem_clients:
            score = c.get("health_score", 0)
            color = _score_color(score)
            rssi = c.get("rssi", 0)
            rows.append(
                f"<tr>"
                f'<td>{_esc(c.get("hostname", "?")[:20])}</td>'
                f'<td>{_esc(c.get("ap_name", "?"))}</td>'
                f'<td>{_band_pill(c.get("channel", 0), score)}</td>'
                f"<td>{rssi} dBm</td>"
                f'<td style="color:{color};font-weight:600">{score} ({c.get("grade", "?")})</td>'
                f'<td>{c.get("roam_count", 0)} roams</td>'
                f'<td>{c.get("disconnect_count", 0)} disc</td>'
                f"</tr>"
            )
        client_table = (
            '<table class="client-table">'
            "<tr><th>Client</th><th>AP</th><th>Band</th><th>Signal</th>"
            "<th>Health</th><th>Roaming</th><th>Disconnects</th></tr>" + "".join(rows) + "</table>"
        )
    else:
        client_table = (
            '<div style="color:#34a853;padding:12px">All wireless clients are healthy.</div>'
        )

    # Client journeys — top 3 issues
    journey_html = ""
    top_issues = journeys.get("top_issues", [])
    journey_clients = journeys.get("clients", {})
    for issue in top_issues[:8]:
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
        journey_html = (
            '<div style="color:#5f6368;padding:8px">No significant roaming issues detected.</div>'
        )

    total_tracked = journeys.get("total_tracked", 0)

    # Full client list expandable
    all_wireless = [c for c in all_clients if not c.get("is_wired")]
    all_wireless.sort(key=lambda c: c.get("health_score", 100))
    full_rows = []
    # Build mac→satisfaction lookup from journey data
    journey_sats = {}
    for mac, jd in journey_clients.items():
        if "avg_satisfaction" in jd:
            journey_sats[mac] = jd["avg_satisfaction"]

    for c in all_wireless:
        score = c.get("health_score", 0)
        color = _score_color(score)
        rssi = c.get("rssi", 0)
        sat = journey_sats.get(c.get("mac", ""), None)
        sat_str = f"{sat:.0f}%" if sat is not None else "—"
        sat_color = (
            "#ea4335"
            if sat is not None and sat < 60
            else "#fbbc04" if sat is not None and sat < 80 else "#9aa0a6"
        )
        full_rows.append(
            f'<tr><td style="color:#e8eaed">{_esc(c.get("hostname", "?")[:22])}</td>'
            f'<td>{_esc(c.get("ap_name", "?"))}</td>'
            f"<td>{rssi} dBm</td>"
            f'<td style="color:{color}">{score} ({c.get("grade", "?")})</td>'
            f'<td>{c.get("channel", 0)}</td>'
            f'<td>{c.get("roam_count", 0)}</td>'
            f'<td style="color:{sat_color}">{sat_str}</td></tr>'
        )
    full_client_expand = (
        (
            f"<details><summary>View all {len(all_wireless)} wireless clients</summary>"
            f'<div class="detail-body"><table>'
            f"<tr><th>Client</th><th>AP</th><th>Signal</th><th>Health</th><th>Channel</th><th>Roams</th><th>WiFi Sat</th></tr>"
            f'{"".join(full_rows)}</table></div></details>'
        )
        if all_wireless
        else ""
    )

    # Health issues expandable
    health_cats = analysis_data.get("health_analysis", {}).get("categories", {})
    health_detail = ""
    for cat_name, cat_data in health_cats.items():
        issues = cat_data.get("issues", [])
        if not issues:
            continue
        issue_cards = []
        for issue in issues:
            sev = issue.get("severity", "medium")
            issue_cards.append(
                f'<div class="health-issue {sev}">'
                f'<div class="issue-msg">{_esc(issue.get("message", ""))}</div>'
                f'<div class="issue-impact">{_esc(issue.get("impact", ""))}</div>'
                f'<div class="issue-fix">{_esc(issue.get("recommendation", ""))}</div>'
                f"</div>"
            )
        label = cat_name.replace("_", " ").title()
        health_detail += (
            f'<details><summary>{_esc(label)} — {len(issues)} issue{"s" if len(issues) > 1 else ""}</summary>'
            f'<div class="detail-body">{"".join(issue_cards)}</div></details>'
        )

    # Low-satisfaction clients from daily stats
    low_sat_html = ""
    low_sat_clients = journeys.get("low_satisfaction_clients", [])
    if low_sat_clients:
        low_sat_rows = []
        for lsc in low_sat_clients:
            sat = lsc.get("avg_satisfaction", 0)
            sat_color = "#ea4335" if sat < 60 else "#fbbc04" if sat < 80 else "#34a853"
            low_sat_rows.append(
                f'<tr><td style="color:#e8eaed">{_esc(lsc.get("client", "?"))}</td>'
                f'<td style="color:{sat_color}">{sat:.0f}%</td>'
                f'<td>{lsc.get("days_tracked", 0)} days</td></tr>'
            )
        low_sat_html = (
            f'<div class="panel-card full">'
            f'<h3>Low WiFi Satisfaction <span class="count">({len(low_sat_clients)} clients)</span></h3>'
            f'<div style="font-size:11px;color:#9aa0a6;margin-bottom:6px">'
            f"Clients with average satisfaction below 80% over the past week</div>"
            f"<table><tr><th>Client</th><th>Avg Satisfaction</th><th>Tracked</th></tr>"
            f'{"".join(low_sat_rows)}</table></div>'
        )

    return (
        f'<div class="panel-grid">'
        f'<div class="panel-card full">'
        f'<h3>Problem Clients <span class="count">({len(problem_clients)} of {ca.get("total_clients", 0)})</span></h3>'
        f"{needs_attention_html}"
        f"{client_table}"
        f"{full_client_expand}</div>"
        f'<div class="panel-card full">'
        f'<h3>Client Journeys <span class="count">({total_tracked} tracked)</span></h3>'
        f"{journey_html}</div>"
        f"{low_sat_html}"
        f'<div class="panel-card full">'
        f"<h3>Health Issues</h3>"
        f'{health_detail if health_detail else "<span style=color:#34a853>No health issues detected.</span>"}'
        f"</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Deep Dive: Infrastructure
# ---------------------------------------------------------------------------


def _infra_panel(analysis_data):
    # --- Device Overview: Uptime, Firmware, WiFi Score ---
    devices = analysis_data.get("devices", [])
    dev_rows = []
    firmware_set = set()
    for dev in sorted(devices, key=lambda d: d.get("uptime", 0)):
        name = dev.get("name", "?")
        dtype = dev.get("type", "?")
        uptime_s = dev.get("uptime", 0) or 0
        fw = dev.get("version", dev.get("displayable_version", ""))
        sat = dev.get("satisfaction", "")
        if fw:
            firmware_set.add(fw)

        # Format uptime
        if uptime_s < 3600:
            up_str = f"{uptime_s // 60}m"
        elif uptime_s < 86400:
            up_str = f"{uptime_s // 3600}h"
        else:
            up_str = f"{uptime_s // 86400}d {(uptime_s % 86400) // 3600}h"

        # Color code uptime (short = warning)
        up_color = "#ea4335" if uptime_s < 3600 else "#ea8600" if uptime_s < 86400 else "#9aa0a6"

        # WiFi score color
        sat_str = ""
        if sat and dtype == "uap":
            sat_color = "#34a853" if sat >= 90 else "#fbbc04" if sat >= 70 else "#ea4335"
            sat_str = f'<span style="color:{sat_color}">{sat}%</span>'

        icon = "📡" if dtype == "uap" else "🔌"
        dev_rows.append(
            f"<tr><td>{icon} {_esc(name)}</td>"
            f'<td style="color:{up_color}">{up_str}</td>'
            f'<td style="font-size:11px">{_esc(fw[:12])}</td>'
            f"<td>{sat_str}</td></tr>"
        )

    fw_note = ""
    if len(firmware_set) > 1:
        fw_note = f'<div style="font-size:11px;color:#ea8600;margin-top:6px">⚠ {len(firmware_set)} different firmware versions — consider updating for consistency</div>'

    device_table = (
        (
            f'<div class="panel-card full">'
            f'<h3>Device Health <span class="count">({len(devices)} devices)</span></h3>'
            f'<table class="matrix-table">'
            f"<tr><th>Device</th><th>Uptime</th><th>Firmware</th><th>WiFi Score</th></tr>"
            f'{"".join(dev_rows)}</table>'
            f"{fw_note}</div>"
        )
        if dev_rows
        else ""
    )

    sw_analysis = analysis_data.get("switch_analysis", {})
    switches = sw_analysis.get("switches", [])
    if not switches and not dev_rows:
        return '<div class="panel-card"><p style="color:#5f6368">No infrastructure data.</p></div>'

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
        poe_used = (
            sw.get("system_stats", {}).get("output_power") if sw.get("system_stats") else None
        )
        if poe and poe > 0:
            used = poe_used or 0
            poe_html = f'<div style="font-size:12px;color:#9aa0a6;margin-top:6px">PoE: {used:.0f}W / {poe}W</div>'

        # Issues
        issue_html = ""
        sw_issues = [
            i for i in sw_analysis.get("issues", []) if i.get("switch") == name or name in str(i)
        ]
        if sw_issues:
            issue_items = []
            for si in sw_issues[:5]:
                issue_items.append(
                    f'<div style="font-size:11px;color:#ea8600;margin:2px 0">{_esc(str(si.get("message", si.get("issue", "?")))[:80])}</div>'
                )
            issue_html = f'<div style="margin-top:8px">{"".join(issue_items)}</div>'

        active_ports = sum(1 for p in ports if (p.get("speed", 0) or 0) > 0)
        total_ports = len(ports)

        # Expandable port detail table
        port_detail_rows = []
        for p in ports:
            pidx = p.get("port_idx", "?")
            pspeed = p.get("speed", 0) or 0
            pname = p.get("name", "")
            connected = p.get("connected_client", "")
            rx_d = p.get("rx_dropped", 0) or 0
            tx_d = p.get("tx_dropped", 0) or 0
            p_is_ap = p.get("is_ap", False)
            poe_w = p.get("poe_power")
            speed_str = f"{pspeed}M" if pspeed else "Down"
            drops_str = f"{_fmt(rx_d + tx_d)}" if (rx_d + tx_d) > 0 else "0"
            label = pname if pname and pname != f"Port {pidx}" else connected or ""
            if p_is_ap:
                label += " [AP]"
            try:
                poe_str = f"{float(poe_w):.1f}W" if poe_w else ""
            except (ValueError, TypeError):
                poe_str = str(poe_w) if poe_w else ""
            port_detail_rows.append(
                f'<tr><td>{pidx}</td><td style="color:#e8eaed">{_esc(label[:24])}</td>'
                f"<td>{speed_str}</td><td>{drops_str}</td><td>{poe_str}</td></tr>"
            )
        port_detail = (
            f"<details><summary>Port details</summary>"
            f'<div class="detail-body"><table>'
            f"<tr><th>#</th><th>Device</th><th>Speed</th><th>Drops</th><th>PoE</th></tr>"
            f'{"".join(port_detail_rows)}</table></div></details>'
        )

        cards.append(
            f'<div class="panel-card">'
            f'<h3>{_esc(name)} <span class="count">{model}</span></h3>'
            f'<div style="font-size:12px;color:#9aa0a6;margin-bottom:8px">{active_ports}/{total_ports} ports active</div>'
            f'<div class="port-grid">{"".join(port_cells)}</div>'
            f"{poe_html}{issue_html}{port_detail}"
            f"</div>"
        )

    switch_html = ""
    if cards:
        legend = (
            '<div style="display:flex;gap:16px;margin-top:4px;font-size:11px;color:#5f6368">'
            "<span>🟢 1Gbps</span><span>🟡 Slow</span><span>🔴 Drops</span><span>⚪ Inactive</span>"
            '<span style="color:#006fff">◎ AP port</span>'
            "</div>"
        )
        switch_html = (
            f'{"".join(cards)}'
            f'<div class="panel-card full" style="padding:12px 20px">{legend}</div>'
        )

    return f'<div class="panel-grid">{device_table}{switch_html}</div>'


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
    for radio_info in (
        mr.get("radios_with_min_rssi", [])
        if isinstance(mr.get("radios_with_min_rssi"), list)
        else []
    ):
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
        mesh_cell = (
            '<td class="feat-yes">Mesh</td>' if is_mesh else '<td style="color:#5f6368">Wired</td>'
        )

        rows.append(f"<tr><td>{_esc(name[:18])}</td>{mesh_cell}{bs_cell}{mr_cell}</tr>")

    ap_matrix = (
        '<table class="matrix-table">'
        "<tr><th>AP</th><th>Uplink</th><th>Band Steering</th><th>Min RSSI</th></tr>"
        + "".join(rows)
        + "</table>"
    )

    # Roaming features (per-WLAN)
    roaming = fr.get("roaming_features", {})
    r11r = roaming.get("802.11r", {})
    r11k = roaming.get("802.11k", {})
    r11v = roaming.get("802.11v", {})
    wlan_count = fr.get("wlan_count", 0) or (
        r11r.get("enabled_count", 0) + r11r.get("disabled_count", 0)
    )

    roaming_rows = [
        f"<tr><td>802.11r (Fast Transition)</td>"
        f'<td class="{"feat-yes" if r11r.get("enabled_count") else "feat-no"}">'
        f'{r11r.get("enabled_count", 0)}/{wlan_count} WLANs</td></tr>',
        f"<tr><td>802.11k (Neighbor Reports)</td>"
        f'<td class="{"feat-yes" if r11k.get("enabled_count") else "feat-no"}">'
        f'{r11k.get("enabled_count", 0)}/{wlan_count} WLANs</td></tr>',
        f"<tr><td>802.11v (BSS Transition)</td>"
        f'<td class="{"feat-yes" if r11v.get("enabled_count") else "feat-no"}">'
        f'{r11v.get("enabled_count", 0)}/{wlan_count} WLANs</td></tr>',
    ]
    roaming_table = (
        '<table class="matrix-table">'
        "<tr><th>Feature</th><th>Status</th></tr>" + "".join(roaming_rows) + "</table>"
    )

    # VLAN/Security
    health_cats = analysis_data.get("health_analysis", {}).get("categories", {})
    vlan = health_cats.get("vlan_segmentation", {})
    vlan_status = vlan.get("status", "unknown")
    vlan_color = (
        "#34a853"
        if vlan_status == "healthy"
        else "#fbbc04" if vlan_status == "warning" else "#ea4335"
    )
    vlan_issues = vlan.get("issues", [])
    vlan_html = f'<div style="color:{vlan_color};font-size:13px;font-weight:500">{vlan_status.title()}</div>'
    if vlan_issues:
        for vi in vlan_issues[:2]:
            vlan_html += f'<div style="font-size:12px;color:#9aa0a6;margin-top:4px">{_esc(vi.get("message", ""))[:80]}</div>'

    return (
        f'<div class="panel-grid">'
        f'<div class="panel-card">'
        f"<h3>AP Configuration</h3>"
        f"{ap_matrix}</div>"
        f'<div class="panel-card">'
        f"<h3>Roaming Features</h3>"
        f"{roaming_table}</div>"
        f'<div class="panel-card">'
        f"<h3>Network Segmentation</h3>"
        f"{vlan_html}</div>"
        f"</div>"
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
        f'<button class="tab-btn" onclick="switchTab(\'trends\', this)">&#x1F4C8; Trends</button>'
        f"</div>"
        f'<div id="tab-rf" class="tab-content active">{_rf_panel(analysis_data)}</div>'
        f'<div id="tab-clients" class="tab-content">{_clients_panel(analysis_data)}</div>'
        f'<div id="tab-infra" class="tab-content">{_infra_panel(analysis_data)}</div>'
        f'<div id="tab-config" class="tab-content">{_config_panel(analysis_data)}</div>'
        f'<div id="tab-trends" class="tab-content">{_trend_tab_panel(analysis_data)}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Main Generator
# ---------------------------------------------------------------------------


def generate_v2_report(
    analysis_data, recommendations, site_name, output_dir="reports", ai_summary=None
):
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
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f"<title>UniFi Network Report — {_esc(site_name)}</title>",
        "<style>",
        _css(),
        TREND_CSS,
        "</style>",
        "</head>",
        "<body>",
        _header(site_name, analysis_data),
        '<div class="container">',
        _data_quality_banner(analysis_data),
        _ai_summary_card(ai_summary),
        _hero(analysis_data, recommendations, site_name),
        _quick_actions_card(analysis_data, recommendations),
        _topology(analysis_data),
        _svg_device_timeline(analysis_data),
        _actions(recommendations, analysis_data),
        _trend_summary_card(analysis_data.get("trend_analysis")),
        _tabs(analysis_data),
        '<div style="text-align:center;margin:24px 0">'
        '<button class="print-btn" onclick="printReport()">🖨 Expand All &amp; Print</button>'
        "</div>",
        "</div>",
        "<script>",
        _js(),
        "</script>",
        "</body></html>",
    ]

    html = "\n".join(parts)

    with open(filepath, "w") as f:
        f.write(html)

    # V2 is fully self-contained (no CDN deps) — same file is shareable
    return filepath, filepath
