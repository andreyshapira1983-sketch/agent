"""Текст записи собирается при исполнении, а не при планировании.

Корень 2026-09-22: аргументы шага фиксируются планом, поэтому текст файла
сочинялся ДО того, как чтения вернулись — «тест импортировал несуществующую
функцию» (10:45), правка по памяти (14:37), конспект-болванка (17:02).
Откладывание записи на круг было подпоркой. Теперь план может назвать
ЗАДАНИЕ (write_instruction), а текст собирается по выводам этого же пакета.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.step_sanitizer import sanitize_step
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


def test_the_sanitizer_accepts_an_instruction_instead_of_text() -> None:
    step = sanitize_step("file_write", {"path": "notes.md", "write_instruction": "итог чтения"},
                         None, 0, [])
    assert step["arguments"] == {"path": "notes.md", "write_instruction": "итог чтения"}


def test_a_write_without_text_and_without_instruction_is_dropped() -> None:
    warnings: list[str] = []
    assert sanitize_step("file_write", {"path": "notes.md"}, None, 0, warnings) is None
    assert "content must be a string" in warnings[0]
    assert "write_instruction" in warnings[0]


def test_the_text_is_built_from_the_outputs_of_this_batch() -> None:
    loop = SimpleNamespace(llm=_LLM("def f():\n    return 2\n"), model_router=None)

    text = compose_content(loop, _step(), _done("1: def f():\n2:     return 1"))

    assert text == "def f():\n    return 2"
    assert "return 1" in loop.llm.prompts[0], "в задание ушёл вывод шага чтения"


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
