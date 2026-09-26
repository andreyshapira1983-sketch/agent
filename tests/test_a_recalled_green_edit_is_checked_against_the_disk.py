"""Слова прошлого захода «правки зелёные и применяются» доходят до следующего сверенными с диском."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.goal_progress import PassProgress

GOAL = "Подключи к себе проверенные локальные модели сервера как инструменты"
EDIT = "proposals/selffix/local_models_tools/edits.txt"
CLAIM = "Conclusion: правки зелёные и применяются"
PATCH = "FILE: tools/local_models.py\n<<<<<<< SEARCH\n=======\ndef local_models():\n    return []\n>>>>>>> REPLACE\n"


def _next_prompt(ws: Path, written: str = EDIT) -> str:
    agent = SimpleNamespace(compensation_log=[])
    first = PassProgress(agent, ws, GOAL)
    agent.compensation_log.append(SimpleNamespace(description=f"undo creation of '{written}' by deleting it"))
    first.finish(CLAIM)
    return PassProgress(agent, ws, GOAL).prompt(GOAL)


def _edit_file(ws: Path) -> None:
    (ws / EDIT).parent.mkdir(parents=True)
    (ws / EDIT).write_text(PATCH, encoding="utf-8")


def test_a_green_edit_that_never_landed_is_named_as_not_landed(tmp_path: Path) -> None:
    """Живой случай 26.09: файл правки есть, tools/ пуст — следующий заход слышит, что правка не поставлена."""
    (tmp_path / "tools").mkdir()
    _edit_file(tmp_path)

    prompt = _next_prompt(tmp_path)

    assert "правки зелёные и применяются" in prompt
    assert f"{EDIT} (правка не поставлена в tools/local_models.py)" in prompt, prompt[-500:]
    assert "итог (слова захода, не проверка)" in prompt


def test_a_written_file_that_is_gone_is_named_as_gone(tmp_path: Path) -> None:
    """Записанного файла на диске больше нет — так и сказано."""
    prompt = _next_prompt(tmp_path)

    assert f"{EDIT} (на диске нет)" in prompt, prompt[-500:]


def test_a_landed_edit_is_shown_plainly(tmp_path: Path) -> None:
    """Контроль: правка на диске — путь без пометок."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "local_models.py").write_text("def local_models():\n    return []\n", encoding="utf-8")
    _edit_file(tmp_path)

    prompt = _next_prompt(tmp_path)

    assert f"записано: {EDIT};" in prompt and "(правка не поставлена" not in prompt, prompt[-500:]


def test_a_path_outside_the_workspace_is_not_read(tmp_path: Path) -> None:
    """Путь вне рабочей папки не открывается: он «на диске нет» для этой цели."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")

    prompt = _next_prompt(ws, written="../secret.txt")

    assert "../secret.txt (на диске нет)" in prompt, prompt[-500:]
