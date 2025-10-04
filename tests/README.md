# UniFi Audit Test Suite

```
    ⚡ ╔═══════════════════════════════════════╗ ⚡
      ║                                       ║
      ║    🧪 TESTING LABORATORY 🧪          ║
      ║                                       ║
      ║  📋 Unit Tests    │  🔗 Integration   ║
      ║  🔧 Mock Servers  │  ✅ Validation    ║
      ║                                       ║
      ║      Ensuring UniFi Audit Quality     ║
      ╚═══════════════════════════════════════╝
```

This directory contains comprehensive unit tests and integration tests for the UniFi Audit Tool. Tests have been organized into logical categories for better maintenance and clarity.

## Directory Structure

```
tests/
├── cloudkey/              # CloudKey-specific tests
│   ├── test_cloudkey_api_endpoints.py   # Tests CloudKey API endpoints
│   ├── test_cloudkey_auth_fix.py        # Tests CSRF token and auth fixes
│   ├── diagnose_cloudkey_api.py         # Diagnoses API connectivity issues
│   ├── test_cloudkey_auth.py            # Tests CloudKey authentication
│   └── test_cloudkey_api.py             # Tests CloudKey API functionality
├── data/                  # Test data files
│   └── ...
├── test_connection_analysis.py   # Tests connection analysis functionality
├── test_auth.py           # Tests authentication
├── test_integration.py    # Integration tests
└── test_utils.py          # Tests for utility functions
```

## Test Categories

### 1. CloudKey Tests
- **`cloudkey/test_cloudkey_api_endpoints.py`**: Tests all CloudKey API endpoints for connectivity
- **`cloudkey/test_cloudkey_auth_fix.py`**: Tests CSRF token and auth fixes for Gen2+ devices
- **`cloudkey/diagnose_cloudkey_api.py`**: Diagnostics tool for CloudKey API issues
- **`cloudkey/test_cloudkey_auth.py`**: Authentication tests specific to CloudKey
- **`cloudkey/test_cloudkey_api.py`**: Tests for specific API functionality with CloudKey

### 2. Core Functionality Tests
- **`test_connection_analysis.py`**: Tests for connection log analysis functionality
- **`test_utils.py`**: Tests for utility functions
  - URL normalization (`192.168.1.1` → `https://192.168.1.1`)
  - Data validation functions
  - Helper functions

### 3. Authentication Tests
- **`test_auth.py`**: Tests for authentication functions
  - Username/password authentication
  - Session management
  - Error handling during authentication

### 4. Integration Tests
- **`test_integration.py`**: End-to-end tests with mock UniFi servers
  - Complete audit workflow testing
  - API communication validation

## Running Tests

### Run All Tests
```bash
python -m tests.run_tests
```

### Run Tests by Category
```bash
python -m tests.run_tests --category cloudkey   # Run all CloudKey tests
python -m tests.run_tests --category auth       # Run authentication tests
```

### Run Individual Test Files
```bash
python -m tests.cloudkey.test_cloudkey_api_endpoints  # Run CloudKey API endpoint tests
python -m tests.test_utils                            # Run utility tests
python -m tests.test_connection_analysis              # Run connection analysis tests
```

### Running Tests with a Real Controller
Some tests can be run against a real UniFi controller for verification:

```bash
python -m tests.cloudkey.test_cloudkey_auth_fix --host 192.168.1.1 --username admin --password secret
```

## Adding New Tests

When adding new tests:

1. Place them in the appropriate category directory
2. If creating a new category, add an `__init__.py` file to the directory
3. Update this README if necessary
4. Follow the existing test patterns for consistency

## Test Data

Test data files should be placed in the `tests/data/` directory. The test framework will
automatically load data from this location when needed.

### Run Tests with Coverage
```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

## Manual Verification

For manual verification of features with your actual UniFi controller:

```bash
python3 verify.py
```

This interactive script allows you to test:
- URL normalization with built-in examples or custom input
- Authentication flow with your actual UniFi controller
- 2FA handling in real scenarios

## Test Coverage

The tests comprehensively cover:

### URL Normalization (`test_utils.py`)
- ✅ Adding `https://` prefix to bare IP addresses
- ✅ Converting `http://` URLs to `https://`
- ✅ Preserving existing `https://` URLs
- ✅ Handling empty/invalid URLs gracefully

### Authentication Flow (`test_auth.py`)
- ✅ API key authentication setup
- ✅ Username/password authentication success cases
- ✅ Two-factor authentication detection and handling
- ✅ Proper session management
- ✅ Error handling for failed authentication

### Integration Testing (`test_integration.py`)
- ✅ Mock HTTP server setup for realistic testing
- ✅ Normal authentication without 2FA
- ✅ 2FA authentication with code verification
- ✅ Network error handling
- ✅ Response parsing and validation

## Test Architecture

### Mock Framework
- Uses Python's built-in `unittest.mock` for function mocking
- Custom mock HTTP servers for realistic network simulation
- Isolated test environments to prevent side effects

### Test Data
- Predefined test credentials and responses
- Realistic UniFi API response simulation
- Edge case handling for various error conditions

### Assertions
- Comprehensive verification of function calls
- Response validation and error checking
- State verification after operations

## Adding New Tests

When adding new functionality, please include:

1. **Unit tests** for individual functions
2. **Integration tests** for complete workflows
3. **Error case testing** for edge conditions
4. **Mock data** that represents realistic scenarios

Example test structure:
```python
def test_new_feature(self):
    # Arrange
    mock_setup()
    
    # Act
    result = function_under_test()
    
    # Assert
    self.assertEqual(expected, result)
    verify_side_effects()
```

## Continuous Integration

The test suite is designed to run in automated environments:
- No external dependencies required
- Self-contained mock servers
- Deterministic test results
- Fast execution time (< 2 seconds total)

Run before committing changes:
```bash
python3 tests/run_tests.py && echo "✅ All tests passed"
```
