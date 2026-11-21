"""Текст записи собирается при исполнении, а не при планировании.

Корень 2026-09-22: аргументы шага фиксируются планом, поэтому текст файла
сочинялся ДО того, как чтения вернулись — «тест импортировал несуществующую
функцию» (10:45), правка по памяти (14:37), конспект-болванка (17:02).
Откладывание записи на круг было подпоркой. Теперь план может назвать
ЗАДАНИЕ (write_instruction), а текст собирается по выводам этого же пакета.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.write_at_execution import compose_content


class _LLM:
    def __init__(self, answer: str):
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.prompts.append(user)
        return self.answer


def _step(instruction: str = "перескажи вывод") -> SimpleNamespace:
    return SimpleNamespace(order=2, id="s2", action_spec={
        "tool_name": "file_write",
        "arguments": {"path": "notes.md", "write_instruction": instruction}})


def _done(output: str) -> list:
    read = SimpleNamespace(order=1, action_spec={"tool_name": "file_read", "source_label": "file:a.py"})
    return [(read, {"output": output}, None)]


def test_an_empty_or_template_answer_is_refused() -> None:
    loop = SimpleNamespace(llm=_LLM("   "), model_router=None)
    try:
        compose_content(loop, _step(), _done("вывод"))
    except RuntimeError as exc:
        assert "пустой" in str(exc)
    else:
        raise AssertionError("пустой текст не должен записываться")

    loop = SimpleNamespace(llm=_LLM("<to be synthesized from the three files>"), model_router=None)
    try:
        compose_content(loop, _step(), _done("вывод"))
    except RuntimeError as exc:
        assert "шаблон" in str(exc)
    else:
        raise AssertionError("шаблон не должен записываться")
