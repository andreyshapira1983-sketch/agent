"""Файл из вывода ответа, который старше хода, этим ходом не создан — и это сказано.

24.09, 18:51 (trace_22b942a8): рендер новой сцены упал, а результатом ответа
значилось видео 17:46 от прошлой просьбы. Решают путь на диске и его время
(W3C PROV: порождено / использовано), а не слова ответа.
"""
from __future__ import annotations

import inspect
import os
import time
from pathlib import Path

from core import loop_response_deciders
from core.turn_provenance import older_than_turn

OLD_VIDEO = "converted/build_character_v4__blender_script_20260924-174639/character.mp4"
HEAD = f"Задача выполнена частично: готовый видеофайл `{OLD_VIDEO}` существует, 200586 байт."


def _old_video(root: Path, started: float) -> None:
    video = root / OLD_VIDEO
    video.parent.mkdir(parents=True)
    video.write_bytes(b"\0" * 64)
    os.utime(video, (started - 3900, started - 3900))  # 17:46 против 18:51


def test_an_old_video_named_as_the_result_is_called_out(tmp_path: Path) -> None:
    started = time.time()
    _old_video(tmp_path, started)
    notice = older_than_turn(HEAD, tmp_path, started, ["file_write", "convert_file", "python_probe"])
    assert notice and OLD_VIDEO in notice and "НЕ создано" in notice


def test_a_file_made_in_this_turn_is_not_called_out(tmp_path: Path) -> None:
    started = time.time() - 60
    video = tmp_path / "converted/scene/scene.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"\0")
    assert older_than_turn("Готово: converted/scene/scene.mp4", tmp_path, started, ["convert_file"]) is None


def test_a_turn_that_only_read_may_name_old_files(tmp_path: Path) -> None:
    started = time.time()
    _old_video(tmp_path, started)
    assert older_than_turn(HEAD, tmp_path, started, ["file_read", "list_dir"]) is None


def test_the_answer_path_consults_it() -> None:
    assert "older_than_turn(" in inspect.getsource(loop_response_deciders)
