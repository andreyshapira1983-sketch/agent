"""
Security policy: allowed commands, paths, limits.
"""
from __future__ import annotations

ALLOWED_COMMAND_PREFIXES = ["echo", "date", "pwd"]
ALLOWED_PATH_PREFIXES: list[str] = []


def set_allowed_paths(paths: list[str]) -> None:
    global ALLOWED_PATH_PREFIXES
    ALLOWED_PATH_PREFIXES = list(paths)
