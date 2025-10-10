#!/usr/bin/env python3
"""
HTML Report Generator for UniFi Network Analysis

Generates comprehensive HTML reports with all expert analysis findings,
recommendations, RSSI data, and executive summaries.
"""

from datetime import datetime
from pathlib import Path


def make_collapsible(section_id, section_title, section_content):
    """Wrap a section in collapsible HTML structure

    If section_content is already a complete <div class="section"> block,
    just add the collapsible controls around it.
    """
    if not section_content or section_content.strip() == "":
        return ""

    return f"""
        <div class="collapsible-section">
            <div class="section-header" onclick="toggleSection('{section_id}')">
                <h2>{section_title}</h2>
                <span class="section-toggle">▼</span>
            </div>
            <div id="{section_id}" class="section-content">
                {section_content}
            </div>
        </div>
"""


def generate_navigation_menu(analysis_data, recommendations, client_health, switch_analysis):
    """Generate sticky navigation menu for quick access to sections"""
    nav_items = []

    # Always show these core sections
    nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-summary\'); return false;">📊 Summary</a>'))
    nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-recommendations\'); return false;">💡 Recommendations</a>'))

    # Conditional sections based on data availability
    if client_health:
        nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-client-health\'); return false;">👥 Clients</a>'))
        if client_health.get("disconnection_prone"):
            nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-disconnected-clients\'); return false;">🔄 Disconnects</a>'))

    if analysis_data.get("ap_analysis"):
        nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-access-points\'); return false;">📡 APs</a>'))

    if analysis_data.get("channel_analysis"):
        nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-channels\'); return false;">📻 Channels</a>'))

    if switch_analysis and switch_analysis.get("switches"):
        nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-switches\'); return false;">🔌 Switches</a>'))

    if analysis_data.get("mesh_analysis", {}).get("mesh_aps"):
        nav_items.append(('<a class="nav-link" href="#" onclick="scrollToSection(\'section-mesh\'); return false;">🔗 Mesh</a>'))

    # Control buttons
    nav_items.append('<a class="nav-link" href="#" onclick="expandAll(); return false;" style="margin-left: 20px; background: rgba(255,255,255,0.1);">⬇ Expand All</a>')
    nav_items.append('<a class="nav-link" href="#" onclick="collapseAll(); return false;" style="background: rgba(255,255,255,0.1);">⬆ Collapse All</a>')

    nav_html = '\n'.join(nav_items)

    return f"""
        <div class="nav-menu">
            <span class="nav-menu-title">Jump to:</span>
            {nav_html}
        </div>
"""


def generate_html_report(analysis_data, recommendations, site_name, output_dir="reports"):
    """
    Generate a comprehensive HTML report from analysis data

    Args:
        analysis_data: Full analysis dictionary from ExpertNetworkAnalyzer
        recommendations: List of recommendations
        site_name: Name of the site analyzed
        output_dir: Directory to save report (default: 'reports')

    Returns:
        str: Path to generated HTML file
    """
    # Create output directory if it doesn't exist
    Path(output_dir).mkdir(exist_ok=True)

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unifi_analysis_{site_name}_{timestamp}.html"
    filepath = Path(output_dir) / filename

    # Extract data from analysis
    ap_analysis = analysis_data.get("ap_analysis", {})
    channel_analysis = analysis_data.get("channel_analysis", {})
    client_health = analysis_data.get("client_health", {})
    signal_distribution = analysis_data.get("signal_distribution", {})
    mesh_aps = analysis_data.get("mesh_analysis", {})

    # Build HTML content
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UniFi Network Analysis Report - {site_name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}

        .header .subtitle {{
            font-size: 1.2em;
            opacity: 0.9;
        }}

        .header .timestamp {{
            margin-top: 15px;
            font-size: 0.9em;
            opacity: 0.8;
        }}

        .content {{
            padding: 40px;
        }}

        .section {{
            margin-bottom: 40px;
            padding: 30px;
            background: #f8f9fa;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }}

        .section h2 {{
            color: #667eea;
            font-size: 1.8em;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e0e0e0;
        }}

        .section h3 {{
            color: #555;
            font-size: 1.3em;
            margin-top: 20px;
            margin-bottom: 15px;
        }}

        .executive-summary {{
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 40px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }}

        .executive-summary h2 {{
            color: white;
            border-bottom: 2px solid rgba(255,255,255,0.3);
        }}

        .executive-summary p {{
            font-size: 1.1em;
            line-height: 1.8;
            margin-bottom: 15px;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}

        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-top: 3px solid #667eea;
        }}

        .stat-card .value {{
            font-size: 2.5em;
            font-weight: bold;
            color: #667eea;
            margin-bottom: 5px;
        }}

        .stat-card .label {{
            color: #666;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .rssi-chart {{
            margin: 20px 0;
            padding: 20px;
            background: white;
            border-radius: 8px;
        }}

        .rssi-bar {{
            margin: 15px 0;
        }}

        .rssi-bar .label {{
            display: inline-block;
            width: 180px;
            font-weight: 500;
            color: #555;
        }}

        .rssi-bar .bar-container {{
            display: inline-block;
            width: calc(100% - 250px);
            height: 30px;
            background: #e0e0e0;
            border-radius: 15px;
            overflow: hidden;
            vertical-align: middle;
        }}

        .rssi-bar .bar {{
            height: 100%;
            transition: width 0.3s ease;
            display: flex;
            align-items: center;
            padding-left: 10px;
            color: white;
            font-weight: bold;
            font-size: 0.9em;
        }}

        .rssi-bar .count {{
            display: inline-block;
            width: 60px;
            text-align: right;
            font-weight: bold;
            color: #667eea;
        }}

        .excellent {{ background: #10b981; }}
        .good {{ background: #3b82f6; }}
        .fair {{ background: #f59e0b; }}
        .poor {{ background: #ef4444; }}
        .very-poor {{ background: #991b1b; }}
        .wired {{ background: #6b7280; }}

        .recommendation-list {{
            list-style: none;
        }}

        .recommendation-item {{
            background: white;
            padding: 20px;
            margin: 15px 0;
            border-radius: 8px;
            border-left: 4px solid #f59e0b;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}

        .recommendation-item.high-priority {{
            border-left-color: #ef4444;
        }}

        .recommendation-item.medium-priority {{
            border-left-color: #f59e0b;
        }}

        .recommendation-item.low-priority {{
            border-left-color: #3b82f6;
        }}

        .recommendation-item h4 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 1.1em;
        }}

        .recommendation-item .device {{
            color: #667eea;
            font-weight: bold;
        }}

        .recommendation-item .impact {{
            margin-top: 10px;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 4px;
            font-size: 0.9em;
        }}

        .ap-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}

        .ap-table th {{
            background: #667eea;
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 600;
        }}

        .ap-table td {{
            padding: 12px 15px;
            border-bottom: 1px solid #e0e0e0;
        }}

        .ap-table tr:last-child td {{
            border-bottom: none;
        }}

        .ap-table tr:hover {{
            background: #f8f9fa;
        }}

        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .badge.mesh {{
            background: #dbeafe;
            color: #1e40af;
        }}

        .badge.wired {{
            background: #d1fae5;
            color: #065f46;
        }}

        .badge.issue {{
            background: #fee2e2;
            color: #991b1b;
        }}

        .badge.ok {{
            background: #d1fae5;
            color: #065f46;
        }}

        .finding {{
            background: white;
            padding: 15px;
            margin: 10px 0;
            border-radius: 6px;
            border-left: 3px solid #f59e0b;
        }}

        .finding.critical {{
            border-left-color: #ef4444;
        }}

        .finding.warning {{
            border-left-color: #f59e0b;
        }}

        .finding.info {{
            border-left-color: #3b82f6;
        }}

        .footer {{
            text-align: center;
            padding: 30px;
            background: #f8f9fa;
            color: #666;
            font-size: 0.9em;
        }}

        .footer a {{
            color: #667eea;
            text-decoration: none;
        }}

        @media print {{
            body {{
                background: white;
                padding: 0;
            }}

            .container {{
                box-shadow: none;
            }}
        }}

        /* Mobile-responsive styles */
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}

            .header {{
                padding: 20px 15px;
            }}

            .header h1 {{
                font-size: 1.8em;
            }}

            .header .subtitle {{
                font-size: 1em;
            }}

            .content {{
                padding: 20px 15px;
            }}

            .section {{
                padding: 20px 15px;
                margin-bottom: 20px;
            }}

            .section h2 {{
                font-size: 1.4em;
            }}

            .section h3 {{
                font-size: 1.1em;
            }}

            /* Stack grids on mobile */
            .stats-grid {{
                grid-template-columns: 1fr;
                gap: 15px;
            }}

            /* Make tables scrollable */
            .ap-table {{
                display: block;
                overflow-x: auto;
                white-space: nowrap;
                font-size: 0.85em;
            }}

            .ap-table th,
            .ap-table td {{
                padding: 8px 6px;
            }}

            /* Stack RSSI bars vertically */
            .rssi-bar .label {{
                display: block;
                width: 100%;
                margin-bottom: 5px;
            }}

            .rssi-bar .bar-container {{
                display: block;
                width: calc(100% - 60px);
            }}

            .rssi-bar .count {{
                display: block;
                width: 100%;
                text-align: left;
                margin-top: 5px;
            }}

            /* Responsive cards for average utilization */
            div[style*="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))"] {{
                grid-template-columns: 1fr !important;
            }}

            div[style*="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))"] {{
                grid-template-columns: 1fr !important;
            }}

            /* Make stat cards full width */
            .stat-card {{
                padding: 15px;
            }}

            .stat-card .value {{
                font-size: 2em;
            }}

            /* Reduce executive summary padding */
            .executive-summary {{
                padding: 20px 15px;
            }}

            .executive-summary p {{
                font-size: 1em;
            }}

            /* Make recommendation items more compact */
            .recommendation-item {{
                padding: 15px;
            }}

            /* Scale down charts/images */
            canvas, img {{
                max-width: 100% !important;
                height: auto !important;
            }}
        }}

        @media (max-width: 480px) {{
            .header h1 {{
                font-size: 1.5em;
            }}

            .section h2 {{
                font-size: 1.2em;
            }}

            .stat-card .value {{
                font-size: 1.5em;
            }}

            /* Even more compact tables */
            .ap-table {{
                font-size: 0.75em;
            }}

            .ap-table th,
            .ap-table td {{
                padding: 6px 4px;
            }}
        }}

        /* Navigation Menu Styles */
        .nav-menu {{
            position: sticky;
            top: 0;
            z-index: 1000;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 15px 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
            justify-content: center;
        }}

        .nav-menu-title {{
            color: white;
            font-weight: 600;
            margin-right: 15px;
            font-size: 0.95em;
        }}

        .nav-link {{
            color: white;
            text-decoration: none;
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 0.9em;
            transition: background 0.2s;
            display: inline-block;
        }}

        .nav-link:hover {{
            background: rgba(255,255,255,0.2);
        }}

        /* Collapsible Section Styles */
        .collapsible-section {{
            margin-bottom: 20px;
        }}

        .section-header {{
            cursor: pointer;
            user-select: none;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 8px 8px 0 0;
            transition: background 0.3s;
        }}

        .section-header:hover {{
            background: linear-gradient(135deg, #5a6fd8 0%, #6a4190 100%);
        }}

        .section-header h2 {{
            color: white;
            margin: 0;
            border: none;
            padding: 0;
        }}

        .section-toggle {{
            font-size: 1.5em;
            transition: transform 0.3s;
            font-weight: bold;
        }}

        .section-toggle.collapsed {{
            transform: rotate(-90deg);
        }}

        .section-content {{
            max-height: 10000px;
            overflow: hidden;
            transition: max-height 0.3s ease-out, opacity 0.3s ease-out;
            opacity: 1;
        }}

        .section-content.collapsed {{
            max-height: 0;
            opacity: 0;
        }}

        .section-inner {{
            padding: 30px;
            background: #f8f9fa;
            border-radius: 0 0 8px 8px;
            border-left: 4px solid #667eea;
        }}

        @media (max-width: 768px) {{
            .nav-menu {{
                padding: 10px;
            }}

            .nav-menu-title {{
                width: 100%;
                text-align: center;
                margin-bottom: 5px;
            }}

            .nav-link {{
                font-size: 0.8em;
                padding: 5px 8px;
            }}
        }}
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script>
        // Collapsible section toggle functionality
        function toggleSection(sectionId) {{
            const content = document.getElementById(sectionId);
            const toggle = document.querySelector(`[onclick="toggleSection('${{sectionId}}')"] .section-toggle`);

            if (content.classList.contains('collapsed')) {{
                content.classList.remove('collapsed');
                toggle.classList.remove('collapsed');
                // Save state
                localStorage.setItem(sectionId, 'expanded');
            }} else {{
                content.classList.add('collapsed');
                toggle.classList.add('collapsed');
                // Save state
                localStorage.setItem(sectionId, 'collapsed');
            }}
        }}

        // Restore section states on page load
        window.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('[id^="section-"]').forEach(function(section) {{
                const state = localStorage.getItem(section.id);
                if (state === 'collapsed') {{
                    section.classList.add('collapsed');
                    const toggle = document.querySelector(`[onclick="toggleSection('${{section.id}}')"] .section-toggle`);
                    if (toggle) toggle.classList.add('collapsed');
                }}
            }});
        }});

        // Expand all sections
        function expandAll() {{
            document.querySelectorAll('[id^="section-"]').forEach(function(section) {{
                section.classList.remove('collapsed');
                localStorage.setItem(section.id, 'expanded');
            }});
            document.querySelectorAll('.section-toggle').forEach(function(toggle) {{
                toggle.classList.remove('collapsed');
            }});
        }}

        // Collapse all sections
        function collapseAll() {{
            document.querySelectorAll('[id^="section-"]').forEach(function(section) {{
                section.classList.add('collapsed');
                localStorage.setItem(section.id, 'collapsed');
            }});
            document.querySelectorAll('.section-toggle').forEach(function(toggle) {{
                toggle.classList.add('collapsed');
            }});
        }}

        // Smooth scroll to section and expand it
        function scrollToSection(sectionId) {{
            const section = document.getElementById(sectionId);
            if (section) {{
                section.classList.remove('collapsed');
                const toggle = document.querySelector(`[onclick="toggleSection('${{sectionId}}')"] .section-toggle`);
                if (toggle) toggle.classList.remove('collapsed');
                localStorage.setItem(sectionId, 'expanded');

                // Scroll to the section header
                const header = section.previousElementSibling;
                if (header) {{
                    header.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                }}
            }}
        }}
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 UniFi Network Analysis Report</h1>
            <div class="subtitle">Site: {site_name}</div>
            <div class="timestamp">Generated: {datetime.now().strftime("%B %d, %Y at %I:%M %p")}</div>
        </div>

        <div class="content">
"""

    # Add Navigation Menu
    switch_analysis = analysis_data.get("switch_analysis")
    html_content += generate_navigation_menu(analysis_data, recommendations, client_health, switch_analysis)

    # API Error Warning Banner (if there were errors)
    api_errors = analysis_data.get("api_errors")
    if api_errors:
        html_content += generate_api_error_warning_html(api_errors)

    # Quick Health Dashboard (prominently at top)
    has_incomplete_data = analysis_data.get("has_incomplete_data", False)
    if not has_incomplete_data:
        html_content += generate_quick_health_dashboard_html(analysis_data, recommendations)
    else:
        # Show warning if data is incomplete
        html_content += """
            <div class="section" style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 20px; margin-bottom: 30px;">
                <h2 style="color: #f59e0b; margin-top: 0;">⚠️ Network Grade Unavailable</h2>
                <p style="color: #92400e; margin-bottom: 0;">Due to incomplete data from API errors, an overall network health grade cannot be calculated. Please address the authentication/permission issues and re-run the analysis.</p>
            </div>
"""

    # Executive Summary Section (Collapsible)
    summary_content = generate_executive_summary_html(analysis_data, recommendations)
    if summary_content:
        html_content += make_collapsible("section-summary", "📊 Executive Summary", summary_content)

    # Network Health Analysis Section
    health_analysis = analysis_data.get("health_analysis")
    health_score = analysis_data.get("health_score")  # Get Grade-based score
    if health_analysis:
        html_content += generate_network_health_html(health_analysis, health_score)

    # Key Metrics Section
    html_content += generate_key_metrics_html(analysis_data, client_health)

    # RSSI Distribution Section
    html_content += generate_rssi_distribution_html(signal_distribution)

    # DFS Analysis Section
    dfs_analysis = analysis_data.get("dfs_analysis")
    if dfs_analysis:
        html_content += generate_dfs_analysis_html(dfs_analysis)

    # Band Steering Analysis Section
    band_steering = analysis_data.get("band_steering_analysis")
    if band_steering:
        html_content += generate_band_steering_html(band_steering)

    # Min RSSI Analysis Section
    min_rssi = analysis_data.get("min_rssi_analysis")
    if min_rssi:
        html_content += generate_min_rssi_html(min_rssi)

    # Airtime Analysis Section
    airtime_analysis = analysis_data.get("airtime_analysis")
    if airtime_analysis:
        html_content += generate_airtime_analysis_html(airtime_analysis)

    # Firmware Analysis Section
    firmware_analysis = analysis_data.get("firmware_analysis")
    if firmware_analysis and firmware_analysis.get("total_aps") > 0:
        html_content += generate_firmware_analysis_html(firmware_analysis)

    # Client Capabilities Section
    client_capabilities = analysis_data.get("client_capabilities")
    if client_capabilities:
        html_content += generate_client_capabilities_html(client_capabilities)

    # Client Security Section
    client_security = analysis_data.get("client_security")
    if client_security and (
        client_security.get("blocked_clients") or client_security.get("isolated_clients")
    ):
        html_content += generate_client_security_html(client_security)

    # Manufacturer Analysis Section
    manufacturer_analysis = analysis_data.get("manufacturer_analysis")
    if manufacturer_analysis:
        from core.manufacturer_analyzer import generate_manufacturer_insights_html

        html_content += generate_manufacturer_insights_html(manufacturer_analysis)

    # Switch Analysis Section (Collapsible)
    switch_port_history = analysis_data.get("switch_port_history")
    if switch_analysis and switch_analysis.get("switches"):
        switch_content = generate_switch_analysis_html(switch_analysis, switch_port_history)
        html_content += make_collapsible("section-switches", "🔌 Switch Analysis", switch_content)

    # Access Points Section (Collapsible)
    ap_content = generate_ap_overview_html(
        ap_analysis, mesh_aps, analysis_data.get("devices", [])
    )
    if ap_content:
        html_content += make_collapsible("section-access-points", "📡 Access Points", ap_content)

    # Mesh Topology Section (Collapsible, if mesh APs exist)
    if mesh_aps and mesh_aps.get("mesh_aps"):
        mesh_content = generate_mesh_topology_html(mesh_aps, analysis_data.get("devices", []))
        html_content += make_collapsible("section-mesh", "🔗 Mesh Topology", mesh_content)

    # Recommendations Section (Collapsible)
    recs_content = generate_recommendations_html(recommendations)
    if recs_content:
        html_content += make_collapsible("section-recommendations", "💡 Recommendations", recs_content)

    # Channel Analysis Section (Collapsible)
    channel_content = generate_channel_analysis_html(channel_analysis)
    if channel_content:
        html_content += make_collapsible("section-channels", "📻 Channel Analysis", channel_content)

    # Client Health Section (Collapsible)
    client_health_content = generate_client_health_html(client_health)
    if client_health_content:
        html_content += make_collapsible("section-client-health", "👥 Client Health", client_health_content)

    # Frequently Disconnected Clients Section (Collapsible)
    disconnected_content = generate_disconnected_clients_html(client_health)
    if disconnected_content:
        html_content += make_collapsible("section-disconnected-clients", "🔄 Frequently Disconnected Clients", disconnected_content)

    # Findings Section
    html_content += generate_findings_html(analysis_data)

    # Footer
    html_content += f"""
        </div>

        <div class="footer">
            <p>Generated by UniFi Network Optimizer</p>
            <p>Report generated on {datetime.now().strftime("%Y-%m-%d at %H:%M:%S")}</p>
            <p><a href="https://github.com/gneitzke/UnifiOptimizer">UnifiOptimizer on GitHub</a></p>
        </div>
    </div>
</body>
</html>
"""

    # Write to file
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return str(filepath)


def generate_api_error_warning_html(api_errors):
    """Generate warning banner for API errors"""
    if not api_errors:
        return ""

    total_errors = api_errors.get("total_errors", 0)
    critical_errors = api_errors.get("critical_errors", [])
    errors_by_type = api_errors.get("errors_by_type", {})
    failed_endpoints = api_errors.get("failed_endpoints", [])

    # Determine severity
    has_auth_errors = any(str(k) in ["401", "403"] for k in errors_by_type.keys())
    severity_color = "#dc2626" if has_auth_errors else "#f59e0b"
    severity_bg = "#fef2f2" if has_auth_errors else "#fffbeb"

    # Build error list
    error_list_html = ""
    if failed_endpoints:
        error_list_html = "<h3 style='color: #92400e; font-size: 1.1em; margin-top: 15px;'>Missing Data From:</h3><ul style='color: #92400e; margin-left: 20px;'>"
        for endpoint in failed_endpoints[:10]:  # Show max 10
            # Make endpoint names more user-friendly
            friendly_name = (
                endpoint.replace("s/default/", "").replace("s/home/", "").replace("stat/", "")
            )
            if "device" in friendly_name:
                friendly_name = "Access Points & Devices"
            elif "sta" in friendly_name:
                friendly_name = "Connected Clients"
            elif "event" in friendly_name:
                friendly_name = "Network Events & History"
            elif "health" in friendly_name:
                friendly_name = "Health Metrics"
            error_list_html += f"<li>• {friendly_name}</li>"
        error_list_html += "</ul>"

        if len(failed_endpoints) > 10:
            error_list_html += f"<p style='color: #92400e; margin-left: 20px;'><em>...and {len(failed_endpoints) - 10} more endpoints</em></p>"

    # Build error type summary
    error_types_html = ""
    if errors_by_type:
        error_types_html = (
            "<div style='margin-top: 15px; padding: 10px; background: white; border-radius: 4px;'>"
        )
        for error_type, errors in errors_by_type.items():
            count = len(errors)
            if error_type == "401":
                desc = "Unauthorized - Session expired or authentication failed"
                icon = "🔒"
            elif error_type == "403":
                desc = "Forbidden - Insufficient permissions for these features"
                icon = "⛔"
            elif error_type == "404":
                desc = "Not Found - Endpoints may not exist on this controller version"
                icon = "❓"
            elif error_type == "500":
                desc = "Server Error - Controller is experiencing issues"
                icon = "⚠️"
            else:
                desc = f"Error type: {error_type}"
                icon = "❌"

            error_types_html += f"<div style='margin-bottom: 8px;'><strong>{icon} {desc}</strong><br><span style='color: #666; font-size: 0.9em;'>Affected {count} API call(s)</span></div>"
        error_types_html += "</div>"

    return f"""
            <div class="section" style="background: {severity_bg}; border-left: 4px solid {severity_color}; padding: 20px; margin-bottom: 30px;">
                <h2 style="color: {severity_color}; margin-top: 0;">⚠️ Warning: Incomplete Analysis</h2>
                <p style="color: #92400e; font-size: 1.1em; margin-bottom: 15px;">
                    <strong>{total_errors} API call(s) failed during analysis.</strong> This report may be missing important information.
                </p>
                {error_types_html}
                {error_list_html}
                <div style="margin-top: 20px; padding: 15px; background: white; border-left: 3px solid #3b82f6; border-radius: 4px;">
                    <strong style="color: #1e40af;">💡 Recommended Actions:</strong>
                    <ul style="margin-top: 10px; color: #1e3a8a;">
                        {"<li>• <strong>Re-authenticate:</strong> Your session may have expired. Please log out and log back in.</li>" if has_auth_errors else ""}
                        {"<li>• <strong>Check Permissions:</strong> Ensure your user account has administrator/full access privileges.</li>" if has_auth_errors else ""}
                        <li>• <strong>Verify Network:</strong> Ensure the controller is reachable and not overloaded.</li>
                        <li>• <strong>Re-run Analysis:</strong> Once issues are resolved, generate a new report for complete data.</li>
                    </ul>
                </div>
            </div>
"""


def generate_quick_health_dashboard_html(analysis_data, recommendations):
    """Generate a visual health dashboard with key metrics"""

    # Get health score
    health_score = analysis_data.get("health_score", {})
    score = health_score.get("score", 0)
    grade = health_score.get("grade", "N/A")

    # Grade-based styling
    if grade == "A":
        grade_color = "#10b981"
        grade_bg = "#d1fae5"
        grade_emoji = "🟢"
    elif grade == "B":
        grade_color = "#3b82f6"
        grade_bg = "#dbeafe"
        grade_emoji = "🔵"
    elif grade == "C":
        grade_color = "#f59e0b"
        grade_bg = "#fef3c7"
        grade_emoji = "🟡"
    elif grade == "D":
        grade_color = "#f97316"
        grade_bg = "#ffedd5"
        grade_emoji = "🟠"
    else:
        grade_color = "#ef4444"
        grade_bg = "#fee2e2"
        grade_emoji = "🔴"

    # Count issues by severity
    critical_count = len(
        [r for r in recommendations if r.get("priority") == "high" or r.get("severity") == "high"]
    )
    warning_count = len(
        [
            r
            for r in recommendations
            if r.get("priority") == "medium" or r.get("severity") == "medium"
        ]
    )
    info_count = len(
        [r for r in recommendations if r.get("priority") == "low" or r.get("severity") == "low"]
    )

    # Get advanced metrics
    band_steering = analysis_data.get("band_steering_analysis", {})
    wifi7_clients = band_steering.get("wifi7_clients_suboptimal", 0)
    tri_band_clients = band_steering.get("tri_band_clients_suboptimal", 0)
    misplaced_clients = band_steering.get("dual_band_clients_on_2ghz", 0)

    airtime_analysis = analysis_data.get("airtime_analysis", {})
    saturated_aps = len(airtime_analysis.get("saturated_aps", []))

    dfs_analysis = analysis_data.get("dfs_analysis", {})
    dfs_events = dfs_analysis.get("total_events", 0)

    # Build key findings
    key_findings_html = ""
    if wifi7_clients > 0 or tri_band_clients > 0 or saturated_aps > 0 or dfs_events > 0:
        key_findings_html = "<div style='margin-top: 20px;'><h3 style='margin-bottom: 10px; color: #374151;'>Key Findings:</h3>"

        if wifi7_clients > 0:
            key_findings_html += f"""
                <div style='background: #f3e8ff; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #a855f7;'>
                    <span style='font-size: 1.2em;'>📡</span>
                    <strong style='color: #7c3aed;'>{wifi7_clients} WiFi 7 capable clients</strong> on suboptimal bands
                    <div style='font-size: 0.9em; color: #6b21a8; margin-top: 4px;'>Could benefit from 6GHz with 320MHz channels and MLO</div>
                </div>
            """
        elif tri_band_clients > 0:
            key_findings_html += f"""
                <div style='background: #ede9fe; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #8b5cf6;'>
                    <span style='font-size: 1.2em;'>📡</span>
                    <strong style='color: #7c3aed;'>{tri_band_clients} WiFi 6E capable clients</strong> on suboptimal bands
                    <div style='font-size: 0.9em; color: #6b21a8; margin-top: 4px;'>Could benefit from less congested 6GHz band</div>
                </div>
            """

        if saturated_aps > 0:
            key_findings_html += f"""
                <div style='background: #fee2e2; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #ef4444;'>
                    <span style='font-size: 1.2em;'>📶</span>
                    <strong style='color: #dc2626;'>{saturated_aps} APs with high airtime utilization</strong>
                    <div style='font-size: 0.9em; color: #991b1b; margin-top: 4px;'>May cause performance degradation</div>
                </div>
            """

        if dfs_events > 0:
            key_findings_html += f"""
                <div style='background: #ffedd5; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #f97316;'>
                    <span style='font-size: 1.2em;'>⚠️</span>
                    <strong style='color: #ea580c;'>{dfs_events} DFS radar interference events</strong>
                    <div style='font-size: 0.9em; color: #9a3412; margin-top: 4px;'>May cause intermittent disconnections</div>
                </div>
            """

        key_findings_html += "</div>"

    return f"""
            <div class="section" style="background: linear-gradient(135deg, {grade_bg} 0%, #ffffff 100%); padding: 30px; border-radius: 12px; margin-bottom: 30px; border: 2px solid {grade_color};">
                <div style="text-align: center; margin-bottom: 25px;">
                    <div style="display: inline-block; background: white; padding: 20px 40px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                        <div style="font-size: 3em; margin-bottom: 10px;">{grade_emoji}</div>
                        <div style="font-size: 2.5em; font-weight: bold; color: {grade_color};">Grade {grade}</div>
                        <div style="font-size: 1.2em; color: #6b7280; margin-top: 5px;">{score}/100</div>
                    </div>
                </div>

                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px;">
                    {"" if critical_count == 0 else f'''
                    <div style="background: #fee2e2; padding: 15px; border-radius: 8px; text-align: center; border-left: 4px solid #ef4444;">
                        <div style="font-size: 2em; margin-bottom: 5px;">🔴</div>
                        <div style="font-size: 2em; font-weight: bold; color: #dc2626;">{critical_count}</div>
                        <div style="color: #991b1b; font-weight: 500;">Critical Issues</div>
                    </div>
                    '''}

                    {"" if warning_count == 0 else f'''
                    <div style="background: #fef3c7; padding: 15px; border-radius: 8px; text-align: center; border-left: 4px solid #f59e0b;">
                        <div style="font-size: 2em; margin-bottom: 5px;">🟡</div>
                        <div style="font-size: 2em; font-weight: bold; color: #d97706;">{warning_count}</div>
                        <div style="color: #92400e; font-weight: 500;">Warnings</div>
                    </div>
                    '''}

                    {"" if info_count == 0 else f'''
                    <div style="background: #dbeafe; padding: 15px; border-radius: 8px; text-align: center; border-left: 4px solid #3b82f6;">
                        <div style="font-size: 2em; margin-bottom: 5px;">🔵</div>
                        <div style="font-size: 2em; font-weight: bold; color: #2563eb;">{info_count}</div>
                        <div style="color: #1e3a8a; font-weight: 500;">Info</div>
                    </div>
                    '''}

                    {"" if critical_count > 0 or warning_count > 0 or info_count > 0 else '''
                    <div style="background: #d1fae5; padding: 15px; border-radius: 8px; text-align: center; border-left: 4px solid #10b981; grid-column: 1 / -1;">
                        <div style="font-size: 2em; margin-bottom: 5px;">✨</div>
                        <div style="font-size: 1.3em; font-weight: bold; color: #059669;">No Issues Found</div>
                        <div style="color: #065f46;">Network is optimized!</div>
                    </div>
                    '''}
                </div>

                {key_findings_html}
            </div>
"""


def generate_executive_summary_html(analysis_data, recommendations):
    """Generate executive summary section"""
    findings_count = len(analysis_data.get("findings", []))
    recommendations_count = len(recommendations)

    # Get infrastructure counts
    devices = analysis_data.get("devices", [])
    ap_count = len([d for d in devices if d.get("type") == "uap"])
    switch_count = len([d for d in devices if d.get("type") == "usw"])

    # Get mesh topology counts
    mesh_aps = []
    mesh_parent_macs = set()
    ap_macs = {}  # Build MAC to device lookup for APs only
    all_device_macs = {}  # Build MAC lookup for all devices

    # First pass: identify mesh children and collect device MACs
    for device in devices:
        mac = device.get("mac")
        if mac:
            all_device_macs[mac] = device
            if device.get("type") == "uap":
                ap_macs[mac] = device

        if device.get("type") == "uap":
            uplink_type = device.get("uplink", {}).get("type", "")
            uplink_rssi = device.get("uplink", {}).get("rssi")
            is_mesh = device.get("adopted", False) and (
                uplink_type == "wireless" or (uplink_rssi and uplink_rssi < -70)
            )
            if is_mesh:
                mesh_aps.append(device)
                parent_mac = device.get("uplink", {}).get("uplink_remote_mac")
                if parent_mac:
                    mesh_parent_macs.add(parent_mac)

    # Second pass: count parent devices (could be APs or gateways)
    # Only count as "parent AP" if it's actually an AP type device
    mesh_parent_count = len([mac for mac in mesh_parent_macs if mac in ap_macs])

    # If parent is a gateway (UDM, UDM Pro, etc), we still protect it but call it differently
    mesh_parent_gateways = len(
        [mac for mac in mesh_parent_macs if mac in all_device_macs and mac not in ap_macs]
    )

    mesh_child_count = len(mesh_aps)
    total_mesh_protected = mesh_child_count + mesh_parent_count

    # Get switch issues
    switch_analysis = analysis_data.get("switch_analysis", {})
    switch_issues = len(switch_analysis.get("issues", []))
    switch_high_issues = len(
        [i for i in switch_analysis.get("issues", []) if i.get("severity") == "high"]
    )

    # Get wireless issues
    airtime = analysis_data.get("airtime_analysis", {})
    saturated_aps = len(airtime.get("saturated_aps", []))

    # Build infrastructure summary
    infra_summary = f"{ap_count} access point{'s' if ap_count != 1 else ''}"
    if mesh_child_count > 0:
        infra_summary += (
            f" (including {mesh_child_count} mesh node{'s' if mesh_child_count != 1 else ''})"
        )
    if switch_count > 0:
        infra_summary += f" and {switch_count} managed switch{'es' if switch_count != 1 else ''}"

    # Build issues summary
    issues_parts = []
    if findings_count > 0:
        issues_parts.append(
            f"{findings_count} wireless finding{'s' if findings_count != 1 else ''}"
        )
    if switch_issues > 0:
        issues_parts.append(f"{switch_issues} switch issue{'s' if switch_issues != 1 else ''}")

    issues_summary = " and ".join(issues_parts) if issues_parts else "no critical issues"

    # Critical issues highlight
    critical_highlights = []
    if switch_high_issues > 0:
        critical_highlights.append(
            f"{switch_high_issues} high-severity switch port issue{'s' if switch_high_issues != 1 else ''}"
        )
    if saturated_aps > 0:
        critical_highlights.append(
            f"{saturated_aps} saturated AP{'s' if saturated_aps != 1 else ''}"
        )

    critical_text = ""
    if critical_highlights:
        critical_text = f" Critical issues include {' and '.join(critical_highlights)}."

    # Build impact statement based on what issues exist
    impact_parts = []

    # Wireless improvements
    if findings_count > 0 or saturated_aps > 0:
        impact_parts.append("reduce wireless interference")
        if mesh_child_count > 0:
            impact_parts.append("maintain reliable mesh backhaul")
        impact_parts.append("optimize coverage patterns")

    # Physical infrastructure improvements
    if switch_issues > 0:
        all_switch_issues = switch_analysis.get("issues", [])
        slow_links = [i for i in all_switch_issues if i.get("type") == "slow_link"]
        dropped_packets = [i for i in all_switch_issues if i.get("type") == "dropped_packets"]
        high_errors = [i for i in all_switch_issues if i.get("type") == "high_errors"]
        half_duplex = [i for i in all_switch_issues if i.get("type") == "half_duplex"]

        if slow_links:
            impact_parts.append("restore full gigabit speeds on degraded ports")
        if dropped_packets or high_errors:
            impact_parts.append("eliminate packet loss and network instability")
        if half_duplex:
            impact_parts.append("fix duplex mismatches")

    impact_statement = ", ".join(impact_parts[:-1]) + (
        f", and {impact_parts[-1]}"
        if len(impact_parts) > 1
        else impact_parts[0] if impact_parts else "improve network performance"
    )

    # Build mesh protection note if applicable
    mesh_note = ""
    if total_mesh_protected > 0:
        # Build parent description
        total_parents = mesh_parent_count + mesh_parent_gateways

        if mesh_parent_count > 0 and mesh_parent_gateways > 0:
            parent_desc = f"{mesh_parent_count} parent AP{'s' if mesh_parent_count != 1 else ''} and {mesh_parent_gateways} parent gateway{'s' if mesh_parent_gateways != 1 else ''}"
        elif mesh_parent_count > 0:
            parent_desc = f"{mesh_parent_count} parent AP{'s' if mesh_parent_count != 1 else ''}"
        elif mesh_parent_gateways > 0:
            parent_desc = (
                f"{mesh_parent_gateways} parent gateway{'s' if mesh_parent_gateways != 1 else ''}"
            )
        else:
            parent_desc = "parent devices"

        # Total protected includes mesh children + parent APs (but not parent gateways, as we don't reduce their power anyway)
        total_aps_protected = mesh_child_count + mesh_parent_count

        mesh_note = f"""
                <p style="background: #ecfdf5; border-left: 4px solid #10b981; padding: 12px; margin-top: 12px; border-radius: 4px; color: #065f46;">
                    <strong style="color: #047857;">🛡️ Mesh Network Protection:</strong> Your network includes a mesh topology with {mesh_child_count} mesh child node{'s' if mesh_child_count != 1 else ''} connecting to {parent_desc}. All {total_aps_protected} mesh-related AP{'s' if total_aps_protected != 1 else ''} are protected from power reduction to maintain reliable wireless backhaul.
                </p>
"""

    return f"""
            <div class="executive-summary">
                <h2>📊 Executive Summary</h2>
                <p>
                    <strong>Network Health Assessment:</strong> Your network analysis identified
                    <strong>{issues_summary}</strong> across your UniFi infrastructure ({infra_summary}).
                </p>
                <p>
                    Based on 3-day historical data and current configuration, we recommend
                    <strong>{recommendations_count} optimization actions</strong> to improve network performance,
                    reliability, and client experience.
                </p>
                <p>
                    <strong>Expected Impact:</strong> Implementing these recommendations will {impact_statement}.{critical_text}
                </p>
                {mesh_note}
            </div>
"""


def generate_key_metrics_html(analysis_data, client_health):
    """Generate key metrics cards"""
    # Get AP count from multiple possible sources
    ap_analysis = analysis_data.get("ap_analysis", {})
    ap_count = ap_analysis.get("total_aps", 0)
    if ap_count == 0:
        ap_count = len(ap_analysis.get("access_points", []))
    if ap_count == 0:
        # Try counting from devices
        devices = analysis_data.get("devices", [])
        ap_count = len([d for d in devices if d.get("type") == "uap"])

    # Get client count
    client_analysis = analysis_data.get("client_analysis", {})
    client_count = client_analysis.get("total_clients", 0)
    if client_count == 0:
        client_count = len(client_health.get("health_scores", []))
    if client_count == 0:
        # Try counting from clients
        clients = analysis_data.get("clients", [])
        client_count = len(clients)

    # Get mesh count
    mesh_aps = ap_analysis.get("mesh_aps", [])
    mesh_count = len(mesh_aps)

    # Get issues count from multiple sources
    issues_count = len(
        [f for f in analysis_data.get("findings", []) if f.get("severity") in ["high", "critical"]]
    )

    # Also count from recommendations
    recommendations = analysis_data.get("recommendations", [])
    high_priority_recs = len([r for r in recommendations if r.get("priority") == "high"])

    # Also count from advanced analysis
    dfs_analysis = analysis_data.get("dfs_analysis", {})
    if dfs_analysis.get("severity") == "high":
        issues_count += 1

    band_steering = analysis_data.get("band_steering_analysis", {})
    if band_steering.get("severity") == "high":
        issues_count += 1

    airtime = analysis_data.get("airtime_analysis", {})
    saturated_count = len(airtime.get("saturated_aps", []))
    issues_count += saturated_count

    # Get switch metrics
    switch_analysis = analysis_data.get("switch_analysis", {})
    switches = switch_analysis.get("switches", [])
    switch_count = len(switches)

    # Count switch ports and issues
    total_switch_ports = sum(s.get("active_ports", 0) for s in switches)
    switch_issues = len(switch_analysis.get("issues", []))
    issues_count += len(
        [i for i in switch_analysis.get("issues", []) if i.get("severity") == "high"]
    )

    # Build stats grid with conditional switch card
    stats_cards = f"""
                    <div class="stat-card">
                        <div class="value">{ap_count}</div>
                        <div class="label">Access Points</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{client_count}</div>
                        <div class="label">Active Clients</div>
                    </div>
"""

    if mesh_count > 0:
        stats_cards += f"""
                    <div class="stat-card" style="background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border-left: 4px solid #f59e0b;">
                        <div class="value">🔗 {mesh_count}</div>
                        <div class="label">Mesh Nodes</div>
                    </div>
"""
    elif switch_count > 0:
        stats_cards += f"""
                    <div class="stat-card">
                        <div class="value">{total_switch_ports}</div>
                        <div class="label">Switch Ports Active</div>
                    </div>
"""
    else:
        stats_cards += f"""
                    <div class="stat-card">
                        <div class="value">0</div>
                        <div class="label">Mesh APs</div>
                    </div>
"""

    stats_cards += f"""
                    <div class="stat-card">
                        <div class="value">{issues_count}</div>
                        <div class="label">Critical Issues</div>
                    </div>
"""

    return f"""
            <div class="section">
                <h2>📈 Key Metrics</h2>
                <div class="stats-grid">
{stats_cards}
                </div>
            </div>
"""


def generate_network_health_html(health_analysis, health_score=None):
    """Generate comprehensive network health analysis section

    Args:
        health_analysis: Issue-based health analysis
        health_score: Grade-based health score (A-F) for consistency
    """
    if not health_analysis:
        return ""

    categories = health_analysis.get("categories", {})
    issues = health_analysis.get("issues", [])
    recommendations = health_analysis.get("recommendations", [])

    # Use Grade-based scoring for consistency if available
    if health_score and health_score.get("score") is not None:
        overall_score = health_score.get("score", 0)
        grade = health_score.get("grade", "N/A")
        status_text = health_score.get("status", "Unknown")

        # Color code based on Grade for consistency
        if grade == "A":
            score_color = "#10b981"
            score_bg = "#d1fae5"
            status_emoji = "✅"
        elif grade == "B":
            score_color = "#3b82f6"
            score_bg = "#dbeafe"
            status_emoji = "🔵"
        elif grade == "C":
            score_color = "#f59e0b"
            score_bg = "#fef3c7"
            status_emoji = "⚠️"
        elif grade == "D":
            score_color = "#f97316"
            score_bg = "#ffedd5"
            status_emoji = "🟠"
        else:  # F or N/A
            score_color = "#ef4444"
            score_bg = "#fee2e2"
            status_emoji = "🔴"

        grade_display = f" (Grade {grade})"
    else:
        # Fallback to old simple scoring if Grade not available
        overall_score = health_analysis.get("overall_score", 100)
        status_text = "Unknown"
        grade_display = ""

        if overall_score >= 90:
            score_color = "#10b981"
            score_bg = "#d1fae5"
            status_text = "Excellent"
            status_emoji = "✅"
        elif overall_score >= 75:
            score_color = "#f59e0b"
            score_bg = "#fef3c7"
            status_text = "Good"
            status_emoji = "⚠️"
        elif overall_score >= 50:
            score_color = "#ef4444"
            score_bg = "#fee2e2"
            status_text = "Fair"
            status_emoji = "🔴"
        else:
            score_color = "#dc2626"
            score_bg = "#fecaca"
            status_text = "Poor"
            status_emoji = "🔴"

    # Build HTML
    html = f"""
            <div class="section">
                <h2>🏥 Network Health Analysis</h2>

                <!-- Overall Score -->
                <div style="background: {score_bg}; padding: 30px; border-radius: 12px; margin-bottom: 30px; border-left: 6px solid {score_color};">
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 20px;">
                        <div>
                            <div style="font-size: 3em; font-weight: bold; color: {score_color}; margin-bottom: 10px;">
                                {status_emoji} {overall_score}/100{grade_display}
                            </div>
                            <div style="font-size: 1.3em; color: {score_color}; font-weight: 600;">
                                Overall Network Health: {status_text}
                            </div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-size: 0.9em; color: #666;">
                                {len(issues)} total issues detected<br/>
                                {len([i for i in issues if i.get('severity') == 'high'])} critical issues
                            </div>
                        </div>
                    </div>
                </div>
"""

    # Category Summary Cards
    html += """
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px;">
"""

    for category_name, category_data in categories.items():
        category_display = category_data.get("category", category_name.replace("_", " ").title())
        category_status = category_data.get("status", "healthy")
        category_issues = category_data.get("issues", [])

        # Status colors
        if category_status == "critical":
            card_color = "#ef4444"
            card_bg = "#fee2e2"
            status_icon = "🔴"
        elif category_status == "warning":
            card_color = "#f59e0b"
            card_bg = "#fef3c7"
            status_icon = "⚠️"
        else:
            card_color = "#10b981"
            card_bg = "#d1fae5"
            status_icon = "✅"

        issue_count = len(category_issues)

        html += f"""
                    <div style="background: white; border: 2px solid {card_color}; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        <div style="font-size: 2em; margin-bottom: 10px;">{status_icon}</div>
                        <div style="font-weight: bold; font-size: 1.1em; margin-bottom: 5px; color: {card_color};">
                            {category_display}
                        </div>
                        <div style="color: #666; font-size: 0.9em;">
                            {issue_count} issue(s)
                        </div>
                    </div>
"""

    html += """
                </div>
"""

    # Critical Issues
    critical_issues = [i for i in issues if i.get("severity") == "high"]
    if critical_issues:
        html += """
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #ef4444; margin-bottom: 15px;">🔴 Critical Issues Requiring Immediate Attention</h3>
"""

        for issue in critical_issues:
            device = issue.get("device", issue.get("switch", "Network"))
            message = issue.get("message", "Unknown issue")
            impact = issue.get("impact", "")
            recommendation = issue.get("recommendation", "")

            html += f"""
                    <div style="background: #fee2e2; border-left: 4px solid #ef4444; padding: 20px; margin-bottom: 15px; border-radius: 8px;">
                        <div style="font-weight: bold; color: #dc2626; margin-bottom: 8px; font-size: 1.05em;">
                            {device}: {message}
                        </div>
                        {f'<div style="color: #666; margin-bottom: 8px;"><em>Impact:</em> {impact}</div>' if impact else ''}
                        {f'<div style="color: #444;"><strong>Action:</strong> {recommendation}</div>' if recommendation else ''}
                    </div>
"""

        html += """
                </div>
"""

    # Medium Priority Issues
    medium_issues = [i for i in issues if i.get("severity") == "medium"]
    if medium_issues:
        html += """
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #f59e0b; margin-bottom: 15px;">⚠️ Issues Requiring Attention</h3>
                    <div style="max-height: 400px; overflow-y: auto;">
"""

        for issue in medium_issues[:10]:  # Show top 10
            device = issue.get("device", issue.get("switch", issue.get("ap", "Network")))
            message = issue.get("message", "Unknown issue")
            recommendation = issue.get("recommendation", "")

            html += f"""
                        <div style="background: #fffbeb; border-left: 4px solid #f59e0b; padding: 15px; margin-bottom: 10px; border-radius: 8px;">
                            <div style="font-weight: 600; color: #b45309; margin-bottom: 5px;">
                                {device}: {message}
                            </div>
                            {f'<div style="color: #666; font-size: 0.9em;">{recommendation}</div>' if recommendation else ''}
                        </div>
"""

        if len(medium_issues) > 10:
            html += f"""
                        <div style="text-align: center; padding: 10px; color: #666;">
                            ... and {len(medium_issues) - 10} more issues
                        </div>
"""

        html += """
                    </div>
                </div>
"""

    # Recommendations
    if recommendations:
        html += """
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #6366f1; margin-bottom: 15px;">💡 Recommendations</h3>
"""

        high_priority_recs = [r for r in recommendations if r.get("priority") == "high"]
        other_recs = [r for r in recommendations if r.get("priority") != "high"]

        if high_priority_recs:
            html += """
                    <div style="margin-bottom: 20px;">
                        <h4 style="color: #ef4444; margin-bottom: 10px;">High Priority:</h4>
"""
            for rec in high_priority_recs:
                html += f"""
                        <div style="background: #f9fafb; border-left: 3px solid #ef4444; padding: 15px; margin-bottom: 10px; border-radius: 6px;">
                            <div style="font-weight: 600; margin-bottom: 5px;">{rec.get('message', '')}</div>
                            <div style="color: #666; font-size: 0.9em;">{rec.get('action', '')}</div>
                        </div>
"""
            html += """
                    </div>
"""

        if other_recs:
            html += """
                    <div>
                        <h4 style="color: #6366f1; margin-bottom: 10px;">Additional Recommendations:</h4>
"""
            for rec in other_recs[:5]:  # Show top 5
                html += f"""
                        <div style="background: #f9fafb; border-left: 3px solid #6366f1; padding: 15px; margin-bottom: 10px; border-radius: 6px;">
                            <div style="font-weight: 600; margin-bottom: 5px;">{rec.get('message', '')}</div>
                            <div style="color: #666; font-size: 0.9em;">{rec.get('action', '')}</div>
                        </div>
"""
            html += """
                    </div>
"""

        html += """
                </div>
"""

    html += """
            </div>
"""

    return html


def generate_rssi_distribution_html(signal_distribution):
    """Generate RSSI distribution chart"""
    if not signal_distribution:
        return ""

    excellent = signal_distribution.get("excellent", 0)
    good = signal_distribution.get("good", 0)
    fair = signal_distribution.get("fair", 0)
    poor = signal_distribution.get("poor", 0)
    very_poor = signal_distribution.get("very_poor", 0)
    wired = signal_distribution.get("wired", 0)

    total = excellent + good + fair + poor + very_poor
    max_val = max(excellent, good, fair, poor, very_poor, wired, 1)

    def get_width(val):
        return int((val / max_val) * 100) if max_val > 0 else 0

    return f"""
            <div class="section">
                <h2>📡 Client Signal Distribution</h2>
                <div class="rssi-chart">
                    <div class="rssi-bar">
                        <span class="label">Excellent (-50 or better)</span>
                        <div class="bar-container">
                            <div class="bar excellent" style="width: {get_width(excellent)}%">{excellent if excellent > 0 else ''}</div>
                        </div>
                        <span class="count">{excellent}</span>
                    </div>
                    <div class="rssi-bar">
                        <span class="label">Good (-60 to -50)</span>
                        <div class="bar-container">
                            <div class="bar good" style="width: {get_width(good)}%">{good if good > 0 else ''}</div>
                        </div>
                        <span class="count">{good}</span>
                    </div>
                    <div class="rssi-bar">
                        <span class="label">Fair (-70 to -60)</span>
                        <div class="bar-container">
                            <div class="bar fair" style="width: {get_width(fair)}%">{fair if fair > 0 else ''}</div>
                        </div>
                        <span class="count">{fair}</span>
                    </div>
                    <div class="rssi-bar">
                        <span class="label">Poor (-80 to -70)</span>
                        <div class="bar-container">
                            <div class="bar poor" style="width: {get_width(poor)}%">{poor if poor > 0 else ''}</div>
                        </div>
                        <span class="count">{poor}</span>
                    </div>
                    <div class="rssi-bar">
                        <span class="label">Very Poor (below -80)</span>
                        <div class="bar-container">
                            <div class="bar very-poor" style="width: {get_width(very_poor)}%">{very_poor if very_poor > 0 else ''}</div>
                        </div>
                        <span class="count">{very_poor}</span>
                    </div>
                    <div class="rssi-bar">
                        <span class="label">Wired Clients</span>
                        <div class="bar-container">
                            <div class="bar wired" style="width: {get_width(wired)}%">{wired if wired > 0 else ''}</div>
                        </div>
                        <span class="count">{wired}</span>
                    </div>
                </div>
            </div>
"""


def generate_mesh_topology_html(mesh_aps, all_devices):
    """
    Generate beautiful mesh topology visualization showing parent-child relationships
    """
    mesh_ap_list = mesh_aps.get("mesh_aps", [])
    if not mesh_ap_list:
        return ""

    # Build device lookup for parent identification
    device_by_mac = {d.get("mac"): d for d in all_devices if d.get("mac")}

    # Identify parent-child relationships
    topology_data = []
    parent_aps = set()

    for mesh_ap in mesh_ap_list:
        uplink_remote_mac = mesh_ap.get("uplink_remote_mac")
        uplink_rssi = mesh_ap.get("uplink_rssi")
        ap_name = mesh_ap.get("name", "Unknown")

        parent_ap = None
        parent_name = "Unknown"
        if uplink_remote_mac and uplink_remote_mac in device_by_mac:
            parent_ap = device_by_mac[uplink_remote_mac]
            parent_name = parent_ap.get("name", "Unknown Parent")
            parent_aps.add(uplink_remote_mac)

        topology_data.append(
            {
                "child_name": ap_name,
                "parent_name": parent_name,
                "uplink_rssi": uplink_rssi,
                "parent_mac": uplink_remote_mac,
                "is_mesh": True,
            }
        )

    # Generate HTML
    html = """
        <div class="section" style="background: linear-gradient(135deg, #667eea15 0%, #764ba215 100%); border-left: 4px solid #667eea;">
            <h2 style="display: flex; align-items: center; gap: 10px;">
                <span style="font-size: 1.8em;">🔗</span>
                <span>Mesh Network Topology</span>
            </h2>
            <p style="color: #666; margin-bottom: 25px; font-size: 1.05em;">
                Visualizing wireless mesh connections between access points. Both parent and child APs maintain HIGH power for optimal bidirectional communication.
            </p>
"""

    # Create topology cards
    for topo in topology_data:
        child_name = topo["child_name"]
        parent_name = topo["parent_name"]
        uplink_rssi = topo["uplink_rssi"]

        # Determine signal quality and color
        if uplink_rssi and uplink_rssi > -65:
            signal_quality = "Excellent"
            signal_color = "#10b981"
            signal_icon = "🟢"
            signal_bg = "#d1fae5"
        elif uplink_rssi and uplink_rssi > -70:
            signal_quality = "Good"
            signal_color = "#3b82f6"
            signal_icon = "🔵"
            signal_bg = "#dbeafe"
        elif uplink_rssi and uplink_rssi > -75:
            signal_quality = "Fair"
            signal_color = "#f59e0b"
            signal_icon = "🟡"
            signal_bg = "#fef3c7"
        else:
            signal_quality = "Weak"
            signal_color = "#ef4444"
            signal_icon = "🔴"
            signal_bg = "#fee2e2"

        rssi_display = f"{uplink_rssi} dBm" if uplink_rssi else "Unknown"

        html += f"""
            <div style="background: white; padding: 25px; border-radius: 12px; margin: 20px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border: 1px solid #e5e7eb;">
                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 20px;">
                    <!-- Parent AP -->
                    <div style="flex: 1; min-width: 200px;">
                        <div style="background: #f0f9ff; padding: 15px; border-radius: 8px; border-left: 4px solid #3b82f6;">
                            <div style="color: #6b7280; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 5px; font-weight: 600;">
                                📡 Parent AP
                            </div>
                            <div style="font-size: 1.3em; font-weight: 700; color: #1e40af; margin-bottom: 8px;">
                                {parent_name}
                            </div>
                            <div style="background: #3b82f614; color: #1e40af; padding: 6px 12px; border-radius: 20px; display: inline-block; font-size: 0.85em; font-weight: 600;">
                                🔋 HIGH Power (TX → Child)
                            </div>
                        </div>
                    </div>

                    <!-- Connection Arrow -->
                    <div style="flex: 0 0 auto; text-align: center; padding: 0 15px;">
                        <div style="font-size: 2.5em; line-height: 1;">→</div>
                        <div style="margin-top: 8px; background: {signal_bg}; color: {signal_color}; padding: 8px 16px; border-radius: 20px; white-space: nowrap; font-weight: 600; font-size: 0.9em;">
                            {signal_icon} {rssi_display}
                        </div>
                        <div style="margin-top: 5px; color: {signal_color}; font-size: 0.85em; font-weight: 600;">
                            {signal_quality}
                        </div>
                    </div>

                    <!-- Child AP (Mesh) -->
                    <div style="flex: 1; min-width: 200px;">
                        <div style="background: #fef3c7; padding: 15px; border-radius: 8px; border-left: 4px solid #f59e0b;">
                            <div style="color: #6b7280; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 5px; font-weight: 600;">
                                🔗 Mesh AP (Child)
                            </div>
                            <div style="font-size: 1.3em; font-weight: 700; color: #92400e; margin-bottom: 8px;">
                                {child_name}
                            </div>
                            <div style="background: #f59e0b14; color: #92400e; padding: 6px 12px; border-radius: 20px; display: inline-block; font-size: 0.85em; font-weight: 600;">
                                🔋 HIGH Power (TX → Parent)
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Connection Info -->
                <div style="margin-top: 20px; padding: 15px; background: #f9fafb; border-radius: 8px; border-left: 3px solid {signal_color};">
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                        <div>
                            <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 4px;">📶 Uplink Signal</div>
                            <div style="font-weight: 600; color: {signal_color};">{rssi_display} ({signal_quality})</div>
                        </div>
                        <div>
                            <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 4px;">⚡ Connection Type</div>
                            <div style="font-weight: 600; color: #374151;">Wireless Mesh</div>
                        </div>
                        <div>
                            <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 4px;">🛡️ Power Protection</div>
                            <div style="font-weight: 600; color: #10b981;">Both APs Protected</div>
                        </div>
                    </div>
                </div>
            </div>
"""

    # Add protection summary
    total_protected = len(topology_data) + len(parent_aps)
    html += f"""
            <div style="background: #ecfdf5; padding: 20px; border-radius: 10px; border-left: 4px solid #10b981; margin-top: 25px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="font-size: 2.5em;">🛡️</div>
                    <div>
                        <div style="font-size: 1.2em; font-weight: 700; color: #047857; margin-bottom: 5px;">
                            Comprehensive Mesh Protection Active
                        </div>
                        <div style="color: #065f46; font-size: 0.95em; line-height: 1.6;">
                            <strong>{total_protected} APs protected</strong> from power reduction:
                            • {len(topology_data)} mesh child APs need HIGH power for TX back to parent<br>
                            • {len(parent_aps)} parent APs need HIGH power for TX to mesh children<br>
                            • Bidirectional signal strength (TX + RX) ensures reliable mesh connections
                        </div>
                    </div>
                </div>
            </div>
        </div>
"""

    return html


def generate_ap_overview_html(ap_analysis, mesh_aps, all_devices=None):
    """Generate access points overview table with channel and power details"""
    aps = ap_analysis.get("access_points", [])
    if not aps:
        return ""

    # Build device lookup for mesh parent detection
    device_by_mac = {}
    if all_devices:
        device_by_mac = {d.get("mac"): d for d in all_devices if d.get("mac")}

    # Identify which APs are mesh parents
    mesh_parent_macs = set()
    if all_devices:
        for device in all_devices:
            uplink = device.get("uplink", {})
            if uplink.get("type") == "wireless":
                parent_mac = uplink.get("uplink_remote_mac")
                if parent_mac:
                    mesh_parent_macs.add(parent_mac)

    rows = ""
    for ap in aps:
        name = ap.get("name", "Unknown")
        model = ap.get("model", "Unknown")
        ap_mac = ap.get("mac")
        is_mesh = ap.get("_id") in [m.get("_id") for m in mesh_aps.get("mesh_aps", [])]
        is_mesh_parent = ap_mac in mesh_parent_macs
        clients_ng = ap.get("num_sta", {}).get("ng", 0)
        clients_na = ap.get("num_sta", {}).get("na", 0)
        total_clients = clients_ng + clients_na

        # Get radio details
        radio_table = ap.get("radio_table", [])
        ng_channel = ""
        ng_power = ""
        ng_width = ""
        na_channel = ""
        na_power = ""
        na_width = ""

        for radio in radio_table:
            radio_name = radio.get("radio", "")
            channel = radio.get("channel", "")
            power = radio.get("tx_power_mode", "auto")
            ht = radio.get("ht", 20)

            if radio_name == "ng":  # 2.4GHz
                ng_channel = f"Ch {channel}"
                ng_power = power.capitalize() if power else "Auto"
                ng_width = f"{ht}MHz"
            elif radio_name == "na":  # 5GHz
                na_channel = f"Ch {channel}"
                na_power = power.capitalize() if power else "Auto"
                na_width = f"{ht}MHz"

        # Enhanced badges with styling
        badges = ""
        if is_mesh:
            badges += '<span style="background: #fef3c7; color: #92400e; padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: 600; margin-left: 8px;">🔗 Mesh Child</span>'
        if is_mesh_parent:
            badges += '<span style="background: #dbeafe; color: #1e40af; padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: 600; margin-left: 8px;">📡 Mesh Parent</span>'

        # Power protection indicator
        power_protection = ""
        if is_mesh or is_mesh_parent:
            power_protection = '<div style="margin-top: 6px; color: #10b981; font-size: 0.85em;">🛡️ Power Protected</div>'

        # Build 2.4GHz details with power highlighting
        ng_details = f"{ng_channel}" if ng_channel else "-"
        if ng_power or ng_width:
            power_color = (
                "#10b981"
                if ng_power == "High"
                else "#f59e0b" if ng_power == "Medium" else "#6b7280"
            )
            ng_details += (
                f"<br><span style='font-size: 0.85em; color: {power_color}; font-weight: 600;'>{ng_power}</span>"
                f"<span style='font-size: 0.85em; color: #666;'>, {ng_width}</span>"
            )

        # Build 5GHz details with power highlighting
        na_details = f"{na_channel}" if na_channel else "-"
        if na_power or na_width:
            power_color = (
                "#10b981"
                if na_power == "High"
                else "#f59e0b" if na_power == "Medium" else "#6b7280"
            )
            na_details += (
                f"<br><span style='font-size: 0.85em; color: {power_color}; font-weight: 600;'>{na_power}</span>"
                f"<span style='font-size: 0.85em; color: #666;'>, {na_width}</span>"
            )

        rows += f"""
                    <tr>
                        <td>
                            <strong>{name}</strong>{badges}
                            {power_protection}
                        </td>
                        <td>{model}</td>
                        <td style="text-align: center;">{total_clients}</td>
                        <td>
                            <div style="margin-bottom: 4px;"><strong>{clients_ng} clients</strong></div>
                            <div>{ng_details}</div>
                        </td>
                        <td>
                            <div style="margin-bottom: 4px;"><strong>{clients_na} clients</strong></div>
                            <div>{na_details}</div>
                        </td>
                    </tr>
"""

    return f"""
            <div class="section">
                <h2>📶 Access Points Overview</h2>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Access Point</th>
                            <th>Model</th>
                            <th>Total Clients</th>
                            <th>2.4GHz Details</th>
                            <th>5GHz Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
                <div style="margin-top: 10px; font-size: 0.9em; color: #666;">
                    <strong>Legend:</strong> Ch = Channel, Power levels (High/Medium/Low/Auto), Width in MHz
                </div>
            </div>
"""


def generate_firmware_analysis_html(firmware_analysis):
    """Generate firmware consistency analysis section"""
    total_aps = firmware_analysis.get("total_aps", 0)
    inconsistencies = firmware_analysis.get("inconsistencies", [])
    recommendations = firmware_analysis.get("recommendations", [])
    severity = firmware_analysis.get("severity", "ok")

    # Determine status color and icon
    if severity == "warning":
        status_color = "#f59e0b"
        status_icon = "⚠️"
        status_text = "Issues Detected"
    elif severity == "info":
        status_color = "#3b82f6"
        status_icon = "ℹ️"
        status_text = "Minor Issues"
    else:
        status_color = "#10b981"
        status_icon = "✅"
        status_text = "All Consistent"

    html = f"""
        <div class="section">
            <h2>🔧 Firmware Consistency Analysis</h2>
            <div class="health-score" style="background: {status_color}15; border-left: 4px solid {status_color}; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <div style="display: flex; align-items: center; gap: 15px;">
                    <div style="font-size: 3em;">{status_icon}</div>
                    <div>
                        <div style="font-size: 1.5em; font-weight: bold; color: {status_color};">{status_text}</div>
                        <div style="color: #666; margin-top: 5px;">{total_aps} AP(s) analyzed</div>
                    </div>
                </div>
            </div>
"""

    if not inconsistencies:
        html += """
            <div style="background: #f0fdf4; padding: 20px; border-radius: 8px; border-left: 4px solid #10b981;">
                <p style="color: #059669; font-size: 1.1em; margin: 0;">
                    <strong>✅ All APs are running consistent firmware versions within their models</strong>
                </p>
                <p style="color: #6b7280; margin-top: 10px;">
                    Uniform firmware improves network stability, ensures feature consistency, and simplifies troubleshooting.
                </p>
            </div>
        </div>
"""
        return html

    # Display inconsistencies
    html += """
        <h3 style="color: #f59e0b; margin-top: 20px;">Firmware Version Inconsistencies</h3>
        <p style="color: #666; margin-bottom: 20px;">
            Mixed firmware versions can cause subtle compatibility issues, inconsistent behavior, and make troubleshooting more difficult.
        </p>
"""

    for inc in inconsistencies:
        model = inc.get("model", "Unknown")
        versions_found = inc.get("versions_found", [])
        newest_version = inc.get("newest_version", "Unknown")
        outdated_count = inc.get("outdated_count", 0)
        total_count = inc.get("total_count", 0)
        details = inc.get("details", {})

        html += f"""
        <div style="background: white; padding: 20px; border-radius: 8px; margin: 15px 0; border: 1px solid #e5e7eb;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h4 style="color: #374151; margin: 0; font-size: 1.2em;">{model}</h4>
                <span style="background: #fef3c7; color: #d97706; padding: 5px 15px; border-radius: 20px; font-weight: 500; font-size: 0.9em;">
                    {outdated_count}/{total_count} outdated
                </span>
            </div>
            <div style="margin-bottom: 15px;">
                <strong style="color: #10b981;">Latest Version:</strong> {newest_version}
            </div>
"""

        for version, version_info in details.items():
            status = version_info.get("status", "unknown")
            aps = version_info.get("aps", [])
            count = version_info.get("count", 0)

            if status == "current":
                badge_color = "#10b981"
                badge_bg = "#d1fae5"
                badge_text = "✓ Current"
            else:
                badge_color = "#ef4444"
                badge_bg = "#fee2e2"
                badge_text = "⚠ Outdated"

            html += f"""
            <div style="padding: 10px; background: #f9fafb; border-radius: 6px; margin: 10px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-family: monospace; font-size: 0.95em; color: #374151;">{version}</span>
                    <span style="background: {badge_bg}; color: {badge_color}; padding: 3px 10px; border-radius: 12px; font-size: 0.85em; font-weight: 500;">
                        {badge_text}
                    </span>
                </div>
                <div style="margin-top: 8px; color: #6b7280; font-size: 0.9em;">
                    <strong>{count} AP(s):</strong> {", ".join(aps)}
                </div>
            </div>
"""

        html += """
        </div>
"""

    # Display recommendations
    if recommendations:
        html += """
        <h3 style="color: #374151; margin-top: 30px;">Recommended Actions</h3>
"""

        for rec in recommendations:
            rec_type = rec.get("type", "unknown")
            severity_level = rec.get("severity", "medium")
            action = rec.get("action", "")
            current_state = rec.get("current_state", "")
            target_state = rec.get("target_state", "")
            rationale = rec.get("rationale", [])
            aps_to_upgrade = rec.get("aps_to_upgrade", [])

            # Severity styling
            if severity_level == "high":
                sev_color = "#ef4444"
                sev_bg = "#fee2e2"
                sev_icon = "🔴"
            elif severity_level == "medium":
                sev_color = "#f59e0b"
                sev_bg = "#fed7aa"
                sev_icon = "🟡"
            else:
                sev_color = "#3b82f6"
                sev_bg = "#dbeafe"
                sev_icon = "🔵"

            html += f"""
        <div style="background: white; padding: 20px; border-radius: 8px; margin: 15px 0; border-left: 4px solid {sev_color}; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
                <span style="font-size: 1.5em;">{sev_icon}</span>
                <div>
                    <div style="font-weight: 600; color: #111827; font-size: 1.1em;">{action}</div>
                    <div style="background: {sev_bg}; color: {sev_color}; display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-weight: 500; margin-top: 5px;">
                        {severity_level.upper()} PRIORITY
                    </div>
                </div>
            </div>

            <div style="margin: 15px 0; padding: 15px; background: #f9fafb; border-radius: 6px;">
                <div style="margin-bottom: 10px;">
                    <strong style="color: #6b7280;">Current State:</strong>
                    <div style="color: #374151; margin-top: 5px;">{current_state}</div>
                </div>
                <div>
                    <strong style="color: #6b7280;">Target State:</strong>
                    <div style="color: #10b981; margin-top: 5px;">{target_state}</div>
                </div>
            </div>
"""

            if rationale:
                html += """
            <div style="margin: 15px 0;">
                <strong style="color: #374151;">Why This Matters:</strong>
                <ul style="margin: 10px 0 0 20px; color: #6b7280;">
"""
                for reason in rationale:
                    html += f"                    <li>{reason}</li>\n"
                html += """
                </ul>
            </div>
"""

            if aps_to_upgrade and rec_type == "firmware_upgrade":
                html += """
            <div style="margin: 15px 0;">
                <strong style="color: #374151;">APs to Upgrade:</strong>
                <div style="margin-top: 10px;">
"""
                for ap_info in aps_to_upgrade:
                    ap_name = ap_info.get("name", "Unknown")
                    current_ver = ap_info.get("current_version", "Unknown")
                    html += f"""
                    <div style="padding: 8px 12px; background: #fef3c7; border-left: 3px solid #f59e0b; border-radius: 4px; margin: 5px 0; font-size: 0.95em;">
                        <span style="font-weight: 500; color: #92400e;">{ap_name}</span>
                        <span style="color: #78350f; margin-left: 10px;">→ {current_ver}</span>
                    </div>
"""
                html += """
                </div>
            </div>
"""

            html += """
        </div>
"""

    html += """
        </div>
"""

    return html


def generate_recommendations_html(recommendations):
    """Generate recommendations section"""
    if not recommendations:
        return """
            <div class="section">
                <h2>✅ Recommendations</h2>
                <p style="color: #10b981; font-size: 1.2em;"><strong>No recommendations - your network is optimized!</strong></p>
            </div>
"""

    # Knowledge base URLs for different issue types
    learn_more_urls = {
        "channel": "https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design",
        "power": "https://help.ui.com/hc/en-us/articles/360039664734-UniFi-Best-Practices-for-Wireless-Coverage",
        "band_steering": "https://help.ui.com/hc/en-us/articles/115012700547-UniFi-Understanding-and-Using-Band-Steering",
        "fast_roaming": "https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design#5",
        "airtime_fairness": "https://help.ui.com/hc/en-us/articles/115004662107",
        "mesh": "https://help.ui.com/hc/en-us/articles/115002262328-UniFi-Wireless-Meshing",
        "dfs": "https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design#3",
        "legacy_devices": "https://community.ui.com/questions/Legacy-client-support/b2c2c2ec-f0f4-4848-9e39-b8652e5e8267",
        "default": "https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design",
    }

    def get_learn_more_url(rec):
        """Determine the best learn more URL for this recommendation"""
        action = rec.get("action", "")
        rec_type = rec.get("type", "")
        message = rec.get("message", "").lower()

        if action == "channel_change" or "channel" in message:
            return learn_more_urls["channel"]
        elif action == "power_change" or "power" in message or "transmit" in message:
            return learn_more_urls["power"]
        elif "band steering" in message or "band_steering" in rec_type:
            return learn_more_urls["band_steering"]
        elif "roaming" in message or "802.11r" in message:
            return learn_more_urls["fast_roaming"]
        elif "airtime" in message or "legacy" in rec_type:
            return learn_more_urls["legacy_devices"]
        elif "mesh" in message:
            return learn_more_urls["mesh"]
        elif "dfs" in message or "radar" in message:
            return learn_more_urls["dfs"]
        else:
            return learn_more_urls["default"]

    items = ""
    for i, rec in enumerate(recommendations, 1):
        priority = rec.get("priority", "medium")

        # Handle device name from multiple possible formats
        if isinstance(rec.get("device"), dict):
            device_name = rec.get("device", {}).get("name", "Unknown Device")
        elif isinstance(rec.get("device"), str):
            device_name = rec.get("device", "Unknown Device")
        elif isinstance(rec.get("ap"), dict):
            device_name = rec.get("ap", {}).get("name", "Unknown Device")
        else:
            device_name = "Unknown Device"

        # Handle both recommendation formats (expert analyzer and converted)
        # If "reason" exists, it's the converted format with message+recommendation combined
        if "reason" in rec and not rec.get("message"):
            # Converted format: "reason" contains both message and recommendation
            # Try to split it back or use as-is
            reason = rec.get("reason", "")
            if ". " in reason:
                parts = reason.split(". ", 1)
                message = parts[0]
                recommendation = parts[1] if len(parts) > 1 else ""
            else:
                message = reason
                recommendation = ""
        else:
            # Original format: separate message and recommendation
            message = rec.get("message", "Optimization recommended")
            recommendation = rec.get("recommendation", "")

        # If no explicit recommendation, build it from action details
        if not recommendation:
            action = rec.get("action", "")
            if action == "channel_change":
                current = rec.get("current_channel", "N/A")
                new = rec.get("new_channel", "N/A")
                radio = rec.get("radio", "unknown")
                band = (
                    "2.4GHz"
                    if radio == "ng"
                    else (
                        "5GHz"
                        if radio == "na"
                        else "6GHz" if radio in ["6e", "ax", "6g"] else "Unknown"
                    )
                )
                recommendation = f"Change {band} channel from {current} to {new}"
            elif action == "power_change":
                current = rec.get("current_power", "N/A")
                new = rec.get("new_power", "N/A")
                radio = rec.get("radio", "unknown")
                band = (
                    "2.4GHz"
                    if radio == "ng"
                    else (
                        "5GHz"
                        if radio == "na"
                        else "6GHz" if radio in ["6e", "ax", "6g"] else "Unknown"
                    )
                )
                recommendation = f"Change {band} transmit power from {current} to {new}"

        # Get appropriate learn more URL
        learn_more_url = get_learn_more_url(rec)

        items += f"""
                <li class="recommendation-item {priority}-priority">
                    <h4>{i}. <span class="device">{device_name}</span></h4>
                    <p><strong>Issue:</strong> {message}</p>
                    <p><strong>Recommendation:</strong> {recommendation}</p>
                    <p style="margin-top: 10px;">
                        <a href="{learn_more_url}" target="_blank"
                           style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                                  text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                            📚 Learn More
                        </a>
                    </p>
                </li>
"""

    return f"""
            <div class="section">
                <h2>💡 Recommendations</h2>
                <ul class="recommendation-list">
                    {items}
                </ul>
            </div>
"""


def generate_channel_analysis_html(channel_analysis):
    """Generate channel analysis section"""
    if not channel_analysis:
        return ""

    return f"""
            <div class="section">
                <h2>📻 Channel Analysis</h2>
                <p>Channel utilization and interference analysis performed across all bands.</p>
                <p><em>Detailed channel metrics available in full analysis data.</em></p>
            </div>
"""


def generate_client_health_html(client_health):
    """Generate client health section"""
    health_scores = client_health.get("health_scores", [])
    if not health_scores:
        return ""

    # Count clients by grade
    grades = {}
    for client in health_scores:
        grade = client.get("grade", "F")
        grades[grade] = grades.get(grade, 0) + 1

    grade_rows = ""
    for grade in ["A", "B", "C", "D", "F"]:
        count = grades.get(grade, 0)
        if count > 0:
            grade_rows += f"""
                <tr>
                    <td><strong>{grade}</strong></td>
                    <td>{count}</td>
                </tr>
"""

    # Generate signal strength histogram from by_signal_quality data
    by_signal = client_health.get("by_signal_quality", {})
    signal_categories = {
        "Excellent (> -50 dBm)": len(by_signal.get("excellent", [])),
        "Good (-50 to -60 dBm)": len(by_signal.get("good", [])),
        "Fair (-60 to -70 dBm)": len(by_signal.get("fair", [])),
        "Poor (-70 to -80 dBm)": len(by_signal.get("poor", [])),
        "Critical (< -80 dBm)": len(by_signal.get("critical", [])),
    }

    # Count total wireless clients (exclude wired)
    total_wireless = sum(signal_categories.values())
    wired_count = len(by_signal.get("wired", []))

    # Generate histogram bars
    histogram_html = ""
    colors = {
        "Excellent (> -50 dBm)": "#10b981",  # green
        "Good (-50 to -60 dBm)": "#3b82f6",  # blue
        "Fair (-60 to -70 dBm)": "#f59e0b",  # amber
        "Poor (-70 to -80 dBm)": "#a855f7",  # purple
        "Critical (< -80 dBm)": "#ef4444",  # red
    }

    max_count = max(signal_categories.values()) if signal_categories.values() else 1

    for label, count in signal_categories.items():
        color = colors.get(label, "#6b7280")
        percentage = (count / total_wireless * 100) if total_wireless > 0 else 0
        bar_width = (count / max_count * 100) if max_count > 0 else 0

        histogram_html += f"""
                <div style="margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 14px;">
                        <span style="font-weight: 500;">{label}</span>
                        <span style="color: #6b7280;">{count} clients ({percentage:.1f}%)</span>
                    </div>
                    <div style="background: #f3f4f6; border-radius: 4px; height: 24px; overflow: hidden;">
                        <div style="background: {color}; height: 100%; width: {bar_width}%; transition: width 0.3s ease;"></div>
                    </div>
                </div>
"""

    # Add wired client note if any
    wired_note = ""
    if wired_count > 0:
        wired_note = f"""
                <p style="margin-top: 16px; padding: 12px; background: #f0fdf4; border-left: 4px solid #10b981; border-radius: 4px; font-size: 14px;">
                    <strong>✓ Wired Clients:</strong> {wired_count} (not included in signal strength histogram)
                </p>
"""

    return f"""
            <div class="section">
                <h2>📊 Signal Strength Distribution</h2>
                <p style="color: #6b7280; margin-bottom: 20px;">Visual representation of wireless client signal quality across your network</p>
                <div style="max-width: 800px;">
                    {histogram_html}
                </div>
                {wired_note}
            </div>

            <div class="section">
                <h2>👥 Client Health Grades</h2>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Grade</th>
                            <th>Client Count</th>
                        </tr>
                    </thead>
                    <tbody>
                        {grade_rows}
                    </tbody>
                </table>
            </div>
"""


def generate_disconnected_clients_html(client_health):
    """Generate frequently disconnected clients section"""
    disconnection_prone = client_health.get("disconnection_prone", [])
    if not disconnection_prone:
        return ""

    # Sort by disconnect count (highest first)
    sorted_clients = sorted(disconnection_prone, key=lambda x: x.get("disconnect_count", 0), reverse=True)

    clients_html = ""
    for client in sorted_clients[:20]:  # Show top 20
        mac = client.get("mac", "Unknown")
        hostname = client.get("hostname", "Unknown")
        ip = client.get("ip", "Unknown")
        disconnect_count = client.get("disconnect_count", 0)
        rssi = client.get("rssi", 0)
        ap_mac = client.get("ap_mac", "Unknown")

        # Fix RSSI if positive
        if rssi > 0:
            rssi = -rssi

        # Determine signal quality
        if rssi > -50:
            signal_badge = '<span style="background: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;">Excellent</span>'
        elif rssi > -60:
            signal_badge = '<span style="background: #3b82f6; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;">Good</span>'
        elif rssi > -70:
            signal_badge = '<span style="background: #f59e0b; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;">Fair</span>'
        elif rssi > -80:
            signal_badge = '<span style="background: #f97316; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;">Poor</span>'
        else:
            signal_badge = '<span style="background: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;">Critical</span>'

        # Severity color based on disconnect count
        if disconnect_count >= 10:
            severity_color = "#ef4444"  # red
            severity_icon = "🔴"
        elif disconnect_count >= 5:
            severity_color = "#f59e0b"  # amber
            severity_icon = "🟡"
        else:
            severity_color = "#3b82f6"  # blue
            severity_icon = "🔵"

        clients_html += f"""
                <div style="background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border-left: 4px solid {severity_color}; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                        <div style="flex-grow: 1;">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                                <span style="font-size: 1.2em;">{severity_icon}</span>
                                <strong style="color: #111827; font-size: 1.05em;">{hostname}</strong>
                                <span style="background: {severity_color}15; color: {severity_color}; padding: 2px 10px; border-radius: 12px; font-size: 0.85em; font-weight: 500;">
                                    {disconnect_count} disconnects
                                </span>
                            </div>
                            <div style="color: #6b7280; font-size: 0.9em; font-family: monospace;">
                                <span style="margin-right: 15px;">📱 {mac}</span>
                                <span style="margin-right: 15px;">🌐 {ip}</span>
                                <span>📡 AP: {ap_mac[:17] if len(ap_mac) > 17 else ap_mac}</span>
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 15px;">
                            <div style="text-align: right;">
                                <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 4px;">Signal</div>
                                <div style="font-weight: 600; color: #111827; font-size: 1.1em;">{rssi} dBm</div>
                                <div style="margin-top: 4px;">{signal_badge}</div>
                            </div>
                        </div>
                    </div>
                </div>
"""

    return f"""
            <div class="section">
                <h2>🔄 Frequently Disconnected Clients</h2>
                <p style="color: #666; margin-bottom: 20px;">
                    Clients that have experienced multiple disconnections during the analysis period.
                    Frequent disconnections may indicate poor signal strength, roaming issues, interference, or device problems.
                </p>
                <div style="background: #eff6ff; padding: 15px; border-radius: 8px; border-left: 4px solid #3b82f6; margin-bottom: 20px;">
                    <strong style="color: #1e40af;">💡 Troubleshooting Tips:</strong>
                    <ul style="margin-top: 10px; margin-left: 20px; color: #374151;">
                        <li>Check signal strength (RSSI) - clients with poor signal should be closer to an AP or need better coverage</li>
                        <li>Review channel interference - high utilization can cause disconnects</li>
                        <li>Consider enabling Minimum RSSI to force weak clients to roam earlier</li>
                        <li>Check for device-specific issues (old firmware, driver problems)</li>
                        <li>Review band steering settings for dual-band capable devices</li>
                    </ul>
                </div>
                {clients_html}
            </div>
"""


def generate_findings_html(analysis_data):
    """Generate findings section"""
    findings = analysis_data.get("findings", [])
    if not findings:
        return ""

    findings_html = ""
    for finding in findings:
        severity = finding.get("severity", "info")
        message = finding.get("message", "")
        device = finding.get("device", "Network")

        findings_html += f"""
                <div class="finding {severity}">
                    <strong>{device}:</strong> {message}
                </div>
"""

    return f"""
            <div class="section">
                <h2>🔍 Detailed Findings</h2>
                {findings_html}
            </div>
"""


def generate_network_health_score_html(health_score):
    """Generate network health score section with prominent display"""
    if not health_score:
        return ""

    score = health_score.get("score", 0)
    grade = health_score.get("grade", "N/A")
    status = health_score.get("status", "Unknown")
    details = health_score.get("details", {})

    # Color based on grade
    if grade == "A":
        color = "#10b981"  # green
    elif grade == "B":
        color = "#3b82f6"  # blue
    elif grade == "C":
        color = "#f59e0b"  # amber
    elif grade == "D":
        color = "#f97316"  # orange
    else:
        color = "#ef4444"  # red

    # Build details breakdown
    details_html = ""
    detail_items = [
        ("RSSI Score", details.get("rssi_score", 0), 30),
        ("Airtime Score", details.get("airtime_score", 0), 20),
        ("Distribution Score", details.get("distribution_score", 0), 20),
        ("Mesh Score", details.get("mesh_score", 0), 15),
        ("Issues Score", details.get("issues_score", 0), 15),
    ]

    for name, value, max_val in detail_items:
        percentage = (value / max_val * 100) if max_val > 0 else 0
        details_html += f"""
                    <div style="margin-bottom: 12px;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span>{name}</span>
                            <span><strong>{value:.1f}/{max_val}</strong></span>
                        </div>
                        <div style="background: #e5e7eb; height: 8px; border-radius: 4px; overflow: hidden;">
                            <div style="width: {percentage}%; height: 100%; background: {color}; transition: width 0.3s;"></div>
                        </div>
                    </div>
"""

    return f"""
            <div class="section" style="background: linear-gradient(135deg, {color}15 0%, {color}05 100%); border-left: 4px solid {color};">
                <h2 style="color: {color};">🏆 Network Health Score</h2>
                <div style="display: flex; align-items: center; gap: 30px; margin-bottom: 30px;">
                    <div style="text-align: center; flex-shrink: 0;">
                        <div style="width: 150px; height: 150px; border-radius: 50%; background: linear-gradient(135deg, {color} 0%, {color}cc 100%); display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 20px rgba(0,0,0,0.15);">
                            <div style="text-align: center; color: white;">
                                <div style="font-size: 3em; font-weight: bold; line-height: 1;">{score}</div>
                                <div style="font-size: 0.9em; opacity: 0.9;">out of 100</div>
                            </div>
                        </div>
                        <div style="margin-top: 15px; font-size: 1.5em; font-weight: bold; color: {color};">
                            Grade {grade}
                        </div>
                        <div style="color: #666; font-size: 1.1em;">{status}</div>
                    </div>
                    <div style="flex-grow: 1;">
                        <h3 style="margin-bottom: 20px;">Score Breakdown</h3>
                        {details_html}
                    </div>
                </div>
                <div style="background: white; padding: 15px; border-radius: 8px; border: 1px solid {color}30;">
                    <strong>What This Means:</strong>
                    <ul style="margin-top: 10px; margin-left: 20px;">
                        {'<li>Your network is performing excellently with minimal issues</li>' if grade == 'A' else ''}
                        {'<li>Your network is performing well but has room for improvement</li>' if grade == 'B' else ''}
                        {'<li>Your network is experiencing notable issues that should be addressed</li>' if grade == 'C' else ''}
                        {'<li>Your network has significant issues impacting client experience</li>' if grade == 'D' else ''}
                        {'<li>Your network is experiencing critical issues requiring immediate attention</li>' if grade == 'F' else ''}
                    </ul>
                </div>
            </div>
"""


def generate_dfs_analysis_html(dfs_analysis):
    """Generate DFS event analysis section"""
    if not dfs_analysis or dfs_analysis.get("total_events", 0) == 0:
        return """
            <div class="section">
                <h2>📡 DFS Radar Events</h2>
                <div style="background: #d1fae5; padding: 20px; border-radius: 8px; border-left: 4px solid #10b981;">
                    <strong style="color: #047857;">✓ No DFS Events Detected</strong>
                    <p style="margin-top: 10px; color: #065f46;">Your network has not experienced any DFS radar detection events during the analysis period. This is excellent!</p>
                    <p style="margin-top: 10px;">
                        <a href="https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design#3" target="_blank"
                           style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                                  text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                            📚 Learn More About DFS
                        </a>
                    </p>
                </div>
            </div>
"""

    total_events = dfs_analysis.get("total_events", 0)
    severity = dfs_analysis.get("severity", "ok")
    events_by_ap = dfs_analysis.get("events_by_ap", {})
    affected_channels = dfs_analysis.get("affected_channels", [])
    recommendations = dfs_analysis.get("recommendations", [])

    severity_color = {"high": "#ef4444", "medium": "#f59e0b", "ok": "#10b981"}.get(
        severity, "#6b7280"
    )

    # Build events table
    events_html = ""
    for ap_name, events in events_by_ap.items():
        events_html += f"""
                    <tr>
                        <td>{ap_name}</td>
                        <td>{len(events)}</td>
                        <td>{', '.join(str(e.get('channel', 'N/A')) for e in events if e.get('channel'))}</td>
                    </tr>
"""

    # Build recommendations
    recs_html = ""
    for rec in recommendations:
        priority = rec.get("priority", "low")
        priority_color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#10b981"}.get(
            priority, "#6b7280"
        )
        recs_html += f"""
                    <div style="background: {priority_color}15; padding: 15px; border-radius: 8px; border-left: 4px solid {priority_color}; margin-bottom: 10px;">
                        <strong style="color: {priority_color}; text-transform: uppercase; font-size: 0.85em;">{priority} Priority</strong>
                        <div style="margin-top: 8px;">{rec.get('message', '')}</div>
                        <div style="margin-top: 8px; color: #666;"><strong>Recommendation:</strong> {rec.get('recommendation', '')}</div>
                    </div>
"""

    return f"""
            <div class="section">
                <h2>📡 DFS Radar Events Analysis</h2>
                <div style="background: {severity_color}15; padding: 20px; border-radius: 8px; border-left: 4px solid {severity_color}; margin-bottom: 20px;">
                    <div style="font-size: 1.2em; margin-bottom: 10px;">
                        <strong style="color: {severity_color};">Detected {total_events} DFS Event(s)</strong>
                    </div>
                    <p style="color: #666;">DFS (Dynamic Frequency Selection) events occur when radar is detected on channels 52-144. These cause sudden channel changes and client disconnects.</p>
                </div>

                <h3>Events by Access Point</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Access Point</th>
                            <th>Event Count</th>
                            <th>Affected Channels</th>
                        </tr>
                    </thead>
                    <tbody>
                        {events_html}
                    </tbody>
                </table>

                {'<div style="margin-top: 30px;"><h3>Recommendations</h3>' + recs_html + '</div>' if recs_html else ''}

                <div style="margin-top: 20px; padding: 15px; background: #f3f4f6; border-radius: 8px;">
                    <strong>Non-DFS Channels (Recommended for Stability):</strong>
                    <div style="margin-top: 10px;">36, 40, 44, 48, 149, 153, 157, 161, 165</div>
                    <p style="margin-top: 10px;">
                        <a href="https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design#3" target="_blank"
                           style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                                  text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                            📚 Learn More About DFS
                        </a>
                    </p>
                </div>
            </div>
"""


def generate_band_steering_html(band_steering):
    """Generate band steering analysis section"""
    if not band_steering:
        return ""

    misplaced_count = band_steering.get("dual_band_clients_on_2ghz", 0)
    tri_band_count = band_steering.get("tri_band_clients_suboptimal", 0)
    wifi7_count = band_steering.get("wifi7_clients_suboptimal", 0)
    misplaced_clients = band_steering.get("misplaced_clients", [])
    band_steering_enabled = band_steering.get("band_steering_enabled", {})
    severity = band_steering.get("severity", "ok")
    recommendations = band_steering.get("recommendations", [])

    # AP configuration table
    ap_config_html = ""
    for ap_name, enabled in band_steering_enabled.items():
        status_color = "#10b981" if enabled else "#ef4444"
        status_text = "Enabled" if enabled else "Disabled"
        ap_config_html += f"""
                    <tr>
                        <td>{ap_name}</td>
                        <td style="color: {status_color}; font-weight: bold;">{status_text}</td>
                    </tr>
"""

    # Misplaced clients table
    clients_html = ""
    for client in misplaced_clients[:15]:  # Show top 15 to include 6GHz clients
        last_seen = client.get("last_seen", "Unknown")
        current_band = client.get("current_band", "Unknown")
        capability = client.get("capability", client.get("radio_proto", "N/A"))
        is_6ghz = client.get("is_6ghz_capable", False)
        is_wifi7 = client.get("is_wifi7_capable", False)

        # Color-code based on band
        band_color = "#f59e0b" if current_band == "2.4GHz" else "#3b82f6"
        # WiFi 7 = purple, WiFi 6E = violet, WiFi 5/6 = green
        capability_color = "#a855f7" if is_wifi7 else "#8b5cf6" if is_6ghz else "#10b981"

        clients_html += f"""
                    <tr>
                        <td>{client.get('hostname', 'Unknown')}</td>
                        <td>{client.get('ap', 'Unknown')}</td>
                        <td style="color: #666; font-size: 0.9em;">{last_seen}</td>
                        <td style="color: {band_color}; font-weight: 500;">{current_band}</td>
                        <td>{client.get('rssi', 'N/A')} dBm</td>
                        <td style="color: {capability_color}; font-weight: 500;">{capability}</td>
                    </tr>
"""

    severity_color = {"high": "#ef4444", "medium": "#f59e0b", "ok": "#10b981"}.get(
        severity, "#6b7280"
    )

    # Build summary message
    summary_msg = f"{misplaced_count} Client(s) on Suboptimal Bands"
    detail_msg = "These clients could benefit from better band placement."
    if wifi7_count > 0:
        detail_msg = f"Includes {wifi7_count} WiFi 7 capable client(s) that could utilize the 6GHz band with 320MHz channels and Multi-Link Operation for maximum performance."
    elif tri_band_count > 0:
        detail_msg = f"Includes {tri_band_count} WiFi 6E capable client(s) that could use the faster, less congested 6GHz band for optimal performance."

    return f"""
            <div class="section">
                <h2>🔄 Band Steering Analysis</h2>
                <div style="background: {severity_color}15; padding: 20px; border-radius: 8px; border-left: 4px solid {severity_color}; margin-bottom: 20px;">
                    <strong style="font-size: 1.2em; color: {severity_color};">{summary_msg}</strong>
                    <p style="margin-top: 10px; color: #666;">{detail_msg}</p>
                </div>

                <h3>Band Steering Configuration by AP</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Access Point</th>
                            <th>Band Steering</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ap_config_html if ap_config_html else '<tr><td colspan="2">No APs found</td></tr>'}
                    </tbody>
                </table>

                {f'''
                <h3 style="margin-top: 30px;">Clients on Suboptimal Bands</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Client</th>
                            <th>Connected AP</th>
                            <th>Last Seen</th>
                            <th>Current Band</th>
                            <th>Signal</th>
                            <th>Capability</th>
                        </tr>
                    </thead>
                    <tbody>
                        {clients_html}
                    </tbody>
                </table>
                ''' if clients_html else ''}

                <div style="margin-top: 20px; padding: 15px; background: #f3f4f6; border-radius: 8px;">
                    <strong>About Band Steering:</strong>
                    <p style="margin-top: 8px; color: #666;">Band steering encourages capable clients to use less congested bands (5GHz/6GHz), improving overall network performance. WiFi 6E devices can use the new 6GHz band for maximum speed and minimum interference, while WiFi 7 devices can benefit from 320MHz channels and Multi-Link Operation (MLO) for unprecedented performance.</p>
                    <p style="margin-top: 10px;">
                        <a href="https://help.ui.com/hc/en-us/articles/115012700547-UniFi-Understanding-and-Using-Band-Steering" target="_blank"
                           style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                                  text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                            📚 Learn More About Band Steering
                        </a>
                    </p>
                </div>
            </div>
"""


def generate_min_rssi_html(min_rssi):
    """Generate minimum RSSI analysis section"""
    if not min_rssi:
        return ""

    enabled_count = min_rssi.get("enabled_count", 0)
    disabled_count = min_rssi.get("disabled_count", 0)
    total_radios = min_rssi.get("total_radios", 0)
    radios_with = min_rssi.get("radios_with_min_rssi", [])
    radios_without = min_rssi.get("radios_without_min_rssi", [])
    severity = min_rssi.get("severity", "ok")
    recommendations = min_rssi.get("recommendations", [])
    strategy = min_rssi.get("strategy", "optimal")
    ios_device_count = min_rssi.get("ios_device_count", 0)
    ios_devices_detected = min_rssi.get("ios_devices_detected", False)

    # Radio configuration table
    radio_config_html = ""

    # Add enabled radios first
    for radio_info in radios_with:
        device = radio_info.get("device", "Unknown")
        band = radio_info.get("band", "Unknown")
        value = radio_info.get("value", "N/A")
        radio_config_html += f"""
                    <tr>
                        <td>{device}</td>
                        <td>{band}</td>
                        <td style="color: #10b981; font-weight: bold;">Enabled</td>
                        <td>{value} dBm</td>
                    </tr>
"""

    # Add disabled radios
    for radio_info in radios_without:
        device = radio_info.get("device", "Unknown")
        band = radio_info.get("band", "Unknown")
        value = radio_info.get("value", "N/A")
        radio_config_html += f"""
                    <tr>
                        <td>{device}</td>
                        <td>{band}</td>
                        <td style="color: #ef4444; font-weight: bold;">Disabled</td>
                        <td style="color: #999;">{value} dBm</td>
                    </tr>
"""

    severity_color = {"high": "#ef4444", "medium": "#f59e0b", "ok": "#10b981"}.get(
        severity, "#6b7280"
    )

    pct_disabled = (disabled_count / total_radios * 100) if total_radios > 0 else 0

    # Strategy information - Show BOTH strategies side-by-side
    # iOS detection info
    ios_info_html = ""
    if ios_devices_detected:
        ios_info_html = f"""
                <div style="margin-top: 15px; padding: 12px; background: #fffbeb; border-left: 3px solid #f59e0b; border-radius: 4px;">
                    <strong style="color: #f59e0b;">🍎 iOS Devices Detected: {ios_device_count}</strong>
                    <p style="margin: 5px 0 0 0; color: #666; font-size: 0.9em;">
                        iPhone/iPad devices may be more sensitive to aggressive Min RSSI thresholds.
                        The Max Connectivity strategy includes extra tolerance for iOS devices.
                    </p>
                </div>
"""

    # Build strategy comparison section showing BOTH options
    strategies_html = f"""
                <h3 style="margin-bottom: 15px;">Min RSSI Strategy Options</h3>
                <p style="margin-bottom: 20px; color: #666;">Choose the strategy that best fits your network environment. Both options are valid - your choice depends on your priorities.</p>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px;">
                    <!-- Optimal (Aggressive) Strategy -->
                    <div style="border: 2px solid {'#10b981' if strategy == 'optimal' else '#e5e7eb'}; padding: 20px; border-radius: 8px; background: {'#f0fdf4' if strategy == 'optimal' else 'white'}; position: relative;">
                        {('<div style="position: absolute; top: 10px; right: 10px; background: #10b981; color: white; padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: 600;">SELECTED</div>' if strategy == 'optimal' else '')}
                        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                            <span style="font-size: 2em;">⚡</span>
                            <div>
                                <strong style="font-size: 1.2em; color: #10b981;">Optimal (Aggressive)</strong>
                            </div>
                        </div>
                        <p style="margin: 10px 0; color: #666; font-size: 0.95em;">
                            Forces clients to move to better APs early for best performance.
                        </p>

                        <div style="margin-top: 15px;">
                            <strong style="color: #059669; font-size: 0.9em;">✅ PROS:</strong>
                            <ul style="margin: 8px 0; padding-left: 20px; color: #666; font-size: 0.9em; line-height: 1.6;">
                                <li>Better overall network performance</li>
                                <li>Faster roaming to stronger signals</li>
                                <li>Reduces congestion on busy APs</li>
                                <li>Optimal for dense AP deployments</li>
                            </ul>
                        </div>

                        <div style="margin-top: 12px;">
                            <strong style="color: #dc2626; font-size: 0.9em;">⚠️ CONS:</strong>
                            <ul style="margin: 8px 0; padding-left: 20px; color: #666; font-size: 0.9em; line-height: 1.6;">
                                <li>May cause more disconnects in edge areas</li>
                                <li>iOS devices may disconnect more frequently</li>
                                <li>Requires good AP coverage</li>
                            </ul>
                        </div>

                        <div style="margin-top: 15px; padding: 10px; background: #fef3c7; border-radius: 6px; font-size: 0.85em;">
                            <strong>Typical Values:</strong><br>
                            2.4GHz: -75 dBm | 5GHz: -72 dBm | 6GHz: -70 dBm
                        </div>
                    </div>

                    <!-- Max Connectivity (Conservative) Strategy -->
                    <div style="border: 2px solid {'#f59e0b' if strategy == 'max_connectivity' else '#e5e7eb'}; padding: 20px; border-radius: 8px; background: {'#fffbeb' if strategy == 'max_connectivity' else 'white'}; position: relative;">
                        {('<div style="position: absolute; top: 10px; right: 10px; background: #f59e0b; color: white; padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: 600;">SELECTED</div>' if strategy == 'max_connectivity' else '')}
                        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                            <span style="font-size: 2em;">🛡️</span>
                            <div>
                                <strong style="font-size: 1.2em; color: #f59e0b;">Max Connectivity (Conservative)</strong>
                            </div>
                        </div>
                        <p style="margin: 10px 0; color: #666; font-size: 0.95em;">
                            Lets clients stay connected longer for maximum reliability.
                        </p>

                        <div style="margin-top: 15px;">
                            <strong style="color: #059669; font-size: 0.9em;">✅ PROS:</strong>
                            <ul style="margin: 8px 0; padding-left: 20px; color: #666; font-size: 0.9em; line-height: 1.6;">
                                <li>Fewer unexpected disconnects</li>
                                <li>Better for iOS/iPhone users</li>
                                <li>Works with sparse AP coverage</li>
                                <li>More stable connections overall</li>
                            </ul>
                        </div>

                        <div style="margin-top: 12px;">
                            <strong style="color: #dc2626; font-size: 0.9em;">⚠️ CONS:</strong>
                            <ul style="margin: 8px 0; padding-left: 20px; color: #666; font-size: 0.9em; line-height: 1.6;">
                                <li>Clients may stay on distant APs longer</li>
                                <li>Potential for lower throughput</li>
                                <li>More "sticky client" issues</li>
                            </ul>
                        </div>

                        <div style="margin-top: 15px; padding: 10px; background: #fef3c7; border-radius: 6px; font-size: 0.85em;">
                            <strong>Typical Values:</strong><br>
                            2.4GHz: -80 dBm | 5GHz: -77 dBm | 6GHz: -75 dBm
                        </div>
                    </div>
                </div>
                {ios_info_html}
"""

    return f"""
            <div class="section">
                <h2>📡 Minimum RSSI Configuration</h2>

                {strategies_html}

                <div style="background: {severity_color}15; padding: 20px; border-radius: 8px; border-left: 4px solid {severity_color}; margin-bottom: 20px;">
                    <strong style="font-size: 1.2em; color: {severity_color};">{disabled_count} of {total_radios} Radios ({pct_disabled:.0f}%) Without Min RSSI</strong>
                    <p style="margin-top: 10px; color: #666;">Minimum RSSI forces weak clients to roam before signal degrades too much, preventing sticky client problems and improving network performance.</p>
                </div>

                <h3>Minimum RSSI Configuration by Radio</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Access Point</th>
                            <th>Band</th>
                            <th>Status</th>
                            <th>Threshold</th>
                        </tr>
                    </thead>
                    <tbody>
                        {radio_config_html if radio_config_html else '<tr><td colspan="4">No radios found</td></tr>'}
                    </tbody>
                </table>

                <div style="margin-top: 20px; padding: 15px; background: #f3f4f6; border-radius: 8px;">
                    <strong>About Minimum RSSI:</strong>
                    <p style="margin-top: 8px; color: #666;">Minimum RSSI disconnects clients whose signal falls below a threshold, forcing them to roam to a closer AP. This prevents "sticky client" syndrome where devices stay connected to distant APs.</p>
                    <p style="margin-top: 8px; color: #666;"><strong>Recommended values:</strong> 2.4GHz: -75 to -80 dBm, 5GHz: -70 to -75 dBm, 6GHz: -68 to -72 dBm</p>
                    <p style="margin-top: 10px;">
                        <a href="https://help.ui.com/hc/en-us/articles/221321728-UniFi-Understanding-Minimum-RSSI" target="_blank"
                           style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                                  text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                            📚 Learn More About Minimum RSSI
                        </a>
                    </p>
                </div>
            </div>
"""


def generate_airtime_analysis_html(airtime_analysis):
    """Generate airtime utilization section with time-series visualization"""
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
            for band in ["2.4GHz", "5GHz", "6GHz"]:
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
            status = "Critical"
        elif airtime_pct > 50:
            color = "#f59e0b"
            status = "Warning"
        else:
            color = "#10b981"
            status = "Good"

        util_html += f"""
                    <tr>
                        <td>{ap_key}</td>
                        <td>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="flex-grow: 1; background: #e5e7eb; height: 20px; border-radius: 4px; overflow: hidden;">
                                    <div style="width: {airtime_pct}%; height: 100%; background: {color};"></div>
                                </div>
                                <span style="font-weight: bold; color: {color};">{airtime_pct:.1f}%</span>
                            </div>
                        </td>
                        <td>{clients}</td>
                        <td style="color: {color}; font-weight: bold;">{status}</td>
                    </tr>
"""

    # Generate time-series charts if we have historical data
    charts_html = ""
    chart_script = ""

    if time_series:
        import json
        from datetime import datetime

        def group_by_15min(data_points):
            """Group hourly data points into 15-minute buckets for smoother visualization"""
            grouped = {}
            for point in data_points:
                dt_str = point["datetime"]
                # Parse datetime and round to 15-minute intervals
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                minute = (dt.minute // 15) * 15
                bucket = dt.replace(minute=minute, second=0, microsecond=0)
                bucket_key = bucket.strftime("%Y-%m-%d %H:%M")

                if bucket_key not in grouped:
                    grouped[bucket_key] = {
                        "airtime_pct": [],
                        "tx_pct": [],
                        "rx_pct": [],
                        "clients": [],
                    }

                grouped[bucket_key]["airtime_pct"].append(point.get("airtime_pct", 0))
                grouped[bucket_key]["tx_pct"].append(point.get("tx_pct", 0))
                grouped[bucket_key]["rx_pct"].append(point.get("rx_pct", 0))
                grouped[bucket_key]["clients"].append(point.get("clients", 0))

            # Average values in each bucket
            result = []
            for bucket_key in sorted(grouped.keys()):
                bucket_data = grouped[bucket_key]
                result.append(
                    {
                        "datetime": bucket_key,
                        "airtime_pct": sum(bucket_data["airtime_pct"])
                        / len(bucket_data["airtime_pct"]),
                        "tx_pct": sum(bucket_data["tx_pct"]) / len(bucket_data["tx_pct"]),
                        "rx_pct": sum(bucket_data["rx_pct"]) / len(bucket_data["rx_pct"]),
                        "clients": max(bucket_data["clients"]),  # Use max clients in interval
                    }
                )

            return result

        # Create chart for each AP
        for idx, (ap_key, data_points) in enumerate(time_series.items()):
            if not data_points:
                continue

            # Group data into 15-minute intervals
            grouped_data = group_by_15min(data_points)

            if not grouped_data:
                continue

            # Prepare data for Chart.js
            labels = [point["datetime"] for point in grouped_data]
            airtime_data = [point["airtime_pct"] for point in grouped_data]
            tx_data = [point["tx_pct"] for point in grouped_data]
            rx_data = [point["rx_pct"] for point in grouped_data]
            client_data = [point["clients"] for point in grouped_data]

            # Determine color based on average airtime
            avg_airtime = sum(airtime_data) / len(airtime_data) if airtime_data else 0
            if avg_airtime > 70:
                chart_color = "rgba(239, 68, 68, 0.7)"  # red
                border_color = "rgba(239, 68, 68, 1)"
            elif avg_airtime > 50:
                chart_color = "rgba(245, 158, 11, 0.7)"  # yellow
                border_color = "rgba(245, 158, 11, 1)"
            else:
                chart_color = "rgba(16, 185, 129, 0.7)"  # green
                border_color = "rgba(16, 185, 129, 1)"

            # Format labels for display (show only time, group by hour for axis labels)
            display_labels = []
            for i, label in enumerate(labels):
                dt = datetime.fromisoformat(label)
                if dt.minute == 0:
                    display_labels.append(dt.strftime("%H:%M"))
                elif i % 4 == 0:  # Show every hour
                    display_labels.append(dt.strftime("%H:%M"))
                else:
                    display_labels.append("")  # Empty label for intermediate points

            charts_html += f"""
                <div style="margin: 30px 0; padding: 20px; background: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <h4 style="margin: 0;">{ap_key} - Utilization Trend</h4>
                        <span style="background: {chart_color}; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600;">
                            Avg: {avg_airtime:.1f}%
                        </span>
                    </div>
                    <div style="position: relative; height: 300px;">
                        <canvas id="airtimeChart{idx}"></canvas>
                    </div>
                    <div style="margin-top: 15px; padding: 12px; background: white; border-radius: 6px; border: 1px solid #e5e7eb; font-size: 12px; color: #666;">
                        <strong>📊 Data grouped in 15-minute intervals</strong> • Showing last 24 hours of utilization patterns
                    </div>
                </div>
"""

            chart_script += f"""
                new Chart(document.getElementById('airtimeChart{idx}'), {{
                    type: 'line',
                    data: {{
                        labels: {json.dumps(display_labels)},
                        datasets: [{{
                            label: 'Total Airtime %',
                            data: {json.dumps(airtime_data)},
                            borderColor: '{border_color}',
                            backgroundColor: '{chart_color}',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 2,
                            pointHoverRadius: 5,
                            pointBackgroundColor: '{border_color}',
                            order: 1
                        }}, {{
                            label: 'TX %',
                            data: {json.dumps(tx_data)},
                            borderColor: 'rgba(99, 102, 241, 0.8)',
                            backgroundColor: 'rgba(99, 102, 241, 0.2)',
                            borderWidth: 1.5,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 0,
                            pointHoverRadius: 4,
                            order: 2
                        }}, {{
                            label: 'RX %',
                            data: {json.dumps(rx_data)},
                            borderColor: 'rgba(147, 51, 234, 0.8)',
                            backgroundColor: 'rgba(147, 51, 234, 0.2)',
                            borderWidth: 1.5,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 0,
                            pointHoverRadius: 4,
                            order: 3
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {{
                            mode: 'index',
                            intersect: false
                        }},
                        plugins: {{
                            legend: {{
                                position: 'bottom',
                                labels: {{
                                    padding: 15,
                                    usePointStyle: true,
                                    font: {{
                                        size: 11
                                    }}
                                }}
                            }},
                            tooltip: {{
                                mode: 'index',
                                intersect: false,
                                backgroundColor: 'rgba(0, 0, 0, 0.8)',
                                padding: 12,
                                titleFont: {{
                                    size: 13,
                                    weight: 'bold'
                                }},
                                bodyFont: {{
                                    size: 12
                                }},
                                callbacks: {{
                                    title: function(tooltipItems) {{
                                        const index = tooltipItems[0].dataIndex;
                                        const fullLabels = {json.dumps(labels)};
                                        return fullLabels[index];
                                    }},
                                    afterBody: function(tooltipItems) {{
                                        const index = tooltipItems[0].dataIndex;
                                        const clients = {json.dumps(client_data)};
                                        return '\\nClients: ' + clients[index];
                                    }}
                                }}
                            }}
                        }},
                        scales: {{
                            y: {{
                                beginAtZero: true,
                                max: 100,
                                grid: {{
                                    color: 'rgba(0, 0, 0, 0.05)'
                                }},
                                title: {{
                                    display: true,
                                    text: 'Utilization %',
                                    font: {{
                                        size: 12,
                                        weight: 'bold'
                                    }}
                                }},
                                ticks: {{
                                    callback: function(value) {{
                                        return value + '%';
                                    }}
                                }}
                            }},
                            x: {{
                                grid: {{
                                    color: 'rgba(0, 0, 0, 0.05)'
                                }},
                                title: {{
                                    display: true,
                                    text: 'Time (15-min intervals)',
                                    font: {{
                                        size: 12,
                                        weight: 'bold'
                                    }}
                                }},
                                ticks: {{
                                    maxRotation: 45,
                                    minRotation: 45,
                                    autoSkip: true,
                                    font: {{
                                        size: 10
                                    }}
                                }}
                            }}
                        }}
                    }}
                }});
"""

    # Build complete HTML with embedded Chart.js library
    chart_js_script = ""
    if time_series:
        try:
            import os

            # Try to load embedded Chart.js from assets directory
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            chart_js_path = os.path.join(script_dir, "assets", "chart.umd.min.js")

            if os.path.exists(chart_js_path):
                with open(chart_js_path, "r", encoding="utf-8") as f:
                    chart_js_lib = f.read()
                chart_js_script = f"<script>{chart_js_lib}</script>"
            else:
                # Fallback to CDN if local file not found
                chart_js_script = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
        except Exception as e:
            # Fallback to CDN on any error
            chart_js_script = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'

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

                {f'<h3 style="margin-top: 40px; margin-bottom: 15px;">📊 Historical Trends (Last 24 Hours)</h3>' if time_series else ''}
                {charts_html}
                {chart_js_script}
                <script>
                    {chart_script}
                </script>
            </div>
"""


def generate_client_capabilities_html(capabilities):
    """Generate client capabilities matrix section"""
    if not capabilities:
        return ""

    cap_dist = capabilities.get("capability_distribution", {})
    channel_width = capabilities.get("channel_width", {})
    problem_devices = capabilities.get("problem_devices", [])

    # Build capability chart
    total_clients = sum(cap_dist.values())
    cap_rows = ""
    for standard, count in cap_dist.items():
        if count == 0:
            continue
        percentage = (count / total_clients * 100) if total_clients > 0 else 0
        color = {
            "802.11ax": "#10b981",
            "802.11ac": "#3b82f6",
            "802.11n": "#f59e0b",
            "legacy": "#ef4444",
        }.get(standard, "#6b7280")
        cap_rows += f"""
                    <tr>
                        <td><strong>{standard}</strong></td>
                        <td>{count}</td>
                        <td>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="flex-grow: 1; background: #e5e7eb; height: 20px; border-radius: 4px; overflow: hidden;">
                                    <div style="width: {percentage}%; height: 100%; background: {color};"></div>
                                </div>
                                <span>{percentage:.1f}%</span>
                            </div>
                        </td>
                    </tr>
"""

    # Channel width distribution
    width_rows = ""
    for width, count in channel_width.items():
        if count == 0:
            continue
        width_rows += f"""
                    <tr>
                        <td>{width}</td>
                        <td>{count}</td>
                    </tr>
"""

    # Problem devices
    problem_html = ""
    if problem_devices:
        for device in problem_devices[:10]:
            problem_html += f"""
                    <tr>
                        <td>{device.get('hostname', 'Unknown')}</td>
                        <td style="font-family: monospace; font-size: 0.9em;">{device.get('mac', 'N/A')}</td>
                        <td>{device.get('radio_proto', 'N/A')}</td>
                        <td style="color: #ef4444;">{device.get('issue', 'Unknown issue')}</td>
                    </tr>
"""

    return f"""
            <div class="section">
                <h2>📊 Client Capability Matrix</h2>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px;">
                    <div>
                        <h3>802.11 Standard Distribution</h3>
                        <table class="ap-table">
                            <thead>
                                <tr>
                                    <th>Standard</th>
                                    <th>Count</th>
                                    <th>Percentage</th>
                                </tr>
                            </thead>
                            <tbody>
                                {cap_rows}
                            </tbody>
                        </table>
                    </div>
                    <div>
                        <h3>Channel Width Support</h3>
                        <table class="ap-table">
                            <thead>
                                <tr>
                                    <th>Width</th>
                                    <th>Count</th>
                                </tr>
                            </thead>
                            <tbody>
                                {width_rows if width_rows else '<tr><td colspan="2">No data available</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>

                {f'''
                <h3>⚠ Problem Devices</h3>
                <div style="background: #fef2f2; padding: 15px; border-radius: 8px; border-left: 4px solid #ef4444; margin-bottom: 15px;">
                    <strong style="color: #ef4444;">{len(problem_devices)} Legacy Device(s) Detected</strong>
                    <p style="margin-top: 8px; color: #666;">Legacy 802.11a/b/g devices can slow down your entire network. Consider upgrading or isolating them on a separate SSID.</p>
                    <p style="margin-top: 10px;">
                        <a href="https://community.ui.com/questions/Legacy-client-support/b2c2c2ec-f0f4-4848-9e39-b8652e5e8267" target="_blank"
                           style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                                  text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                            📚 Learn More About Legacy Devices
                        </a>
                    </p>
                </div>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Device</th>
                            <th>MAC Address</th>
                            <th>Protocol</th>
                            <th>Issue</th>
                        </tr>
                    </thead>
                    <tbody>
                        {problem_html}
                    </tbody>
                </table>
                ''' if problem_html else ''}
            </div>
"""


def generate_client_security_html(security):
    """Generate client security analysis section"""
    blocked_clients = security.get("blocked_clients", [])
    isolated_clients = security.get("isolated_clients", [])
    severity = security.get("severity", "ok")

    severity_colors = {
        "high": "#ef4444",
        "medium": "#f59e0b",
        "ok": "#10b981",
        "critical": "#dc2626",
    }
    severity_color = severity_colors.get(severity, "#6b7280")

    # Blocked clients table
    blocked_html = ""
    for client in blocked_clients[:20]:  # Show top 20
        connection_type = "🔌 Wired" if client.get("is_wired") else "📡 Wireless"
        blocked_html += f"""
                    <tr style="background: #fee2e2;">
                        <td><strong>{client.get('hostname', 'Unknown')}</strong></td>
                        <td><code style="font-size: 0.85em;">{client.get('mac', 'Unknown')}</code></td>
                        <td>{connection_type}</td>
                        <td style="color: #dc2626; font-weight: 500;">{client.get('reason', 'Blocked')}</td>
                    </tr>
"""

    # Isolated clients table
    isolated_html = ""
    for client in isolated_clients[:20]:  # Show top 20
        vlan_info = f"VLAN {client.get('vlan', 'Unknown')}"
        isolated_html += f"""
                    <tr style="background: #fef3c7;">
                        <td><strong>{client.get('hostname', 'Unknown')}</strong></td>
                        <td><code style="font-size: 0.85em;">{client.get('mac', 'Unknown')}</code></td>
                        <td>{client.get('network', 'Unknown')}</td>
                        <td>{vlan_info}</td>
                        <td style="color: #92400e;">{client.get('note', 'Isolated')}</td>
                    </tr>
"""

    blocked_count = len(blocked_clients)
    isolated_count = len(isolated_clients)

    return f"""
            <div class="section">
                <h2>🔒 Client Security Status</h2>

                {f'''
                <div style="background: {severity_color}15; padding: 20px; border-radius: 8px; border-left: 4px solid {severity_color}; margin-bottom: 20px;">
                    <strong style="font-size: 1.2em; color: {severity_color};">⚠️ {blocked_count} Blocked Client(s) Detected</strong>
                    <p style="margin-top: 10px; color: #666;">These clients are actively blocked - possible security threats or incorrectly blocked devices.</p>
                </div>

                <h3>Blocked Clients</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Client Name</th>
                            <th>MAC Address</th>
                            <th>Connection</th>
                            <th>Block Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        {blocked_html}
                    </tbody>
                </table>
                ''' if blocked_count > 0 else ''}

                {f'''
                <h3 style="margin-top: 30px;">Isolated Clients</h3>
                <div style="background: #fffbeb; padding: 15px; border-radius: 8px; border-left: 4px solid #f59e0b; margin-bottom: 15px;">
                    <strong style="color: #92400e;">{isolated_count} Client(s) in Isolation/Guest Network</strong>
                    <p style="margin-top: 8px; color: #78350f; font-size: 0.9em;">These clients are isolated from the main network, either in guest mode or restricted VLAN.</p>
                </div>

                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Client Name</th>
                            <th>MAC Address</th>
                            <th>Network</th>
                            <th>VLAN</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {isolated_html}
                    </tbody>
                </table>
                ''' if isolated_count > 0 else ''}

                <div style="margin-top: 20px; padding: 15px; background: #f3f4f6; border-radius: 8px;">
                    <strong>About Client Security:</strong>
                    <p style="margin-top: 8px; color: #666;">
                        <strong>Blocked Clients:</strong> Devices that are actively prevented from connecting, usually due to security policies or manual blocking.
                    </p>
                    <p style="margin-top: 8px; color: #666;">
                        <strong>Isolated Clients:</strong> Devices on guest networks or restricted VLANs with limited access to your main network resources.
                    </p>
                    <p style="margin-top: 10px; color: #ef4444; font-weight: 500;">
                        ⚠️ Review blocked clients immediately - they may indicate security threats or configuration issues.
                    </p>
                </div>
            </div>
"""


def generate_packet_loss_history_html(switch_port_history):
    """Generate packet loss history visualization with trends and Chart.js for critical ports"""
    if not switch_port_history or not switch_port_history.get("port_history"):
        return ""

    import json
    from datetime import datetime

    summary = switch_port_history.get("summary", {})
    port_history = switch_port_history.get("port_history", {})
    trends = switch_port_history.get("trends", {})

    # Summary statistics
    summary_html = f"""
        <h3>📊 Packet Loss Tracking (7 Days)</h3>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px;">
            <div style="background: #f3f4f6; padding: 15px; border-radius: 8px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: #3b82f6;">{summary.get('ports_with_loss', 0)}</div>
                <div style="color: #666; font-size: 0.9em; margin-top: 5px;">Ports with Loss</div>
            </div>
            <div style="background: #d1fae5; padding: 15px; border-radius: 8px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: #10b981;">{summary.get('improving', 0)} ↗️</div>
                <div style="color: #065f46; font-size: 0.9em; margin-top: 5px;">Improving</div>
            </div>
            <div style="background: #fef3c7; padding: 15px; border-radius: 8px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: #f59e0b;">{summary.get('stable', 0)} →</div>
                <div style="color: #92400e; font-size: 0.9em; margin-top: 5px;">Stable</div>
            </div>
            <div style="background: #fee2e2; padding: 15px; border-radius: 8px; text-align: center;">
                <div style="font-size: 1.5em; font-weight: bold; color: #ef4444;">{summary.get('worsening', 0)} ↘️</div>
                <div style="color: #991b1b; font-size: 0.9em; margin-top: 5px;">Worsening</div>
            </div>
        </div>
        <p style="color: #666; font-style: italic; margin-bottom: 20px;">{summary.get('message', '')}</p>
    """

    def group_by_15min(hourly_data):
        """Group hourly data into 15-minute buckets"""
        grouped = {}
        for point in hourly_data:
            dt_str = point["datetime"]
            dt = datetime.fromisoformat(dt_str)
            minute = (dt.minute // 15) * 15
            bucket = dt.replace(minute=minute, second=0, microsecond=0)
            bucket_key = bucket.strftime("%Y-%m-%d %H:%M")

            if bucket_key not in grouped:
                grouped[bucket_key] = {
                    "packet_loss_pct": [],
                    "rx_dropped": [],
                    "tx_dropped": [],
                    "total_packets": [],
                }

            grouped[bucket_key]["packet_loss_pct"].append(point.get("packet_loss_pct", 0))
            grouped[bucket_key]["rx_dropped"].append(point.get("rx_dropped", 0))
            grouped[bucket_key]["tx_dropped"].append(point.get("tx_dropped", 0))
            grouped[bucket_key]["total_packets"].append(point.get("total_packets", 0))

        # Average values in each bucket
        result = []
        for bucket_key in sorted(grouped.keys()):
            bucket_data = grouped[bucket_key]
            result.append(
                {
                    "datetime": bucket_key,
                    "packet_loss_pct": sum(bucket_data["packet_loss_pct"])
                    / len(bucket_data["packet_loss_pct"]),
                    "rx_dropped": sum(bucket_data["rx_dropped"]) / len(bucket_data["rx_dropped"]),
                    "tx_dropped": sum(bucket_data["tx_dropped"]) / len(bucket_data["tx_dropped"]),
                    "total_packets": sum(bucket_data["total_packets"])
                    / len(bucket_data["total_packets"]),
                }
            )

        return result

    # Per-port details with Chart.js for critical ports
    ports_html = ""
    chart_script = ""
    chart_idx = 0
    for port_key, port_data in sorted(port_history.items()):
        stats = port_data.get("statistics", {})
        trend = stats.get("trend", "unknown")
        avg_loss = stats.get("avg_loss", 0)

        # Determine if this is a critical port (>1% average loss) needing detailed Chart.js
        is_critical = avg_loss > 1.0

        # Trend styling
        if trend == "improving":
            trend_color = "#10b981"
            trend_bg = "#d1fae5"
            trend_icon = "↗️"
            trend_text = "Improving"
        elif trend == "worsening":
            trend_color = "#ef4444"
            trend_bg = "#fee2e2"
            trend_icon = "↘️"
            trend_text = "Worsening"
        else:
            trend_color = "#f59e0b"
            trend_bg = "#fef3c7"
            trend_icon = "→"
            trend_text = "Stable"

        # Get hourly data
        hourly_data = port_data.get("hourly_data", [])

        # For critical ports, create Chart.js visualization with 15-min grouping
        chart_html = ""
        if is_critical and len(hourly_data) > 0:
            # Group data into 15-minute intervals
            grouped_data = group_by_15min(hourly_data)

            if grouped_data:
                labels = [point["datetime"] for point in grouped_data]
                packet_loss_data = [point["packet_loss_pct"] for point in grouped_data]
                rx_dropped_data = [point["rx_dropped"] for point in grouped_data]
                tx_dropped_data = [point["tx_dropped"] for point in grouped_data]

                # Format labels for display
                display_labels = []
                for i, label in enumerate(labels):
                    dt = datetime.fromisoformat(label)
                    if dt.minute == 0 or i % 4 == 0:
                        display_labels.append(dt.strftime("%m/%d %H:%M"))
                    else:
                        display_labels.append("")

                chart_html = f"""
                <div style="background: #fff; padding: 20px; border-radius: 8px; margin-top: 15px; border: 1px solid #e5e7eb;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <h5 style="margin: 0; color: #374151;">📈 Detailed Packet Loss Analysis</h5>
                        <span style="background: #fef2f2; color: #dc2626; padding: 4px 12px; border-radius: 12px; font-size: 11px; font-weight: 600;">
                            CRITICAL PORT
                        </span>
                    </div>
                    <div style="position: relative; height: 300px;">
                        <canvas id="packetLossChart{chart_idx}"></canvas>
                    </div>
                    <div style="margin-top: 12px; padding: 10px; background: #f9fafb; border-radius: 6px; font-size: 11px; color: #6b7280;">
                        <strong>💡 Chart shows:</strong> Total packet loss % (red), RX dropped packets (blue), TX dropped packets (purple) • Grouped in 15-minute intervals
                    </div>
                </div>
                """

                chart_script += f"""
                new Chart(document.getElementById('packetLossChart{chart_idx}'), {{
                    type: 'line',
                    data: {{
                        labels: {json.dumps(display_labels)},
                        datasets: [{{
                            label: 'Packet Loss %',
                            data: {json.dumps(packet_loss_data)},
                            borderColor: 'rgba(220, 38, 38, 1)',
                            backgroundColor: 'rgba(220, 38, 38, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 2,
                            pointHoverRadius: 6,
                            yAxisID: 'y'
                        }}, {{
                            label: 'RX Dropped',
                            data: {json.dumps(rx_dropped_data)},
                            borderColor: 'rgba(59, 130, 246, 0.8)',
                            backgroundColor: 'rgba(59, 130, 246, 0.2)',
                            borderWidth: 1.5,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 0,
                            pointHoverRadius: 4,
                            yAxisID: 'y1'
                        }}, {{
                            label: 'TX Dropped',
                            data: {json.dumps(tx_dropped_data)},
                            borderColor: 'rgba(147, 51, 234, 0.8)',
                            backgroundColor: 'rgba(147, 51, 234, 0.2)',
                            borderWidth: 1.5,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 0,
                            pointHoverRadius: 4,
                            yAxisID: 'y1'
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {{
                            mode: 'index',
                            intersect: false
                        }},
                        plugins: {{
                            legend: {{
                                position: 'bottom',
                                labels: {{
                                    padding: 12,
                                    usePointStyle: true,
                                    font: {{
                                        size: 11
                                    }}
                                }}
                            }},
                            tooltip: {{
                                mode: 'index',
                                intersect: false,
                                backgroundColor: 'rgba(0, 0, 0, 0.8)',
                                padding: 12,
                                callbacks: {{
                                    title: function(tooltipItems) {{
                                        const index = tooltipItems[0].dataIndex;
                                        const fullLabels = {json.dumps(labels)};
                                        return fullLabels[index];
                                    }}
                                }}
                            }}
                        }},
                        scales: {{
                            y: {{
                                type: 'linear',
                                display: true,
                                position: 'left',
                                beginAtZero: true,
                                grid: {{
                                    color: 'rgba(0, 0, 0, 0.05)'
                                }},
                                title: {{
                                    display: true,
                                    text: 'Packet Loss %',
                                    color: '#dc2626',
                                    font: {{
                                        size: 11,
                                        weight: 'bold'
                                    }}
                                }},
                                ticks: {{
                                    callback: function(value) {{
                                        return value.toFixed(2) + '%';
                                    }},
                                    font: {{
                                        size: 10
                                    }}
                                }}
                            }},
                            y1: {{
                                type: 'linear',
                                display: true,
                                position: 'right',
                                beginAtZero: true,
                                grid: {{
                                    drawOnChartArea: false
                                }},
                                title: {{
                                    display: true,
                                    text: 'Dropped Packets',
                                    color: '#3b82f6',
                                    font: {{
                                        size: 11,
                                        weight: 'bold'
                                    }}
                                }},
                                ticks: {{
                                    font: {{
                                        size: 10
                                    }}
                                }}
                            }},
                            x: {{
                                grid: {{
                                    color: 'rgba(0, 0, 0, 0.05)'
                                }},
                                title: {{
                                    display: true,
                                    text: 'Time (15-min intervals)',
                                    font: {{
                                        size: 11,
                                        weight: 'bold'
                                    }}
                                }},
                                ticks: {{
                                    maxRotation: 45,
                                    minRotation: 45,
                                    autoSkip: true,
                                    font: {{
                                        size: 9
                                    }}
                                }}
                            }}
                        }}
                    }}
                }});
                """

                chart_idx += 1

        # Create simple sparkline for all ports
        sparkline_html = ""
        if len(hourly_data) > 0:
            # Sample every 6 hours to keep visualization simple (max 28 bars for 7 days)
            sample_rate = max(1, len(hourly_data) // 28)
            sampled_data = hourly_data[::sample_rate]
            loss_values = [h.get("packet_loss_pct", 0) for h in sampled_data]
            max_loss = max(loss_values) if loss_values else 1

            # FIX: Ensure max_loss is never 0 to avoid division by zero
            if max_loss == 0:
                max_loss = 0.001

            # Create simple bar chart
            for loss in loss_values:
                height_pct = (loss / max_loss * 100) if max_loss > 0 else 0
                bar_color = "#ef4444" if loss > 5 else "#f59e0b" if loss > 1 else "#10b981"
                sparkline_html += f'<div style="flex: 1; height: 40px; display: flex; align-items: flex-end;"><div style="width: 100%; height: {height_pct}%; background: {bar_color}; border-radius: 2px 2px 0 0;"></div></div>'
        else:
            sparkline_html = '<div style="color: #999;">No data</div>'

        ports_html += f"""
            <div style="background: white; border: 1px solid {'#dc2626' if is_critical else '#e5e7eb'}; border-width: {'2px' if is_critical else '1px'}; border-radius: 8px; padding: 20px; margin-bottom: 15px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                    <div>
                        <h4 style="margin: 0; color: #1f2937;">{port_data.get('switch_name', 'Unknown Switch')} - {port_data.get('port_name', 'Unknown Port')}</h4>
                        <p style="margin: 5px 0 0 0; color: #6b7280; font-size: 0.9em;">Port {port_data.get('port_idx', '?')}</p>
                    </div>
                    <div style="background: {trend_bg}; color: {trend_color}; padding: 8px 16px; border-radius: 20px; font-weight: bold; font-size: 0.9em;">
                        {trend_icon} {trend_text}
                        {f" ({abs(stats.get('trend_pct', 0)):.1f}%)" if stats.get('trend_pct', 0) != 0 else ""}
                    </div>
                </div>

                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 15px; margin-bottom: 15px;">
                    <div>
                        <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 5px;">Current Loss</div>
                        <div style="font-size: 1.2em; font-weight: bold; color: {'#dc2626' if stats.get('current_loss', 0) > 1 else '#1f2937'};">{stats.get('current_loss', 0):.3f}%</div>
                    </div>
                    <div>
                        <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 5px;">Average Loss</div>
                        <div style="font-size: 1.2em; font-weight: bold; color: {'#dc2626' if avg_loss > 1 else '#1f2937'};">{avg_loss:.3f}%</div>
                    </div>
                    <div>
                        <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 5px;">Max Loss</div>
                        <div style="font-size: 1.2em; font-weight: bold; color: #ef4444;">{stats.get('max_loss', 0):.3f}%</div>
                    </div>
                    <div>
                        <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 5px;">Data Points</div>
                        <div style="font-size: 1.2em; font-weight: bold; color: #1f2937;">{stats.get('data_points', 0)}</div>
                    </div>
                </div>

                <div style="background: #f9fafb; padding: 15px; border-radius: 6px;">
                    <div style="color: #6b7280; font-size: 0.85em; margin-bottom: 10px; font-weight: 500;">7-Day Packet Loss Overview</div>
                    <div style="display: flex; gap: 2px; height: 40px;">
                        {sparkline_html}
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 5px; color: #9ca3af; font-size: 0.75em;">
                        <span>7 days ago</span>
                        <span>Now</span>
                    </div>
                </div>

                {chart_html}
            </div>
        """

    if not ports_html:
        return f"""
            <div style="margin: 20px 0;">
                {summary_html}
                <div style="background: #d1fae5; padding: 15px; border-radius: 8px; color: #065f46;">
                    <strong>✓ No ports with significant packet loss detected</strong>
                    <p style="margin-top: 8px;">All monitored switch ports are operating normally with minimal packet loss (&lt;0.1%).</p>
                </div>
            </div>
        """

    # Add Chart.js script for critical ports
    script_html = ""
    if chart_script:
        script_html = f"""
        <script>
            {chart_script}
        </script>
        """

    return f"""
        <div style="margin: 20px 0;">
            {summary_html}
            {ports_html}
            {script_html}
        </div>
    """


def generate_switch_analysis_html(switch_analysis, switch_port_history=None):
    """Generate switch analysis section with port details, PoE, and packet loss history"""
    if not switch_analysis or not switch_analysis.get("switches"):
        return ""

    switches = switch_analysis["switches"]
    poe_analysis = switch_analysis.get("poe_analysis", {})
    port_analysis = switch_analysis.get("port_analysis", {})

    switches_html = ""
    for switch in switches:
        # Port status table
        ports_html = ""
        for port in switch["ports"]:
            if not port["up"] and not port["enabled"]:
                continue  # Skip disabled/down ports

            status_color = "#10b981" if port["up"] else "#ef4444"
            status_text = "Up" if port["up"] else "Down"
            speed = f"{port['speed']}M" if port["speed"] > 0 else "---"

            # PoE info
            poe_text = ""
            if port["poe_enable"]:
                poe_power = port.get("poe_power", 0)
                if isinstance(poe_power, str):
                    poe_text = f"{poe_power}W"
                else:
                    poe_text = f"{poe_power:.1f}W"
            else:
                poe_text = "---"

            # Client name - mark AP uplink ports with network icon
            # Only use red color if there are high-severity issues on the AP port
            client_name = port.get("connected_client", "")
            if client_name:
                if port.get("is_ap"):
                    # Check if this AP port has high-severity issues
                    has_high_severity = any(
                        issue.get("severity") == "high" for issue in port.get("issues", [])
                    )
                    if has_high_severity:
                        # Critical AP uplink issues - use red and bold
                        client_display = f'<span style="color: #ef4444; font-weight: bold; font-size: 0.9em;">&#128225; {client_name}</span>'
                    else:
                        # Normal AP uplink - just add network icon, same color
                        client_display = f'<span style="color: #6366f1; font-size: 0.9em;">&#128225; {client_name}</span>'
                else:
                    client_display = (
                        f'<span style="color: #6366f1; font-size: 0.9em;">{client_name}</span>'
                    )
            else:
                client_display = '<span style="color: #9ca3af; font-size: 0.9em;">---</span>'

            port_name = port.get("name", f"Port {port['port_idx']}")

            # Main port row
            ports_html += f"""
                <tr>
                    <td>{port['port_idx']}</td>
                    <td>{port_name}</td>
                    <td>{client_display}</td>
                    <td style="color: {status_color}; font-weight: bold;">{status_text}</td>
                    <td>{speed}</td>
                    <td>{"Full" if port['full_duplex'] else "Half"}</td>
                    <td>{poe_text}</td>
                    <td>{"✓" if not port.get('issues') else f"⚠️ {len(port['issues'])}"}</td>
                </tr>
"""

            # Add inline issue rows if there are any
            if port.get("issues"):
                for issue in port["issues"]:
                    severity = issue.get("severity", "low")
                    color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#3b82f6"}.get(
                        severity, "#6b7280"
                    )
                    bg_color = {"high": "#fef2f2", "medium": "#fffbeb", "low": "#eff6ff"}.get(
                        severity, "#f9fafb"
                    )

                    issue_type = issue.get("type", "unknown").replace("_", " ").title()
                    message = issue.get("message", "Unknown issue")

                    # Format metrics inline
                    metric_display = ""
                    if issue.get("total_dropped"):
                        metric_display = f" ({issue['total_dropped']:,} total)"
                    elif issue.get("total_errors"):
                        metric_display = f" ({issue['total_errors']:,} errors)"
                    elif issue.get("speed"):
                        metric_display = (
                            f" ({issue['speed']}Mbps → {issue.get('expected_speed', 1000)}Mbps)"
                        )

                    impact = issue.get("impact", "")
                    recommendation = issue.get("recommendation", "")

                    ports_html += f"""
                <tr style="background: {bg_color}; border-left: 4px solid {color};">
                    <td colspan="2" style="padding-left: 30px; font-size: 0.9em;">
                        <strong style="color: {color};">└ {issue_type}</strong>{metric_display}
                    </td>
                    <td colspan="6" style="font-size: 0.85em; color: #666;">
                        {message}
                        {f"<br/><em style='color: {color};'>Impact:</em> {impact}" if impact else ""}
                        {f"<br/><em>Action:</em> {recommendation}" if recommendation else ""}
                    </td>
                </tr>
"""

                # Add inline Chart.js graph if history is available
                if port.get("mini_history"):
                    import json

                    history = port["mini_history"]

                    # Generate unique chart ID
                    chart_id = f"portChart_{switch['mac'].replace(':', '')}_{port['port_idx']}"

                    # Check if this is daily aggregated data (has 'date' field) or hourly
                    is_daily = "date" in history[0] if history else False

                    # Extract data for chart
                    if is_daily:
                        # Daily data: show date labels and dropped packet counts
                        labels = [h["date"] for h in history]
                        dropped_data = [h["total_dropped"] for h in history]
                        rx_dropped_data = [h["rx_dropped"] for h in history]
                        tx_dropped_data = [h["tx_dropped"] for h in history]
                        timeframe_label = f"{len(history)}-Day History"
                        max_val = max(dropped_data) if dropped_data else 0
                        min_val = min(dropped_data) if dropped_data else 0
                        avg_val = sum(dropped_data) / len(dropped_data) if dropped_data else 0
                        unit = "packets/day"
                    else:
                        # Hourly data: aggregate into 4-hour buckets for 7-day view (168 hours → 42 points)
                        # This keeps the graph readable while showing the full 7-day trend
                        from collections import defaultdict
                        from datetime import datetime

                        # Determine if this is 7-day data (>48 hours) or 24-hour data
                        is_7day = len(history) > 48

                        if is_7day:
                            # Group into 4-hour buckets for readability
                            bucket_size = 4
                            buckets = defaultdict(
                                lambda: {
                                    "rx_dropped": [],
                                    "tx_dropped": [],
                                    "total_dropped": [],
                                    "timestamps": [],
                                }
                            )

                            for h in history:
                                # Round timestamp to nearest 4-hour bucket
                                dt = datetime.fromisoformat(h["datetime"])
                                bucket_hour = (dt.hour // bucket_size) * bucket_size
                                bucket_key = f"{dt.strftime('%Y-%m-%d')} {bucket_hour:02d}:00"

                                buckets[bucket_key]["rx_dropped"].append(h.get("rx_dropped", 0))
                                buckets[bucket_key]["tx_dropped"].append(h.get("tx_dropped", 0))
                                buckets[bucket_key]["total_dropped"].append(
                                    h.get("total_dropped", 0)
                                )
                                buckets[bucket_key]["timestamps"].append(h["datetime"])

                            # Average each bucket
                            labels = []
                            rx_dropped_data = []
                            tx_dropped_data = []
                            total_dropped_data = []

                            for bucket_key in sorted(buckets.keys()):
                                bucket = buckets[bucket_key]
                                labels.append(bucket_key)
                                rx_dropped_data.append(
                                    sum(bucket["rx_dropped"]) / len(bucket["rx_dropped"])
                                    if bucket["rx_dropped"]
                                    else 0
                                )
                                tx_dropped_data.append(
                                    sum(bucket["tx_dropped"]) / len(bucket["tx_dropped"])
                                    if bucket["tx_dropped"]
                                    else 0
                                )
                                total_dropped_data.append(
                                    sum(bucket["total_dropped"]) / len(bucket["total_dropped"])
                                    if bucket["total_dropped"]
                                    else 0
                                )

                            # Calculate statistics on total dropped
                            timeframe_label = "7-Day History (4-hour average)"
                            max_val = max(total_dropped_data) if total_dropped_data else 0
                            min_val = min(total_dropped_data) if total_dropped_data else 0
                            avg_val = (
                                sum(total_dropped_data) / len(total_dropped_data)
                                if total_dropped_data
                                else 0
                            )
                            unit = "packets/4h"

                        else:
                            # 24-hour data: show hourly with full granularity
                            labels = [
                                h["datetime"].split("T")[1].split(":")[0] + ":00" for h in history
                            ]
                            rx_dropped_data = [h.get("rx_dropped", 0) for h in history]
                            tx_dropped_data = [h.get("tx_dropped", 0) for h in history]
                            total_dropped_data = [h.get("total_dropped", 0) for h in history]

                            timeframe_label = "24-Hour History (hourly)"
                            max_val = max(total_dropped_data) if total_dropped_data else 0
                            min_val = min(total_dropped_data) if total_dropped_data else 0
                            avg_val = (
                                sum(total_dropped_data) / len(total_dropped_data)
                                if total_dropped_data
                                else 0
                            )
                            unit = "packets/hour"

                    # Build dataset configuration based on data type
                    # All data types now show RX/TX dropped packets
                    datasets_js = f"""
                                datasets.push({{
                                    label: 'Total Dropped',
                                    data: {json.dumps(total_dropped_data if not is_daily else dropped_data)},
                                    borderColor: '{color}',
                                    backgroundColor: '{color}33',
                                    borderWidth: 2,
                                    fill: true,
                                    tension: 0.3,
                                    pointRadius: 3 if is_daily else 1,
                                    pointHoverRadius: 6
                                }});
                                datasets.push({{
                                    label: 'RX Dropped',
                                    data: {json.dumps(rx_dropped_data)},
                                    borderColor: '#3b82f6',
                                    backgroundColor: '#3b82f633',
                                    borderWidth: 1.5,
                                    fill: false,
                                    tension: 0.3,
                                    pointRadius: 2 if is_daily else 1,
                                    pointHoverRadius: 5
                                }});
                                datasets.push({{
                                    label: 'TX Dropped',
                                    data: {json.dumps(tx_dropped_data)},
                                    borderColor: '#8b5cf6',
                                    backgroundColor: '#8b5cf633',
                                    borderWidth: 1.5,
                                    fill: false,
                                    tension: 0.3,
                                    pointRadius: 2 if is_daily else 1,
                                    pointHoverRadius: 5
                                }});
"""
                    tooltip_format = "context.dataset.label + ': ' + context.parsed.y.toLocaleString() + ' packets'"
                    x_axis_label = f"Time - {timeframe_label}"
                    y_axis_label = "Dropped Packets"

                    ports_html += f"""
                <tr style="background: {bg_color};">
                    <td colspan="8" style="padding: 20px;">
                        <div style="background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                            <h4 style="margin: 0 0 10px 0; color: #374151; font-size: 0.95em;">
                                📊 {timeframe_label}
                                <span style="font-weight: normal; color: #6b7280; font-size: 0.9em;">
                                    (Max: {max_val:,.0f if is_daily else .2f}{unit} | Avg: {avg_val:,.0f if is_daily else .2f}{unit} | Min: {min_val:,.0f if is_daily else .2f}{unit})
                                </span>
                            </h4>
                            <canvas id="{chart_id}" style="max-height: 200px;"></canvas>
                            <script>
                            (function() {{
                                const ctx = document.getElementById('{chart_id}').getContext('2d');
                                const datasets = [];

{datasets_js}
                                new Chart(ctx, {{
                                    type: 'line',
                                    data: {{
                                        labels: {json.dumps(labels)},
                                        datasets: datasets
                                    }},
                                    options: {{
                                        responsive: true,
                                        maintainAspectRatio: true,
                                        plugins: {{
                                            legend: {{
                                                display: true,
                                                position: 'top',
                                                labels: {{
                                                    boxWidth: 12,
                                                    padding: 10,
                                                    font: {{ size: 11 }}
                                                }}
                                            }},
                                            tooltip: {{
                                                mode: 'index',
                                                intersect: false,
                                                callbacks: {{
                                                    label: function(context) {{
                                                        return {tooltip_format};
                                                    }}
                                                }}
                                            }}
                                        }},
                                        scales: {{
                                            x: {{
                                                display: true,
                                                title: {{
                                                    display: true,
                                                    text: '{x_axis_label}',
                                                    font: {{ size: 11 }}
                                                }},
                                                ticks: {{
                                                    maxRotation: 45,
                                                    minRotation: 45,
                                                    font: {{ size: 10 }}
                                                }}
                                            }},
                                            y: {{
                                                display: true,
                                                title: {{
                                                    display: true,
                                                    text: '{y_axis_label}',
                                                    font: {{ size: 11 }}
                                                }},
                                                beginAtZero: true,
                                                ticks: {{
                                                    font: {{ size: 10 }}
                                                }}
                                            }}
                                        }}
                                    }}
                                }});
                            }})();
                            </script>
                        </div>
                    </td>
                </tr>
"""

        # PoE utilization bar
        poe_util_html = ""
        if switch["poe_capable"]:
            poe_util = (
                (switch["poe_usage"] / switch["poe_max"] * 100) if switch["poe_max"] > 0 else 0
            )
            poe_color = "#ef4444" if poe_util > 90 else "#f59e0b" if poe_util > 75 else "#10b981"
            poe_util_html = f"""
                <div style="margin-bottom: 20px; padding: 15px; background: #f9fafb; border-radius: 8px;">
                    <strong>PoE Utilization:</strong>
                    <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px;">
                        <div style="flex-grow: 1; background: #e5e7eb; height: 24px; border-radius: 4px; overflow: hidden;">
                            <div style="width: {poe_util}%; height: 100%; background: {poe_color}; transition: width 0.3s;"></div>
                        </div>
                        <span style="font-weight: bold; color: {poe_color};">{switch['poe_usage']:.1f}W / {switch['poe_max']}W ({poe_util:.0f}%)</span>
                    </div>
                </div>
"""

        # Issues summary
        issues_summary_html = ""
        if switch.get("issues"):
            high_issues = [i for i in switch["issues"] if i.get("severity") == "high"]
            medium_issues = [i for i in switch["issues"] if i.get("severity") == "medium"]
            low_issues = [i for i in switch["issues"] if i.get("severity") == "low"]

            if high_issues or medium_issues:
                issues_summary_html = f"""
                <div style="margin-bottom: 20px; padding: 15px; background: #fef2f2; border-radius: 8px; border-left: 4px solid #ef4444;">
                    <strong style="color: #ef4444;">⚠️ Issues Detected:</strong>
                    <p style="margin-top: 8px; color: #666;">
                        {len(high_issues)} high priority, {len(medium_issues)} medium priority, {len(low_issues)} low priority
                    </p>
                </div>
"""

        switches_html += f"""
            <div style="margin-bottom: 40px;">
                <h3>🔌 {switch['name']}</h3>
                <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px;">
                    <div style="background: #f9fafb; padding: 15px; border-radius: 8px;">
                        <div style="color: #6b7280; font-size: 0.9em;">Model</div>
                        <div style="font-size: 1.2em; font-weight: bold; margin-top: 5px;">{switch['model']}</div>
                    </div>
                    <div style="background: #f9fafb; padding: 15px; border-radius: 8px;">
                        <div style="color: #6b7280; font-size: 0.9em;">Ports Active</div>
                        <div style="font-size: 1.2em; font-weight: bold; margin-top: 5px;">{switch['active_ports']} / {switch['total_ports']}</div>
                    </div>
                    <div style="background: #f9fafb; padding: 15px; border-radius: 8px;">
                        <div style="color: #6b7280; font-size: 0.9em;">Uptime</div>
                        <div style="font-size: 1.2em; font-weight: bold; margin-top: 5px;">{switch['uptime']/86400:.1f} days</div>
                    </div>
                </div>

                {poe_util_html}
                {issues_summary_html}

                <h4>Port Status</h4>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Port</th>
                            <th>Name</th>
                            <th>Connected Client</th>
                            <th>Status</th>
                            <th>Speed</th>
                            <th>Duplex</th>
                            <th>PoE</th>
                            <th>Issues</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ports_html if ports_html else '<tr><td colspan="8">No active ports</td></tr>'}
                    </tbody>
                </table>
            </div>
"""

    # Recommendations
    recs_html = ""
    if switch_analysis.get("recommendations"):
        for rec in switch_analysis["recommendations"]:
            priority = rec.get("priority", "low")
            priority_color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#3b82f6"}.get(
                priority, "#6b7280"
            )
            recs_html += f"""
                <div style="background: {priority_color}15; padding: 15px; border-radius: 8px; border-left: 4px solid {priority_color}; margin-bottom: 10px;">
                    <strong style="color: {priority_color}; text-transform: uppercase; font-size: 0.85em;">{priority} Priority</strong>
                    <div style="margin-top: 8px;"><strong>{rec.get('message', '')}</strong></div>
                    <div style="margin-top: 8px; color: #666;">{rec.get('recommendation', '')}</div>
                    <div style="margin-top: 8px; color: #666; font-style: italic;">Impact: {rec.get('impact', '')}</div>
                </div>
"""

    # Generate packet loss history section
    packet_loss_html = (
        generate_packet_loss_history_html(switch_port_history) if switch_port_history else ""
    )

    return f"""
        <div class="section">
            <h2>🔌 Switch Analysis</h2>
            {switches_html}

            {packet_loss_html}

            {f'<h3>Recommendations</h3>{recs_html}' if recs_html else ''}

            <div style="margin-top: 20px; padding: 15px; background: #f3f4f6; border-radius: 8px;">
                <strong>About Switch Optimization:</strong>
                <p style="margin-top: 8px; color: #666;">Regular monitoring of switch ports, PoE usage, and cabling helps maintain optimal network performance and reliability.</p>
                <p style="margin-top: 10px;">
                    <a href="https://help.ui.com/hc/en-us/articles/360008365334-UniFi-USW-Troubleshooting-Slow-Ethernet-Link-Speeds" target="_blank"
                       style="display: inline-block; padding: 6px 12px; background: #3b82f6; color: white;
                              text-decoration: none; border-radius: 4px; font-size: 0.9em; font-weight: 500;">
                        📚 Learn More About Switch Troubleshooting
                    </a>
                </p>
            </div>
        </div>
"""
