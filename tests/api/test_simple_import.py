#!/usr/bin/env python3
"""
Simple import test for session_manager
"""

import sys
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

print("Testing session_manager...")
try:
    from api.auth_manager import get_session, refresh_session, clear_session
    print("Successfully imported auth_manager session functions")
except Exception as e:
    print(f"Error importing auth_manager: {e}")
