import unittest
from src.evolution.auto_patch import AutoPatch

class TestAutoPatch(unittest.TestCase):
    def setUp(self):
        self.auto_patch = AutoPatch('tests/patches')

    def test_apply_patch_not_found(self):
        with self.assertRaises(FileNotFoundError):
            self.auto_patch.apply_patch('non_existent_patch.patch')

    # Добавить остальные тесты, как например успешное применение патча.

if __name__ == '__main__':
    unittest.main()
