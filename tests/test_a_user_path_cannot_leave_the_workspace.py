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


def test_traversal_and_absolute_and_rooted_are_all_refused(tmp_path: Path) -> None:
    (tmp_path / "inside.txt").write_text("x", encoding="utf-8")

    for hostile in ("../secrets.txt",
                    "core/../../outside.txt",
                    "C:/Windows/system.ini",
                    "/etc/passwd",
                    "\\\\server\\share\\x"):
        verdict = validate_user_file_path(hostile, workspace=tmp_path)
        assert verdict["ok"] is False, hostile
