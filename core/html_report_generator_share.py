#!/usr/bin/env python3
"""
HTML Report Generator for UniFi Network Analysis - SHARING VERSION
Uses static matplotlib images instead of Chart.js for compatibility with email and iMessage.

This version generates reports that work in:
- Email clients (Gmail, Outlook, Apple Mail)
- iMessage and other messaging apps
- Any environment where JavaScript is disabled
"""

import base64
import io

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt

# Import the original generator
from core.html_report_generator import generate_html_report as _original_generate_html_report


def generate_chart_image(ap_key, labels, airtime_data, tx_data, rx_data, avg_airtime):
    """
    Generate a static PNG chart image and return it as base64 encoded string
    for embedding directly in HTML (works in email clients and iMessage)
    """
    # Create figure with good size for reports
    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)

    # Determine color based on average airtime
    if avg_airtime > 70:
        main_color = "#ef4444"  # red
    elif avg_airtime > 50:
        main_color = "#f59e0b"  # yellow
    else:
        main_color = "#10b981"  # green

    # Parse time labels (just use indices for x-axis)
    x = range(len(labels))
    time_labels = [label.split("T")[1][:5] if "T" in label else label for label in labels]

    # Plot lines
    ax.plot(
        x,
        airtime_data,
        color=main_color,
        linewidth=2,
        label="Total Airtime %",
        marker="o",
        markersize=3,
    )
    ax.fill_between(x, tx_data, alpha=0.3, color="#6366f1", label="TX %")
    ax.fill_between(x, rx_data, alpha=0.3, color="#9333ea", label="RX %")

    # Formatting
    ax.set_ylim(0, 100)
    ax.set_xlabel("Time", fontsize=10)
    ax.set_ylabel("Utilization %", fontsize=10)
    ax.set_title(f"{ap_key} - Historical Airtime", fontsize=12, fontweight="bold")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.3, linestyle="--")

    # Set x-axis labels (show every Nth label to avoid crowding)
    step = max(1, len(time_labels) // 10)
    ax.set_xticks([i for i in range(0, len(time_labels), step)])
    ax.set_xticklabels(
        [time_labels[i] for i in range(0, len(time_labels), step)],
        rotation=45,
        ha="right",
        fontsize=8,
    )

    # Tight layout
    plt.tight_layout()

    # Save to bytes buffer
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    buf.seek(0)

    # Encode as base64
    img_base64 = base64.b64encode(buf.read()).decode("utf-8")

    # Close figure to free memory
    plt.close(fig)

    return img_base64


def generate_airtime_analysis_html(airtime_analysis):
    """Generate airtime utilization section with static matplotlib images for sharing"""
    if not airtime_analysis:
        return ""

    ap_utilization = airtime_analysis.get("ap_utilization", {})
    saturated_aps = airtime_analysis.get("saturated_aps", [])
    time_series = airtime_analysis.get("time_series", {})

    # Calculate average utilization from time series data
    ap_averages = {}
    for ap_key, data_points in time_series.items():
        if data_points:
            airtime_values = [p.get("airtime_pct", 0) for p in data_points if isinstance(p, dict)]
            if airtime_values:
                ap_averages[ap_key] = sum(airtime_values) / len(airtime_values)

    # Group by AP name (without band suffix)
    ap_grouped = {}
    for ap_key, avg_util in ap_averages.items():
        # Extract AP name and band (e.g., "Hallway (2.4GHz)" -> "Hallway", "2.4GHz")
        if "(" in ap_key and ")" in ap_key:
            ap_name = ap_key.split("(")[0].strip()
            band = ap_key.split("(")[1].split(")")[0].strip()
        else:
            ap_name = ap_key
            band = "Unknown"

        if ap_name not in ap_grouped:
            ap_grouped[ap_name] = {}

        ap_grouped[ap_name][band] = {
            "avg": avg_util,
            "current": ap_utilization.get(ap_key, {}).get("airtime_pct", 0),
            "clients": ap_utilization.get(ap_key, {}).get("clients", 0),
            "full_key": ap_key,
        }

    # Build average utilization summary cards (grouped by AP)
    avg_summary_html = ""
    if ap_grouped:
        avg_summary_html = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 30px;">'

        # Sort by worst utilization across bands
        sorted_aps = sorted(
            ap_grouped.items(),
            key=lambda x: max(band_data["avg"] for band_data in x[1].values()),
            reverse=True,
        )

        for ap_name, bands_data in sorted_aps:
            # Determine overall color based on worst band
            max_util = max(band_data["avg"] for band_data in bands_data.values())

            if max_util > 70:
                border_color = "#ef4444"  # Red
                emoji = "🔴"
            elif max_util > 50:
                border_color = "#f59e0b"  # Yellow
                emoji = "🟡"
            else:
                border_color = "#10b981"  # Green
                emoji = "🟢"

            avg_summary_html += f"""
                <div style="border: 2px solid {border_color}; padding: 15px; border-radius: 8px; background: white;">
                    <div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">{emoji} {ap_name}</div>
            """

            # Add each band's data
            for band in ["2.4GHz", "5GHz"]:
                if band in bands_data:
                    band_info = bands_data[band]
                    avg_util = band_info["avg"]
                    current_util = band_info["current"]
                    clients = band_info["clients"]

                    # Color for this specific band
                    if avg_util > 70:
                        band_color = "#ef4444"
                        status = "Needs Attention"
                    elif avg_util > 50:
                        band_color = "#f59e0b"
                        status = "Monitor"
                    else:
                        band_color = "#10b981"
                        status = "Good"

                    avg_summary_html += f"""
                    <div style="margin-bottom: 10px; padding: 10px; background: #f9fafb; border-radius: 6px; border-left: 3px solid {band_color};">
                        <div style="font-weight: 600; font-size: 12px; color: #666; margin-bottom: 4px;">📡 {band}</div>
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <div style="font-size: 1.8em; font-weight: bold; color: {band_color};">{avg_util:.1f}%</div>
                                <div style="font-size: 11px; color: #666;">Avg Utilization</div>
                            </div>
                            <div style="text-align: right; font-size: 11px; color: #666;">
                                <div>Current: {current_util:.1f}%</div>
                                <div>{clients} clients</div>
                            </div>
                        </div>
                        <div style="font-size: 10px; color: {band_color}; font-weight: 600; margin-top: 4px;">{status}</div>
                    </div>
                    """

            avg_summary_html += "</div>"

        avg_summary_html += "</div>"

    # Build utilization table
    util_html = ""
    for ap_key, data in ap_utilization.items():
        airtime_pct = data.get("airtime_pct", 0)
        clients = data.get("clients", 0)

        # Color code by utilization
        if airtime_pct > 70:
            color = "#ef4444"
            status = "🔴 Saturated"
        elif airtime_pct > 50:
            color = "#f59e0b"
            status = "🟡 High"
        else:
            color = "#10b981"
            status = "🟢 Good"

        util_html += f"""
                    <tr>
                        <td>{ap_key}</td>
                        <td style="color: {color}; font-weight: bold;">{airtime_pct:.1f}%</td>
                        <td>{clients}</td>
                        <td style="color: {color}; font-weight: bold;">{status}</td>
                    </tr>
"""

    # Generate static chart images if we have historical data
    charts_html = ""

    if time_series:
        # Create chart for each AP using static matplotlib images
        for idx, (ap_key, data_points) in enumerate(time_series.items()):
            if not data_points:
                continue

            # Prepare data
            labels = [point["datetime"] for point in data_points]
            airtime_data = [point["airtime_pct"] for point in data_points]
            tx_data = [point["tx_pct"] for point in data_points]
            rx_data = [point["rx_pct"] for point in data_points]

            # Calculate average airtime for color coding
            avg_airtime = sum(airtime_data) / len(airtime_data) if airtime_data else 0

            # Generate static chart image as base64
            img_base64 = generate_chart_image(
                ap_key, labels, airtime_data, tx_data, rx_data, avg_airtime
            )

            # Embed as data URI (works in email clients and iMessage)
            charts_html += f"""
                <div style="margin: 30px 0; padding: 20px; background: #f9fafb; border-radius: 8px;">
                    <img src="data:image/png;base64,{img_base64}" 
                         alt="{ap_key} Airtime Chart" 
                         style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />
                </div>
"""

    return f"""
            <div class="section">
                <h2>⏱️ Airtime Utilization</h2>
                <div style="background: #eff6ff; padding: 20px; border-radius: 8px; border-left: 4px solid #3b82f6; margin-bottom: 20px;">
                    <strong>What is Airtime?</strong>
                    <p style="margin-top: 10px; color: #666;">Airtime utilization measures how busy the wireless channel is. High airtime (>70%) indicates saturation even with few clients, typically caused by interference, legacy devices, or poor signal quality forcing retransmissions.</p>
                </div>
                
                {f'<h3 style="margin-top: 30px; margin-bottom: 15px;">📊 Average Utilization (24h)</h3>{avg_summary_html}' if avg_summary_html else ''}
                
                <h3 style="margin-top: 30px; margin-bottom: 15px;">Current Status</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Access Point (Band)</th>
                            <th>Airtime Utilization</th>
                            <th>Clients</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {util_html if util_html else '<tr><td colspan="4">No utilization data available</td></tr>'}
                    </tbody>
                </table>
                
                {f'<div style="margin-top: 20px; padding: 15px; background: #fef2f2; border-radius: 8px; border-left: 4px solid #ef4444;"><strong style="color: #ef4444;">⚠ {len(saturated_aps)} Saturated AP(s) Detected</strong><p style="margin-top: 10px; color: #666;">Consider adding additional APs or redistributing clients to reduce load.</p></div>' if saturated_aps else ''}
                
                {f'<h3 style="margin-top: 40px; margin-bottom: 15px;">📊 Historical Trends (Last 24 Hours)</h3><div style="background: #fef3c7; padding: 15px; border-radius: 8px; border-left: 4px solid #f59e0b; margin-bottom: 20px;"><strong>📱 Sharing-Friendly Version</strong><p style="margin-top: 10px; color: #666;">This report uses static images that work in email and iMessage.</p></div>' if time_series else ''}
                {charts_html}
            </div>
"""


def generate_html_report(analysis_data, recommendations, site_name, output_dir="reports"):
    """
    Generate a sharing-friendly HTML report with static matplotlib images

    This version wraps the original generator but replaces the airtime section
    with static images for compatibility with email clients and iMessage.
    """
    # Call original generator
    original_report_path = _original_generate_html_report(
        analysis_data, recommendations, site_name, output_dir
    )

    # Read the original report
    with open(original_report_path, "r", encoding="utf-8") as f:
        original_html = f.read()

    # Generate sharing-friendly airtime section
    airtime_analysis = analysis_data.get("airtime_analysis", {})
    new_airtime_html = generate_airtime_analysis_html(airtime_analysis)

    # Replace the airtime section in the HTML
    # Find the airtime section and replace it
    import re

    pattern = r'<div class="section">\s*<h2>⏱️ Airtime Utilization</h2>.*?</div>\s*(?=<div class="section">|$)'
    modified_html = re.sub(pattern, new_airtime_html, original_html, flags=re.DOTALL)

    # Create new filename with _share suffix
    share_report_path = original_report_path.replace(".html", "_share.html")

    # Write the modified report
    with open(share_report_path, "w", encoding="utf-8") as f:
        f.write(modified_html)

    return share_report_path
