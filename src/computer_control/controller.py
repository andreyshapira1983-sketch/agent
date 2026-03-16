"""
Computer control: run commands / file ops via environment. Security checks before use.
"""
from __future__ import annotations

from src.environment import filesystem, virtual_env
from src.security.validator import allow_command, allow_path


def list_directory(path: str) -> list[str]:
    if not allow_path(path):
        return []
    return filesystem.list_dir(path)


def run_command(cmd: list[str]) -> str:
    if not allow_command(cmd):
        return "Command not allowed"
    return virtual_env.run_in_sandbox(cmd)
