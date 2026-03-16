"""
Tool: run pytest for a path. Used to verify modules after changes.
"""
from __future__ import annotations

import subprocess  # nosec B404 — run pytest tool, path validated
import sys
from pathlib import Path

from src.tools.base import tool_schema
from src.tools.registry import register


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _run_pytest(path: str) -> str:
    root = _project_root()
    p = (root / path).resolve()
    if not str(p).startswith(str(root)):
        return "Error: path must be inside project"
    try:
        r = subprocess.run(  # nosec B603
            [sys.executable, "-m", "pytest", str(p), "-v", "--tb=short"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            out = f"Exit code {r.returncode}\n{out}"
        return out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "pytest timeout (120s)"
    except Exception as e:
        return f"Error: {e}"


def register_run_pytest_tool() -> None:
    register(
        "run_pytest",
        tool_schema(
            "run_pytest",
            "Run pytest on a path (e.g. tests/ or tests/test_core_prompt.py). Use after changing code to verify nothing is broken.",
            {"path": {"type": "string", "description": "Path to test file or dir, relative to project root"}},
            required=["path"],
        ),
        _run_pytest,
    )
