import unittest
from src.monitoring.monitor import Monitor

class TestMonitor(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor()

    def test_log_calls(self):
        self.monitor.log_call()
        self.assertEqual(self.monitor.metrics['calls'], 1)

    def test_log_errors(self):
        self.monitor.log_error()
        self.assertEqual(self.monitor.metrics['errors'], 1)

    def test_log_successes(self):
        self.monitor.log_success()
        self.assertEqual(self.monitor.metrics['successes'], 1)

    def test_report(self):
        self.monitor.log_call()
        self.monitor.log_success()
        self.assertEqual(self.monitor.report(), {'calls': 1, 'errors': 0, 'successes': 1})

if __name__ == '__main__':
    unittest.main()
