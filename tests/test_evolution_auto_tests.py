import unittest
from unittest.mock import patch
from src.evolution.auto_tests import AutoTests

class TestAutoTests(unittest.TestCase):
    
    @patch('subprocess.run')
    def test_run_tests_pass(self, mock_run):
        mock_run.return_value.returncode = 0
        result = AutoTests.run_tests('tests/')
        self.assertTrue(result)

    @patch('subprocess.run')
    def test_run_tests_fail(self, mock_run):
        mock_run.return_value.returncode = 1
        result = AutoTests.run_tests('tests/')
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()
