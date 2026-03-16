"""Тесты TTS: текущий API — функция synthesize() в src.communication.tts."""
import unittest
from src.communication.tts import synthesize


class TestTTS(unittest.TestCase):
    def test_synthesize_empty_text(self):
        self.assertIn("Error", synthesize(""))
        self.assertIn("Error", synthesize("   "))

    def test_synthesize_returns_string(self):
        result = synthesize("Привет")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_synthesize_language_param(self):
        result = synthesize("Test", language_code="en-US")
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
