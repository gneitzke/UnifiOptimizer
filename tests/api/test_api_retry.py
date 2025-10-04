#!/usr/bin/env python3
"""
Test API client retry functionality
"""
import sys
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

print("Testing API retry functionality...")
try:
    from api.client import api_get
    import requests
    
    # Check if the API function is correctly set up
    print("Checking API functions...")
    print(f"api_get exists: {api_get is not None}")
    
    # Examine the module to check for our retry mechanism
    import api.client as api_client
    module_attrs = dir(api_client)
    print(f"Module has SESSION_MANAGER_AVAILABLE: {'SESSION_MANAGER_AVAILABLE' in module_attrs}")
    
    # Print available attributes to inspect
    print("\nAPI client module attributes:")
    for attr in module_attrs:
        if not attr.startswith('__'):
            print(f"- {attr}")
            
    # Test retry mechanism
    print("\nTesting retry configuration...")
    if hasattr(api_client, 'MAX_RETRIES'):
        print(f"MAX_RETRIES: {api_client.MAX_RETRIES}")
    else:
        print("MAX_RETRIES not found")
        
    if hasattr(api_client, 'RETRY_BACKOFF_FACTOR'):
        print(f"RETRY_BACKOFF_FACTOR: {api_client.RETRY_BACKOFF_FACTOR}")
    else:
        print("RETRY_BACKOFF_FACTOR not found")
        
    print("\nRetry mechanism testing complete.")
    
except ImportError as e:
    print(f"Import error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
    import traceback
    traceback.print_exc()
