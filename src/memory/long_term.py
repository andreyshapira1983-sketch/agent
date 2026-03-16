"""
Long-term (episodic) memory: persistent store.
Сохраняется на диск в data/long_term_memory.json при каждом add (агент знает эпизоды после перезапуска).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_long_term: dict[str, list[dict[str, Any]]] = {}
_MAX_ENTRIES_PER_USER = 200

_project_root = Path(__file__).resolve().parent.parent.parent
_STORAGE_PATH = _project_root / "data" / "long_term_memory.json"


def _load() -> None:
    """Загрузить long-term из data/long_term_memory.json при старте."""
    global _long_term
    if not _STORAGE_PATH.exists():
        return
    try:
        raw = json.loads(_STORAGE_PATH.read_text(encoding="utf-8"))
        _long_term = dict(raw) if isinstance(raw, dict) else {}
        for uid in _long_term:
            if isinstance(_long_term[uid], list):
                _long_term[uid] = _long_term[uid][-_MAX_ENTRIES_PER_USER:]
    except Exception:
        pass


def _save() -> None:
    """Сохранить long-term в data/long_term_memory.json."""
    try:
        _STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        to_save = {k: v[-_MAX_ENTRIES_PER_USER:] for k, v in _long_term.items()}
        _STORAGE_PATH.write_text(json.dumps(to_save, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


_load()


def add(user_id: str, entry: dict[str, Any]) -> None:
    if user_id not in _long_term:
        _long_term[user_id] = []
    _long_term[user_id].append(entry)
    _long_term[user_id] = _long_term[user_id][-_MAX_ENTRIES_PER_USER:]
    _save()


def get_recent(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    if user_id not in _long_term:
        return []
    return list(_long_term[user_id][-limit:])
