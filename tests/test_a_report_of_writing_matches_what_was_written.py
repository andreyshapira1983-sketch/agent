"""Доклад о записи сверяется с тем, что цикл действительно выполнил.

Разговор через мостик 2026-09-21: ход без единого выполненного инструмента
(план дважды не собрался) ответил «записано …_v3.md, по измерению 9412 байт»;
следующий ход, где прошли четыре file_write, ответил «не записал ни одного
файла». Проверка видела пустоту («0 из 9»), а ответ уходил как есть.
"""
from __future__ import annotations

# Формулировки обновлены 2026-09-23: показание больше не называет `file_write`
# единственным способом записи. Повод — живой случай: агент записал вопрос в
# голосовой ящик через `journal_append`, запись легла, а показание ответило
# «НЕ выполнено ни одной записи файла». Считаются все три пишущих инструмента
# (file_write, journal_append, memory_bank); `patch_check` не считается — он
# примеряет правку на клоне и рабочую папку не меняет.
from core.answer_contradiction import action_report_mismatch


def test_a_claimed_write_without_any_write_is_flagged() -> None:
    answer = ("Conclusion: Исправленное предложение записано новым файлом "
              "proposals/x_v3.md, 9412 байт.\n\nFacts:\n- ...")
    note = action_report_mismatch(answer, [])
    assert note and "записей: 0" in note


def test_a_denied_write_after_real_writes_is_flagged() -> None:
    answer = "В этом ходе я не записал ни одного файла и не выполнил ни одной пробы."
    note = action_report_mismatch(answer, ["list_dir", "file_write", "file_write", "file_read"])
    assert note and "записей: 2" in note


def test_an_honest_report_gets_only_the_plain_fact() -> None:
    note = action_report_mismatch("Conclusion: Записал proposals/x.md.", ["file_write"])
    assert note and note.startswith("ℹ️") and "1" in note
    assert action_report_mismatch("Conclusion: Ничего не записано: запись заблокирована.", []) is None
    assert action_report_mismatch("Conclusion: Прочитал core/a.py, строка 12.", ["file_read"]) is None


def test_a_denial_is_caught_in_any_form() -> None:
    assert action_report_mismatch("Conclusion: Файл не записан.", ["file_write"]).startswith("⚠️")


def test_a_denial_in_words_nobody_listed_still_meets_the_fact() -> None:
    """2026-09-21 ~18:22: «запись в файл не состоялась» после успешной перезаписи
    в первом круге — ловля отрицаний не знала слова «не состоялась»."""
    note = action_report_mismatch(
        "Задача не выполнена: запись в файл не состоялась — final.md остался прежним.",
        ["file_read", "file_write", "find_in_files"])
    assert note and "записей в этом ходе: 1" in note
