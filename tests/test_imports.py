#!/usr/bin/env python3
"""
Test imports from various modules
"""
import sys
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from api.auth_manager import get_session, refresh_session, clear_session
    print('Session manager imported successfully')
except Exception as e:
    print(f'Error importing api.auth_manager: {e}')

try:
    from api.cloudkey_compat import detect_controller_type, convert_to_cloudkey_format
    print('CloudKey compatibility module imported successfully')
except Exception as e:
    print(f'Error importing api.cloudkey_compat: {e}')

try:
    from api.csrf_token_manager import setup_csrf_session, get_csrf_token
    print('CSRF token manager imported successfully')
except Exception as e:
    print(f'Error importing api.csrf_token_manager: {e}')

try:
    from core.analyzer import Analyzer
    print('Analyzer module imported successfully')
except Exception as e:
    print(f'Error importing core.analyzer: {e}')

try:
    from utils.rssi import analyze_connection_logs
    print('RSSI utilities imported successfully')
except Exception as e:
    print(f'Error importing utils.rssi: {e}')
