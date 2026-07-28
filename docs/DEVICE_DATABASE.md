# WiFi Device Capability Database

## Overview

The WiFi device capability database (`wifi_device_capabilities.json`) defines which devices support which WiFi standards. A baseline ships with the package; you can override it with your own copy, so devices can be added without modifying code.

## Location

A baseline copy ships inside the package, so every install — wheel, container, or
source checkout — has a working database with nothing to set up:

```
netadmin/data/wifi_device_capabilities.json
```

To customise it, put your own copy in the runtime data directory (the same one
holding `secrets.env` and the database). It replaces the baseline wholesale:

```
data/wifi_device_capabilities.json
```

Resolution order:

1. `thresholds['client.known_pathology']['kb_path']` in `data/config.yaml` — an
   explicit path, if you want the file somewhere else entirely. **Used as-is: if
   that file is missing or invalid it does *not* fall back to the paths below**,
   because quietly reading a different file than you named would be worse. It
   also applies to `client.known_pathology` only — the wired bad-cable detector
   always resolves through 2 and 3, so setting it splits the two readers.
2. `<data dir>/wifi_device_capabilities.json` — your copy. The data directory is
   `NETADMIN_DATA_DIR` when set, otherwise `./data` relative to the working
   directory the daemon was started from (under systemd, `WorkingDirectory=`).
3. The packaged baseline above.

With no `kb_path` set, the loader takes the first of 2 and 3 that exists. On a
successful load the daemon logs one line naming the file and which tier it came
from — the quickest way to confirm your copy is the one being read.

Because your copy lives in the data directory, `pip install --upgrade` never
touches it. Copy the packaged file to start from the shipped baseline:

```bash
mkdir -p data && cp "$(python -c 'import netadmin.detect.device_kb as k; print(k.PACKAGED_KB_PATH)')" data/
```

## Purpose

> **Only `known_2.4ghz_only` currently drives detection.** The `wifi7_devices`,
> `wifi6e_devices` and `dual_band_devices` sections are reserved — no code reads
> them yet, so adding patterns there has no effect on what the daemon reports.
> They are kept because the classification is useful reference and the sections
> are expected to gain consumers.

The `known_2.4ghz_only` list drives two detectors:

- **`client.known_pathology`** fires a P3 `iot_pmf_11r` finding when a device in
  this class disconnects repeatedly — the pattern of a 2.4-GHz-only chip that is
  PMF / 802.11r intolerant, rather than a coverage problem.
- **`wired.bad_cable`** uses the same list to *suppress* a speed-downshift
  finding: a gigabit port sitting at 100 Mbps to one of these devices is the
  device's design, not a broken pair.

> **The KB is not the only source for the wired detector.** `wired.bad_cable` also
> carries a built-in list of device classes that ship a 10/100 port by design
> (`_KNOWN_100MBPS_HINTS` in `netadmin/detect/detectors/wired.py`) — smart plugs,
> print servers, and the UniFi Protect cameras whose published spec is
> "10/100 MbE RJ45 port". You do not need to add those to this file. Suppression
> applies only to the peer on the affected port and only at 100 Mbps: a 10/100
> device negotiating 10 Mbps is still reported, because that is a fallback, not a
> design limit.

The device classes:
- **WiFi 7 (802.11be)** devices: 6GHz capable with 320MHz channels and MLO *(reserved)*
- **WiFi 6E (802.11ax-6e)** devices: 6GHz capable with 160MHz channels *(reserved)*
- **Dual-band (WiFi 5/6)** devices: 2.4GHz + 5GHz capable *(reserved)*
- **2.4GHz-only** devices: IoT/smart home devices — the one section read today

## Adding New Devices

### Quick Start

To add a new device, edit your copy at `data/wifi_device_capabilities.json` (see
[Location](#location)) and add the device name pattern to the appropriate section:

**For a new WiFi 7 device:**
```
"wifi7_devices": {
  "patterns": [
    "iphone 16",
    "galaxy s24 ultra",
    "YOUR NEW DEVICE NAME"  ← Add here
  ]
}
```

**For a new WiFi 6E device:**
```
"wifi6e_devices": {
  "patterns": [
    "iphone 13",
    "pixel 6",
    "YOUR NEW DEVICE NAME"  ← Add here
  ]
}
```

### Pattern Matching Rules

Matching is a plain case-insensitive substring test against the client name (as
the controller reports it) plus the OUI. That is all it is — there is no
normalization and no regex.

1. **Case-Insensitive**: patterns and the name are both lowercased
   - `"echo dot"` matches `"Kitchen-Echo Dot"` and `"ECHO DOT 3"`

2. **No punctuation normalization**: hyphens and underscores are *not* treated as
   spaces. This is the mistake that costs people the most time
   - ✅ `"iphone"` matches `"Johns-iPhone-16-Pro"`
   - ❌ `"iphone 16"` does **not** match `"Johns-iPhone-16-Pro"` — the hostname
     separates those words with a hyphen, not a space
   - Prefer single-word patterns, or match the punctuation your controller
     actually reports

3. **Substring Matching**: a pattern matches anywhere in the name
   - `"esp32"` matches `"Garage-ESP32-sensor"`

4. **Order does not matter**: sections are read independently and there is no
   cross-section precedence. Keep patterns specific enough not to over-match
   within their own section

5. **No empty patterns**: an empty string would match every device, so blank
   entries are discarded on load

### Best Practices

**✅ DO:**
- Use lowercase in patterns
- Use spaces (not hyphens) in patterns
- Be specific for newer devices (`"iphone 16"`, `"galaxy s24 ultra"`)
- Be generic for older device families (`"iphone"`, `"galaxy"`)
- Add comments for clarity using `"_comment"` fields — JSON has no inline
  comment syntax, and a `//` or `#` anywhere makes the whole file unparseable

**❌ DON'T:**
- Use overly generic patterns in WiFi 7/6E sections (will match too many devices)
- Duplicate patterns across sections
- Add patterns with special characters (!, @, #, etc.)
- Use regex patterns (simple substring matching only)

### Examples

**Adding a New WiFi 7 Phone:**
```
"wifi7_devices": {
  "patterns": [
    "iphone 16",
    "iphone 17",
    "galaxy s25",
    "pixel 10 pro"  ← Add the newest Google Pixel
  ]
}
```

**Adding a New WiFi 6E Laptop:**
```
"wifi6e_devices": {
  "patterns": [
    "macbook pro 2024",
    "macbook air 2024",
    "surface laptop 7",
    "thinkpad x1 carbon gen 11"  ← Add new ThinkPad model
  ]
}
```

**Adding IoT Devices (2.4GHz-only):**
```
"known_2.4ghz_only": {
  "patterns": [
    "ring doorbell",
    "nest thermostat",
    "wyze cam",        ← Add Wyze cameras
    "tasmota"          ← Add Tasmota smart plugs
  ]
}
```

## Testing Changes

After updating the device database, validate your changes:

```bash
# Confirm which file is being read, and that it parses
python -c "
from netadmin.detect import device_kb
print('reading:', device_kb.default_kb_path())
kb = device_kb.load_kb()
print('parsed OK' if kb is not None else 'NOT LOADED - check JSON syntax and path')
for section in ('wifi7_devices', 'wifi6e_devices', 'known_2.4ghz_only'):
    print(section, len(device_kb.section_patterns(kb, section)), 'patterns')
"
```

Run it from the same working directory the daemon uses, so `./data` resolves the
same way. If `reading:` prints a path ending in `netadmin/data/` — whether under
`site-packages` or inside a source checkout — your own copy was not found and the
packaged baseline is in use.

A section reporting `0 patterns` when you have entries in it means that section is
the wrong shape. Each one must be an object with a `patterns` list — a bare list
(`"known_2.4ghz_only": ["esp32"]`) or a bare string (`"patterns": "esp32"`) is
ignored:

```json
"known_2.4ghz_only": { "patterns": ["esp32", "tuya"] }
```

## Database Structure

### Section Descriptions

| Section | Purpose | Example Patterns |
|---------|---------|------------------|
| `wifi7_devices` | WiFi 7 (802.11be) capable - 6GHz + 320MHz + MLO *(reserved, unread)* | `iphone 16`, `galaxy s25` |
| `wifi6e_devices` | WiFi 6E (802.11ax-6e) capable - 6GHz + 160MHz *(reserved, unread)* | `iphone 14`, `pixel 7` |
| `dual_band_devices` | Dual-band (2.4/5 GHz) capable - WiFi 5/6 *(reserved, unread)* | `iphone`, `galaxy`, `macbook` |
| `known_2.4ghz_only` | 2.4GHz-only devices - fires `iot_pmf_11r` on repeat disconnects, and suppresses `wired.bad_cable` downshifts | `ring doorbell`, `echo dot` |

### Metadata Fields

| Field | Purpose |
|-------|---------|
| `_comment` | Human-readable description |
| `_description` | Technical details about the section |
| `_standards` | WiFi standards supported |
| `_features` | Key capabilities |
| `_last_updated` | Date of last update |
| `_version` | Database version |

*Note: Fields starting with `_` are ignored by the parser and are for documentation only.*

## Common Device Families

### Apple Devices
- **WiFi 7**: iPhone 16 series (2024+)
- **WiFi 6E**: iPhone 13-15, iPad Pro (2021+), MacBook Pro/Air (2021+)
- **Dual-band**: All iPhones since iPhone 5, all iPads since iPad 3, all Macs since 2013

### Samsung Galaxy
- **WiFi 7**: Galaxy S24 Ultra, S25 series (2024+)
- **WiFi 6E**: Galaxy S21-S24, Z Fold 3-5, Z Flip 3-5
- **Dual-band**: All Galaxy S/Note/A series since 2013

### Google Pixel
- **WiFi 7**: Pixel 9 Pro, Pixel 10 series (2024+)
- **WiFi 6E**: Pixel 6-9 series
- **Dual-band**: All Pixels

### Laptops
- **WiFi 7**: High-end 2024+ models (MacBook Pro 2024, Surface Laptop 7)
- **WiFi 6E**: Premium 2021-2023 models (MacBook Pro 2021-2023, Surface Laptop 5-6)
- **Dual-band**: Most laptops since 2015

### IoT/Smart Home (2.4GHz-only)
- Video doorbells (Ring, Nest, Wyze)
- Smart thermostats (Nest, Ecobee)
- Smart plugs/switches
- Voice assistants (Echo Dot, Google Home Mini)
- Streaming sticks (Chromecast, Fire TV Stick)
- Some security cameras

## Real-World Example

**Scenario**: a 2.4-GHz-only smart plug keeps dropping off the network. The
daemon reports the disconnects but never explains them, because the plug is not
in the database — so `client.known_pathology` cannot tell a chip limitation from
a coverage problem, and no `iot_pmf_11r` finding is raised.

**Solution**:
1. Find the name the controller shows for it, e.g. `Laundry-Tasmota-Plug`
2. Open your copy at `data/wifi_device_capabilities.json` (see [Location](#location))
3. Find the `known_2.4ghz_only` section
4. Add a pattern that appears in that name: `"tasmota"`
5. Save the file
6. Restart the daemon so the detectors reload the database

After the next WINDOW pass the repeat disconnects are attributed as
`iot_pmf_11r` (a 2.4-GHz-only device likely PMF / 802.11r intolerant) rather than
left unexplained. The same entry stops a gigabit switch port that negotiates 100
Mbps to that plug from being reported as a bad cable.

Note `"tasmota"` is a single word on purpose. `"tasmota plug"` would **not**
match `Laundry-Tasmota-Plug`, because the hostname uses hyphens and no
normalization happens.

## Troubleshooting

### Device Not Being Detected

**Problem**: Your device shows as "Unknown" or wrong capability level.

**Solutions**:
1. Check the hostname the controller actually reports — patterns match the client
   name as UniFi sees it, which is often not the name on the device itself
2. Add pattern: Ensure pattern is in correct section and lowercase
3. Check priority: More specific patterns should be in higher-priority sections (WiFi 7 before dual-band)
4. Test matching:
   ```python
   from netadmin.detect import device_kb
   kb = device_kb.load_kb()
   name = "Your-Device-Name".lower()
   for section in ("wifi7_devices", "wifi6e_devices", "dual_band_devices", "known_2.4ghz_only"):
       hits = [p for p in device_kb.section_patterns(kb, section) if p in name]
       print(section, "->", hits)
   ```

### Pattern Not Loading

**Problem**: Changes to JSON file not taking effect.

**Solutions**:
1. Check which file is actually being read: run the snippet under
   [Testing Changes](#testing-changes). If it prints a `site-packages` path, your
   copy is not where the loader looks — see [Location](#location).
2. Check the data directory: your copy must be at
   `<data dir>/wifi_device_capabilities.json`, where the data directory is
   `NETADMIN_DATA_DIR` if set, else `./data` relative to the daemon's working
   directory (for systemd, that is `WorkingDirectory=`).
3. Check JSON syntax: a malformed file is skipped, and the daemon logs
   `known_pathology: could not load device KB at ...; running KB-empty`.
4. Restart the daemon: the database is cached in-process.
5. Check permissions: ensure the file is readable by the daemon's user.

### False Positives

**Problem**: Generic devices being flagged as WiFi 7/6E.

**Solutions**:
1. Use more specific patterns: `"galaxy s24 ultra"` not just `"galaxy"`
2. Check pattern order: Specific patterns before generic
3. Add to 2.4GHz-only: If device should never be on 5GHz/6GHz

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-01-05 | Initial release with WiFi 7/6E/dual-band/2.4GHz-only sections |

## Contributing

When adding devices to the database:
1. Test on your own network first
2. Verify the device actually supports the claimed capability
3. Use official device specs as reference
4. Submit changes with description of devices added
5. Include model numbers and years if applicable

## References

- [WiFi Alliance Device Database](https://www.wi-fi.org/product-finder)
- [IEEE 802.11 Standards](https://www.ieee802.org/11/)
- [UniFi Community](https://community.ui.com/)

## Support

For questions about the device database:
1. Check if device is already in database: search JSON file
2. Verify device actually supports claimed WiFi standard: check manufacturer specs
3. Test pattern matching: use test commands above
4. Check documentation: read this file thoroughly
