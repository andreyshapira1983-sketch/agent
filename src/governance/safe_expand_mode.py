"""
Режимы безопасного расширения: управление из Telegram (/safe_expand, /apply_sandbox_only)
или из .env при старте (APPLY_SANDBOX_ONLY=1, SAFE_EXPAND=1).

Правило: применять к основной кодовой базе можно только артефакты из песочницы
с меткой «прошло тесты и не вредит архитектуре». Если не помечено — не применять.

- safe_expand: расширяться только через песочницу (propose_patch → validate_patch → accept_patch).
- apply_sandbox_only: применять только помеченное (accept_patch для validated; write_file/propose_file_edit в автономном режиме блокируются).
"""
from __future__ import annotations

import os

_safe_expand = False
_apply_sandbox_only = False
_env_applied = False


def _init_from_env() -> None:
    """Прочитать .env один раз при старте: APPLY_SANDBOX_ONLY=1, SAFE_EXPAND=1 — режимы включены без команды."""
    global _safe_expand, _apply_sandbox_only, _env_applied
    if _env_applied:
        return
    _env_applied = True
    v = (os.getenv("APPLY_SANDBOX_ONLY") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        _apply_sandbox_only = True
    v = (os.getenv("SAFE_EXPAND") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        _safe_expand = True


def set_safe_expand(enabled: bool) -> None:
    global _safe_expand
    _safe_expand = bool(enabled)


def safe_expand_enabled() -> bool:
    _init_from_env()
    return _safe_expand


def set_apply_sandbox_only(enabled: bool) -> None:
    global _apply_sandbox_only
    _apply_sandbox_only = bool(enabled)


def apply_sandbox_only_enabled() -> bool:
    """В автономном режиме применять только accept_patch (validated); write_file/propose_file_edit блокируются."""
    _init_from_env()
    return _apply_sandbox_only


def get_status() -> dict[str, bool]:
    _init_from_env()
    return {"safe_expand": _safe_expand, "apply_sandbox_only": _apply_sandbox_only}
