"""Trend Report Components for UniFi Network Optimizer
=======================================================
Inline SVG sparklines, trend badges, and tab panel for the HTML report.
No external dependencies — everything renders inline in the self-contained report.
"""

import html as _html
from datetime import datetime


def _esc(text):
    return _html.escape(str(text)) if text else ""


# ---------------------------------------------------------------------------
# CSS additions (injected into the report stylesheet)
# ---------------------------------------------------------------------------

TREND_CSS = """
/* Trend tab */
.trend-headline {
    font-size: 14px; color: #e8eaed; line-height: 1.6;
    padding: 12px 16px; background: #0d1a2e; border-radius: 8px;
    border: 1px solid #1e2d4a; margin-bottom: 20px;
}
.trend-badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
    white-space: nowrap;
}
.trend-badge.improving { background: #34a85320; color: #34a853; }
.trend-badge.degrading { background: #ea433520; color: #ea4335; }
.trend-badge.stable    { background: #1e2d4a;   color: #9aa0a6; }
.trend-badge.increasing { background: #ea433520; color: #ea4335; }
.trend-badge.decreasing { background: #34a85320; color: #34a853; }
.trend-network-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px;
}
.trend-network-card {
    background: #111d33; border-radius: 10px; padding: 16px;
    border: 1px solid #1e2d4a; text-align: center;
}
.trend-network-card .tnc-label {
    font-size: 11px; color: #5f6368; text-transform: uppercase;
    letter-spacing: 0.5px; margin-bottom: 8px;
}
.trend-network-card .tnc-badge { margin-bottom: 4px; }
.trend-network-card .tnc-slope { font-size: 11px; color: #9aa0a6; }
.sparkline-cell { line-height: 0; }
svg.sparkline { display: inline; vertical-align: middle; }
.trend-ap-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.trend-ap-table th {
    text-align: left; padding: 8px 10px; color: #5f6368;
    border-bottom: 1px solid #1e2d4a; font-weight: 500; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.3px;
}
.trend-ap-table td { padding: 7px 10px; border-bottom: 1px solid #1e2d4a15; }
.trend-ap-table tr:hover td { background: #162540; }
.anomaly-row {
    display: flex; align-items: center; gap: 10px;
    padding: 6px 12px; background: #ea433510;
    border: 1px solid #ea433530; border-radius: 6px; margin-bottom: 6px;
    font-size: 12px;
}
.anomaly-time { color: #9aa0a6; flex-shrink: 0; }
.anomaly-val  { color: #ea4335; font-weight: 600; }
.anomaly-exp  { color: #9aa0a6; }
.flagged-client-row {
    display: flex; align-items: center; gap: 10px;
    padding: 6px 10px; background: #0d1a2e;
    border: 1px solid #1e2d4a15; border-radius: 6px; margin-bottom: 4px;
    font-size: 12px;
}
.fc-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; color: #e8eaed; }
.fc-slope { color: #9aa0a6; flex-shrink: 0; font-size: 11px; }
.no-trend-data {
    text-align: center; padding: 40px 20px; color: #5f6368; font-size: 13px;
}
@media (max-width: 768px) {
    .trend-network-grid { grid-template-columns: 1fr; }
}
"""


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def _svg_sparkline(values, width=120, height=30, color="#4fc3f7"):
    """Return an inline SVG sparkline for a list of numeric values.

    Args:
        values: list of float values.
        width:  SVG width in pixels.
        height: SVG height in pixels.
        color:  stroke color.

    Returns:
        str: inline SVG markup, or empty string if fewer than 2 values.
    """
    if len(values) < 2:
        return ""

    vmin = min(values)
    vmax = max(values)
    vrange = vmax - vmin if vmax != vmin else 1.0

    pad = 2
    w = width - 2 * pad
    h = height - 2 * pad

    def px(i, v):
        x = pad + i * w / (len(values) - 1)
        y = pad + h - (v - vmin) / vrange * h
        return f"{x:.1f},{y:.1f}"

    points = " ".join(px(i, v) for i, v in enumerate(values))
    return (
        f'<svg class="sparkline" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{points}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>"
    )


def _trend_badge(trend, slope=None):
    """Return an HTML badge for a trend string.

    Args:
        trend: "improving", "degrading", "stable", "increasing", "decreasing"
        slope: optional numeric slope for tooltip/display.

    Returns:
        str: HTML badge markup.
    """
    icons = {
        "improving": "&#x2191;",  # ↑
        "degrading": "&#x2193;",  # ↓
        "stable": "&#x2014;",  # —
        "increasing": "&#x2191;",  # ↑
        "decreasing": "&#x2193;",  # ↓
    }
    labels = {
        "improving": "Improving",
        "degrading": "Degrading",
        "stable": "Stable",
        "increasing": "Increasing",
        "decreasing": "Decreasing",
    }
    icon = icons.get(trend, "&#x2014;")
    label = labels.get(trend, _esc(trend).title())
    css_class = (
        trend
        if trend in ("improving", "degrading", "stable", "increasing", "decreasing")
        else "stable"
    )

    slope_str = ""
    if slope is not None:
        slope_str = f" ({slope:+.2f}/day)"

    return (
        f'<span class="trend-badge {_esc(css_class)}">'
        f"{icon} {_esc(label)}{_esc(slope_str)}"
        f"</span>"
    )


# ---------------------------------------------------------------------------
# Network-level summary card (hero-adjacent)
# ---------------------------------------------------------------------------


def _trend_summary_card(trend_data):
    """Render a headline + network-level trend card.

    Args:
        trend_data: the trend_analysis dict from build_trend_summary().

    Returns:
        str: HTML card markup.
    """
    if not trend_data or trend_data.get("enabled") is False:
        return ""

    headline = _esc(trend_data.get("headline", ""))
    network = trend_data.get("network", {})

    if not network:
        return ""

    sat_trend = network.get("satisfaction_trend", "stable")
    sat_slope = network.get("satisfaction_slope", 0.0)
    cli_trend = network.get("client_count_trend", "stable")
    cli_slope = network.get("client_count_slope", 0.0)
    dfs_trend = network.get("dfs_event_trend", "stable")

    return f"""
<div class="panel-card" style="margin-bottom:20px;">
  <h3>&#x1F4C8; Historical Trends</h3>
  <div class="trend-headline">{headline}</div>
  <div class="trend-network-grid">
    <div class="trend-network-card">
      <div class="tnc-label">Satisfaction</div>
      <div class="tnc-badge">{_trend_badge(sat_trend, sat_slope)}</div>
      <div class="tnc-slope">{sat_slope:+.2f} / day</div>
    </div>
    <div class="trend-network-card">
      <div class="tnc-label">Client Count</div>
      <div class="tnc-badge">{_trend_badge(cli_trend, cli_slope)}</div>
      <div class="tnc-slope">{cli_slope:+.2f} / day</div>
    </div>
    <div class="trend-network-card">
      <div class="tnc-label">DFS Events</div>
      <div class="tnc-badge">{_trend_badge(dfs_trend)}</div>
      <div class="tnc-slope">&nbsp;</div>
    </div>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Per-AP trend table
# ---------------------------------------------------------------------------


def _ap_trend_table(per_ap):
    """Render the per-AP trend table with sparklines.

    Args:
        per_ap: the per_ap dict from build_trend_summary().

    Returns:
        str: HTML table markup.
    """
    if not per_ap:
        return '<p class="no-trend-data">No per-AP trend data available.</p>'

    # Sort: degrading first, then stable, then improving
    order = {"degrading": 0, "stable": 1, "improving": 2}
    rows = sorted(
        per_ap.items(), key=lambda kv: order.get(kv[1].get("satisfaction_trend", "stable"), 1)
    )

    thead = (
        "<thead><tr>"
        "<th>Access Point</th>"
        "<th>Satisfaction</th>"
        "<th>Sat. Trend</th>"
        "<th>Clients</th>"
        "<th>Client Trend</th>"
        "<th>Satisfaction Sparkline</th>"
        "<th>Points</th>"
        "</tr></thead>"
    )

    tbody_rows = []
    for name, info in rows:
        sat_trend = info.get("satisfaction_trend", "stable")
        sat_slope = info.get("satisfaction_slope", 0.0)
        cli_trend = info.get("client_count_trend", "stable")
        cli_slope = info.get("client_count_slope", 0.0)
        sparkline_vals = info.get("sparkline_satisfaction", [])
        n_pts = info.get("data_points", 0)

        # Color sparkline by trend
        spark_color = {"improving": "#34a853", "degrading": "#ea4335", "stable": "#4fc3f7"}.get(
            sat_trend, "#4fc3f7"
        )
        spark_html = (
            _svg_sparkline(sparkline_vals, color=spark_color)
            or "<span style='color:#5f6368'>—</span>"
        )

        tbody_rows.append(
            f"<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{sat_slope:+.2f}/day</td>"
            f"<td>{_trend_badge(sat_trend)}</td>"
            f"<td>{cli_slope:+.2f}/day</td>"
            f"<td>{_trend_badge(cli_trend)}</td>"
            f'<td class="sparkline-cell">{spark_html}</td>'
            f"<td style='color:#5f6368'>{n_pts}</td>"
            f"</tr>"
        )

    tbody = "<tbody>" + "".join(tbody_rows) + "</tbody>"
    return f'<table class="trend-ap-table">{thead}{tbody}</table>'


# ---------------------------------------------------------------------------
# Anomaly timeline
# ---------------------------------------------------------------------------


def _anomaly_section(trend_data):
    """Render network + per-AP anomaly events.

    Args:
        trend_data: the full trend_analysis dict.

    Returns:
        str: HTML markup.
    """
    all_anomalies = []

    # Network-level anomalies
    for a in trend_data.get("network", {}).get("anomalies", []):
        all_anomalies.append(("Network", a))

    # Per-AP anomalies
    for ap_name, info in trend_data.get("per_ap", {}).items():
        for a in info.get("anomalies", []):
            all_anomalies.append((ap_name, a))

    if not all_anomalies:
        return "<p style='color:#5f6368;font-size:12px;'>No anomalies detected.</p>"

    # Sort by time descending (most recent first)
    all_anomalies.sort(key=lambda x: x[1].get("time", 0), reverse=True)

    rows = []
    for source, a in all_anomalies[:20]:
        ts = a.get("time", 0)
        try:
            time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            time_str = "?"
        val = a.get("value", "?")
        expected = a.get("expected", "?")
        dev = a.get("deviation", "")
        metric = a.get("metric", "value")
        dev_str = f" ({dev:.1f}σ)" if dev else ""
        rows.append(
            f'<div class="anomaly-row">'
            f'<span class="anomaly-time">{_esc(time_str)}</span>'
            f'<span style="color:#9aa0a6">{_esc(source)}</span>'
            f'<span style="color:#9aa0a6">{_esc(metric)}</span>'
            f'<span class="anomaly-val">{val}</span>'
            f'<span class="anomaly-exp">(expected ≈ {expected}{_esc(dev_str)})</span>'
            f"</div>"
        )
    return "".join(rows)


# ---------------------------------------------------------------------------
# Flagged clients section
# ---------------------------------------------------------------------------


def _flagged_clients_section(flagged_clients):
    """Render the list of clients with declining satisfaction.

    Args:
        flagged_clients: list of client dicts from build_trend_summary().

    Returns:
        str: HTML markup.
    """
    if not flagged_clients:
        return "<p style='color:#5f6368;font-size:12px;'>No clients with notable trend changes.</p>"

    rows = []
    for c in flagged_clients:
        name = c.get("name") or c.get("mac", "Unknown")
        mac = c.get("mac", "")
        trend = c.get("satisfaction_trend", "stable")
        slope = c.get("slope", 0.0)
        latest = c.get("latest_satisfaction")
        latest_str = f" — current: {latest:.0f}" if latest is not None else ""
        display = _esc(name) if name != mac else _esc(mac)

        rows.append(
            f'<div class="flagged-client-row">'
            f'<span class="fc-name">{display}</span>'
            f'<span style="color:#5f6368;font-size:11px">{_esc(mac)}</span>'
            f"<span>{_trend_badge(trend)}</span>"
            f'<span class="fc-slope">{slope:+.2f}/day{_esc(latest_str)}</span>'
            f"</div>"
        )
    return "".join(rows)


# ---------------------------------------------------------------------------
# Full tab panel
# ---------------------------------------------------------------------------


def _trend_tab_panel(analysis_data):
    """Render the complete Trends tab content.

    Args:
        analysis_data: the full analysis dict (reads analysis_data["trend_analysis"]).

    Returns:
        str: HTML markup for the tab panel.
    """
    trend_data = analysis_data.get("trend_analysis") if analysis_data else None

    if not trend_data or trend_data.get("enabled") is False:
        return (
            '<div class="panel-card">'
            '<div class="no-trend-data">'
            "&#x1F4C8; Trend data not available.<br>"
            '<span style="font-size:11px;color:#5f6368">'
            "Run a full analysis to populate trend history."
            "</span>"
            "</div>"
            "</div>"
        )

    per_ap = trend_data.get("per_ap", {})
    flagged = trend_data.get("flagged_clients", [])

    return f"""
<div class="panel-grid">
  <div class="panel-card full">
    <h3>Per-AP Trend Analysis</h3>
    {_ap_trend_table(per_ap)}
  </div>
</div>
<div class="panel-grid">
  <div class="panel-card">
    <h3>Anomaly Timeline</h3>
    {_anomaly_section(trend_data)}
  </div>
  <div class="panel-card">
    <h3>Clients with Declining Satisfaction
      <span class="count"> ({len(flagged)})</span>
    </h3>
    {_flagged_clients_section(flagged)}
  </div>
</div>"""
