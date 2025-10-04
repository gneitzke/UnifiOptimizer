#!/usr/bin/env python3
"""
HTML Report Generator for UniFi Network Analysis

Generates comprehensive HTML reports with all expert analysis findings,
recommendations, RSSI data, and executive summaries.
"""

from datetime import datetime
from pathlib import Path
import json


def generate_html_report(analysis_data, recommendations, site_name, output_dir='reports'):
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
    ap_analysis = analysis_data.get('ap_analysis', {})
    channel_analysis = analysis_data.get('channel_analysis', {})
    client_health = analysis_data.get('client_health', {})
    signal_distribution = analysis_data.get('signal_distribution', {})
    mesh_aps = analysis_data.get('mesh_analysis', {})
    
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
    </style>
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
    
    # Network Health Score Section (prominently at top)
    health_score = analysis_data.get('health_score')
    if health_score:
        html_content += generate_network_health_score_html(health_score)
    
    # Executive Summary Section
    html_content += generate_executive_summary_html(analysis_data, recommendations)
    
    # Network Health Analysis Section
    health_analysis = analysis_data.get('health_analysis')
    if health_analysis:
        html_content += generate_network_health_html(health_analysis)
    
    # Key Metrics Section
    html_content += generate_key_metrics_html(analysis_data, client_health)
    
    # RSSI Distribution Section
    html_content += generate_rssi_distribution_html(signal_distribution)
    
    # DFS Analysis Section
    dfs_analysis = analysis_data.get('dfs_analysis')
    if dfs_analysis:
        html_content += generate_dfs_analysis_html(dfs_analysis)
    
    # Band Steering Analysis Section
    band_steering = analysis_data.get('band_steering_analysis')
    if band_steering:
        html_content += generate_band_steering_html(band_steering)
    
    # Airtime Analysis Section
    airtime_analysis = analysis_data.get('airtime_analysis')
    if airtime_analysis:
        html_content += generate_airtime_analysis_html(airtime_analysis)
    
    # Client Capabilities Section
    client_capabilities = analysis_data.get('client_capabilities')
    if client_capabilities:
        html_content += generate_client_capabilities_html(client_capabilities)
    
    # Manufacturer Analysis Section
    manufacturer_analysis = analysis_data.get('manufacturer_analysis')
    if manufacturer_analysis:
        from core.manufacturer_analyzer import generate_manufacturer_insights_html
        html_content += generate_manufacturer_insights_html(manufacturer_analysis)
    
    # Switch Analysis Section
    switch_analysis = analysis_data.get('switch_analysis')
    if switch_analysis and switch_analysis.get('switches'):
        html_content += generate_switch_analysis_html(switch_analysis)
    
    # Access Points Section
    html_content += generate_ap_overview_html(ap_analysis, mesh_aps)
    
    # Recommendations Section
    html_content += generate_recommendations_html(recommendations)
    
    # Channel Analysis Section
    html_content += generate_channel_analysis_html(channel_analysis)
    
    # Client Health Section
    html_content += generate_client_health_html(client_health)
    
    # Findings Section
    html_content += generate_findings_html(analysis_data)
    
    # Footer
    html_content += f"""
        </div>
        
        <div class="footer">
            <p>Generated by UniFi Network Optimizer</p>
            <p>Report generated on {datetime.now().strftime("%Y-%m-%d at %H:%M:%S")}</p>
            <p><a href="https://github.com/yourusername/unifi-optimizer">UniFi Optimizer on GitHub</a></p>
        </div>
    </div>
</body>
</html>
"""
    
    # Write to file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return str(filepath)


def generate_executive_summary_html(analysis_data, recommendations):
    """Generate executive summary section"""
    findings_count = len(analysis_data.get('findings', []))
    recommendations_count = len(recommendations)
    
    # Get infrastructure counts
    devices = analysis_data.get('devices', [])
    ap_count = len([d for d in devices if d.get('type') == 'uap'])
    switch_count = len([d for d in devices if d.get('type') == 'usw'])
    
    # Get switch issues
    switch_analysis = analysis_data.get('switch_analysis', {})
    switch_issues = len(switch_analysis.get('issues', []))
    switch_high_issues = len([i for i in switch_analysis.get('issues', []) if i.get('severity') == 'high'])
    
    # Get wireless issues
    airtime = analysis_data.get('airtime_analysis', {})
    saturated_aps = len(airtime.get('saturated_aps', []))
    
    # Build infrastructure summary
    infra_summary = f"{ap_count} access point{'s' if ap_count != 1 else ''}"
    if switch_count > 0:
        infra_summary += f" and {switch_count} managed switch{'es' if switch_count != 1 else ''}"
    
    # Build issues summary
    issues_parts = []
    if findings_count > 0:
        issues_parts.append(f"{findings_count} wireless finding{'s' if findings_count != 1 else ''}")
    if switch_issues > 0:
        issues_parts.append(f"{switch_issues} switch issue{'s' if switch_issues != 1 else ''}")
    
    issues_summary = " and ".join(issues_parts) if issues_parts else "no critical issues"
    
    # Critical issues highlight
    critical_highlights = []
    if switch_high_issues > 0:
        critical_highlights.append(f"{switch_high_issues} high-severity switch port issue{'s' if switch_high_issues != 1 else ''}")
    if saturated_aps > 0:
        critical_highlights.append(f"{saturated_aps} saturated AP{'s' if saturated_aps != 1 else ''}")
    
    critical_text = ""
    if critical_highlights:
        critical_text = f" Critical issues include {' and '.join(critical_highlights)}."
    
    # Build impact statement based on what issues exist
    impact_parts = []
    
    # Wireless improvements
    if findings_count > 0 or saturated_aps > 0:
        impact_parts.append("reduce wireless interference")
        impact_parts.append("improve mesh reliability")
        impact_parts.append("optimize coverage patterns")
    
    # Physical infrastructure improvements
    if switch_issues > 0:
        all_switch_issues = switch_analysis.get('issues', [])
        slow_links = [i for i in all_switch_issues if i.get('type') == 'slow_link']
        dropped_packets = [i for i in all_switch_issues if i.get('type') == 'dropped_packets']
        high_errors = [i for i in all_switch_issues if i.get('type') == 'high_errors']
        half_duplex = [i for i in all_switch_issues if i.get('type') == 'half_duplex']
        
        if slow_links:
            impact_parts.append("restore full gigabit speeds on degraded ports")
        if dropped_packets or high_errors:
            impact_parts.append("eliminate packet loss and network instability")
        if half_duplex:
            impact_parts.append("fix duplex mismatches")
    
    impact_statement = ", ".join(impact_parts[:-1]) + (f", and {impact_parts[-1]}" if len(impact_parts) > 1 else impact_parts[0] if impact_parts else "improve network performance")
    
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
            </div>
"""


def generate_key_metrics_html(analysis_data, client_health):
    """Generate key metrics cards"""
    # Get AP count from multiple possible sources
    ap_analysis = analysis_data.get('ap_analysis', {})
    ap_count = ap_analysis.get('total_aps', 0)
    if ap_count == 0:
        ap_count = len(ap_analysis.get('access_points', []))
    if ap_count == 0:
        # Try counting from devices
        devices = analysis_data.get('devices', [])
        ap_count = len([d for d in devices if d.get('type') == 'uap'])
    
    # Get client count
    client_analysis = analysis_data.get('client_analysis', {})
    client_count = client_analysis.get('total_clients', 0)
    if client_count == 0:
        client_count = len(client_health.get('health_scores', []))
    if client_count == 0:
        # Try counting from clients
        clients = analysis_data.get('clients', [])
        client_count = len(clients)
    
    # Get mesh count
    mesh_aps = ap_analysis.get('mesh_aps', [])
    mesh_count = len(mesh_aps)
    
    # Get issues count from multiple sources
    issues_count = len([f for f in analysis_data.get('findings', []) if f.get('severity') in ['high', 'critical']])
    
    # Also count from recommendations
    recommendations = analysis_data.get('recommendations', [])
    high_priority_recs = len([r for r in recommendations if r.get('priority') == 'high'])
    
    # Also count from advanced analysis
    dfs_analysis = analysis_data.get('dfs_analysis', {})
    if dfs_analysis.get('severity') == 'high':
        issues_count += 1
    
    band_steering = analysis_data.get('band_steering_analysis', {})
    if band_steering.get('severity') == 'high':
        issues_count += 1
    
    airtime = analysis_data.get('airtime_analysis', {})
    saturated_count = len(airtime.get('saturated_aps', []))
    issues_count += saturated_count
    
    # Get switch metrics
    switch_analysis = analysis_data.get('switch_analysis', {})
    switches = switch_analysis.get('switches', [])
    switch_count = len(switches)
    
    # Count switch ports and issues
    total_switch_ports = sum(s.get('active_ports', 0) for s in switches)
    switch_issues = len(switch_analysis.get('issues', []))
    issues_count += len([i for i in switch_analysis.get('issues', []) if i.get('severity') == 'high'])
    
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
    
    if switch_count > 0:
        stats_cards += f"""
                    <div class="stat-card">
                        <div class="value">{total_switch_ports}</div>
                        <div class="label">Switch Ports Active</div>
                    </div>
"""
    else:
        stats_cards += f"""
                    <div class="stat-card">
                        <div class="value">{mesh_count}</div>
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


def generate_network_health_html(health_analysis):
    """Generate comprehensive network health analysis section"""
    if not health_analysis:
        return ""
    
    overall_score = health_analysis.get('overall_score', 100)
    severity = health_analysis.get('severity', 'low')
    categories = health_analysis.get('categories', {})
    issues = health_analysis.get('issues', [])
    recommendations = health_analysis.get('recommendations', [])
    
    # Determine score color and status
    if overall_score >= 90:
        score_color = '#10b981'
        score_bg = '#d1fae5'
        status_text = 'Excellent'
        status_emoji = '✅'
    elif overall_score >= 75:
        score_color = '#f59e0b'
        score_bg = '#fef3c7'
        status_text = 'Good'
        status_emoji = '⚠️'
    elif overall_score >= 50:
        score_color = '#ef4444'
        score_bg = '#fee2e2'
        status_text = 'Fair'
        status_emoji = '🔴'
    else:
        score_color = '#dc2626'
        score_bg = '#fecaca'
        status_text = 'Poor'
        status_emoji = '🔴'
    
    # Build HTML
    html = f"""
            <div class="section">
                <h2>🏥 Network Health Analysis</h2>
                
                <!-- Overall Score -->
                <div style="background: {score_bg}; padding: 30px; border-radius: 12px; margin-bottom: 30px; border-left: 6px solid {score_color};">
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 20px;">
                        <div>
                            <div style="font-size: 3em; font-weight: bold; color: {score_color}; margin-bottom: 10px;">
                                {status_emoji} {overall_score}/100
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
        category_display = category_data.get('category', category_name.replace('_', ' ').title())
        category_status = category_data.get('status', 'healthy')
        category_issues = category_data.get('issues', [])
        
        # Status colors
        if category_status == 'critical':
            card_color = '#ef4444'
            card_bg = '#fee2e2'
            status_icon = '🔴'
        elif category_status == 'warning':
            card_color = '#f59e0b'
            card_bg = '#fef3c7'
            status_icon = '⚠️'
        else:
            card_color = '#10b981'
            card_bg = '#d1fae5'
            status_icon = '✅'
        
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
    critical_issues = [i for i in issues if i.get('severity') == 'high']
    if critical_issues:
        html += """
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #ef4444; margin-bottom: 15px;">🔴 Critical Issues Requiring Immediate Attention</h3>
"""
        
        for issue in critical_issues:
            device = issue.get('device', issue.get('switch', 'Network'))
            message = issue.get('message', 'Unknown issue')
            impact = issue.get('impact', '')
            recommendation = issue.get('recommendation', '')
            
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
    medium_issues = [i for i in issues if i.get('severity') == 'medium']
    if medium_issues:
        html += """
                <div style="margin-bottom: 30px;">
                    <h3 style="color: #f59e0b; margin-bottom: 15px;">⚠️ Issues Requiring Attention</h3>
                    <div style="max-height: 400px; overflow-y: auto;">
"""
        
        for issue in medium_issues[:10]:  # Show top 10
            device = issue.get('device', issue.get('switch', issue.get('ap', 'Network')))
            message = issue.get('message', 'Unknown issue')
            recommendation = issue.get('recommendation', '')
            
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
        
        high_priority_recs = [r for r in recommendations if r.get('priority') == 'high']
        other_recs = [r for r in recommendations if r.get('priority') != 'high']
        
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
    
    excellent = signal_distribution.get('excellent', 0)
    good = signal_distribution.get('good', 0)
    fair = signal_distribution.get('fair', 0)
    poor = signal_distribution.get('poor', 0)
    very_poor = signal_distribution.get('very_poor', 0)
    wired = signal_distribution.get('wired', 0)
    
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


def generate_ap_overview_html(ap_analysis, mesh_aps):
    """Generate access points overview table"""
    aps = ap_analysis.get('access_points', [])
    if not aps:
        return ""
    
    rows = ""
    for ap in aps:
        name = ap.get('name', 'Unknown')
        model = ap.get('model', 'Unknown')
        is_mesh = ap.get('_id') in [m.get('_id') for m in mesh_aps.get('mesh_aps', [])]
        clients_ng = ap.get('num_sta', {}).get('ng', 0)
        clients_na = ap.get('num_sta', {}).get('na', 0)
        total_clients = clients_ng + clients_na
        
        mesh_badge = '<span class="badge mesh">Mesh</span>' if is_mesh else ''
        
        rows += f"""
                    <tr>
                        <td><strong>{name}</strong> {mesh_badge}</td>
                        <td>{model}</td>
                        <td>{total_clients}</td>
                        <td>{clients_ng}</td>
                        <td>{clients_na}</td>
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
                            <th>2.4GHz</th>
                            <th>5GHz</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
"""


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
        'channel': 'https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design',
        'power': 'https://help.ui.com/hc/en-us/articles/360039664734-UniFi-Best-Practices-for-Wireless-Coverage',
        'band_steering': 'https://help.ui.com/hc/en-us/articles/115012700547-UniFi-Understanding-and-Using-Band-Steering',
        'fast_roaming': 'https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design#5',
        'airtime_fairness': 'https://help.ui.com/hc/en-us/articles/115004662107',
        'mesh': 'https://help.ui.com/hc/en-us/articles/115002262328-UniFi-Wireless-Meshing',
        'dfs': 'https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design#3',
        'legacy_devices': 'https://community.ui.com/questions/Legacy-client-support/b2c2c2ec-f0f4-4848-9e39-b8652e5e8267',
        'default': 'https://help.ui.com/hc/en-us/articles/115004662107-UniFi-Best-Practices-for-Wireless-Network-Design'
    }
    
    def get_learn_more_url(rec):
        """Determine the best learn more URL for this recommendation"""
        action = rec.get('action', '')
        rec_type = rec.get('type', '')
        message = rec.get('message', '').lower()
        
        if action == 'channel_change' or 'channel' in message:
            return learn_more_urls['channel']
        elif action == 'power_change' or 'power' in message or 'transmit' in message:
            return learn_more_urls['power']
        elif 'band steering' in message or 'band_steering' in rec_type:
            return learn_more_urls['band_steering']
        elif 'roaming' in message or '802.11r' in message:
            return learn_more_urls['fast_roaming']
        elif 'airtime' in message or 'legacy' in rec_type:
            return learn_more_urls['legacy_devices']
        elif 'mesh' in message:
            return learn_more_urls['mesh']
        elif 'dfs' in message or 'radar' in message:
            return learn_more_urls['dfs']
        else:
            return learn_more_urls['default']
    
    items = ""
    for i, rec in enumerate(recommendations, 1):
        priority = rec.get('priority', 'medium')
        device_name = rec.get('device', {}).get('name', 'Unknown Device')
        
        # Handle both recommendation formats (expert analyzer and converted)
        message = rec.get('message', rec.get('reason', 'Optimization recommended'))
        recommendation = rec.get('recommendation', '')
        
        # If no explicit recommendation, build it from action details
        if not recommendation:
            action = rec.get('action', '')
            if action == 'channel_change':
                current = rec.get('current_channel', 'N/A')
                new = rec.get('new_channel', 'N/A')
                radio = rec.get('radio', 'unknown')
                band = '2.4GHz' if radio == 'ng' else '5GHz'
                recommendation = f"Change {band} channel from {current} to {new}"
            elif action == 'power_change':
                current = rec.get('current_power', 'N/A')
                new = rec.get('new_power', 'N/A')
                radio = rec.get('radio', 'unknown')
                band = '2.4GHz' if radio == 'ng' else '5GHz'
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
    health_scores = client_health.get('health_scores', [])
    if not health_scores:
        return ""
    
    # Count clients by grade
    grades = {}
    for client in health_scores:
        grade = client.get('grade', 'F')
        grades[grade] = grades.get(grade, 0) + 1
    
    grade_rows = ""
    for grade in ['A', 'B', 'C', 'D', 'F']:
        count = grades.get(grade, 0)
        if count > 0:
            grade_rows += f"""
                <tr>
                    <td><strong>{grade}</strong></td>
                    <td>{count}</td>
                </tr>
"""
    
    return f"""
            <div class="section">
                <h2>👥 Client Health Summary</h2>
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


def generate_findings_html(analysis_data):
    """Generate findings section"""
    findings = analysis_data.get('findings', [])
    if not findings:
        return ""
    
    findings_html = ""
    for finding in findings:
        severity = finding.get('severity', 'info')
        message = finding.get('message', '')
        device = finding.get('device', 'Network')
        
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
    
    score = health_score.get('score', 0)
    grade = health_score.get('grade', 'N/A')
    status = health_score.get('status', 'Unknown')
    details = health_score.get('details', {})
    
    # Color based on grade
    if grade == 'A':
        color = '#10b981'  # green
    elif grade == 'B':
        color = '#3b82f6'  # blue
    elif grade == 'C':
        color = '#f59e0b'  # amber
    elif grade == 'D':
        color = '#f97316'  # orange
    else:
        color = '#ef4444'  # red
    
    # Build details breakdown
    details_html = ""
    detail_items = [
        ('RSSI Score', details.get('rssi_score', 0), 30),
        ('Airtime Score', details.get('airtime_score', 0), 20),
        ('Distribution Score', details.get('distribution_score', 0), 20),
        ('Mesh Score', details.get('mesh_score', 0), 15),
        ('Issues Score', details.get('issues_score', 0), 15)
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
    if not dfs_analysis or dfs_analysis.get('total_events', 0) == 0:
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
    
    total_events = dfs_analysis.get('total_events', 0)
    severity = dfs_analysis.get('severity', 'ok')
    events_by_ap = dfs_analysis.get('events_by_ap', {})
    affected_channels = dfs_analysis.get('affected_channels', [])
    recommendations = dfs_analysis.get('recommendations', [])
    
    severity_color = {'high': '#ef4444', 'medium': '#f59e0b', 'ok': '#10b981'}.get(severity, '#6b7280')
    
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
        priority = rec.get('priority', 'low')
        priority_color = {'high': '#ef4444', 'medium': '#f59e0b', 'low': '#10b981'}.get(priority, '#6b7280')
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
    
    misplaced_count = band_steering.get('dual_band_clients_on_2ghz', 0)
    misplaced_clients = band_steering.get('misplaced_clients', [])
    band_steering_enabled = band_steering.get('band_steering_enabled', {})
    severity = band_steering.get('severity', 'ok')
    recommendations = band_steering.get('recommendations', [])
    
    # AP configuration table
    ap_config_html = ""
    for ap_name, enabled in band_steering_enabled.items():
        status_color = '#10b981' if enabled else '#ef4444'
        status_text = 'Enabled' if enabled else 'Disabled'
        ap_config_html += f"""
                    <tr>
                        <td>{ap_name}</td>
                        <td style="color: {status_color}; font-weight: bold;">{status_text}</td>
                    </tr>
"""
    
    # Misplaced clients table
    clients_html = ""
    for client in misplaced_clients[:10]:  # Show top 10
        clients_html += f"""
                    <tr>
                        <td>{client.get('hostname', 'Unknown')}</td>
                        <td>{client.get('ap', 'Unknown')}</td>
                        <td>{client.get('rssi', 'N/A')} dBm</td>
                        <td>{client.get('radio_proto', 'N/A')}</td>
                    </tr>
"""
    
    severity_color = {'high': '#ef4444', 'medium': '#f59e0b', 'ok': '#10b981'}.get(severity, '#6b7280')
    
    return f"""
            <div class="section">
                <h2>🔄 Band Steering Analysis</h2>
                <div style="background: {severity_color}15; padding: 20px; border-radius: 8px; border-left: 4px solid {severity_color}; margin-bottom: 20px;">
                    <strong style="font-size: 1.2em; color: {severity_color};">{misplaced_count} Dual-Band Client(s) on 2.4GHz</strong>
                    <p style="margin-top: 10px; color: #666;">These clients support 5GHz but are connected to 2.4GHz, likely due to disabled band steering or poor 5GHz coverage.</p>
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
                <h3 style="margin-top: 30px;">Misplaced Clients</h3>
                <table class="ap-table">
                    <thead>
                        <tr>
                            <th>Client</th>
                            <th>Connected AP</th>
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
                    <p style="margin-top: 8px; color: #666;">Band steering encourages dual-band capable clients to use the less congested 5GHz band, improving overall network performance.</p>
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


def generate_airtime_analysis_html(airtime_analysis):
    """Generate airtime utilization section with time-series visualization"""
    if not airtime_analysis:
        return ""
    
    ap_utilization = airtime_analysis.get('ap_utilization', {})
    saturated_aps = airtime_analysis.get('saturated_aps', [])
    time_series = airtime_analysis.get('time_series', {})
    
    # Calculate average utilization from time series data
    ap_averages = {}
    for ap_key, data_points in time_series.items():
        if data_points:
            airtime_values = [p.get('airtime_pct', 0) for p in data_points if isinstance(p, dict)]
            if airtime_values:
                ap_averages[ap_key] = sum(airtime_values) / len(airtime_values)
    
    # Group by AP name (without band suffix)
    ap_grouped = {}
    for ap_key, avg_util in ap_averages.items():
        # Extract AP name and band (e.g., "Hallway (2.4GHz)" -> "Hallway", "2.4GHz")
        if '(' in ap_key and ')' in ap_key:
            ap_name = ap_key.split('(')[0].strip()
            band = ap_key.split('(')[1].split(')')[0].strip()
        else:
            ap_name = ap_key
            band = "Unknown"
        
        if ap_name not in ap_grouped:
            ap_grouped[ap_name] = {}
        
        ap_grouped[ap_name][band] = {
            'avg': avg_util,
            'current': ap_utilization.get(ap_key, {}).get('airtime_pct', 0),
            'clients': ap_utilization.get(ap_key, {}).get('clients', 0),
            'full_key': ap_key
        }
    
    # Build average utilization summary cards (grouped by AP)
    avg_summary_html = ""
    if ap_grouped:
        avg_summary_html = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-bottom: 30px;">'
        
        # Sort by worst utilization across bands
        sorted_aps = sorted(ap_grouped.items(), 
                          key=lambda x: max(band_data['avg'] for band_data in x[1].values()), 
                          reverse=True)
        
        for ap_name, bands_data in sorted_aps:
            # Determine overall color based on worst band
            max_util = max(band_data['avg'] for band_data in bands_data.values())
            
            if max_util > 70:
                border_color = '#ef4444'  # Red
                emoji = '🔴'
            elif max_util > 50:
                border_color = '#f59e0b'  # Yellow
                emoji = '🟡'
            else:
                border_color = '#10b981'  # Green
                emoji = '🟢'
            
            avg_summary_html += f'''
                <div style="border: 2px solid {border_color}; padding: 15px; border-radius: 8px; background: white;">
                    <div style="font-weight: bold; font-size: 16px; margin-bottom: 12px;">{emoji} {ap_name}</div>
            '''
            
            # Add each band's data
            for band in ['2.4GHz', '5GHz']:
                if band in bands_data:
                    band_info = bands_data[band]
                    avg_util = band_info['avg']
                    current_util = band_info['current']
                    clients = band_info['clients']
                    
                    # Color for this specific band
                    if avg_util > 70:
                        band_color = '#ef4444'
                        status = 'Needs Attention'
                    elif avg_util > 50:
                        band_color = '#f59e0b'
                        status = 'Monitor'
                    else:
                        band_color = '#10b981'
                        status = 'Good'
                    
                    avg_summary_html += f'''
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
                    '''
            
            avg_summary_html += '</div>'
        
        avg_summary_html += '</div>'
    
    # Build utilization table
    util_html = ""
    for ap_key, data in ap_utilization.items():
        airtime_pct = data.get('airtime_pct', 0)
        clients = data.get('clients', 0)
        
        # Color code by utilization
        if airtime_pct > 70:
            color = '#ef4444'
            status = 'Critical'
        elif airtime_pct > 50:
            color = '#f59e0b'
            status = 'Warning'
        else:
            color = '#10b981'
            status = 'Good'
        
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
        
        # Create chart for each AP
        for idx, (ap_key, data_points) in enumerate(time_series.items()):
            if not data_points:
                continue
            
            # Prepare data for Chart.js
            labels = [point['datetime'] for point in data_points]
            airtime_data = [point['airtime_pct'] for point in data_points]
            tx_data = [point['tx_pct'] for point in data_points]
            rx_data = [point['rx_pct'] for point in data_points]
            client_data = [point['clients'] for point in data_points]
            
            # Determine color based on average airtime
            avg_airtime = sum(airtime_data) / len(airtime_data) if airtime_data else 0
            if avg_airtime > 70:
                chart_color = 'rgba(239, 68, 68, 0.7)'  # red
            elif avg_airtime > 50:
                chart_color = 'rgba(245, 158, 11, 0.7)'  # yellow
            else:
                chart_color = 'rgba(16, 185, 129, 0.7)'  # green
            
            charts_html += f"""
                <div style="margin: 30px 0; padding: 20px; background: #f9fafb; border-radius: 8px;">
                    <h4 style="margin-bottom: 15px;">{ap_key} - Historical Airtime</h4>
                    <canvas id="airtimeChart{idx}" style="max-height: 300px;"></canvas>
                </div>
"""
            
            chart_script += f"""
                new Chart(document.getElementById('airtimeChart{idx}'), {{
                    type: 'line',
                    data: {{
                        labels: {json.dumps([dt.split('T')[1][:5] for dt in labels])},
                        datasets: [{{
                            label: 'Total Airtime %',
                            data: {json.dumps(airtime_data)},
                            borderColor: '{chart_color}',
                            backgroundColor: '{chart_color}',
                            fill: false,
                            tension: 0.4
                        }}, {{
                            label: 'TX %',
                            data: {json.dumps(tx_data)},
                            borderColor: 'rgba(99, 102, 241, 0.7)',
                            backgroundColor: 'rgba(99, 102, 241, 0.3)',
                            fill: true,
                            tension: 0.4
                        }}, {{
                            label: 'RX %',
                            data: {json.dumps(rx_data)},
                            borderColor: 'rgba(147, 51, 234, 0.7)',
                            backgroundColor: 'rgba(147, 51, 234, 0.3)',
                            fill: true,
                            tension: 0.4
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            legend: {{
                                position: 'bottom'
                            }},
                            tooltip: {{
                                mode: 'index',
                                intersect: false
                            }}
                        }},
                        scales: {{
                            y: {{
                                beginAtZero: true,
                                max: 100,
                                title: {{
                                    display: true,
                                    text: 'Utilization %'
                                }}
                            }},
                            x: {{
                                title: {{
                                    display: true,
                                    text: 'Time'
                                }}
                            }}
                        }}
                    }}
                }});
"""
    
    # Build complete HTML with embedded Chart.js library
    chart_js_script = ''
    if time_series:
        try:
            import os
            # Try to load embedded Chart.js from assets directory
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            chart_js_path = os.path.join(script_dir, 'assets', 'chart.umd.min.js')
            
            if os.path.exists(chart_js_path):
                with open(chart_js_path, 'r', encoding='utf-8') as f:
                    chart_js_lib = f.read()
                chart_js_script = f'<script>{chart_js_lib}</script>'
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
    
    cap_dist = capabilities.get('capability_distribution', {})
    channel_width = capabilities.get('channel_width', {})
    problem_devices = capabilities.get('problem_devices', [])
    
    # Build capability chart
    total_clients = sum(cap_dist.values())
    cap_rows = ""
    for standard, count in cap_dist.items():
        if count == 0:
            continue
        percentage = (count / total_clients * 100) if total_clients > 0 else 0
        color = {'802.11ax': '#10b981', '802.11ac': '#3b82f6', '802.11n': '#f59e0b', 'legacy': '#ef4444'}.get(standard, '#6b7280')
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


def generate_switch_analysis_html(switch_analysis):
    """Generate switch analysis section with port details and PoE"""
    if not switch_analysis or not switch_analysis.get('switches'):
        return ""
    
    switches = switch_analysis['switches']
    poe_analysis = switch_analysis.get('poe_analysis', {})
    port_analysis = switch_analysis.get('port_analysis', {})
    
    switches_html = ""
    for switch in switches:
        # Port status table
        ports_html = ""
        for port in switch['ports']:
            if not port['up'] and not port['enabled']:
                continue  # Skip disabled/down ports
            
            status_color = '#10b981' if port['up'] else '#ef4444'
            status_text = 'Up' if port['up'] else 'Down'
            speed = f"{port['speed']}M" if port['speed'] > 0 else '---'
            
            # PoE info
            poe_text = ''
            if port['poe_enable']:
                poe_power = port.get('poe_power', 0)
                if isinstance(poe_power, str):
                    poe_text = f"{poe_power}W"
                else:
                    poe_text = f"{poe_power:.1f}W"
            else:
                poe_text = '---'
            
            # Client name - mark AP uplink ports with network icon
            # Only use red color if there are high-severity issues on the AP port
            client_name = port.get('connected_client', '')
            if client_name:
                if port.get('is_ap'):
                    # Check if this AP port has high-severity issues
                    has_high_severity = any(issue.get('severity') == 'high' for issue in port.get('issues', []))
                    if has_high_severity:
                        # Critical AP uplink issues - use red and bold
                        client_display = f'<span style="color: #ef4444; font-weight: bold; font-size: 0.9em;">&#128225; {client_name}</span>'
                    else:
                        # Normal AP uplink - just add network icon, same color
                        client_display = f'<span style="color: #6366f1; font-size: 0.9em;">&#128225; {client_name}</span>'
                else:
                    client_display = f'<span style="color: #6366f1; font-size: 0.9em;">{client_name}</span>'
            else:
                client_display = '<span style="color: #9ca3af; font-size: 0.9em;">---</span>'
            
            port_name = port.get('name', f"Port {port['port_idx']}")
            
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
            if port.get('issues'):
                for issue in port['issues']:
                    severity = issue.get('severity', 'low')
                    color = {'high': '#ef4444', 'medium': '#f59e0b', 'low': '#3b82f6'}.get(severity, '#6b7280')
                    bg_color = {'high': '#fef2f2', 'medium': '#fffbeb', 'low': '#eff6ff'}.get(severity, '#f9fafb')
                    
                    issue_type = issue.get('type', 'unknown').replace('_', ' ').title()
                    message = issue.get('message', 'Unknown issue')
                    
                    # Format metrics inline
                    metric_display = ''
                    if issue.get('total_dropped'):
                        metric_display = f" ({issue['total_dropped']:,} total)"
                    elif issue.get('total_errors'):
                        metric_display = f" ({issue['total_errors']:,} errors)"
                    elif issue.get('speed'):
                        metric_display = f" ({issue['speed']}Mbps → {issue.get('expected_speed', 1000)}Mbps)"
                    
                    impact = issue.get('impact', '')
                    recommendation = issue.get('recommendation', '')
                    
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
        
        # PoE utilization bar
        poe_util_html = ''
        if switch['poe_capable']:
            poe_util = (switch['poe_usage'] / switch['poe_max'] * 100) if switch['poe_max'] > 0 else 0
            poe_color = '#ef4444' if poe_util > 90 else '#f59e0b' if poe_util > 75 else '#10b981'
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
        issues_summary_html = ''
        if switch.get('issues'):
            high_issues = [i for i in switch['issues'] if i.get('severity') == 'high']
            medium_issues = [i for i in switch['issues'] if i.get('severity') == 'medium']
            low_issues = [i for i in switch['issues'] if i.get('severity') == 'low']
            
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
    recs_html = ''
    if switch_analysis.get('recommendations'):
        for rec in switch_analysis['recommendations']:
            priority = rec.get('priority', 'low')
            priority_color = {'high': '#ef4444', 'medium': '#f59e0b', 'low': '#3b82f6'}.get(priority, '#6b7280')
            recs_html += f"""
                <div style="background: {priority_color}15; padding: 15px; border-radius: 8px; border-left: 4px solid {priority_color}; margin-bottom: 10px;">
                    <strong style="color: {priority_color}; text-transform: uppercase; font-size: 0.85em;">{priority} Priority</strong>
                    <div style="margin-top: 8px;"><strong>{rec.get('message', '')}</strong></div>
                    <div style="margin-top: 8px; color: #666;">{rec.get('recommendation', '')}</div>
                    <div style="margin-top: 8px; color: #666; font-style: italic;">Impact: {rec.get('impact', '')}</div>
                </div>
"""
    
    return f"""
        <div class="section">
            <h2>🔌 Switch Analysis</h2>
            {switches_html}
            
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
