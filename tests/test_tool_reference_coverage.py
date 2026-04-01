import unittest

from config.tool_reference import TOOL_REFERENCE


class ToolReferenceCoverageTests(unittest.TestCase):
    def test_tool_reference_contains_core_sections(self):
        self.assertTrue(TOOL_REFERENCE.strip())
        self.assertIn('=== ИНСТРУМЕНТЫ АГЕНТА', TOOL_REFERENCE)
        self.assertIn('ФАЙЛОВАЯ СИСТЕМА', TOOL_REFERENCE)
        self.assertIn('ТЕРМИНАЛ / КОМАНДЫ', TOOL_REFERENCE)
        self.assertIn('PDF', TOOL_REFERENCE)
        self.assertIn('EXCEL / SPREADSHEET', TOOL_REFERENCE)
        self.assertIn('GITHUB', TOOL_REFERENCE)
        self.assertIn('GOOGLE CALENDAR', TOOL_REFERENCE)

    def test_tool_reference_has_guard_rules(self):
        self.assertIn('Все создаваемые файлы → в папку outputs/', TOOL_REFERENCE)
        self.assertIn("НЕ используй action='create' для spreadsheet", TOOL_REFERENCE)
        self.assertIn("save_path=", TOOL_REFERENCE)
        self.assertIn("tool_layer.use('http_client'", TOOL_REFERENCE)


if __name__ == '__main__':
    unittest.main()
