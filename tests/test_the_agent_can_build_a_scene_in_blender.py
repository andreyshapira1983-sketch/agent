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
import shutil
from pathlib import Path

import pytest

from tools import convert_file
from tools.convert_file import ConvertFileTool, _argv, _collect_tree_into, sandbox_available

needs_blender_sandbox = pytest.mark.skipif(
    sandbox_available() is not None or not shutil.which("blender"),
    reason="нужны песочница (Linux, root, setpriv) и blender")


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


def test_a_file_the_script_did_not_create_is_not_collected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "mine.txt").write_text("x", encoding="utf-8")
    owner = (out / "mine.txt").lstat().st_uid + 1
    picked, skipped = _collect_tree_into(out, tmp_path / "dest", owner=owner)
    assert picked == [] and skipped == 1


def test_the_collection_is_capped(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out"
    out.mkdir()
    for i in range(3):
        (out / f"f{i}.png").write_bytes(b"x")
    monkeypatch.setattr(convert_file, "_MAX_TREE_FILES", 2)
    picked, skipped = _collect_tree_into(out, tmp_path / "dest", owner=None)
    assert len(picked) == 2 and skipped == 1


def _script(tmp_path: Path, name: str, body: str) -> str:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return name


@needs_blender_sandbox
def test_blender_builds_a_scene_and_renders_a_frame(tmp_path: Path) -> None:
    name = _script(tmp_path, "scene.py", (
        "import bpy\n"
        "bpy.ops.mesh.primitive_uv_sphere_add(radius=1)\n"
        "s = bpy.context.scene\n"
        "s.render.engine = 'CYCLES'; s.cycles.samples = 2; s.cycles.use_denoising = False\n"
        "s.render.resolution_x = 64; s.render.resolution_y = 48\n"
        "s.render.filepath = 'out/frames/f_'\n"
        "bpy.ops.wm.save_as_mainfile(filepath='out/scene.blend')\n"
        "bpy.ops.render.render(write_still=True)\n"))
    result = ConvertFileTool(workspace_root=tmp_path).run(op="blender_script", path=name)
    assert result["exit_code"] == 0, result["log_tail"]
    names = [Path(p).name for p in result["outputs"]]
    assert "scene.blend" in names and any(n.endswith(".png") for n in names), result
    assert all((tmp_path / p).exists() for p in result["outputs"])


@needs_blender_sandbox
def test_a_python_error_comes_back_with_its_traceback(tmp_path: Path) -> None:
    name = _script(tmp_path, "broken.py", "import bpy\nraise RuntimeError('scene is broken')\n")
    result = ConvertFileTool(workspace_root=tmp_path).run(op="blender_script", path=name)
    assert result["exit_code"] == 1
    assert "scene is broken" in result["log_tail"]


@needs_blender_sandbox
def test_the_script_cannot_smuggle_a_root_file_out(tmp_path: Path) -> None:
    secret = tmp_path / "secret.env"
    secret.write_text("KEY=do-not-leak\n", encoding="utf-8")
    secret.chmod(0o600)
    name = _script(tmp_path, "smuggle.py", (
        "import os\n"
        "os.makedirs('out', exist_ok=True)\n"
        f"os.symlink({str(secret)!r}, 'out/leak.txt')\n"
        "open('out/ok.txt', 'w').write('fine')\n"))
    result = ConvertFileTool(workspace_root=tmp_path).run(op="blender_script", path=name)
    assert [Path(p).name for p in result["outputs"]] == ["ok.txt"], result
    assert result["skipped_outputs"] == 1
    for p in result["outputs"]:
        assert "do-not-leak" not in (tmp_path / p).read_text(encoding="utf-8")
