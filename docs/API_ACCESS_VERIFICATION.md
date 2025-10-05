# API Access Verification

## Overview

The tool now automatically verifies that your account has proper API access **immediately after login**, before attempting any analysis. This catches permission issues early with clear error messages, rather than failing silently or showing confusing errors later.

## The Problem

### Before This Feature

**Scenario:** User logs in with a read-only account
```bash
$ python3 optimizer.py analyze --host https://192.168.1.1 --username readonly_user
✓ Logged in to CloudKey Gen2+

Analyzing network...
⚠ GET s/default/stat/device returned 403
⚠ GET s/default/stat/sta returned 403
⚠ GET s/default/stat/health returned 403
⚠ Warning: 15 API call(s) failed
```

**Problems:**
- ❌ User thinks they're logged in successfully
- ❌ Analysis appears to run but fails everywhere
- ❌ Confusing error messages throughout
- ❌ Unclear what the actual problem is (permissions vs. network vs. controller)
- ❌ Wasted time troubleshooting the wrong things

### After This Feature

**Same scenario with read-only account:**
```bash
$ python3 optimizer.py analyze --host https://192.168.1.1 --username readonly_user
✓ Logged in to CloudKey Gen2+

✗ API Access Denied: Insufficient Permissions
Your account does not have the required permissions.

Required: Administrator or Full Management role

To fix this:
  1. Log into the UniFi Controller as an administrator
  2. Go to Settings → Admins
  3. Find user 'readonly_user'
  4. Change role to 'Administrator' or 'Full Management'

Read-only accounts cannot use this tool.
```

**Benefits:**
- ✅ Immediate clear error message
- ✅ Exact problem identified (insufficient permissions)
- ✅ Step-by-step instructions to fix
- ✅ No wasted time analyzing with wrong credentials
- ✅ No confusing failures throughout analysis

---

## How It Works

### Verification Process

After successful login, the tool:

1. **Tests API Access** - Calls a basic UniFi API endpoint
2. **Analyzes Response** - Checks the HTTP status code
3. **Detects Issues** - Identifies specific permission problems
4. **Provides Guidance** - Shows clear error messages with solutions

### Tested Endpoints

The verification tries two endpoints in order:

1. **Primary:** `GET /proxy/network/api/s/{site}/self`
   - User profile endpoint
   - Should return 200 for all authenticated users with API access

2. **Fallback:** `GET /proxy/network/api/s/{site}/stat/device`
   - Device list endpoint
   - Used if primary returns 404 (API version differences)

### Response Codes & Actions

| Code | Meaning | Tool Action |
|------|---------|-------------|
| **200** | API access confirmed | ✓ Continue with analysis |
| **401** | Authentication failed | ✗ Show auth error, exit |
| **403** | Insufficient permissions | ✗ Show permission error, exit |
| **404** | Endpoint not found | Try fallback endpoint |
| **Other** | Unexpected error | ⚠ Log warning, continue (fail later if real issue) |

---

## Error Messages Explained

### 1. 403 Forbidden - Insufficient Permissions

**When you see this:**
```
✗ API Access Denied: Insufficient Permissions
Your account does not have the required permissions.

Required: Administrator or Full Management role

To fix this:
  1. Log into the UniFi Controller as an administrator
  2. Go to Settings → Admins
  3. Find user 'audit'
  4. Change role to 'Administrator' or 'Full Management'

Read-only accounts cannot use this tool.
```

**What it means:**
- Your login credentials were **valid**
- You successfully **authenticated**
- But your account has **read-only** or **limited** role
- The UniFi API rejected your access with 403 Forbidden

**Common causes:**
- Using a "Read Only" account
- Using a "Limited Admin" account
- Account was recently demoted from Admin to Read Only
- Using a viewer/guest account

**Solution:**
Promote the account to Administrator or Full Management role in controller settings.

### 2. 401 Unauthorized - Session Issues

**When you see this:**
```
✗ API Access Denied: Authentication failed
Your credentials were accepted for login, but API access was denied.

Possible causes:
  • Session expired immediately after login
  • Controller authentication system issue
  • Try logging in again
```

**What it means:**
- You logged in successfully
- But the session token became invalid immediately
- This is rare and usually indicates a controller issue

**Common causes:**
- Controller session timeout set to 0 seconds (misconfiguration)
- Controller authentication system malfunction
- Network interruption between login and API test
- Controller restarting/reloading during login

**Solution:**
1. Try again - might be a temporary glitch
2. Check controller logs for authentication errors
3. Restart controller if issue persists
4. Check network connectivity

---

## Verbose Mode Output

With `--verbose` flag, you'll see detailed verification logging:

### Success Case
```
2025-10-05 14:30:23 - INFO - ✓ Login successful
2025-10-05 14:30:23 - INFO - Verifying API access...
2025-10-05 14:30:23 - INFO - Testing: https://192.168.1.1/proxy/network/api/s/default/self
2025-10-05 14:30:23 - INFO - API access test response: 200
2025-10-05 14:30:23 - INFO - ✓ API access verified
```

### Failure Case (403)
```
2025-10-05 14:30:23 - INFO - ✓ Login successful
2025-10-05 14:30:23 - INFO - Verifying API access...
2025-10-05 14:30:23 - INFO - Testing: https://192.168.1.1/proxy/network/api/s/default/self
2025-10-05 14:30:23 - INFO - API access test response: 403
2025-10-05 14:30:23 - ERROR - API access test failed: 403 Forbidden
2025-10-05 14:30:23 - ERROR - Response: {"meta":{"rc":"error","msg":"api.err.NoPermission"},"data":[]}
```

### Fallback Case (404 → 200)
```
2025-10-05 14:30:23 - INFO - ✓ Login successful
2025-10-05 14:30:23 - INFO - Verifying API access...
2025-10-05 14:30:23 - INFO - Testing: https://192.168.1.1/proxy/network/api/s/default/self
2025-10-05 14:30:23 - INFO - API access test response: 404
2025-10-05 14:30:23 - INFO - Testing alternative endpoint: https://192.168.1.1/proxy/network/api/s/default/stat/device
2025-10-05 14:30:23 - INFO - Alternative test response: 200
2025-10-05 14:30:23 - INFO - ✓ API access verified (alternative endpoint)
```

---

## Role Requirements

### Required Roles ✅

These roles have full API access and will work:

1. **Administrator** (Super Admin)
   - Full access to all features
   - Can read and write all settings
   - Can manage other admins

2. **Full Management**
   - Full access to network settings
   - Can read and write device configs
   - Cannot manage other admins (but that's fine for this tool)

### Insufficient Roles ❌

These roles will be **rejected** by the API access check:

1. **Read Only**
   - Can view settings but cannot access API
   - Login succeeds but API calls return 403
   - **Will not work with this tool**

2. **Limited Admin** (site-specific, no API)
   - Limited to specific sites
   - May not have API endpoint access
   - **Will not work with this tool**

3. **Viewer/Guest**
   - View-only access
   - No API access at all
   - **Will not work with this tool**

---

## Implementation Details

### Code Location
`api/cloudkey_gen2_client.py` - Method `_verify_api_access()`

### When It Runs
- Called automatically after successful login
- Before any analysis begins
- Part of the login flow in `login()` method

### What It Does
```python
def _verify_api_access(self):
    """
    Verify that the user has API access by testing a basic UniFi endpoint
    This catches read-only users or users without proper permissions early
    
    Returns:
        bool: True if API access is available, False otherwise
    """
    # Test endpoint
    test_url = self._get_api_url(f"s/{self.site}/self")
    response = self.session.get(test_url, verify=self.verify_ssl)
    
    # Check response code
    if response.status_code == 403:
        # Show detailed error message
        console.print("[red]✗ API Access Denied: Insufficient Permissions[/red]")
        # ... detailed guidance ...
        return False
    
    elif response.status_code == 200:
        # Success
        return True
    
    # ... handle other cases ...
```

### Error Handling
- Does **not** fail on network errors (continues, will fail later if real issue)
- Does **not** fail on unexpected status codes (logs warning, continues)
- **Only** fails on clear permission issues (403 Forbidden)
- **Only** fails on authentication issues (401 Unauthorized after login)

---

## User Impact

### Before API Verification
**Time to identify permission issue:** 5-10 minutes
1. Run analysis
2. See many 403 errors
3. Google "unifi api 403 forbidden"
4. Try different credentials
5. Eventually realize it's a permissions issue
6. Fix role
7. Re-run

**User experience:** Frustrating, confusing, time-consuming

### After API Verification
**Time to identify permission issue:** < 30 seconds
1. Run analysis
2. See clear "Insufficient Permissions" error
3. Follow step-by-step instructions
4. Fix role
5. Re-run

**User experience:** Clear, fast, guided

---

## Testing Scenarios

### Test Case 1: Administrator Account ✅
```bash
$ python3 optimizer.py analyze --host https://192.168.1.1 --username admin
✓ Logged in to CloudKey Gen2+
✓ API access verified
Analyzing network...
```
**Expected:** Analysis proceeds normally

### Test Case 2: Read-Only Account ❌
```bash
$ python3 optimizer.py analyze --host https://192.168.1.1 --username readonly
✓ Logged in to CloudKey Gen2+
✗ API Access Denied: Insufficient Permissions
```
**Expected:** Clear error, exits immediately, no wasted analysis

### Test Case 3: Invalid Credentials ❌
```bash
$ python3 optimizer.py analyze --host https://192.168.1.1 --username wrong
Login failed: 401
Response: {"meta":{"rc":"error","msg":"api.err.Invalid"}}
```
**Expected:** Login fails before API verification runs

### Test Case 4: Session Timeout ⚠️
```bash
$ python3 optimizer.py analyze --host https://192.168.1.1 --username admin
✓ Logged in to CloudKey Gen2+
✗ API Access Denied: Authentication failed
Your credentials were accepted for login, but API access was denied.
```
**Expected:** Rare edge case detected and explained

---

## Benefits Summary

### For Users
✅ **Immediate feedback** - Know right away if permissions are wrong  
✅ **Clear error messages** - Exactly what's wrong and how to fix it  
✅ **Time savings** - No wasted analysis attempts with wrong credentials  
✅ **Step-by-step guidance** - Explicit instructions to resolve the issue  
✅ **Better experience** - Less frustration, more productivity  

### For Support
✅ **Fewer support tickets** - Users can self-resolve permission issues  
✅ **Better bug reports** - Clear distinction between permission vs. actual bugs  
✅ **Faster diagnosis** - Verbose logs show exact verification results  
✅ **Reduced confusion** - Users understand read-only accounts don't work  

### For Developers
✅ **Cleaner error handling** - Permission issues caught early  
✅ **Better logging** - Verification results logged in verbose mode  
✅ **Easier debugging** - Clear separation of auth vs. permission vs. API issues  
✅ **More reliable** - No mysterious 403s throughout analysis  

---

## Configuration

### Disabling Verification (Not Recommended)

The verification is **always enabled** and cannot be disabled. This is intentional because:
- It runs only once (negligible performance impact)
- It prevents wasted time on doomed analysis runs
- It provides essential user experience improvement
- It helps users self-diagnose permission issues

If you really need to bypass it (e.g., for testing), you would need to modify the code:

```python
# In cloudkey_gen2_client.py, login() method
# Comment out this line:
# if not self._verify_api_access():
#     return False
```

**⚠️ Not recommended** - You'll get confusing 403 errors throughout analysis instead.

---

## Summary

The API access verification feature:

- ✅ **Runs automatically** after successful login
- ✅ **Tests API access** with a basic endpoint call
- ✅ **Detects permission issues** immediately
- ✅ **Shows clear errors** with step-by-step solutions
- ✅ **Saves time** by failing fast on permission issues
- ✅ **Improves experience** with actionable error messages
- ✅ **Logs details** in verbose mode for troubleshooting

**Bottom line:** If you can login but don't have API access, you'll know immediately with clear guidance on how to fix it. No more wasted time analyzing with the wrong account type.
