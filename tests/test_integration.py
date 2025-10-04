#!/usr/bin/env python3
"""
Integration tests for the UniFi EasyToolkit authentication flow.
"""
import unittest
import sys
import os
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import ssl
from unittest.mock import patch
import socket

# Add the parent directory to sys.path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import modules to test
from DeepAnalyse import auth_session
import utils

# Create a mock server for testing
class MockUniFiHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler for UniFi API"""
    
    def do_POST(self):
        """Handle POST requests"""
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        payload = json.loads(post_data.decode('utf-8'))
        
        # Path to handle login requests
        if self.path == '/api/auth/login':
            # Check if we're simulating 2FA
            if self.server.require_2fa and 'token' not in payload:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "errors": {"X_HAS_2FA_ENABLED": True},
                    "meta": {"rc": "error"}
                }).encode('utf-8'))
                return
            
            # Check credentials (username/password) - accept any for test
            if 'username' in payload and 'password' in payload:
                # If 2FA is enabled and token is provided, check it
                if self.server.require_2fa and ('token' not in payload or payload['token'] != '123456'):
                    self.send_response(401)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "meta": {"rc": "error", "msg": "Invalid 2FA token"}
                    }).encode('utf-8'))
                    return
                
                # Success case
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "meta": {"rc": "ok"},
                    "data": {"token": "mock-token-123"}
                }).encode('utf-8'))
                return
        
        # Default error response
        self.send_response(404)
        self.end_headers()
    
    def log_message(self, format, *args):
        """Silence log messages"""
        pass

class MockUniFiServer(HTTPServer):
    """Mock UniFi server"""
    def __init__(self, server_address, RequestHandlerClass, require_2fa=False):
        super().__init__(server_address, RequestHandlerClass)
        self.require_2fa = require_2fa

def get_free_port():
    """Get a free port to use for the mock server"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('localhost', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port

class TestAuthIntegration(unittest.TestCase):
    """Integration tests for authentication functionality"""
    
    @classmethod
    def setUpClass(cls):
        """Set up the mock server once for all tests"""
        cls.normal_port = get_free_port()
        cls.tfa_port = get_free_port()
        
        # Create normal server
        cls.normal_server = MockUniFiServer(('localhost', cls.normal_port), MockUniFiHandler, require_2fa=False)
        cls.normal_server_thread = threading.Thread(target=cls.normal_server.serve_forever)
        cls.normal_server_thread.daemon = True
        cls.normal_server_thread.start()
        
        # Create 2FA server
        cls.tfa_server = MockUniFiServer(('localhost', cls.tfa_port), MockUniFiHandler, require_2fa=True)
        cls.tfa_server_thread = threading.Thread(target=cls.tfa_server.serve_forever)
        cls.tfa_server_thread.daemon = True
        cls.tfa_server_thread.start()
        
        # Wait for servers to start
        time.sleep(0.1)
    
    @classmethod
    def tearDownClass(cls):
        """Shut down the mock server"""
        cls.normal_server.shutdown()
        cls.tfa_server.shutdown()
        cls.normal_server_thread.join()
        cls.tfa_server_thread.join()
    
    @patch('DeepAnalyse.prompt')
    @patch('DeepAnalyse.sys.exit')  # Patch sys.exit to prevent test from exiting on error
    @patch('utils.normalize_host_url')  # Patch normalize_host_url to keep HTTP protocol
    def test_normal_authentication(self, mock_normalize_url, mock_exit, mock_prompt):
        """Test regular authentication flow without 2FA"""
        # Setup mocks
        host = f"http://localhost:{self.normal_port}"
        mock_normalize_url.return_value = host  # Don't convert to HTTPS for testing
        
        # Setup prompt mock to return username and password
        mock_prompt.side_effect = ["testuser", "testpassword"]
        
        # Create steps list
        steps = []
        
        # Call auth_session
        try:
            session = auth_session(host, "user", steps)
            
            # Check if authentication was successful
            self.assertIsNotNone(session)
            self.assertIn("Auth: Authentication successful", steps)
        except SystemExit:
            self.fail("auth_session called sys.exit unexpectedly")
    
    @patch('DeepAnalyse.prompt')
    @patch('DeepAnalyse.sys.exit')  # Patch sys.exit to prevent test from exiting on error
    @patch('utils.normalize_host_url')  # Patch normalize_host_url to keep HTTP protocol
    def test_2fa_authentication(self, mock_normalize_url, mock_exit, mock_prompt):
        """Test 2FA authentication flow"""
        # Setup mocks
        host = f"http://localhost:{self.tfa_port}"
        mock_normalize_url.return_value = host  # Don't convert to HTTPS for testing
        
        # Setup prompt mock to return username, password, and 2FA code
        mock_prompt.side_effect = ["testuser", "testpassword", "123456"]
        
        # Create steps list
        steps = []
        
        # Call auth_session
        try:
            session = auth_session(host, "user", steps)
            
            # Check if authentication was successful
            self.assertIsNotNone(session)
            self.assertIn("Auth: 2FA authentication successful", steps)
        except SystemExit:
            self.fail("auth_session called sys.exit unexpectedly")

if __name__ == '__main__':
    unittest.main()
