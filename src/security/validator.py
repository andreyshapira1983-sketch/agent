"""
Validator: check if command/path is allowed.
"""
from __future__ import annotations

from src.security.policy import ALLOWED_COMMAND_PREFIXES, ALLOWED_PATH_PREFIXES


def allow_command(cmd: list[str]) -> bool:
    if not cmd:
        return False
    first = (cmd[0] or "").lower()
    return any(first.startswith(p) for p in ALLOWED_COMMAND_PREFIXES)


def allow_path(path: str) -> bool:
    if not ALLOWED_PATH_PREFIXES:
        return False
    path = path.replace("\\", "/")
    return any(path.startswith(p.replace("\\", "/")) for p in ALLOWED_PATH_PREFIXES)
