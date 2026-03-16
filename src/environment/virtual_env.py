"""
Virtual/isolated env for running code. MVP: placeholder; Security/Sandbox integrate here.
"""
from __future__ import annotations

import subprocess  # nosec B404 — sandbox runner, cmd from trusted config

def run_in_sandbox(cmd: list[str], timeout: float = 30.0) -> str:
    """Run command in isolated env. MVP: run with timeout only."""
    try:
        r = subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout or r.stderr or ""
    except subprocess.TimeoutExpired:
        return "Timeout"
    except Exception as e:
        return str(e)
