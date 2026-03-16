import unittest
import os
from src.evolution.manager import EvolutionManager


class TestEvolutionManager(unittest.TestCase):

    def setUp(self):
        os.makedirs("tests/patches", exist_ok=True)
        os.makedirs("tests/backups", exist_ok=True)
        self.manager = EvolutionManager("tests/patches", "tests/", "tests/backups/")
        test_file_path = "tests/target_file.py"
        with open(test_file_path, "w", encoding="utf-8") as f:
            f.write("original code")

    def tearDown(self):
        if os.path.exists("tests/target_file.py"):
            os.remove("tests/target_file.py")

    def test_apply_patch_non_existent_file(self):
        """Патч-файл отсутствует в каталоге патчей."""
        with self.assertRaises(FileNotFoundError):
            self.manager.apply_patch("non_existent.patch")

    def test_rollback_no_backup(self):
        """Откат при отсутствии бэкапа для файла."""
        with self.assertRaises(FileNotFoundError):
            self.manager.rollback_patch("tests/target_file.py")


if __name__ == "__main__":
    unittest.main()
