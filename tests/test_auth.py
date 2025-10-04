#!/usr/bin/env python3
"""
Unit tests for the authentication functionality, particularly the 2FA support.
"""
import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json
import requests

# Add the parent directory to sys.path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the function to test
from DeepAnalyse import auth_session

class TestAuthentication(unittest.TestCase):
    """Test the authentication functionality"""
    
    @patch('DeepAnalyse.utils.normalize_host_url')
    @patch('DeepAnalyse.requests.Session')
    @patch('DeepAnalyse.prompt')
    @patch('DeepAnalyse.console')
    def test_api_key_authentication(self, mock_console, mock_prompt, mock_session, mock_normalize_url):
        """Test authentication with API key"""
        # Setup mocks
        mock_normalize_url.return_value = "https://192.168.1.1"
        mock_session_instance = MagicMock()
        mock_session.return_value = mock_session_instance
        mock_prompt.return_value = "testApiKey123"
        steps = []
        
        # Call the function
        result = auth_session("192.168.1.1", "key", steps)
        
        # Assertions
        mock_normalize_url.assert_called_once_with("192.168.1.1")
        mock_session_instance.headers.update.assert_any_call({"Accept": "application/json"})
        mock_session_instance.headers.update.assert_any_call({"Authorization": "Bearer testApiKey123"})
        self.assertEqual(result, mock_session_instance)
        self.assertIn("Auth: API key header set", steps)
    
    @patch('DeepAnalyse.utils.normalize_host_url')
    @patch('DeepAnalyse.requests.Session')
    @patch('DeepAnalyse.prompt')
    @patch('DeepAnalyse.console')
    def test_user_password_authentication_success(self, mock_console, mock_prompt, mock_session, mock_normalize_url):
        """Test successful authentication with username and password"""
        # Setup mocks
        mock_normalize_url.return_value = "https://192.168.1.1"
        mock_session_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({"meta": {"rc": "ok"}})
        mock_response.json.return_value = {"meta": {"rc": "ok"}}
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        
        # Setup prompt mock to return username and password
        mock_prompt.side_effect = ["testuser", "testpassword"]
        steps = []
        
        # Call the function
        result = auth_session("192.168.1.1", "user", steps)
        
        # Assertions
        self.assertEqual(result, mock_session_instance)
        self.assertIn("Auth: Authentication successful", steps)
        mock_session_instance.post.assert_called_with("https://192.168.1.1/api/auth/login", json={"username": "testuser", "password": "testpassword"}, timeout=20)
    
    @patch('DeepAnalyse.utils.normalize_host_url')
    @patch('DeepAnalyse.requests.Session')
    @patch('DeepAnalyse.prompt')
    @patch('DeepAnalyse.console')
    def test_2fa_authentication(self, mock_console, mock_prompt, mock_session, mock_normalize_url):
        """Test 2FA authentication flow"""
        # Setup mocks
        mock_normalize_url.return_value = "https://192.168.1.1"
        mock_session_instance = MagicMock()
        
        def custom_post(*args, **kwargs):
            """Custom post function that returns different responses based on payload"""
            payload = kwargs.get('json', {})
            # If no token in payload, return 2FA required
            if 'token' not in payload:
                response = MagicMock()
                response.status_code = 400
                response.text = json.dumps({"errors": {"X_HAS_2FA_ENABLED": True}})
                response.json.return_value = {"errors": {"X_HAS_2FA_ENABLED": True}}
                return response
            else:
                # If token in payload, return success
                response = MagicMock()
                response.status_code = 200
                response.text = json.dumps({"meta": {"rc": "ok"}})
                response.json.return_value = {"meta": {"rc": "ok"}}
                return response
        
        # Set up session to use custom post function
        mock_session_instance.post.side_effect = custom_post
        mock_session.return_value = mock_session_instance
        
        # Setup prompt mock to return username, password, and 2FA code
        mock_prompt.side_effect = ["testuser", "testpassword", "123456"]
        steps = []
        
        # Call the function
        result = auth_session("192.168.1.1", "user", steps)
        
        # Assertions
        self.assertEqual(result, mock_session_instance)
        self.assertIn("Auth: 2FA authentication successful", steps)
        
        # Check that post was called twice with correct parameters
        self.assertEqual(mock_session_instance.post.call_count, 2)
        mock_session_instance.post.assert_any_call("https://192.168.1.1/api/auth/login", json={"username": "testuser", "password": "testpassword"}, timeout=20)
        mock_session_instance.post.assert_any_call("https://192.168.1.1/api/auth/login", json={"username": "testuser", "password": "testpassword", "token": "123456"}, timeout=20)

if __name__ == '__main__':
    unittest.main()
