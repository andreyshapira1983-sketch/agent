"""
Sandbox: isolate execution. MVP: validator + virtual_env.
"""
from __future__ import annotations

from src.security.validator import allow_command
from src.environment import virtual_env


def run_sandboxed(cmd: list[str]) -> str:
    if not allow_command(cmd):
        return "Blocked by policy"
    return virtual_env.run_in_sandbox(cmd)
