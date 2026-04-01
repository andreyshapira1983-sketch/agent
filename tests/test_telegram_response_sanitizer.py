import unittest

from communication.response_sanitizer import (
    looks_internal_telemetry,
    sanitize_user_response,
)


class TelegramResponseSanitizerTests(unittest.TestCase):
    def test_looks_internal_telemetry_true_with_multiple_markers(self):
        text = "Success Rate: 0.5, цикл 3, score 0.8"
        self.assertTrue(looks_internal_telemetry(text))

    def test_looks_internal_telemetry_false_with_single_marker(self):
        text = "У тебя хороший score"
        self.assertFalse(looks_internal_telemetry(text))

    def test_looks_internal_telemetry_true_for_blank_text(self):
        self.assertTrue(looks_internal_telemetry("   \n"))

    def test_sanitize_empty_text_returns_fallback(self):
        got = sanitize_user_response("", user_text="")
        self.assertIn("Повтори запрос", got)

    def test_sanitize_removes_internal_lines_and_keeps_human_part(self):
        raw = (
            "Суть сообщения кратка.\n"
            "Результат вектор-серч: найдено 6.\n"
            "Success Rate: 0.5\n"
            "Вот что реально делать: начни с ассортимента и маржи."
        )
        got = sanitize_user_response(raw, user_text="как управлять магазином")
        self.assertEqual(got, "Вот что реально делать: начни с ассортимента и маржи.")

    def test_sanitize_fallback_when_all_lines_removed(self):
        raw = "Суть сообщения кратка.\nSuccess Rate: 0.7\nТекст обрезанно"
        got = sanitize_user_response(raw, user_text="как управлять магазином")
        self.assertIn("Понял запрос: как управлять магазином.", got)
        self.assertIn("план в 5 шагов", got)

    def test_sanitize_fallback_when_telemetry_leaks_after_cleanup(self):
        raw = "цикл 2 и оценка 0.4, но ответа по сути нет"
        got = sanitize_user_response(raw, user_text="помоги с магазином")
        self.assertIn("Понял запрос: помоги с магазином.", got)

    def test_sanitize_fallback_without_user_text(self):
        raw = "system debug telemetry"
        got = sanitize_user_response(raw, user_text="")
        self.assertIn("сформулируй задачу одной фразой", got)

    def test_sanitize_compacts_spaces(self):
        raw = "Привет,   Андрей.\n\n\nДержи   короткий план."
        got = sanitize_user_response(raw, user_text="")
        self.assertEqual(got, "Привет, Андрей.\nДержи короткий план.")


if __name__ == '__main__':
    unittest.main()
