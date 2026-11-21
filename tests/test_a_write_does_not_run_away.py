"""Сборка текста записи не уходит в бесконечность.

Замер 2026-09-24, ночь кампании: модель собирала edits.txt для patch_check,
зациклилась на одном SEARCH-блоке, а обёртка «продолжила» её четыре раза с
растущим бюджетом — 159k токенов на вход, 116 736 на выход, $0.58 за вызов,
файл на 568 КБ из сотен одинаковых блоков. Дважды за ночь (23:16 и 23:51 UTC).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.write_at_execution import compose_content

_BLOCK = (
    "FILE: tools/python_probe.py\n"
    "<<<<<<< SEARCH\n"
    "    def _missing_inputs(self, code: str, inputs: list[str] | None) -> list[str]:\n"
    "=======\n"
    "    def _missing_inputs(self, code: str, inputs: list[str] | None) -> list[str]:  # glob\n"
    ">>>>>>> REPLACE\n"
)


class _LLM:
    def __init__(self, answer: str, truncated: bool = False) -> None:
        self.answer, self.truncated, self.calls = answer, truncated, []
        self.last_answer_was_truncated = False

    def complete(self, *, system: str, user: str, **kw) -> str:
        self.calls.append(kw)
        self.last_answer_was_truncated = self.truncated
        return self.answer


def _run(llm: _LLM) -> str:
    loop = SimpleNamespace(model_router=None, llm=llm)
    step = SimpleNamespace(order=2, action_spec={"tool_name": "file_write", "arguments": {
        "path": "proposals/selffix/x/edits.txt", "write_instruction": "собери правку"}})
    return compose_content(loop, step, [])


def test_a_runaway_repetition_is_not_written() -> None:
    with pytest.raises(RuntimeError, match="зациклилась"):
        _run(_LLM(_BLOCK * 40))


def test_an_answer_cut_at_the_limit_is_not_written() -> None:
    with pytest.raises(RuntimeError, match="оборван на пределе"):
        _run(_LLM(_BLOCK, truncated=True))
