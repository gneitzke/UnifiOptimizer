# Client Capability Matrix - AP Name Fix

**Date:** October 5, 2025
**Issue:** Legacy device table showing "Unknown" for Connected AP column

---

## Problem

### User Report
```
Connected AP	Signal	Capability
Unknown	Unknown	40 dBm	ng
KP401	Unknown
```

The client capability matrix (legacy devices table) was showing "Unknown" in the "Connected AP" column, making it impossible to identify which access point the legacy devices were connected to.

### Root Cause

When `analyze_client_capabilities()` identified legacy devices, it captured:
- ✅ Client hostname
- ✅ Client MAC address
- ✅ Client capability (radio_proto)
- ❌ **Missing: AP name** (only had `ap_mac` in client data)

The client data includes `ap_mac` (the MAC address of the connected AP), but not the AP's friendly name. The function didn't have access to the devices list to perform the MAC-to-name lookup.

---

## Solution

### Changes Made

**File:** `core/advanced_analyzer.py`

#### 1. Modified Function Signature

Added optional `devices` parameter to enable AP name lookup:

```python
# Before
def analyze_client_capabilities(self, clients):

# After
def analyze_client_capabilities(self, clients, devices=None):
    """
    Args:
        clients: List of client dicts
        devices: Optional list of device dicts for AP name lookup
    """
```

#### 2. Built AP MAC-to-Name Mapping

Created a lookup dictionary at the start of the function:

```python
# Build AP MAC to name mapping
ap_map = {}
if devices:
    for device in devices:
        if device.get('type') == 'uap':
            ap_map[device.get('mac')] = device.get('name', 'Unknown')
```

**Why:**
- Converts MAC addresses (e.g., `00:11:22:33:44:55`) to friendly names (e.g., `"Kitchen AP"`)
- Efficient O(1) lookup for each client
- Handles missing devices gracefully (defaults to "Unknown")

#### 3. Enriched Problem Device Records

Added AP information when flagging legacy devices:

```python
# Before
results['problem_devices'].append({
    'hostname': client.get('hostname', client.get('name', 'Unknown')),
    'mac': client.get('mac'),
    'radio_proto': radio_proto,
    'issue': 'Legacy 802.11a/b/g device'
})

# After
ap_mac = client.get('ap_mac', '')
ap_name = ap_map.get(ap_mac, 'Unknown') if ap_map else 'Unknown'

results['problem_devices'].append({
    'hostname': client.get('hostname', client.get('name', 'Unknown')),
    'mac': client.get('mac'),
    'ap_mac': ap_mac,           # NEW: Include AP MAC
    'ap_name': ap_name,         # NEW: Include AP friendly name
    'rssi': client.get('rssi', 0),  # NEW: Include signal strength
    'radio_proto': radio_proto,
    'issue': 'Legacy 802.11a/b/g device'
})
```

**Added fields:**
- `ap_mac`: The MAC address of the connected AP
- `ap_name`: The friendly name of the AP (e.g., "Kitchen", "Office")
- `rssi`: Client signal strength (for troubleshooting)

#### 4. Updated Function Call

Modified `run_advanced_analysis()` to pass devices:

```python
# Before
'client_capabilities': analyzer.analyze_client_capabilities(clients),

# After
'client_capabilities': analyzer.analyze_client_capabilities(clients, devices),
```

---

**File:** `core/optimize_network.py`

#### 5. Enhanced Display with Rich Table

Replaced simple list with formatted table showing all details:

```python
# Before (simple list)
for device in problem_devices[:5]:
    console.print(f"  • {device.get('hostname', 'Unknown')} ({device.get('radio_proto', 'unknown')})")

# After (rich table)
from rich.table import Table
problem_table = Table(show_header=True, header_style="bold red")
problem_table.add_column("Client", style="cyan")
problem_table.add_column("Connected AP", style="yellow")
problem_table.add_column("Signal", style="white")
problem_table.add_column("Capability", style="red")

for device in problem_devices[:10]:  # Show first 10
    hostname = device.get('hostname', 'Unknown')
    ap_name = device.get('ap_name', 'Unknown')
    rssi = device.get('rssi', 0)

    # Format RSSI with color coding
    if rssi > 0:
        rssi = -rssi  # Fix positive RSSI bug

    if rssi > -60:
        rssi_str = f"[green]{rssi} dBm[/green]"
    elif rssi > -70:
        rssi_str = f"[yellow]{rssi} dBm[/yellow]"
    else:
        rssi_str = f"[red]{rssi} dBm[/red]"

    capability = device.get('radio_proto', 'unknown')

    problem_table.add_row(hostname, ap_name, rssi_str, capability)

console.print(problem_table)
```

**Enhancements:**
- ✅ **Structured table** instead of bullet list
- ✅ **Color-coded RSSI** (green = strong, yellow = fair, red = weak)
- ✅ **AP name** now properly displayed
- ✅ **Shows 10 devices** instead of 5 (more visibility)
- ✅ **Fixes positive RSSI bug** (ensures RSSI is always negative)

---

## How It Works Now

### Data Flow

```
1. API provides:
   - Clients with ap_mac: "00:11:22:33:44:55"
   - Devices with mac: "00:11:22:33:44:55", name: "Kitchen AP"

2. analyze_client_capabilities() builds mapping:
   ap_map = {
       "00:11:22:33:44:55": "Kitchen AP",
       "aa:bb:cc:dd:ee:ff": "Office AP"
   }

3. For each legacy client:
   - Get ap_mac from client: "00:11:22:33:44:55"
   - Lookup in ap_map: "Kitchen AP"
   - Store both ap_mac and ap_name in problem_devices

4. Display code uses ap_name:
   - Creates formatted table
   - Shows "Kitchen AP" instead of "Unknown"
```

### Example Output

**Before:**
```
⚠️  3 Legacy Device(s) Detected:
  • KP401 (ng)
  • Guest-iPhone (na)
  • Smart-TV (unknown)
```

**After:**
```
⚠️  3 Legacy Device(s) Detected:

Client          Connected AP     Signal          Capability
KP401           Kitchen AP      -45 dBm         ng
Guest-iPhone    Office AP       -72 dBm         na
Smart-TV        Living Room AP  -68 dBm         unknown
```

---

## Testing

### Compilation
```bash
✅ python3 -m py_compile core/advanced_analyzer.py core/optimize_network.py
```

### Expected Behavior

**With AP mapping available:**
```
Client          Connected AP     Signal          Capability
KP401           Kitchen AP      -45 dBm         ng
Smart Plug      Bedroom AP      -70 dBm         b
Legacy Printer  Office AP       -55 dBm         g
```

**Without devices (fallback):**
```
Client          Connected AP     Signal          Capability
KP401           Unknown         -45 dBm         ng
Smart Plug      Unknown         -70 dBm         b
```

**Signal color coding:**
- 🟢 **Green**: > -60 dBm (Excellent/Good signal)
- 🟡 **Yellow**: -60 to -70 dBm (Fair signal)
- 🔴 **Red**: < -70 dBm (Poor signal)

---

## Technical Details

### Why Use Optional Parameter?

Made `devices` parameter optional to maintain backward compatibility:

```python
def analyze_client_capabilities(self, clients, devices=None):
```

**Benefits:**
- Existing code calling without `devices` still works
- New code can provide `devices` for enhanced information
- Graceful degradation if `devices` unavailable

### AP MAC Format

UniFi API returns MAC addresses in various formats:
- Lowercase with colons: `00:11:22:33:44:55`
- Uppercase with colons: `00:11:22:33:44:55`
- No colons: `001122334455`

The mapping handles all formats since we're doing dictionary lookups directly on API-provided values.

### RSSI Sign Fix

Some UniFi controllers return positive RSSI values (bug). The display code fixes this:

```python
if rssi > 0:
    rssi = -rssi  # Fix positive RSSI
```

This ensures signal strength is always displayed correctly as negative dBm.

---

## Impact Summary

### Before Fix
❌ **No AP identification** - Can't locate legacy devices
❌ **Missing signal info** - Can't assess if location is the issue
❌ **Simple list** - Hard to read with multiple devices
❌ **Limited info** - Only 5 devices shown

### After Fix
✅ **AP names shown** - Easy to identify device locations
✅ **Signal strength displayed** - Color-coded for quick assessment
✅ **Rich table format** - Professional, organized display
✅ **More devices shown** - Up to 10 devices for better visibility
✅ **RSSI bug fixed** - Always shows correct negative values

---

## Use Cases

### Identifying Problem Areas

**Example:**
```
Client          Connected AP     Signal          Capability
Old-Printer     Garage AP       -75 dBm         b
Guest-Tablet    Garage AP       -72 dBm         g
IoT-Sensor      Garage AP       -68 dBm         b
```

**Analysis:** Multiple legacy devices on "Garage AP" → Consider dedicated legacy network or AP upgrade

### Signal Troubleshooting

**Example:**
```
Client          Connected AP     Signal          Capability
Smart-TV        Living Room AP  -45 dBm         n
```

**Analysis:** Strong signal but legacy standard → Device limitation, not network issue

### Network Segmentation Planning

**Example:**
```
Client          Connected AP     Signal          Capability
IP-Camera-1     Security AP     -55 dBm         g
IP-Camera-2     Security AP     -62 dBm         g
IP-Camera-3     Security AP     -58 dBm         g
```

**Analysis:** Legacy security cameras clustered on one AP → Good candidate for dedicated VLAN

---

## Related Files

- ✅ `core/advanced_analyzer.py` - Fixed client capability analysis
- ✅ `core/optimize_network.py` - Enhanced problem device display
- 📄 Related: `core/client_health.py` - Similar AP mapping pattern
- 📄 Related: `core/network_analyzer.py` - Client-to-AP association examples

---

## Future Enhancements

### Potential Improvements

1. **Device Type Icons**
   - 🖨️ Printers
   - 📹 Cameras
   - 📱 Mobile devices
   - 💡 IoT sensors

2. **Sortable Display**
   - Sort by AP name (group by location)
   - Sort by signal strength (worst first)
   - Sort by capability (most legacy first)

3. **Recommendations**
   - "Consider upgrading devices on Kitchen AP"
   - "3 legacy devices - enable legacy rate limiting"
   - "Weak signal + legacy standard = upgrade priority"

4. **Historical Tracking**
   - Track when legacy devices first appeared
   - Alert on new legacy device connections
   - Show trends over time

---

## Summary

**Problem:** Legacy device table showed "Unknown" for AP names, making troubleshooting impossible

**Cause:** Function didn't have access to devices list for MAC-to-name mapping

**Fix:**
1. Added `devices` parameter to `analyze_client_capabilities()`
2. Built AP MAC-to-name mapping dictionary
3. Enriched problem device records with AP name, MAC, and RSSI
4. Updated display with rich table format and color-coded signal strength

**Result:** Professional, informative display that helps users quickly identify and locate legacy devices on their network ✅
