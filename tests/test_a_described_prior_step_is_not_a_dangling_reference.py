"""«Предыдущий шаг» в вопросе, называющем свои файлы, — описание, не ссылка.

Эпизод 2026-09-21 ~14:05: Claude описал дефект через мостик («в путь
подставился ВЕСЬ вывод предыдущего шага», с путём к следу и к файлу), мостик
открывает сессию без истории, и пятые ворота вернули «В этой сессии ещё нет
предыдущего шага — уточните…». Модель вопрос не увидела.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.loop_gates import AgentLoopGates


def _loop():
    return SimpleNamespace(clarification_enabled=True, memory=None,
                           log=SimpleNamespace(log=lambda *a, **k: None),
                           _stream_on_token=None)


def test_a_question_that_names_its_files_passes() -> None:
    q = ("В следе logs/trace_965c.jsonl шаг file_read получил путь, в который "
         "подставился весь вывод предыдущего шага. Разберись по core/step_references.py.")
    assert AgentLoopGates._prior_step_gate(_loop(), q) is None


def test_a_bare_reference_with_no_history_still_asks() -> None:
    asked = AgentLoopGates._prior_step_gate(_loop(), "Сохрани результат предыдущего шага в файл.")
    assert asked and "нет предыдущего шага" in asked
