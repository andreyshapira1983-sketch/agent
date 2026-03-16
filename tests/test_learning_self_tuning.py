import unittest
from src.learning.self_tuning import SelfTuning

class TestSelfTuning(unittest.TestCase):
    def setUp(self):
        self.self_tuning = SelfTuning()

    def test_analyze_positive_feedback(self):
        result = self.self_tuning.analyze_feedback({'success': True})
        self.assertEqual(result, [])

    def test_analyze_negative_feedback(self):
        result = self.self_tuning.analyze_feedback({'success': False})
        self.assertIn("Review approach.", result)

if __name__ == '__main__':
    unittest.main()
