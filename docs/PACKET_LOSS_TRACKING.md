# Switch Port Packet Loss Tracking

## Overview
Added comprehensive time-series packet loss tracking to `SwitchAnalyzer` to monitor problematic switch ports over time and identify trends.

## What Was Added

### New Method: `analyze_switch_port_history()`
**Location**: `core/switch_analyzer.py` (line 528+)

**Features**:
- Collects hourly packet loss statistics for all switch ports
- Default lookback period: 168 hours (7 days)
- Tracks rx_dropped, tx_dropped, rx_packets, tx_packets per port per hour
- Calculates packet loss percentage: `(rx_dropped + tx_dropped) / (rx_packets + tx_packets) * 100`
- Only tracks ports with meaningful traffic (>1000 packets)
- Only reports ports with significant packet loss (>0.1% average)

### Trend Analysis
Determines if packet loss is:
- **Improving** ↗️: Last 25% of data shows <80% of first 25% (packet loss decreasing)
- **Worsening** ↘️: Last 25% of data shows >120% of first 25% (packet loss increasing)
- **Stable** →: Packet loss relatively consistent

### Statistics Provided
For each problematic port:
- Current packet loss percentage
- Average packet loss over tracking period
- Maximum packet loss seen
- Minimum packet loss seen
- Trend direction and percentage change
- Total data points collected
- Hours tracked

### Summary Data
Overall statistics:
- Total ports analyzed
- Ports with packet loss detected
- Count of improving ports
- Count of stable ports
- Count of worsening ports

## How to Use

### In CLI Mode
The packet loss tracking is automatically included when running expert analysis:

```bash
python3 optimizer.py
# Select: 1. Expert Analysis
```

### In Code
```python
from core.switch_analyzer import SwitchAnalyzer
from api.cloudkey_gen2_client import CloudKeyGen2Client

client = CloudKeyGen2Client(host, username, password, site='default')
analyzer = SwitchAnalyzer(client, site='default')

# Get 7-day packet loss history
history = analyzer.analyze_switch_port_history(lookback_hours=168)

# Access results
summary = history['summary']
print(f"Ports with loss: {summary['ports_with_loss']}")
print(f"Improving: {summary['improving']}")
print(f"Worsening: {summary['worsening']}")

# Per-port data
for port_key, port_data in history['port_history'].items():
    stats = port_data['statistics']
    print(f"{port_data['switch_name']} {port_data['port_name']}")
    print(f"  Trend: {stats['trend']}")
    print(f"  Current: {stats['current_loss']}%")
    print(f"  Average: {stats['avg_loss']}%")

    # Hourly time-series data
    for hour in port_data['hourly_data']:
        print(f"    {hour['datetime']}: {hour['packet_loss_pct']}%")
```

## Data Structure

### Port History
```python
{
    "port_history": {
        "switch_mac_port_idx": {
            "switch_name": "Main Switch",
            "switch_mac": "aa:bb:cc:dd:ee:ff",
            "port_idx": 5,
            "port_name": "Port 5",
            "hourly_data": [
                {
                    "timestamp": 1696875600000,
                    "datetime": "2023-10-09T12:00:00",
                    "packet_loss_pct": 2.5,
                    "rx_dropped": 100,
                    "tx_dropped": 50,
                    "total_dropped": 150,
                    "rx_packets": 5000,
                    "tx_packets": 1000,
                    "total_packets": 6000
                },
                // ... more hourly data points
            ],
            "statistics": {
                "current_loss": 2.5,
                "avg_loss": 3.2,
                "max_loss": 5.1,
                "min_loss": 1.2,
                "trend": "improving",
                "trend_pct": 15.5,
                "first_quarter_avg": 4.0,
                "last_quarter_avg": 3.4,
                "data_points": 168,
                "hours_tracked": 168
            }
        }
    },
    "trends": {
        "switch_mac_port_idx": {
            "switch_name": "Main Switch",
            "port_name": "Port 5",
            "trend": "improving",
            "current_loss": 2.5,
            "avg_loss": 3.2,
            "trend_pct": 15.5
        }
    },
    "summary": {
        "total_ports_analyzed": 12,
        "ports_with_loss": 3,
        "improving": 1,
        "stable": 1,
        "worsening": 1,
        "message": "Analyzed 12 ports with packet loss: 1 improving, 1 stable, 1 worsening"
    }
}
```

## Use Cases

1. **Troubleshooting**: Identify which ports have ongoing packet loss issues
2. **Trending**: Determine if recent changes improved or worsened packet loss
3. **Proactive Monitoring**: Catch ports with increasing packet loss before they become critical
4. **Cable Testing**: Validate cable replacements by checking if packet loss improved
5. **Historical Analysis**: Review packet loss patterns over the past week

## Integration with Existing Features

The packet loss tracking complements the existing switch analysis:

- **Current State**: `analyze_switches()` shows current packet loss snapshot
- **Historical Trend**: `analyze_switch_port_history()` shows how it's changing over time
- Both are included in the expert analysis workflow
- Results can be displayed in HTML reports (ready for integration)

## Performance Notes

- Uses UniFi API endpoint: `/api/s/{site}/stat/report/hourly.device/{mac}`
- Queries hourly aggregated data (not live statistics)
- Processing time depends on number of switches and data retention
- Only tracks ports with active traffic to minimize processing
- Filters out noise by requiring >1000 packets and >0.1% loss

## What Was Removed

All web interface code was removed as requested:
- ❌ `/web/app.py` - Flask/SocketIO server
- ❌ `/web/static/js/app.js` - Web UI JavaScript
- ❌ `--node` flag from `optimizer.py`
- ❌ Web-related imports and dependencies

## What Was Kept

✅ **CLI interface** - Main way to run the tool
✅ **HTML static reports** - Existing report generation
✅ **Packet loss tracking** - New feature in `switch_analyzer.py`
✅ All existing analysis features
✅ All existing optimizations and recommendations
