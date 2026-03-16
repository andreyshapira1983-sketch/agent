"""
Short-term (working) memory: last N messages for dialogue context.
Сохраняется на диск в data/short_term_memory.json при каждом add_message (агент знает историю чата после перезапуска).
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

# user_id -> deque of {role, content}
_stores: dict[str, deque[dict[str, str]]] = {}
MAX_MESSAGES = 20

_project_root = Path(__file__).resolve().parent.parent.parent
_STORAGE_PATH = _project_root / "data" / "short_term_memory.json"


def _load() -> None:
    """Загрузить чат из data/short_term_memory.json при старте."""
    global _stores
    if not _STORAGE_PATH.exists():
        return
    try:
        raw = json.loads(_STORAGE_PATH.read_text(encoding="utf-8"))
        for uid, messages in (raw or {}).items():
            if isinstance(messages, list) and messages:
                items = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages[-MAX_MESSAGES:]]
                _stores[uid] = deque(items, maxlen=MAX_MESSAGES)
    except Exception:
        pass


def _save() -> None:
    """Сохранить текущий чат в data/short_term_memory.json."""
    try:
        _STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        raw = {uid: list(d) for uid, d in _stores.items()}
        _STORAGE_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


# Загрузить при импорте модуля
_load()


def add_message(user_id: str, role: str, content: str) -> None:
    if user_id not in _stores:
        _stores[user_id] = deque(maxlen=MAX_MESSAGES)
    _stores[user_id].append({"role": role, "content": content})
    _save()


def get_messages(user_id: str) -> list[dict[str, str]]:
    if user_id not in _stores:
        return []
    return list(_stores[user_id])


def clear(user_id: str) -> None:
    _stores.pop(user_id, None)
    _save()
