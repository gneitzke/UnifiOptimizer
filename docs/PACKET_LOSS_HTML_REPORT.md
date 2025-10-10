# Packet Loss History - HTML Report Integration

## Changes Made

### 1. Data Collection (`core/advanced_analyzer.py`)
**Lines ~2724-2727**: Added packet loss history collection to the analysis pipeline

```python
# Run switch port packet loss history analysis (7 days)
# The switch_analyzer already prints progress messages
switch_port_history = switch_analyzer.analyze_switch_port_history(lookback_hours=168)
```

Added to results dictionary:
```python
"switch_port_history": switch_port_history,
```

### 2. HTML Report Generation (`core/html_report_generator.py`)

#### Updated Function Signature (Line ~3172)
```python
def generate_switch_analysis_html(switch_analysis, switch_port_history=None):
```

#### New Function: `generate_packet_loss_history_html()` (Lines ~3172-3297)
Creates a comprehensive visual display of packet loss trends including:

**Summary Dashboard:**
- Total ports with packet loss
- Count of improving ports (↗️)
- Count of stable ports (→)
- Count of worsening ports (↘️)

**Per-Port Cards:**
Each problematic port gets a detailed card showing:
- Switch name and port name
- Trend badge with color coding:
  - 🟢 Green (Improving) - Loss decreasing over time
  - 🟡 Yellow (Stable) - Loss consistent
  - 🔴 Red (Worsening) - Loss increasing over time
- Current packet loss percentage
- Average packet loss over 7 days
- Maximum packet loss seen
- Number of data points collected
- Mini bar chart showing 7-day trend

**Visual Design:**
- Color-coded cards based on trend
- Mini sparkline charts for quick visualization
- Grid layout for statistics
- Responsive design
- Clean, modern styling

### 3. Integration Point (`core/html_report_generator.py` Line ~601)
```python
# Switch Analysis Section
switch_analysis = analysis_data.get("switch_analysis")
switch_port_history = analysis_data.get("switch_port_history")
if switch_analysis and switch_analysis.get("switches"):
    html_content += generate_switch_analysis_html(switch_analysis, switch_port_history)
```

## What You'll See in the Report

### When No Packet Loss Detected
A green success message:
```
✓ No ports with significant packet loss detected
All monitored switch ports are operating normally with minimal packet loss (<0.1%).
```

### When Packet Loss Detected

**Summary Section:**
```
📊 Packet Loss Tracking (7 Days)
┌────────────────┬────────────────┬────────────────┬────────────────┐
│ Ports with Loss│   Improving ↗️  │    Stable →    │  Worsening ↘️  │
│       3        │       1        │       1        │       1        │
└────────────────┴────────────────┴────────────────┴────────────────┘
Analyzed 3 ports with packet loss: 1 improving, 1 stable, 1 worsening
```

**Individual Port Cards:**
```
┌─────────────────────────────────────────────────────────────────┐
│ Main Switch - Port 5                            [↗️ Improving]   │
│ Port 5                                          (15.5%)         │
├─────────────────────────────────────────────────────────────────┤
│ Current Loss    Average Loss    Max Loss       Data Points     │
│   2.500%          3.200%        5.100%            168          │
├─────────────────────────────────────────────────────────────────┤
│ 7-Day Packet Loss Trend                                        │
│ ▃▄▆█▅▄▃▂▁▁▁▂▂▂▁▁▁▁▁▁▁▁▁▁                                      │
│ 7 days ago                                               Now   │
└─────────────────────────────────────────────────────────────────┘
```

## Color Coding

### Trend Badges
- **Improving (Green #10b981)**: Packet loss decreasing by >20%
- **Stable (Yellow #f59e0b)**: Packet loss within ±20%
- **Worsening (Red #ef4444)**: Packet loss increasing by >20%

### Sparkline Bars
- **Green**: Loss <1%
- **Yellow**: Loss 1-5%
- **Red**: Loss >5%

## Data Displayed

For each problematic port:
1. **Switch Name** and **Port Name/Number**
2. **Trend Status** with percentage change
3. **Current Loss**: Most recent packet loss %
4. **Average Loss**: Mean over 7 days
5. **Max Loss**: Peak packet loss seen
6. **Data Points**: Number of hourly samples collected
7. **Visual Trend**: Bar chart showing loss over time

## Use Cases

1. **Identify Problem Ports**: Quickly see which ports have packet loss
2. **Track Improvements**: Verify if cable replacements worked
3. **Catch Degradation**: Spot ports getting worse over time
4. **Historical Context**: See how long issues have persisted
5. **Prioritize Fixes**: Focus on worsening ports first

## Example Scenarios

### Scenario 1: Cable Replacement Worked ✓
```
Port 8                                    [↗️ Improving (45.2%)]
Current: 0.8%  Avg: 1.5%  Max: 3.2%
```
*Port shows improvement after maintenance*

### Scenario 2: Intermittent Issue ⚠️
```
Port 12                                   [→ Stable]
Current: 2.1%  Avg: 2.0%  Max: 2.5%
```
*Consistent low-level loss, may need investigation*

### Scenario 3: Degrading Connection 🚨
```
Port 3                                    [↘️ Worsening (78.3%)]
Current: 5.2%  Avg: 3.1%  Max: 5.2%
```
*Urgent: Port condition deteriorating, cable likely failing*

## Technical Details

- **Collection Interval**: Hourly via UniFi API
- **Lookback Period**: 168 hours (7 days)
- **Minimum Traffic**: Only tracks ports with >1000 packets
- **Threshold**: Only reports ports with >0.1% average loss
- **Trend Calculation**: Compares first 25% vs last 25% of data
- **Data Source**: `/api/s/{site}/stat/report/hourly.device/{mac}`

## Report Location

The packet loss history section appears in the HTML report:
- **Section**: 🔌 Switch Analysis
- **Position**: After switch port tables, before recommendations
- **Filename**: `reports/unifi_network_report_*.html`

## Future Enhancements (Potential)

- Interactive charts with zoom/pan
- Export to CSV for deeper analysis
- Email alerts for worsening ports
- Comparison with previous weeks
- Port-to-client mapping in visualization
- Configurable thresholds
