"""
Защита от looping patch problem: агент не должен бесконечно править один и тот же файл.

Механизмы:
- Patch cooldown: после изменения файла запрет патчить его N циклов (PATCH_COOLDOWN_CYCLES).
- Patch budget: максимум M патчей на файл (MAX_PATCHES_PER_FILE), затем файл блокируется.

Состояние хранится в data/patch_guard.json. Цикл увеличивается оркестратором в начале каждого run_cycle.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _ROOT / "data"
_STATE_FILE = _DATA_DIR / "patch_guard.json"

# Env: 0 отключает соответствующий лимит.
PATCH_COOLDOWN_CYCLES = int(os.environ.get("PATCH_COOLDOWN_CYCLES", "10"))
MAX_PATCHES_PER_FILE = int(os.environ.get("MAX_PATCHES_PER_FILE", "3"))
# Опционально: лимит патчей на директорию (обход через file_a.py, file_a_utils.py, file_a_helper.py). 0 = выключено.
MAX_PATCHES_PER_DIRECTORY = int(os.environ.get("MAX_PATCHES_PER_DIRECTORY", "0"))


def _normalize_path(path: str) -> str:
    """Единый вид пути для ключа (forward slashes, без ведущего слэша)."""
    p = path.strip().replace("\\", "/").lstrip("/")
    return p or path


def _load_state() -> dict[str, Any]:
    if not _STATE_FILE.exists():
        return {"global_cycle": 0, "files": {}}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return {
            "global_cycle": int(data.get("global_cycle", 0)),
            "files": data.get("files") or {},
        }
    except Exception:
        return {"global_cycle": 0, "files": {}}


def _save_state(state: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def advance_cycle() -> None:
    """Вызвать в начале каждого цикла оркестратора (run_cycle). Увеличивает global_cycle."""
    state = _load_state()
    state["global_cycle"] = state.get("global_cycle", 0) + 1
    _save_state(state)


def _directory_of(path_key: str) -> str:
    """Директория пути (src/foo для src/foo/bar.py)."""
    if "/" not in path_key:
        return path_key
    return path_key.rsplit("/", 1)[0]


def can_patch(path: str) -> tuple[bool, str]:
    """
    Можно ли применять патч к файлу path?
    Проверки: per-file count/cooldown; опционально per-directory count (обход через новые файлы в той же папке).
    """
    if PATCH_COOLDOWN_CYCLES <= 0 and MAX_PATCHES_PER_FILE <= 0 and MAX_PATCHES_PER_DIRECTORY <= 0:
        return (True, "")
    key = _normalize_path(path)
    state = _load_state()
    files = state.get("files") or {}
    current = state.get("global_cycle", 0)
    info = files.get(key) or {}
    count = int(info.get("count", 0))
    last_cycle = int(info.get("last_cycle", -1))

    if MAX_PATCHES_PER_FILE > 0 and count >= MAX_PATCHES_PER_FILE:
        return (
            False,
            f"Looping guard: {path} already patched {count} times (max {MAX_PATCHES_PER_FILE}). File locked. Switch file or task.",
        )
    if PATCH_COOLDOWN_CYCLES > 0 and count > 0 and (current - last_cycle) < PATCH_COOLDOWN_CYCLES:
        left = PATCH_COOLDOWN_CYCLES - (current - last_cycle)
        return (
            False,
            f"Looping guard: {path} was patched recently. Cooldown: {left} cycles left. Switch file or task.",
        )
    if MAX_PATCHES_PER_DIRECTORY > 0:
        dir_key = _directory_of(key)
        dir_count = sum(int((files.get(f) or {}).get("count", 0)) for f in files if _directory_of(f) == dir_key)
        if dir_count >= MAX_PATCHES_PER_DIRECTORY:
            return (
                False,
                f"Looping guard: directory {dir_key} already has {dir_count} patches (max {MAX_PATCHES_PER_DIRECTORY}). Work on another directory.",
            )
    return (True, "")


def record_patch(path: str) -> None:
    """Записать, что файл path был успешно пропатчен (вызывать после write_file / accept_patch). Увеличивает global_cycle, чтобы cooldown работал и без оркестратора."""
    key = _normalize_path(path)
    state = _load_state()
    files = state.get("files") or {}
    info = files.get(key) or {"count": 0, "last_cycle": 0}
    info["count"] = info.get("count", 0) + 1
    state["global_cycle"] = state.get("global_cycle", 0) + 1
    info["last_cycle"] = state["global_cycle"]
    files[key] = info
    state["files"] = files
    _save_state(state)


def reset_file(path: str) -> None:
    """Сбросить счётчик и cooldown для одного файла (для админа/тестов)."""
    key = _normalize_path(path)
    state = _load_state()
    files = state.get("files") or {}
    if key in files:
        del files[key]
    state["files"] = files
    _save_state(state)


def reset_all() -> None:
    """Сбросить всё состояние patch_guard (для админа/новой сессии)."""
    _save_state({"global_cycle": _load_state().get("global_cycle", 0), "files": {}})
