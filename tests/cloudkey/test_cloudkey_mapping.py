#!/usr/bin/env python3
"""
Test for CloudKey field mapping functionality
"""
import sys
import os
# Add parent directory to Python path for proper imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

print("Testing CloudKey field mapping...")
try:
    from api.cloudkey_compat import convert_to_cloudkey_format
    
    test_data = {
        "radio.ng.channel": 6,
        "radio.ng.ht": 20,
        "radio.na.channel": 36,
        "radio.na.ht": 40,
        "wpa_security": "wpa2"
    }
    
    result = convert_to_cloudkey_format(test_data)
    print(f"Original data: {test_data}")
    print(f"Converted data: {result}")
    print("Test complete!")
except ImportError as e:
    print(f"Import error: {e}")
except Exception as e:
    print(f"Error during test: {e}")
