"""
Tools for creating other agents: templates, lint, code check.
"""
from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404 — run ruff/pytest, fixed args
import sys
from pathlib import Path

from src.tools.base import tool_schema
from src.tools.registry import register

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _templates_dir() -> Path:
    return _project_root() / "templates"


def _list_templates() -> str:
    tpl = _templates_dir()
    if not tpl.exists() or not tpl.is_dir():
        return "No templates/ directory. Create templates/<name>/ with project structure."
    names = [d.name for d in tpl.iterdir() if d.is_dir() and not d.name.startswith(".")]
    return "\n".join(sorted(names)) if names else "No template folders in templates/."


def _create_from_template(template_name: str, target_path: str) -> str:
    root = _project_root()
    tpl = _templates_dir() / template_name
    target = (root / target_path).resolve()
    if not tpl.exists() or not tpl.is_dir():
        return f"Template '{template_name}' not found."
    if not str(target).startswith(str(root)):
        return "Target path must be inside project."
    if target.exists():
        return f"Target already exists: {target_path}"
    try:
        shutil.copytree(tpl, target)
        return f"Created {target_path} from template {template_name}."
    except Exception as e:
        return f"Error: {e}"


def _run_lint(path: str) -> str:
    """Run ruff or py_compile on path. Path relative to project root."""
    root = _project_root()
    p = (root / path).resolve()
    if not str(p).startswith(str(root)):
        return "Path must be inside project."
    try:
        r = subprocess.run(  # nosec B603
            [sys.executable, "-m", "ruff", "check", str(p), "--output-format=concise"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (r.stdout or "").strip() + (r.stderr or "").strip()
        if r.returncode != 0 and out:
            return f"Ruff found issues:\n{out[:1000]}"
        if out:
            return out[:1000]
        return "Ruff: no issues."
    except subprocess.TimeoutExpired:
        return "Lint timeout."
    except FileNotFoundError:
        try:
            import py_compile
            py_compile.compile(str(p), doraise=True)
            return "py_compile: OK."
        except py_compile.PyCompileError as e:
            return f"py_compile error: {e}"
        except Exception as e:
            logger.debug("py_compile fallback: %s", e)
        return "Ruff not installed. Install with: pip install ruff"
    except Exception as e:
        return f"Error: {e}"


def register_agent_tools() -> None:
    register(
        "list_templates",
        tool_schema(
            "list_templates",
            "List available project templates (templates/ folder). For creating new agents or projects.",
            {},
            required=[],
        ),
        _list_templates,
    )
    register(
        "create_from_template",
        tool_schema(
            "create_from_template",
            "Create a new project from a template. Copies templates/<template_name> to target_path.",
            {
                "template_name": {"type": "string", "description": "Name of template folder"},
                "target_path": {"type": "string", "description": "Target path relative to project root"},
            },
            required=["template_name", "target_path"],
        ),
        _create_from_template,
    )
    register(
        "run_lint",
        tool_schema(
            "run_lint",
            "Run linter (ruff) on a file or directory. Path relative to project root. Checks code quality.",
            {"path": {"type": "string", "description": "Path to file or dir"}},
            required=["path"],
        ),
        _run_lint,
    )
