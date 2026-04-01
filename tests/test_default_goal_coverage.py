import re
import unittest

from config.default_goal import DEFAULT_GOAL


class DefaultGoalCoverageTests(unittest.TestCase):
    def test_default_goal_has_three_steps_and_outputs(self):
        self.assertIn('Создай три файла и сохрани их в папку outputs/.', DEFAULT_GOAL)
        self.assertIn('Шаг 1:', DEFAULT_GOAL)
        self.assertIn('Шаг 2:', DEFAULT_GOAL)
        self.assertIn('Шаг 3:', DEFAULT_GOAL)

        self.assertIn('outputs/health.json', DEFAULT_GOAL)
        self.assertIn('outputs/daily_report.pdf', DEFAULT_GOAL)
        self.assertIn('outputs/daily_log.xlsx', DEFAULT_GOAL)

    def test_default_goal_contains_three_python_code_blocks(self):
        blocks = re.findall(r'```python\n([\s\S]*?)\n```', DEFAULT_GOAL)
        self.assertEqual(len(blocks), 3)

        for block in blocks:
            compile(block, '<default_goal_block>', 'exec')


if __name__ == '__main__':
    unittest.main()
