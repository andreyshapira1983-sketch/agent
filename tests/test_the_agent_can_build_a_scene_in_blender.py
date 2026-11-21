"""Дверь в Blender: свой скрипт агента в песочнице convert_file (слово оператора 24.09).

24.09, чат: «сделай 3D-персонажа, 10 с анимации». Агент написал
build_character.py, но запустить его было нечем — shell_exec не пускает
blender, лаборатории запрещён subprocess, а render3d только рендерит готовый
.blend. Дверь открыта оператором; держит её та же песочница: nobody, временная
папка, таймаут. Результаты забирает процесс с правами root, поэтому ссылки и
чужие файлы из out/ не забираются.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools import convert_file
from tools.convert_file import ConvertFileTool, _argv, _collect_tree_into


def test_a_failing_script_cannot_look_like_success() -> None:
    """Blender исполняет аргументы по порядку: --python-exit-code после --python
    уже не действует, и упавший скрипт вернул бы 0."""
    argv = _argv("blender_script", "py", "out", lang="eng", width=None, max_pages=1,
                 max_seconds=None)[0]
    assert argv[:3] == ["blender", "--background", "--factory-startup"]
    assert argv.index("--python-exit-code") < argv.index("--python")
    assert argv[-1] == "in.py"


def test_a_crashed_script_is_a_failed_step_with_its_traceback(tmp_path: Path) -> None:
    """24.09, 18:53 (trace_22b942a8): Blender упал с кодом 1, инструмент вернул
    success, цикл сбоя не увидел — тот же скрипт ушёл повторно без правки, а
    результатом выдано старое видео. Код выхода ≠ 0 — провал шага, и причина
    несёт хвост журнала, где Blender перечислил допустимые значения."""
    live = {"op": "blender_script", "input": "scripts/scene_short.py", "exit_code": 1, "outputs": [],
            "log_tail": ("Traceback (most recent call last):\n  File \"in.py\", line 39\n"
                         "TypeError: bpy_struct: item.attr = val: enum \"AgX - Base Contrast\" not found in "
                         "('None', 'Very High Contrast', 'High Contrast', 'Medium Contrast')\n"),
            "error": "blender_script produced no output (exit 1)"}
    ok, issues = ConvertFileTool(tmp_path).validate_output(live)
    assert ok is False
    assert "exit 1" in issues[0] and "Medium Contrast" in issues[0]
    good = {**live, "exit_code": 0, "outputs": ["converted/x/scene.mp4"], "log_tail": ""}
    assert ConvertFileTool(tmp_path).validate_output(good) == (True, [])


def test_a_link_out_of_the_sandbox_is_not_collected(tmp_path: Path) -> None:
    out, dest = tmp_path / "out", tmp_path / "dest"
    (out / "frames").mkdir(parents=True)
    (out / "frames" / "f_0001.png").write_bytes(b"png")
    secret = tmp_path / "secret.env"
    secret.write_text("KEY=do-not-leak", encoding="utf-8")
    try:
        os.symlink(secret, out / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    picked, skipped = _collect_tree_into(out, dest, owner=None)
    assert [p.name for p in picked] == ["f_0001.png"] and skipped == 1
    assert (dest / "frames" / "f_0001.png").exists() and not (dest / "leak.txt").exists()


def test_the_collection_is_capped(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    out.mkdir()
    for i in range(3):
        (out / f"f{i}.png").write_bytes(b"x")
    monkeypatch.setattr(convert_file, "_MAX_TREE_FILES", 2)
    picked, skipped = _collect_tree_into(out, tmp_path / "dest", owner=None)
    assert len(picked) == 2 and skipped == 1
