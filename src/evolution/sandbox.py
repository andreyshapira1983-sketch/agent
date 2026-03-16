"""
Sandbox для патчей: копия проекта → применение патча → pytest → при успехе commit в основной проект.
Агент не пишет в реальный проект до прохождения тестов в sandbox.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _ignore_for_copy(directory: Path | str, names: list[str]) -> list[str]:
    """Игнорировать при копировании проекта в sandbox (callback для shutil.copytree)."""
    ignore = {
        ".git", ".venv", "venv", "ENV", "__pycache__", ".cursor",
        ".pytest_cache", ".mypy_cache", "data", "node_modules",
        "sandbox",
    }
    # Игнорируем только каталоги (sandbox, .git, …), не файлы типа sandbox.py
    return [n for n in names if n in ignore or n.endswith(".pyc") or n.endswith(".bak")]


def create_sandbox(project_root: Path) -> Path:
    """
    Создать копию проекта во временной директории.
    Возвращает путь к корню sandbox. Исключены .git, .venv, __pycache__, data и т.д.
    """
    parent = Path(tempfile.gettempdir())
    sandbox_path = parent / f"sandbox_{os.getpid()}_{time.time_ns()}"
    shutil.copytree(
        project_root,
        sandbox_path,
        ignore=_ignore_for_copy,
        dirs_exist_ok=False,
        symlinks=False,
    )
    return sandbox_path


def apply_in_sandbox(sandbox_root: Path, relative_path: str, content: str) -> None:
    """Записать content в файл sandbox_root/relative_path."""
    target = (sandbox_root / relative_path).resolve()
    if not str(target).startswith(str(sandbox_root)):
        raise PermissionError(f"Path escapes sandbox: {relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def run_pytest_in_sandbox(
    sandbox_root: Path,
    timeout: int = 90,
    test_path: str = "tests/",
) -> tuple[bool, str]:
    """
    Запустить pytest в sandbox. Возвращает (success, output).
    test_path: путь к тестам относительно sandbox (по умолчанию tests/).
    PYTHONPATH устанавливается в sandbox root, чтобы импорты src.* работали.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = str(sandbox_root)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-v", "--tb=line", "-q"],
            cwd=str(sandbox_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            out = f"Exit code {r.returncode}\n{out}"
        return (r.returncode == 0, out.strip() or "(no output)")
    except subprocess.TimeoutExpired:
        return (False, "pytest timeout")
    except Exception as e:
        return (False, f"pytest error: {e!s}")


def cleanup_sandbox(sandbox_root: Path) -> None:
    """Удалить директорию sandbox."""
    try:
        shutil.rmtree(sandbox_root, ignore_errors=True)
    except OSError:
        pass


def run_in_sandbox(
    project_root: Path,
    relative_path: str,
    content: str,
    timeout: int = 90,
    test_path: str = "tests/",
) -> tuple[bool, str, Path | None]:
    """
    Полный цикл: создать sandbox, применить content к relative_path, запустить pytest.
    Возвращает (success, message, sandbox_path).
    sandbox_path не None только при успехе (для последующего cleanup вызывающего кода);
    при неудаче sandbox уже удалён.
    test_path: путь к тестам для pytest (по умолчанию tests/).
    """
    sandbox_path = create_sandbox(project_root)
    try:
        apply_in_sandbox(sandbox_path, relative_path, content)
        ok, out = run_pytest_in_sandbox(sandbox_path, timeout=timeout, test_path=test_path)
        if not ok:
            cleanup_sandbox(sandbox_path)
            return (False, out, None)
        return (True, out, sandbox_path)
    except Exception as e:
        cleanup_sandbox(sandbox_path)
        return (False, str(e), None)
