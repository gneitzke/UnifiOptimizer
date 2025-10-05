#!/usr/bin/env python3
"""
Manufacturer Analysis Module

Analyzes device manufacturers to identify:
- IoT devices that might benefit from dedicated VLANs
- Legacy devices causing performance issues
- Security cameras and smart home devices
- Gaming consoles and streaming devices
- Patterns indicating network segmentation opportunities
"""

# OUI (Organizationally Unique Identifier) prefixes and device types
MANUFACTURER_PATTERNS = {
    # Smart Home / IoT
    "Amazon": {"type": "iot", "concern": "IoT device - consider VLAN isolation", "icon": "🏠"},
    "Google": {
        "type": "iot",
        "concern": "Smart home device - check for firmware updates",
        "icon": "🏠",
    },
    "Apple": {
        "type": "consumer",
        "concern": "Generally well-behaved, supports latest WiFi standards",
        "icon": "🍎",
    },
    "Ring": {
        "type": "security_camera",
        "concern": "Security camera - high bandwidth, consider QoS",
        "icon": "📹",
    },
    "Nest": {"type": "iot", "concern": "Smart home device - ensure on secure VLAN", "icon": "🏠"},
    "Philips": {"type": "iot", "concern": "Smart lighting - typically low bandwidth", "icon": "💡"},
    "Sonos": {
        "type": "streaming",
        "concern": "Audio streaming - sensitive to network latency",
        "icon": "🔊",
    },
    "Roku": {
        "type": "streaming",
        "concern": "Streaming device - benefits from QoS priority",
        "icon": "📺",
    },
    "Samsung": {
        "type": "consumer",
        "concern": "Check if smart TV - may need firmware update",
        "icon": "📱",
    },
    # Security/Cameras
    "Wyze": {
        "type": "security_camera",
        "concern": "Security camera - isolate from main network",
        "icon": "📹",
    },
    "Arlo": {
        "type": "security_camera",
        "concern": "Security camera - high upload bandwidth",
        "icon": "📹",
    },
    "Ubiquiti": {
        "type": "network_equipment",
        "concern": "Network equipment - should be on management VLAN",
        "icon": "📡",
    },
    # Gaming
    "Sony": {
        "type": "gaming",
        "concern": "Gaming console - benefits from low latency",
        "icon": "🎮",
    },
    "Microsoft": {
        "type": "gaming",
        "concern": "Gaming/PC - may need port forwarding",
        "icon": "🎮",
    },
    "Nintendo": {"type": "gaming", "concern": "Gaming console - often has weak WiFi", "icon": "🎮"},
    # Printers
    "Epson": {"type": "printer", "concern": "Printer - isolate from guest network", "icon": "🖨️"},
    "HP": {"type": "printer", "concern": "Printer - check for security updates", "icon": "🖨️"},
    "Canon": {"type": "printer", "concern": "Printer - may have outdated firmware", "icon": "🖨️"},
    "Brother": {"type": "printer", "concern": "Printer - typically reliable", "icon": "🖨️"},
    # Smart Home Hubs
    "Lutron": {
        "type": "smart_home_hub",
        "concern": "Smart home controller - critical for automation",
        "icon": "🏡",
    },
    "Control4": {
        "type": "smart_home_hub",
        "concern": "Home automation - should be on stable network",
        "icon": "🏡",
    },
    # Legacy devices
    "Tesla": {"type": "iot", "concern": "Vehicle - check charging station WiFi", "icon": "🚗"},
}


def analyze_manufacturers(clients):
    """
    Analyze client manufacturers to provide network optimization insights

    Args:
        clients: List of client dictionaries with 'oui' and 'hostname' fields

    Returns:
        dict: Analysis results with recommendations
    """
    if not clients:
        return {}

    manufacturer_stats = {}
    device_categories = {
        "iot": [],
        "security_camera": [],
        "gaming": [],
        "printer": [],
        "streaming": [],
        "smart_home_hub": [],
        "network_equipment": [],
        "consumer": [],
        "unknown": [],
    }

    concerns = []
    recommendations = []

    for client in clients:
        oui = client.get("oui", "Unknown")
        hostname = client.get("hostname", client.get("name", "Unknown"))
        mac = client.get("mac", "Unknown")

        # Match manufacturer patterns
        matched = False
        for manufacturer, info in MANUFACTURER_PATTERNS.items():
            if manufacturer.lower() in oui.lower() or manufacturer.lower() in hostname.lower():
                device_type = info["type"]
                device_categories[device_type].append(
                    {
                        "hostname": hostname,
                        "manufacturer": manufacturer,
                        "mac": mac,
                        "concern": info["concern"],
                        "icon": info["icon"],
                    }
                )

                if manufacturer not in manufacturer_stats:
                    manufacturer_stats[manufacturer] = {
                        "count": 0,
                        "type": device_type,
                        "icon": info["icon"],
                    }
                manufacturer_stats[manufacturer]["count"] += 1
                matched = True
                break

        if not matched:
            device_categories["unknown"].append(
                {"hostname": hostname, "manufacturer": oui, "mac": mac}
            )

    # Generate recommendations based on device mix
    iot_count = len(device_categories["iot"])
    camera_count = len(device_categories["security_camera"])
    gaming_count = len(device_categories["gaming"])

    if iot_count + camera_count > 5:
        recommendations.append(
            {
                "priority": "medium",
                "category": "Network Segmentation",
                "title": f"Consider IoT VLAN - {iot_count + camera_count} smart devices detected",
                "description": "Create a separate VLAN for IoT and security cameras to improve security and network performance.",
                "devices": iot_count + camera_count,
            }
        )

    if camera_count > 2:
        recommendations.append(
            {
                "priority": "medium",
                "category": "Quality of Service",
                "title": f"QoS for {camera_count} security cameras",
                "description": "Security cameras generate continuous upload traffic. Configure QoS to prevent bandwidth saturation.",
                "devices": camera_count,
            }
        )

    if gaming_count > 0:
        recommendations.append(
            {
                "priority": "low",
                "category": "Gaming Optimization",
                "title": f"Low-latency setup for {gaming_count} gaming device(s)",
                "description": "Gaming consoles benefit from wired connections and QoS priority. Consider dedicated ports or 5GHz band.",
                "devices": gaming_count,
            }
        )

    # Check for Sonos-specific multicast issues
    sonos_count = manufacturer_stats.get("Sonos", {}).get("count", 0)
    if sonos_count > 2:
        recommendations.append(
            {
                "priority": "medium",
                "category": "Multicast/IGMP",
                "title": f"{sonos_count} Sonos devices detected",
                "description": "Sonos requires proper IGMP snooping and multicast configuration. Ensure all Sonos devices are on the same VLAN.",
                "devices": sonos_count,
            }
        )

    return {
        "manufacturer_stats": manufacturer_stats,
        "device_categories": device_categories,
        "recommendations": recommendations,
        "total_categorized": sum(
            len(devices) for cat, devices in device_categories.items() if cat != "unknown"
        ),
        "total_unknown": len(device_categories["unknown"]),
    }


def generate_manufacturer_insights_html(manufacturer_analysis):
    """Generate HTML section for manufacturer-based insights"""
    if not manufacturer_analysis:
        return ""

    stats = manufacturer_analysis.get("manufacturer_stats", {})
    categories = manufacturer_analysis.get("device_categories", {})
    recommendations = manufacturer_analysis.get("recommendations", [])

    if not stats and not recommendations:
        return ""

    # Build manufacturer summary
    manufacturer_html = ""
    if stats:
        sorted_manufacturers = sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True)
        for manufacturer, info in sorted_manufacturers[:10]:  # Top 10
            manufacturer_html += f"""
                <div style="display: flex; justify-content: space-between; padding: 12px; background: white; margin: 8px 0; border-radius: 6px; border-left: 3px solid #3b82f6;">
                    <div>
                        <span style="font-size: 1.3em; margin-right: 8px;">{info['icon']}</span>
                        <strong>{manufacturer}</strong>
                        <span style="color: #666; font-size: 0.9em; margin-left: 10px;">({info['type'].replace('_', ' ').title()})</span>
                    </div>
                    <div style="font-weight: bold; color: #3b82f6; font-size: 1.1em;">{info['count']} device(s)</div>
                </div>
            """

    # Build category breakdown - summary only, no device details
    category_html = ""
    category_names = {
        "iot": (
            "🏠 IoT & Smart Home",
            "Smart home devices, voice assistants, and connected appliances",
        ),
        "security_camera": (
            "📹 Security Cameras",
            "IP cameras requiring continuous bandwidth and upload capacity",
        ),
        "gaming": ("🎮 Gaming Devices", "Consoles and gaming PCs requiring low latency"),
        "printer": ("🖨️ Printers", "Network printers and multifunction devices"),
        "streaming": ("📺 Streaming Devices", "Audio/video streaming requiring stable connections"),
        "smart_home_hub": ("🏡 Smart Home Hubs", "Central controllers for home automation"),
        "network_equipment": (
            "📡 Network Equipment",
            "Infrastructure devices requiring management access",
        ),
        "consumer": ("📱 Consumer Electronics", "Phones, tablets, and general computing devices"),
    }

    for category, (display_name, description) in category_names.items():
        devices = categories.get(category, [])
        if devices:
            category_html += f"""
                <div style="margin: 15px 0; padding: 15px; background: white; border-radius: 8px; border-left: 4px solid #3b82f6;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <h4 style="margin: 0; color: #555;">{display_name}</h4>
                        <span style="background: #3b82f6; color: white; padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 0.9em;">{len(devices)}</span>
                    </div>
                    <p style="color: #666; font-size: 0.9em; margin: 0;">{description}</p>
                </div>
            """

    # Build recommendations
    recommendation_html = ""
    if recommendations:
        for rec in recommendations:
            color = (
                "#ef4444"
                if rec["priority"] == "high"
                else "#f59e0b" if rec["priority"] == "medium" else "#3b82f6"
            )
            recommendation_html += f"""
                <div style="padding: 15px; margin: 10px 0; background: white; border-left: 4px solid {color}; border-radius: 6px;">
                    <div style="font-weight: bold; font-size: 1.1em; margin-bottom: 5px;">{rec['title']}</div>
                    <div style="color: #666; margin-bottom: 8px;">{rec['description']}</div>
                    <div style="font-size: 0.85em; color: #888;">
                        <span style="background: #f3f4f6; padding: 4px 8px; border-radius: 4px; margin-right: 10px;">
                            {rec['category']}
                        </span>
                        <span style="color: {color}; font-weight: 600;">{rec['priority'].upper()} PRIORITY</span>
                    </div>
                </div>
            """

    return f"""
        <div class="section">
            <h2>🔍 Device Manufacturer Analysis</h2>
            <div style="background: #eff6ff; padding: 20px; border-radius: 8px; border-left: 4px solid #3b82f6; margin-bottom: 20px;">
                <strong>Smart Network Insights</strong>
                <p style="margin-top: 10px; color: #666;">
                    Analysis of device manufacturers reveals opportunities for network optimization, 
                    security improvements, and performance tuning based on device types and behaviors.
                </p>
            </div>
            
            <h3 style="margin-top: 30px; margin-bottom: 15px;">📊 Top Manufacturers</h3>
            {manufacturer_html if manufacturer_html else '<p style="color: #666;">No manufacturer data available</p>'}
            
            {f'<h3 style="margin-top: 30px; margin-bottom: 15px;">📱 Device Categories</h3>{category_html}' if category_html else ''}
            
            {f'<h3 style="margin-top: 30px; margin-bottom: 15px;">💡 Recommendations</h3>{recommendation_html}' if recommendation_html else ''}
        </div>
    """
