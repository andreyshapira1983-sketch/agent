"""
Feedback: store (request, response, rating) for learning.
Сохраняется на диск в data/feedback_log.json при каждом add_feedback (агент знает историю обменов после перезапуска).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_log: list[dict[str, Any]] = []
_MAX_SAVED = 500  # хранить в файле последние N записей

_project_root = Path(__file__).resolve().parent.parent.parent
_STORAGE_PATH = _project_root / "data" / "feedback_log.json"


def _load() -> None:
    """Загрузить feedback из data/feedback_log.json при старте."""
    global _log
    if not _STORAGE_PATH.exists():
        return
    try:
        raw = json.loads(_STORAGE_PATH.read_text(encoding="utf-8"))
        _log = list(raw) if isinstance(raw, list) else []
    except Exception:
        pass


def _save() -> None:
    """Сохранить последние _MAX_SAVED записей в data/feedback_log.json."""
    try:
        _STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        to_save = _log[-_MAX_SAVED:]
        _STORAGE_PATH.write_text(json.dumps(to_save, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


_load()


def add_feedback(request: str, response: str, rating: float | None = None) -> None:
    _log.append({"request": request, "response": response, "rating": rating})
    _save()


def get_recent_feedback(n: int = 50) -> list[dict[str, Any]]:
    return _log[-n:]
