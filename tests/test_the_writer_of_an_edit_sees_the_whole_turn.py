"""Писатель правки видит весь ход и не кладёт отказ в файл правки (замер 2026-09-24).

Урок 3, вечер 24.09: агент правил сам свой писатель (core/write_at_execution.py)
и за четыре попытки ни разу не дошёл до зелёного — мешал сам писатель:

* трижды из шести записей в edits.txt лёг ТЕКСТ ОТКАЗА («нужные строки не
  прочитаны», «Файл не найден.»), и шаг не провалился;
* после перепланирования писатель не видел прочитанного в прошлом круге:
  done — только текущий план;
* из каждого чтения он видел первые 4000 знаков: конец compose_content
  (строка ~130 из 162) не попадал, и вызов сторожа не вставлялся;
* образца меток в задании не было — модель писала unified diff и «SEARCH:».
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.write_at_execution import compose_content, remember_read

_EDITS = "proposals/selffix/x/edits.txt"
_GOOD = "FILE:core/a.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n"


class _LLM:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.prompts.append(user)
        return self.answer


def _loop(answer: str, trace: str = "t1") -> SimpleNamespace:
    return SimpleNamespace(llm=_LLM(answer), model_router=None, log=SimpleNamespace(trace_id=trace))


def _write(path: str = _EDITS) -> SimpleNamespace:
    return SimpleNamespace(order=5, id="w", action_spec={
        "tool_name": "file_write", "arguments": {"path": path, "write_instruction": "собери правку"}})


def _read(order: int, text: str) -> tuple[SimpleNamespace, dict, None]:
    step = SimpleNamespace(order=order, id=f"r{order}", action_spec={
        "tool_name": "file_read", "arguments": {"path": "core/a.py"}})
    return step, {"status": "success", "output": text}, None


@pytest.mark.parametrize("refusal", [
    "Файл не найден.",
    "нужные строки не прочитаны",
    "В выводах шагов нет данных.",
    "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE",  # блок без FILE:
    "--- a/core/a.py\n+++ b/core/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2",  # unified diff
])
def test_a_refusal_never_lands_in_an_edits_file(refusal: str) -> None:
    with pytest.raises(RuntimeError, match="не перезаписан"):
        compose_content(_loop(refusal), _write(), [_read(1, "x = 1")])


def test_real_blocks_are_written() -> None:
    assert compose_content(_loop(_GOOD), _write(), [_read(1, "x = 1")]) == _GOOD.strip()


def test_a_note_is_not_held_to_the_edit_format() -> None:
    assert compose_content(_loop("итог: 42"), _write("data/notes/n.md"), [_read(1, "42")]) == "итог: 42"


def test_a_read_from_an_earlier_round_is_seen_after_a_replan() -> None:
    loop = _loop(_GOOD)
    remember_read(loop, *_read(1, "SEEN_BEFORE_THE_REPLAN")[:2])
    compose_content(loop, _write(), [])  # новый план: в done пусто
    assert "SEEN_BEFORE_THE_REPLAN" in loop.llm.prompts[-1]


def test_reads_of_another_turn_are_forgotten() -> None:
    loop = _loop(_GOOD, trace="old")
    remember_read(loop, *_read(1, "FROM_THE_OLD_TURN")[:2])
    loop.log.trace_id = "new"
    compose_content(loop, _write(), [_read(2, "x = 1")])
    assert "FROM_THE_OLD_TURN" not in loop.llm.prompts[-1]


def test_the_end_of_a_long_file_is_visible() -> None:
    body = "a = 0\n" * 1500 + "TAIL_OF_COMPOSE_CONTENT\n"  # ~9000 знаков
    loop = _loop(_GOOD)
    compose_content(loop, _write(), [_read(1, body)])
    assert "TAIL_OF_COMPOSE_CONTENT" in loop.llm.prompts[-1]


def test_the_writer_is_shown_the_block_form() -> None:
    loop = _loop(_GOOD)
    compose_content(loop, _write(), [_read(1, "x = 1")])
    prompt = loop.llm.prompts[-1]
    assert "<<<<<<< SEARCH" in prompt and ">>>>>>> REPLACE" in prompt and "FILE:" in prompt
