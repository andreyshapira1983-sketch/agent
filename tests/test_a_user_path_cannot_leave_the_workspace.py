"""Путь из текста оператора не покидает рабочую область — забор приколочен.

Повод: шесть ошибок CodeQL py/path-injection (2026-08-28, мандат оператора
«закрой все 14 сам»). Вердикт разбора: ЛОЖНЫЕ — забор стоял до единой
файловой пробы (отказ абсолютным, отказ «..», resolve().relative_to), но не
был приколочен НИ ОДНИМ тестом: сторож без свидетеля — форма MIR-181.
Закалка попутно: workspace канонизируется тоже (симлинки/короткие имена
Windows не разъедут сравнение).
"""
from __future__ import annotations

from pathlib import Path

from core.file_request_intent import validate_user_file_path


def test_a_relative_file_inside_the_workspace_passes(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "loop.py").write_text("x", encoding="utf-8")

    verdict = validate_user_file_path("core/loop.py", workspace=tmp_path)

    assert verdict["ok"] is True
    assert str(verdict["relative_path"]).replace("\\", "/") == "core/loop.py"


def test_traversal_and_absolute_and_rooted_are_all_refused(tmp_path: Path) -> None:
    (tmp_path / "inside.txt").write_text("x", encoding="utf-8")

    for hostile in ("../secrets.txt",
                    "core/../../outside.txt",
                    "C:/Windows/system.ini",
                    "/etc/passwd",
                    "\\\\server\\share\\x"):
        verdict = validate_user_file_path(hostile, workspace=tmp_path)
        assert verdict["ok"] is False, hostile


def test_a_non_canonical_workspace_does_not_break_the_compare(tmp_path: Path) -> None:
    """Закалка: workspace с хвостом `.` канонизируется, честный файл проходит."""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    verdict = validate_user_file_path("a.txt", workspace=tmp_path / "." / ".")

    assert verdict["ok"] is True


def test_the_fence_stands_before_any_filesystem_probe() -> None:
    """Порядок ворот — закон: сначала resolve/relative_to, потом exists/stat."""
    import inspect

    from core import file_request_intent as mod

    src = inspect.getsource(mod.validate_user_file_path)
    fence = src.index("relative_to(")
    first_probe = min(src.index(".exists()"), src.index(".stat()"))
    assert fence < first_probe, "файловая проба раньше забора — инъекция пути"
