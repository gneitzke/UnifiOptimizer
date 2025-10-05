# Verbose Logging Feature

## Overview

When the `--verbose` flag is used, the tool creates a detailed log file that captures **every API call** made to your UniFi controller. This is invaluable for troubleshooting, debugging, and understanding exactly what the tool is doing.

## Usage

```bash
python3 optimizer.py analyze --host https://192.168.1.1 --username audit --verbose
```

## Log File Details

### File Naming
- **Format:** `verbose_YYYYMMDD_HHMMSS.log`
- **Example:** `verbose_20251005_143022.log`
- **Location:** Current working directory

### What's Logged

#### 1. Session Information
```
2025-10-05 14:30:22 - INFO - ====================================
2025-10-05 14:30:22 - INFO - UniFi Network Optimizer - Verbose Log
2025-10-05 14:30:22 - INFO - ====================================
2025-10-05 14:30:22 - INFO - Controller: https://192.168.1.1
2025-10-05 14:30:22 - INFO - Username: audit
2025-10-05 14:30:22 - INFO - Site: default
2025-10-05 14:30:22 - INFO - SSL Verification: False
2025-10-05 14:30:22 - INFO - ====================================
```

#### 2. Authentication
```
2025-10-05 14:30:23 - INFO - Attempting login...
2025-10-05 14:30:23 - INFO - POST https://192.168.1.1/api/auth/login
2025-10-05 14:30:23 - DEBUG - Login data: {'username': 'audit', 'password': '***REDACTED***', 'remember': False}
2025-10-05 14:30:23 - INFO - Login response: 200
2025-10-05 14:30:23 - INFO - ✓ Login successful
2025-10-05 14:30:23 - INFO - CSRF token obtained from: Set-Cookie header
```

#### 3. API Calls (Success)
```
2025-10-05 14:30:24 - INFO - GET https://192.168.1.1/proxy/network/api/s/default/stat/device
2025-10-05 14:30:24 - INFO - Response: 200
2025-10-05 14:30:24 - INFO - ✓ GET s/default/stat/device successful (returned 7 items)

2025-10-05 14:30:25 - INFO - GET https://192.168.1.1/proxy/network/api/s/default/stat/sta
2025-10-05 14:30:25 - INFO - Response: 200
2025-10-05 14:30:25 - INFO - ✓ GET s/default/stat/sta successful (returned 120 items)
```

#### 4. API Errors
```
2025-10-05 14:30:26 - INFO - GET https://192.168.1.1/proxy/network/api/s/default/stat/event
2025-10-05 14:30:26 - INFO - Response: 401
2025-10-05 14:30:26 - ERROR - ✗ GET s/default/stat/event returned 401
2025-10-05 14:30:26 - ERROR -   Error: Unauthorized - Authentication failed or session expired
2025-10-05 14:30:26 - ERROR -   Response: {"meta":{"rc":"error","msg":"api.err.Unauthorized"},"data":[]}
```

#### 5. Configuration Changes (PUT/POST)
```
2025-10-05 14:31:15 - INFO - PUT https://192.168.1.1/proxy/network/api/s/default/rest/device/abc123
2025-10-05 14:31:15 - DEBUG -   Data: {'band_steering_mode': 'prefer_5g', 'bandsteering_mode': 'prefer_5g'}
2025-10-05 14:31:15 - INFO - Response: 200
2025-10-05 14:31:15 - INFO - ✓ PUT s/default/rest/device/abc123 successful
```

#### 6. Exceptions
```
2025-10-05 14:30:27 - ERROR - ✗ GET error for s/default/stat/health: Connection timeout
2025-10-05 14:30:27 - ERROR -   Exception type: Timeout
2025-10-05 14:30:27 - ERROR -   Traceback:
Traceback (most recent call last):
  File "/path/to/cloudkey_gen2_client.py", line 245, in get
    response = self.session.get(url, verify=self.verify_ssl)
  ...
requests.exceptions.Timeout: Connection timeout after 30 seconds
```

## Benefits

### For Users
- **Troubleshooting**: See exactly which API calls are failing and why
- **Debugging**: Full error messages and stack traces
- **Transparency**: Complete visibility into what the tool is doing
- **Proof**: Evidence of successful operations or specific errors

### For Support/Bug Reports
- **Context**: Timestamp and sequence of operations
- **Reproduction**: Complete request/response history
- **Diagnosis**: Pinpoint exact failure points
- **Security**: Passwords are automatically redacted

## Privacy & Security

### What's Redacted
- ✅ Passwords (shown as `***REDACTED***`)
- ✅ Session tokens (not logged)
- ✅ CSRF tokens (not logged in full)

### What's Included
- ✅ Controller IP/hostname
- ✅ Username
- ✅ API endpoints called
- ✅ Response codes and error messages
- ✅ Data structures (device configs, client info)

**Note:** Log files may contain network configuration details. Review before sharing publicly.

## Common Use Cases

### 1. Troubleshooting Authentication Issues
Look for:
```
ERROR - Login failed: 401
ERROR - Response text: {"meta":{"rc":"error","msg":"api.err.LoginRequired"}}
```

### 2. Identifying Permission Problems
Look for:
```
ERROR - GET s/default/rest/device returned 403
ERROR - Error: Forbidden - Insufficient permissions
```

### 3. Finding Missing Endpoints
Look for:
```
ERROR - GET s/default/stat/event returned 404
ERROR - Error: Not Found - Endpoint does not exist
```

### 4. Tracking Configuration Changes
Look for:
```
INFO - PUT s/default/rest/device/abc123 successful
DEBUG - Data: {'channel': 36, 'tx_power_mode': 'medium'}
```

### 5. Analyzing Performance Issues
- Check timestamps between calls to identify slow endpoints
- Look for timeout errors
- Identify retry patterns

## File Management

### Automatic Creation
- Log file is created when verbose mode is enabled
- First entry logs session information
- File remains open until program exits

### Manual Cleanup
```bash
# Remove all verbose logs
rm verbose_*.log

# Remove logs older than 7 days
find . -name "verbose_*.log" -mtime +7 -delete

# Keep only latest 5 logs
ls -t verbose_*.log | tail -n +6 | xargs rm
```

### Size Considerations
- Typical analysis: 50-200 KB
- Large networks (100+ APs): 500 KB - 2 MB
- With errors/retries: May be larger due to full error text

## Example Session

Complete verbose log for a successful analysis:

```
2025-10-05 14:30:22 - INFO - ====================================
2025-10-05 14:30:22 - INFO - UniFi Network Optimizer - Verbose Log
2025-10-05 14:30:22 - INFO - ====================================
2025-10-05 14:30:22 - INFO - Controller: https://192.168.1.1
2025-10-05 14:30:22 - INFO - Username: audit
2025-10-05 14:30:22 - INFO - Site: default
2025-10-05 14:30:22 - INFO - SSL Verification: False
2025-10-05 14:30:22 - INFO - ====================================
2025-10-05 14:30:23 - INFO - Attempting login...
2025-10-05 14:30:23 - INFO - POST https://192.168.1.1/api/auth/login
2025-10-05 14:30:23 - INFO - Login response: 200
2025-10-05 14:30:23 - INFO - ✓ Login successful
2025-10-05 14:30:23 - INFO - CSRF token obtained from: Set-Cookie header
2025-10-05 14:30:24 - INFO - GET https://192.168.1.1/proxy/network/api/s/default/stat/device
2025-10-05 14:30:24 - INFO - Response: 200
2025-10-05 14:30:24 - INFO - ✓ GET s/default/stat/device successful (returned 7 items)
2025-10-05 14:30:25 - INFO - GET https://192.168.1.1/proxy/network/api/s/default/stat/sta
2025-10-05 14:30:25 - INFO - Response: 200
2025-10-05 14:30:25 - INFO - ✓ GET s/default/stat/sta successful (returned 120 items)
2025-10-05 14:30:26 - INFO - GET https://192.168.1.1/proxy/network/api/s/default/stat/health
2025-10-05 14:30:26 - INFO - Response: 200
2025-10-05 14:30:26 - INFO - ✓ GET s/default/stat/health successful (returned 1 items)
```

## Best Practices

### When to Use Verbose Mode
✅ **DO use when:**
- Troubleshooting connection issues
- Debugging unexpected behavior
- Reporting bugs to developers
- Validating tool behavior
- Documenting analysis runs

❌ **DON'T use when:**
- Running routine analyses (unnecessary overhead)
- You don't need detailed logs
- Disk space is limited
- You won't review the logs

### Sharing Logs Securely
1. **Review first** - Check for sensitive information
2. **Redact if needed** - Remove any private network details
3. **Use secure channels** - Don't post publicly without review
4. **Include context** - Explain what you were trying to do
5. **Specify line numbers** - Point to specific errors

## Summary

The verbose logging feature provides:
- ✅ Complete API call history with timestamps
- ✅ Full error details and stack traces
- ✅ Request/response logging for all endpoints
- ✅ Automatic file creation with timestamp
- ✅ Password redaction for security
- ✅ Invaluable for troubleshooting and debugging

**Bottom line:** Use `--verbose` when you need to know exactly what's happening under the hood. The log file will tell you everything.
