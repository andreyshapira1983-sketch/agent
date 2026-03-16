"""
Состояние для визора инициативы на дашборде: последний выбор планировщика (мета-цель, мысль)
и результат проактивной попытки (написал / молчал и почему).
Читается дашбордом для прозрачности «как агент сам додумывается».
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_path = Path(__file__).resolve().parent.parent.parent / "data" / "initiative_visor_state.json"
_lock = threading.Lock()

_DEFAULT = {
    "meta_goal": "",
    "reason": "",
    "ts": "",
    "initiative_ts": "",
    "initiative_sent": False,
    "initiative_message_preview": "",
    "initiative_silent_reason": "",
}


def _load() -> dict:
    if _path.exists():
        try:
            return json.loads(_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(_DEFAULT)


def _save(data: dict) -> None:
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        _path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


def set_planner_choice(meta_goal: str, reason: str) -> None:
    """Записать последний выбор планировщика (мета-цель и мысль/причину)."""
    from datetime import datetime
    with _lock:
        data = _load()
        data["meta_goal"] = (meta_goal or "")[:80]
        data["reason"] = (reason or "")[:300]
        data["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save(data)


def set_initiative_result(sent: bool, message_preview: str = "", silent_reason: str = "") -> None:
    """Записать результат проактивной попытки: отправил сообщение или молчал и почему."""
    from datetime import datetime
    with _lock:
        data = _load()
        data["initiative_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["initiative_sent"] = bool(sent)
        data["initiative_message_preview"] = (message_preview or "")[:200]
        data["initiative_silent_reason"] = (silent_reason or "")[:120]
        _save(data)


def get_state() -> dict:
    """Прочитать текущее состояние для дашборда."""
    with _lock:
        return dict(_load())
