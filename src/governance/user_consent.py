"""
Согласия пользователя на действия агента (интернет, команды Windows).
Хранятся в data/user_consent.json и сохраняются между перезапусками.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
_STORAGE = _ROOT / "data" / "user_consent.json"

_DEFAULTS = {
    "internet_allowed": False,
    "windows_commands_allowed": False,
}

_state: dict[str, Any] | None = None


def _load_state() -> dict[str, Any]:
    global _state
    if _state is not None:
        return _state
    data = dict(_DEFAULTS)
    if _STORAGE.exists():
        try:
            raw = json.loads(_STORAGE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data["internet_allowed"] = bool(raw.get("internet_allowed", data["internet_allowed"]))
                data["windows_commands_allowed"] = bool(raw.get("windows_commands_allowed", data["windows_commands_allowed"]))
        except Exception:
            pass
    _state = data
    return _state


def _save_state() -> None:
    data = _load_state()
    try:
        _STORAGE.parent.mkdir(parents=True, exist_ok=True)
        _STORAGE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_internet_allowed() -> bool:
    return bool(_load_state().get("internet_allowed", False))


def is_windows_commands_allowed() -> bool:
    return bool(_load_state().get("windows_commands_allowed", False))


def set_internet_allowed(allowed: bool) -> None:
    _load_state()["internet_allowed"] = bool(allowed)
    _save_state()


def set_windows_commands_allowed(allowed: bool) -> None:
    _load_state()["windows_commands_allowed"] = bool(allowed)
    _save_state()


def get_consent_status_text() -> str:
    state = _load_state()
    internet = "разрешен" if state.get("internet_allowed") else "запрещен"
    win = "разрешены" if state.get("windows_commands_allowed") else "запрещены"
    return f"Доступы: интернет {internet}; команды Windows {win}."
