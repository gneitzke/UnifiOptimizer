# WiFi Device Capability Database

## Overview

The WiFi device capability database (`wifi_device_capabilities.json`) is an external configuration file that defines which devices support which WiFi standards. This allows for easy updates without modifying code.

## Location

```
data/wifi_device_capabilities.json
```

## Purpose

The analyzer uses this database to detect:
- **WiFi 7 (802.11be)** devices: 6GHz capable with 320MHz channels and MLO
- **WiFi 6E (802.11ax-6e)** devices: 6GHz capable with 160MHz channels
- **Dual-band (WiFi 5/6)** devices: 2.4GHz + 5GHz capable
- **2.4GHz-only** devices: IoT/smart home devices that should not be flagged

## Adding New Devices

### Quick Start

To add a new device, simply edit `data/wifi_device_capabilities.json` and add the device name pattern to the appropriate section:

**For a new WiFi 7 device:**
```json
"wifi7_devices": {
  "patterns": [
    "iphone 16",
    "galaxy s24 ultra",
    "YOUR NEW DEVICE NAME"  ← Add here
  ]
}
```

**For a new WiFi 6E device:**
```json
"wifi6e_devices": {
  "patterns": [
    "iphone 13",
    "pixel 6",
    "YOUR NEW DEVICE NAME"  ← Add here
  ]
}
```

### Pattern Matching Rules

1. **Case-Insensitive**: Patterns are matched without regard to case
   - Pattern: `"iphone 16"` matches `"iPhone-16-Pro"`, `"IPHONE 16"`, etc.

2. **Punctuation Normalization**: Hyphens, underscores, and extra spaces are normalized
   - Pattern: `"galaxy s24"` matches `"Galaxy-S24"`, `"Galaxy_S24"`, `"Galaxy  S24"`, etc.

3. **Substring Matching**: Patterns match anywhere in the hostname
   - Pattern: `"iphone 16"` matches `"Johns-iPhone-16-Pro"`, `"iPhone-16"`, `"Work-iPhone-16-Max"`, etc.

4. **Order Matters**: More specific patterns should come first
   - ✅ Good: WiFi 7 → WiFi 6E → Dual-band → 2.4GHz-only
   - ❌ Bad: Generic "galaxy" before specific "galaxy s24 ultra"

### Best Practices

**✅ DO:**
- Use lowercase in patterns
- Use spaces (not hyphens) in patterns
- Be specific for newer devices (`"iphone 16"`, `"galaxy s24 ultra"`)
- Be generic for older device families (`"iphone"`, `"galaxy"`)
- Add comments for clarity (use `"_comment"` fields or inline comments in arrays)

**❌ DON'T:**
- Use overly generic patterns in WiFi 7/6E sections (will match too many devices)
- Duplicate patterns across sections
- Add patterns with special characters (!, @, #, etc.)
- Use regex patterns (simple substring matching only)

### Examples

**Adding a New WiFi 7 Phone:**
```json
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
```json
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
```json
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
# Run validation tests
python tests/test_wifi7_validation.py

# Check that patterns are loading correctly
python -c "
from core.advanced_analyzer import AdvancedNetworkAnalyzer
analyzer = AdvancedNetworkAnalyzer(client=None)
print('WiFi 7 devices:', len(analyzer.device_capabilities['wifi7_devices']['patterns']))
print('WiFi 6E devices:', len(analyzer.device_capabilities['wifi6e_devices']['patterns']))
print('Patterns:', analyzer.device_capabilities['wifi7_devices']['patterns'][:10])
"
```

## Database Structure

### Section Descriptions

| Section | Purpose | Example Patterns |
|---------|---------|------------------|
| `wifi7_devices` | WiFi 7 (802.11be) capable - 6GHz + 320MHz + MLO | `iphone 16`, `galaxy s25` |
| `wifi6e_devices` | WiFi 6E (802.11ax-6e) capable - 6GHz + 160MHz | `iphone 14`, `pixel 7` |
| `dual_band_devices` | Dual-band (2.4/5 GHz) capable - WiFi 5/6 | `iphone`, `galaxy`, `macbook` |
| `known_2.4ghz_only` | 2.4GHz-only devices - don't flag as misplaced | `ring doorbell`, `echo dot` |

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

**Scenario**: You just bought an iPhone 16 Pro Max and it's showing as "5GHz capable" instead of "WiFi 7 capable" in your network analysis.

**Solution**:
1. Open `data/wifi_device_capabilities.json`
2. Find the `wifi7_devices` section
3. Check if `"iphone 16"` is in the patterns list (it should be)
4. If not, add it: `"iphone 16 pro max"` or just `"iphone 16"`
5. Save the file
6. Re-run the analysis: `python optimize_network.py analyze`

The pattern `"iphone 16"` will match all variants: iPhone 16, iPhone 16 Plus, iPhone 16 Pro, iPhone 16 Pro Max.

## Troubleshooting

### Device Not Being Detected

**Problem**: Your device shows as "Unknown" or wrong capability level.

**Solutions**:
1. Check hostname format: Run analysis with `--verbose` to see actual hostnames
2. Add pattern: Ensure pattern is in correct section and lowercase
3. Check priority: More specific patterns should be in higher-priority sections (WiFi 7 before dual-band)
4. Test matching:
   ```python
   from core.advanced_analyzer import AdvancedNetworkAnalyzer
   analyzer = AdvancedNetworkAnalyzer(client=None)
   result = analyzer._check_device_pattern("Your-Device-Name", ["your pattern"])
   print(f"Match result: {result}")
   ```

### Pattern Not Loading

**Problem**: Changes to JSON file not taking effect.

**Solutions**:
1. Check JSON syntax: Validate at https://jsonlint.com
2. Check file location: Must be in `data/wifi_device_capabilities.json`
3. Restart analysis: Python caches imports, restart your analysis script
4. Check permissions: Ensure file is readable

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
