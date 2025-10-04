#!/usr/bin/env python3
"""
Unit tests for the utils module functions, particularly the normalize_host_url function.
"""
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the parent directory to sys.path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the module to test
from utils import normalize_host_url

class TestURLNormalization(unittest.TestCase):
    """Test the URL normalization function in utils module"""
    
    @patch('utils.console')
    def test_empty_url(self, mock_console):
        """Test that empty URLs return empty string"""
        self.assertEqual(normalize_host_url(""), "")
        self.assertEqual(normalize_host_url(None), "")
    
    @patch('utils.console')
    def test_already_https_url(self, mock_console):
        """Test that URLs with https:// are not modified"""
        # Test with trailing slash
        self.assertEqual(normalize_host_url("https://192.168.1.1/"), "https://192.168.1.1")
        # Test without trailing slash
        self.assertEqual(normalize_host_url("https://192.168.1.1"), "https://192.168.1.1")
        # Test with domain name
        self.assertEqual(normalize_host_url("https://unifi.local"), "https://unifi.local")
    
    @patch('utils.console')
    def test_http_to_https_conversion(self, mock_console):
        """Test that http:// URLs are converted to https://"""
        # Test with trailing slash
        self.assertEqual(normalize_host_url("http://192.168.1.1/"), "https://192.168.1.1")
        # Test without trailing slash
        self.assertEqual(normalize_host_url("http://192.168.1.1"), "https://192.168.1.1")
        # Test with domain name
        self.assertEqual(normalize_host_url("http://unifi.local"), "https://unifi.local")
        # Verify warning was printed
        mock_console.print.assert_called_with("[yellow]Warning: Using insecure HTTP connection. Changing to HTTPS.[/yellow]")
    
    @patch('utils.console')
    def test_add_https_to_bare_url(self, mock_console):
        """Test that URLs without protocol get https:// added"""
        # Test with IP address
        self.assertEqual(normalize_host_url("192.168.1.1"), "https://192.168.1.1")
        # Test with domain name
        self.assertEqual(normalize_host_url("unifi.local"), "https://unifi.local")
        # Test with trailing slash
        self.assertEqual(normalize_host_url("192.168.1.1/"), "https://192.168.1.1")

if __name__ == '__main__':
    unittest.main()
