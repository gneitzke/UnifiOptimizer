# Verbose Mode Guide

## Overview

The `--verbose` flag enables comprehensive API request/response logging, allowing you to see exactly what data is being sent to and received from your UniFi Controller. This is invaluable for debugging issues, understanding API behavior, and identifying bugs.

## Usage

Add the `--verbose` flag to any command:

```bash
# Analyze network with verbose logging
python optimizer.py --verbose analyze

# Apply changes with verbose logging
python optimizer.py --verbose apply

# Generate report with verbose logging
python optimizer.py --verbose report
```

## What Gets Logged

When verbose mode is enabled, you'll see detailed information for every API call:

### GET Requests

**Request Details:**
- HTTP method (GET)
- Full URL
- API path

**Response Details:**
- Status code
- Number of items returned (for lists)
- Sample data (first item for lists, or full data for single objects)
- Formatted JSON with proper indentation

**Example Output:**
```
→ GET Request
URL: https://192.168.1.1:8443/proxy/network/api/s/default/stat/device
Path: s/default/stat/device

← Response (200)
Data items: 15
Sample item (first of 15):
{
  "adopted": true,
  "config_network": {
    "ip": "192.168.1.100",
    "type": "dhcp"
  },
  "device_id": "abc123...",
  "ip": "192.168.1.100",
  "mac": "aa:bb:cc:dd:ee:ff",
  "model": "U6-Pro",
  "name": "Living Room AP",
  "state": 1,
  "type": "uap",
  ...
}
```

### PUT Requests

**Request Details:**
- HTTP method (PUT)
- Full URL
- API path
- Full request payload (formatted JSON)
- CSRF token (truncated for security)

**Response Details:**
- Status code
- Full response data (formatted JSON)

**Example Output:**
```
→ PUT Request
URL: https://192.168.1.1:8443/proxy/network/api/s/default/rest/device/abc123
Path: s/default/rest/device/abc123
Payload:
{
  "config_network": {
    "type": "dhcp"
  },
  "radio_table": [
    {
      "channel": 36,
      "ht": "80",
      "min_rssi_enabled": true,
      "min_rssi": -75,
      "name": "ra0",
      "radio": "ng"
    }
  ]
}
CSRF Token: c0a8010a7b2c4d5e6f78...

← Response (200)
Response data:
{
  "meta": {
    "rc": "ok"
  },
  "data": [
    {
      "device_id": "abc123",
      "state": 1,
      ...
    }
  ]
}
```

### POST Requests

**Request Details:**
- HTTP method (POST)
- Full URL
- API path
- Full request payload (formatted JSON)
- CSRF token (truncated for security)

**Response Details:**
- Status code
- Full response data (formatted JSON)

**Example Output:**
```
→ POST Request
URL: https://192.168.1.1:8443/proxy/network/api/s/default/cmd/devmgr
Path: s/default/cmd/devmgr
Payload:
{
  "cmd": "set-locate",
  "mac": "aa:bb:cc:dd:ee:ff"
}
CSRF Token: c0a8010a7b2c4d5e6f78...

← Response (200)
Response data:
{
  "meta": {
    "rc": "ok"
  },
  "data": []
}
```

### Error Responses

For non-200 responses, you'll see:
- Status code
- Full error response (formatted JSON or text)
- Error description

**Example Output:**
```
← Response (Non-200)
Status: 401
Response:
{
  "meta": {
    "rc": "error",
    "msg": "api.err.LoginRequired"
  },
  "data": []
}
```

### Exceptions

For exceptions (network errors, timeouts, etc.), you'll see:
- Request path
- Error message
- Exception type
- Full traceback
- Error detail as JSON

**Example Output:**
```
✗ GET Request Failed
Path: s/default/stat/device
Error: Connection timeout
Type: ConnectionTimeout
Traceback:
  File "cloudkey_gen2_client.py", line 421, in get
    response = self.session.get(url, headers=headers, verify=self.verify_ssl)
  ...
Error detail:
{
  "method": "GET",
  "path": "s/default/stat/device",
  "status_code": null,
  "error": "Connection timeout",
  "exception_type": "ConnectionTimeout"
}
```

## Color Coding

Verbose output uses color coding to make it easier to scan:
- **Cyan** - GET requests
- **Green** - Successful responses
- **Yellow** - PUT requests and non-200 responses
- **Magenta** - POST requests
- **Red** - Errors and exceptions
- **Dim** - Stack traces and detailed error info

## Use Cases

### 1. Debugging Connection Issues

If you're having trouble connecting to your controller:
```bash
python optimizer.py --verbose analyze
```

Look for:
- Authentication errors (401)
- Permission errors (403)
- Network timeouts
- SSL certificate issues

### 2. Understanding API Behavior

If you want to see what data the UniFi API returns:
```bash
python optimizer.py --verbose analyze
```

You'll see:
- All device data fields
- Available configuration options
- Response structure
- Data formats

### 3. Identifying Data Issues

If recommendations seem incorrect:
```bash
python optimizer.py --verbose analyze
```

Check:
- Channel data from API
- Client connection info
- Radio settings
- Band steering configuration

### 4. Troubleshooting Apply Failures

If applying changes fails:
```bash
python optimizer.py --verbose apply
```

Look for:
- PUT request payloads (what's being sent)
- Error responses (why it failed)
- Configuration validation errors

### 5. Verifying CSRF Token Handling

If you see CSRF-related errors:
```bash
python optimizer.py --verbose analyze
```

You'll see:
- CSRF tokens being sent with requests
- Token updates from responses
- Token validation errors

## Performance Impact

Verbose mode has minimal performance impact:
- API calls are not slowed down
- JSON formatting happens after response is received
- Console output is buffered
- Only active when `--verbose` flag is used

## Combining with Log Files

Verbose mode complements the log file:
- **Console (verbose)**: Formatted, colorized, easy to scan
- **Log file**: Complete records, timestamps, suitable for sharing

Both capture the same information, just in different formats.

## Tips

1. **Pipe to File**: Save verbose output for later analysis
   ```bash
   python optimizer.py --verbose analyze 2>&1 | tee verbose_output.log
   ```

2. **Filter Output**: Use grep to find specific API calls
   ```bash
   python optimizer.py --verbose analyze 2>&1 | grep "stat/device"
   ```

3. **Compare Responses**: Run multiple times to see how data changes
   ```bash
   python optimizer.py --verbose analyze > run1.log
   # Make changes
   python optimizer.py --verbose analyze > run2.log
   diff run1.log run2.log
   ```

4. **Debug Single Issue**: Focus on one area
   - For device issues: Look for `/stat/device` calls
   - For client issues: Look for `/stat/sta` calls
   - For WLAN issues: Look for `/rest/wlanconf` calls

## Privacy Note

Verbose mode outputs:
- ✅ IP addresses
- ✅ MAC addresses
- ✅ Device names
- ✅ Network configuration
- ✅ Partial CSRF tokens (first 20 chars)
- ❌ NOT passwords or full authentication tokens

Be careful sharing verbose output - sanitize sensitive information first.

## Troubleshooting

### Verbose Output Not Showing

**Problem**: You added `--verbose` but don't see detailed output

**Solutions**:
1. Make sure you're using the flag correctly: `python optimizer.py --verbose analyze`
2. Check that you're not piping output in a way that strips colors: `... 2>&1`
3. Verify the code updated correctly: `grep -n "verbose" api/cloudkey_gen2_client.py`

### Too Much Output

**Problem**: Console is flooded with data

**Solutions**:
1. Pipe to a file instead: `... > output.log`
2. Use grep to filter: `... | grep "specific_thing"`
3. Use `less` for pagination: `... | less -R`

### JSON Not Formatted

**Problem**: JSON appears as one long line

**Solutions**:
1. This shouldn't happen - verbose mode auto-formats JSON
2. Check if you're using an old version of the code
3. Verify `json.dumps(data, indent=2)` is in the code

## Implementation Details

The verbose logging is implemented in `api/cloudkey_gen2_client.py`:

- **Lines 397-500**: GET request logging
- **Lines 555-675**: PUT request logging  
- **Lines 715-830**: POST request logging

Each method logs:
1. **Before request**: Method, URL, payload (if any)
2. **After response**: Status code, formatted response data
3. **On error**: Status code, error details, stack trace

All JSON is formatted with 2-space indentation using `json.dumps(data, indent=2, default=str)`.

The `default=str` parameter ensures objects that aren't JSON-serializable (like datetime) get converted to strings instead of causing errors.

## Related Files

- `api/cloudkey_gen2_client.py` - API client with verbose logging
- `core/optimize_network.py` - Main CLI that passes `--verbose` flag
- `verbose_*.log` - Log files (may include verbose output if logged)

## Future Enhancements

Potential improvements to verbose mode:
- [ ] Option to save verbose output to separate file
- [ ] Filter verbose output by API endpoint pattern
- [ ] Show request/response timing information
- [ ] Highlight differences when re-running same command
- [ ] Export verbose data as structured JSON for analysis

## Getting Help

If you're using verbose mode to debug an issue:

1. Run with verbose flag and capture output:
   ```bash
   python optimizer.py --verbose analyze 2>&1 | tee debug.log
   ```

2. Look for error patterns:
   - Status codes (401, 403, 500, etc.)
   - Exception types
   - Failed endpoints

3. Share relevant sections when asking for help:
   - Include the request that failed
   - Include the error response
   - Sanitize any sensitive info first

4. Check the regular log file too:
   - Look in current directory for `verbose_*.log`
   - Contains timestamps and additional context

## See Also

- [API Compatibility Guide](API_COMPATIBILITY.md) - UniFi API versions
- [Error Handling Guide](../Archive/ERROR_HANDLING_USER_GUIDE.md) - Understanding errors
- [README](../README.md) - Main documentation
