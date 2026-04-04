import re
import unittest

from config.default_goal import DEFAULT_GOAL


class DefaultGoalCoverageTests(unittest.TestCase):
    def test_default_goal_has_steps_and_outputs(self):
        self.assertIn('Шаг 1:', DEFAULT_GOAL)
        self.assertIn('Шаг 2:', DEFAULT_GOAL)
        self.assertIn('Шаг 3:', DEFAULT_GOAL)
        # Цель содержит инструкции для реальной работы с инструментами
        self.assertIn('web_search', DEFAULT_GOAL)
        self.assertIn('web_crawler', DEFAULT_GOAL)
        self.assertIn('outputs/', DEFAULT_GOAL)

    def test_default_goal_is_nonempty_multiline(self):
        lines = DEFAULT_GOAL.strip().splitlines()
        self.assertGreater(len(lines), 5)
        self.assertGreater(len(DEFAULT_GOAL), 200)


if __name__ == '__main__':
    unittest.main()
