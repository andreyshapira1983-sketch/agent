"""Заход «доведи начатое» видит, что лежит в файле правки, и не теряет блоки прошлых заходов."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.write_at_execution import compose_content

_EDITS = "proposals/selffix/local_models_tools/edits.txt"
_CODE = "FILE:core/a.py\n<<<<<<< LINES 1-1\nx = 2\n>>>>>>> REPLACE"
_TEST = ("FILE:tests/test_a.py\n<<<<<<< SEARCH\n=======\nfrom core.a import x\n\n\n"
         "def test_x():\n    assert x == 2\n>>>>>>> REPLACE")


class _FaithfulWriter:
    """Модель переносит показанные ей блоки и добавляет свой — не больше."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.prompts.append(user)
        return (_CODE + "\n" + _TEST) if _CODE in user else _TEST


def _loop(writer: _FaithfulWriter, root: Path, trace: str) -> SimpleNamespace:
    return SimpleNamespace(llm=writer, model_router=None, log=SimpleNamespace(trace_id=trace),
                           _file_read_workspace_root=lambda: root)


def _write_step(path: str, instruction: str) -> SimpleNamespace:
    return SimpleNamespace(order=3, id="w", action_spec={"tool_name": "file_write", "arguments": {
        "path": path, "write_instruction": instruction}})


def test_the_code_block_of_the_last_pass_survives_the_next_write(tmp_path: Path) -> None:
    """Второй заход добавляет тест: блок кода первого захода остаётся в файле правки."""
    (tmp_path / _EDITS).parent.mkdir(parents=True)
    (tmp_path / _EDITS).write_text(_CODE + "\n", encoding="utf-8")
    writer = _FaithfulWriter()
    read = SimpleNamespace(order=1, id="r1", action_spec={"tool_name": "list_dir", "arguments": {"path": "tests"}})

    text = compose_content(_loop(writer, tmp_path, "pass2"), _write_step(_EDITS, "добавь тест к начатой правке"),
                           [(read, {"status": "success", "output": "(empty)"}, None)])

    assert _CODE in writer.prompts[-1]
    assert "FILE:core/a.py" in text and "FILE:tests/test_a.py" in text, text


def test_a_new_edit_file_is_written_as_before(tmp_path: Path) -> None:
    """Граница: файла правки ещё нет — задание писателю то же, что было."""
    writer = _FaithfulWriter()

    assert compose_content(_loop(writer, tmp_path, "pass1"), _write_step(_EDITS, "собери правку"), []) == _TEST
    assert "Сейчас в этом файле" not in writer.prompts[-1]


def test_an_edits_file_outside_the_workspace_is_not_read(tmp_path: Path) -> None:
    """Путь с выходом из рабочей папки не читается и не уходит в модель."""
    root = tmp_path / "ws"
    root.mkdir()
    (tmp_path / "edits.txt").write_text(_CODE + "\n", encoding="utf-8")
    writer = _FaithfulWriter()

    compose_content(_loop(writer, root, "pass1"), _write_step("../edits.txt", "собери правку"), [])

    assert _CODE not in writer.prompts[-1]
