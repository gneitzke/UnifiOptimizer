"""Trend Analysis Engine for UniFi Network Optimizer
=====================================================
Pure-stdlib implementation of trend analysis algorithms.
Compatible with Python 3.7+.

All time-series inputs are lists of (timestamp_seconds, value) tuples.
"""

import math
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Low-level math helpers
# ---------------------------------------------------------------------------


def linear_slope(points):
    """Return the least-squares linear regression slope over time.

    Args:
        points: list of (timestamp, value) tuples. Timestamps are treated as
                relative (only differences matter), so any unit works.

    Returns:
        float slope (value change per unit time), or 0.0 if fewer than 2 points.
    """
    if len(points) < 2:
        return 0.0

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n = len(xs)

    # Normalise x to index (avoids giant floating-point numbers from epoch ts)
    x_min = xs[0]
    x_span = xs[-1] - x_min
    if x_span == 0:
        return 0.0

    xs_n = [(x - x_min) / x_span for x in xs]

    sum_x = sum(xs_n)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs_n, ys))
    sum_xx = sum(x * x for x in xs_n)

    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return 0.0

    # Slope in normalised units; scale back to per-day units using x_span
    slope_normalised = (n * sum_xy - sum_x * sum_y) / denom
    seconds_per_day = 86400.0
    return slope_normalised / x_span * seconds_per_day


def detect_anomalies(points, sigma=2.0):
    """Identify outlier data points using mean ± sigma standard deviations.

    Args:
        points: list of (timestamp, value) tuples.
        sigma: number of standard deviations outside mean to flag as anomaly.

    Returns:
        list of dicts: [{ts, value, expected, deviation}]
    """
    if len(points) < 3:
        return []

    values = [p[1] for p in points]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0.0

    if std == 0:
        return []

    anomalies = []
    for ts, val in points:
        if abs(val - mean) > sigma * std:
            anomalies.append(
                {
                    "time": ts,
                    "value": round(val, 2),
                    "expected": round(mean, 2),
                    "deviation": round(abs(val - mean) / std, 2),
                }
            )
    return anomalies


def rolling_average(points, window=3):
    """Return list of smoothed values using a trailing rolling average.

    Args:
        points: list of (timestamp, value) tuples.
        window: number of data points in each rolling window.

    Returns:
        list of float values (same length as points).
    """
    if not points:
        return []
    values = [p[1] for p in points]
    result = []
    for i, v in enumerate(values):
        start = max(0, i - window + 1)
        window_vals = values[start : i + 1]
        result.append(round(sum(window_vals) / len(window_vals), 2))
    return result


def classify_trend(slope, degrading_threshold=-0.5, improving_threshold=0.5):
    """Classify a slope into a human-readable trend label.

    Args:
        slope: numeric slope value (units per day).
        degrading_threshold: slope below this → "degrading".
        improving_threshold: slope above this → "improving".

    Returns:
        str: "improving", "degrading", or "stable"
    """
    if slope <= degrading_threshold:
        return "degrading"
    if slope >= improving_threshold:
        return "improving"
    return "stable"


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------


def _group_ap_stats_by_mac(stat_records):
    """Group a flat list of AP stat records by their 'mac' field.

    Returns:
        dict: {mac: sorted_list_of_records}
    """
    by_mac = defaultdict(list)
    for rec in stat_records:
        mac = rec.get("mac") or rec.get("ap")
        if mac:
            by_mac[mac].append(rec)
    # Sort each AP's records chronologically
    for mac in by_mac:
        by_mac[mac].sort(key=lambda r: r.get("time", 0))
    return dict(by_mac)


def _ap_name_map(devices):
    """Build {mac: display_name} from device list."""
    return {
        d.get("mac", ""): d.get("name", "Unnamed AP") for d in devices if d.get("type") == "uap"
    }


def _extract_points(records, field):
    """Extract (timestamp_seconds, value) tuples from stat records for a field.

    Skips records where the field is None or absent.
    Converts millisecond timestamps to seconds automatically.
    """
    out = []
    for rec in records:
        val = rec.get(field)
        if val is None:
            continue
        ts = rec.get("time", 0)
        # UniFi reports use seconds; guard against ms by checking magnitude
        if ts > 1e12:
            ts = ts / 1000.0
        out.append((ts, float(val)))
    return out


def _daily_network_points(daily_ap_stats, field):
    """Aggregate a metric across all APs per calendar day → [(ts, total)] sorted."""
    by_day = defaultdict(float)
    for rec in daily_ap_stats:
        val = rec.get(field)
        if val is None:
            continue
        ts = rec.get("time", 0)
        if ts > 1e12:
            ts = ts / 1000.0
        # Round down to day boundary
        day_ts = int(ts / 86400) * 86400
        by_day[day_ts] += float(val)
    return sorted(by_day.items())


def _daily_avg_points(daily_ap_stats, field):
    """Average a metric across all APs per calendar day → [(ts, avg)] sorted."""
    by_day_vals = defaultdict(list)
    for rec in daily_ap_stats:
        val = rec.get(field)
        if val is None:
            continue
        ts = rec.get("time", 0)
        if ts > 1e12:
            ts = ts / 1000.0
        day_ts = int(ts / 86400) * 86400
        by_day_vals[day_ts].append(float(val))
    result = []
    for day_ts in sorted(by_day_vals):
        vals = by_day_vals[day_ts]
        result.append((day_ts, sum(vals) / len(vals)))
    return result


# ---------------------------------------------------------------------------
# High-level analysis functions
# ---------------------------------------------------------------------------


def analyze_ap_trends(hourly_ap_stats, daily_ap_stats, devices, config=None):
    """Compute per-AP trend metrics from collected stat records.

    Args:
        hourly_ap_stats: list of raw hourly AP stat records.
        daily_ap_stats:  list of raw daily AP stat records.
        devices:         list of device dicts (for name lookup).
        config:          optional trend config dict with thresholds.

    Returns:
        dict: {ap_name: trend_info_dict}
    """
    if config is None:
        config = {}
    deg_thresh = config.get("degrading_threshold", -0.5)
    imp_thresh = config.get("improving_threshold", 0.5)
    roll_window = config.get("rolling_window", 3)
    sigma = config.get("anomaly_sigma", 2.0)

    name_map = _ap_name_map(devices)

    # Use daily stats for slope/trend (longer window = more signal)
    # Use hourly stats for sparklines (more recent, finer grain)
    daily_by_mac = _group_ap_stats_by_mac(daily_ap_stats)
    hourly_by_mac = _group_ap_stats_by_mac(hourly_ap_stats)

    per_ap = {}
    for mac, records in daily_by_mac.items():
        name = name_map.get(mac, mac)

        sat_pts = _extract_points(records, "satisfaction")
        client_pts = _extract_points(records, "num_sta")

        sat_slope = linear_slope(sat_pts)
        client_slope = linear_slope(client_pts)

        sat_trend = classify_trend(sat_slope, deg_thresh, imp_thresh)
        client_trend = classify_trend(client_slope, deg_thresh, imp_thresh)

        # Sparklines from hourly data (last ~7 days), fall back to daily
        sparkline_records = hourly_by_mac.get(mac, records)
        spark_sat = rolling_average(_extract_points(sparkline_records, "satisfaction"), roll_window)
        spark_clients = rolling_average(_extract_points(sparkline_records, "num_sta"), roll_window)

        anomalies = detect_anomalies(sat_pts, sigma)
        for a in anomalies:
            a["metric"] = "satisfaction"

        per_ap[name] = {
            "mac": mac,
            "satisfaction_trend": sat_trend,
            "satisfaction_slope": round(sat_slope, 3),
            "client_count_trend": client_trend,
            "client_count_slope": round(client_slope, 3),
            "sparkline_satisfaction": spark_sat[-20:],  # keep last 20 for display
            "sparkline_clients": spark_clients[-20:],
            "anomalies": anomalies,
            "data_points": len(sat_pts),
        }

    return per_ap


def analyze_network_trends(daily_ap_stats, events=None, config=None):
    """Compute network-wide aggregate trend metrics.

    Args:
        daily_ap_stats: list of raw daily AP stat records.
        events:         list of event dicts (for DFS event frequency trend).
        config:         optional trend config dict.

    Returns:
        dict with keys: client_count_trend, satisfaction_trend,
                        dfs_event_trend, anomalies, raw_satisfaction_slope, etc.
    """
    if config is None:
        config = {}
    deg_thresh = config.get("degrading_threshold", -0.5)
    imp_thresh = config.get("improving_threshold", 0.5)
    sigma = config.get("anomaly_sigma", 2.0)

    client_pts = _daily_network_points(daily_ap_stats, "num_sta")
    sat_pts = _daily_avg_points(daily_ap_stats, "satisfaction")

    client_slope = linear_slope(client_pts)
    sat_slope = linear_slope(sat_pts)

    client_trend = classify_trend(client_slope, deg_thresh, imp_thresh)
    sat_trend = classify_trend(sat_slope, deg_thresh, imp_thresh)

    # DFS events: count per day
    dfs_by_day = defaultdict(int)
    for ev in events or []:
        key = ev.get("key", "")
        if "DFS" in key.upper() or "radar" in key.lower():
            ts = ev.get("time", ev.get("datetime", 0))
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0
            elif ts > 1e12:
                ts = ts / 1000.0
            day_ts = int(ts / 86400) * 86400
            dfs_by_day[day_ts] += 1
    dfs_pts = sorted(dfs_by_day.items())
    dfs_slope = linear_slope(dfs_pts)
    dfs_trend = classify_trend(dfs_slope, deg_thresh, imp_thresh)
    # Rename "improving" to "decreasing" and "degrading" to "increasing" for DFS events
    # (more DFS = worse, so slope direction is inverted semantically)
    dfs_display = {
        "improving": "decreasing",
        "degrading": "increasing",
        "stable": "stable",
    }.get(dfs_trend, dfs_trend)

    anomalies = detect_anomalies(sat_pts, sigma)
    for a in anomalies:
        a["metric"] = "satisfaction"

    return {
        "client_count_trend": client_trend,
        "client_count_slope": round(client_slope, 3),
        "satisfaction_trend": sat_trend,
        "satisfaction_slope": round(sat_slope, 3),
        "dfs_event_trend": dfs_display,
        "dfs_event_slope": round(dfs_slope, 3),
        "anomalies": anomalies,
        "satisfaction_points": sat_pts,
        "client_count_points": client_pts,
    }


def analyze_client_trends(daily_user_stats, config=None):
    """Flag clients whose satisfaction is dropping.

    Args:
        daily_user_stats: list of raw daily.user stat records.
                          Each record has: mac, satisfaction, time.
        config:           optional trend config dict.

    Returns:
        list of dicts for clients with a non-stable satisfaction trend,
        sorted by slope ascending (worst degraders first).
    """
    if config is None:
        config = {}
    deg_thresh = config.get("degrading_threshold", -0.5)
    imp_thresh = config.get("improving_threshold", 0.5)

    by_mac = defaultdict(list)
    for rec in daily_user_stats:
        mac = rec.get("mac") or rec.get("user")
        sat = rec.get("satisfaction")
        ts = rec.get("time", 0)
        if mac and sat is not None:
            if ts > 1e12:
                ts = ts / 1000.0
            by_mac[mac].append((ts, float(sat)))

    flagged = []
    for mac, pts in by_mac.items():
        if len(pts) < 2:
            continue
        pts.sort()
        slope = linear_slope(pts)
        trend = classify_trend(slope, deg_thresh, imp_thresh)
        if trend != "stable":
            flagged.append(
                {
                    "mac": mac,
                    "name": mac,  # caller can enrich with client name
                    "satisfaction_trend": trend,
                    "slope": round(slope, 3),
                    "latest_satisfaction": round(pts[-1][1], 1) if pts else None,
                    "data_points": len(pts),
                }
            )

    flagged.sort(key=lambda c: c["slope"])
    return flagged


def build_trend_summary(ap_trends, network_trends, client_trends):
    """Assemble the top-level trend_analysis dict for the report.

    Args:
        ap_trends:       result of analyze_ap_trends()
        network_trends:  result of analyze_network_trends()
        client_trends:   result of analyze_client_trends()

    Returns:
        dict suitable for storage as full_analysis["trend_analysis"]
    """
    # Find the AP degrading fastest by satisfaction slope
    worst_ap = None
    worst_slope = 0.0
    for name, info in ap_trends.items():
        slope = info.get("satisfaction_slope", 0.0)
        if slope < worst_slope:
            worst_slope = slope
            worst_ap = name

    # Build headline string
    sat_trend = network_trends.get("satisfaction_trend", "stable")
    sat_slope = network_trends.get("satisfaction_slope", 0.0)
    if sat_trend == "degrading":
        headline = f"Network satisfaction declining ({sat_slope:+.1f}/day)."
        if worst_ap:
            headline += f" {worst_ap} degrading fastest."
    elif sat_trend == "improving":
        headline = f"Network satisfaction improving ({sat_slope:+.1f}/day)."
        if worst_ap and ap_trends[worst_ap]["satisfaction_trend"] == "degrading":
            headline += f" Watch {worst_ap}."
    else:
        headline = "Network satisfaction is stable."
        if worst_ap:
            headline += f" {worst_ap} shows slight decline."

    return {
        "network": network_trends,
        "per_ap": ap_trends,
        "flagged_clients": client_trends[:10],  # top 10 worst
        "headline": headline,
    }


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def run_trend_analysis(full_analysis, config=None):
    """Run all trend analyses and return the combined trend_analysis dict.

    Args:
        full_analysis: the analysis dict produced by run_expert_analysis().
        config:        optional trend config dict (from data/config.yaml trends section).

    Returns:
        dict: trend_analysis suitable for embedding in analysis cache.
    """
    if config is None:
        try:
            from utils.config import get_config

            config = get_config().get("trends", {})
        except Exception:
            config = {}

    if not config.get("enabled", True):
        return {"enabled": False, "headline": "Trend analysis disabled."}

    hourly_ap_stats = full_analysis.get("hourly_ap_stats", [])
    daily_ap_stats = full_analysis.get("daily_ap_stats", [])
    daily_user_stats = full_analysis.get("daily_user_stats", [])
    devices = full_analysis.get("devices", [])
    events = full_analysis.get("event_timeline", {}).get("events", [])

    # Fall back to raw events list when event_timeline isn't structured
    if not events:
        events = full_analysis.get("events", [])

    ap_trends = analyze_ap_trends(hourly_ap_stats, daily_ap_stats, devices, config)
    network_trends = analyze_network_trends(daily_ap_stats, events, config)
    client_trends = analyze_client_trends(daily_user_stats, config)

    return build_trend_summary(ap_trends, network_trends, client_trends)
