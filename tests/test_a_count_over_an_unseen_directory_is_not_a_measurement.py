"""Ноль по каталогу, которого лаборатория не видит, — не замер.

Замер 2026-09-23. Агента спросили, насколько он понимает свой код. Он померил
пробой: `Path("core").glob("*.py")` → «py_files 0», и то же по `tools`. В
рабочей папке при этом 242 и 30 файлов. Лаборатория начинает в ПУСТОЙ временной
папке, каталога там нет — но сторож `_missing_inputs` смотрел только
`is_file()`, каталог мимо него проходил, и ноль ушёл в ответ как факт о коде.

Это его собственная открытая запись в реестре: «Проба работает во временной
папке: невидимый отказ выглядит как настоящий ноль». Здесь закрыт тот её край,
что касается каталогов: отказ обязан быть видимым, а совет — выполнимым
(каталог через `inputs` не передашь, только перечислением файлов).
"""
from __future__ import annotations

from pathlib import Path

from tools.python_probe import PythonProbeTool


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "drives.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "core" / "loop.py").write_text("y = 2\n", encoding="utf-8")
    return tmp_path


def test_counting_an_unseen_directory_is_called_out(tmp_path: Path) -> None:
    probe = PythonProbeTool(workspace_root=_workspace(tmp_path))

    result = probe.run(code=(
        'from pathlib import Path\n'
        'print("py_files", len(list(Path("core").glob("*.py"))))\n'
    ))

    assert "py_files 0" in result["stdout"], "проба вдруг увидела рабочую папку"
    note = result.get("note") or ""
    assert "core" in note, f"нулевой счёт по невидимому каталогу прошёл молча: {note!r}"
    assert "DIRECTORIES" in note, note
    assert "inputs" in note


def test_the_advice_is_actionable_for_a_directory(tmp_path: Path) -> None:
    """Совет «передай в inputs» для каталога невыполним — сказать, что делать."""
    probe = PythonProbeTool(workspace_root=_workspace(tmp_path))

    note = probe.run(code='from pathlib import Path\nprint(Path("core").exists())')["note"]

    assert "individual files" in note or "file_read" in note, note


def test_a_named_file_needs_no_warning_because_it_is_copied(tmp_path: Path) -> None:
    """Почему каталог — особый случай: файл лаборатория подкапывает сама.

    Названный ФАЙЛ рабочей папки копируется в опыт автоматически
    (`_auto_inputs`), поэтому счёт по нему — настоящий замер и предупреждать
    не о чем. Каталог подкопать нельзя: его содержимое заранее неизвестно, и
    именно поэтому ноль по каталогу требует слова.
    """
    probe = PythonProbeTool(workspace_root=_workspace(tmp_path))

    result = probe.run(code='print(open("core/drives.py").read().strip())')

    assert "x = 1" in result["stdout"], result
    assert not result.get("note"), result.get("note")


def test_passing_the_files_removes_the_warning(tmp_path: Path) -> None:
    probe = PythonProbeTool(workspace_root=_workspace(tmp_path))

    result = probe.run(
        code='from pathlib import Path\nprint("py", len(list(Path("core").glob("*.py"))))',
        inputs=["core/drives.py", "core/loop.py"],
    )

    assert "py 2" in result["stdout"], result
    assert "core" not in (result.get("note") or ""), result.get("note")


def test_an_empty_directory_is_not_worth_a_warning(tmp_path: Path) -> None:
    """Пустой каталог рабочей папки ничего не скрывает: ноль там — правда."""
    (tmp_path / "empty").mkdir()
    probe = PythonProbeTool(workspace_root=tmp_path)

    result = probe.run(code='from pathlib import Path\nprint(Path("empty").exists())')

    assert "empty" not in (result.get("note") or "")
