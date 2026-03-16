"""
Sandbox для патчей: копия проекта → применение патча → pytest → при успехе commit в основной проект.
Агент не пишет в реальный проект до прохождения тестов в sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


_INCREMENTAL_ENV = "EVOLUTION_INCREMENTAL_SANDBOX"
_CACHE_DIR_NAME = "agent_sandbox_cache"


def _ignore_for_copy(directory: Path | str, names: list[str]) -> list[str]:
    """Игнорировать при копировании проекта в sandbox (callback для shutil.copytree)."""
    ignore = {
        ".git", ".venv", "venv", "ENV", "__pycache__", ".cursor",
        ".pytest_cache", ".mypy_cache", "data", "node_modules",
        "sandbox",
    }
    # Игнорируем только каталоги (sandbox, .git, …), не файлы типа sandbox.py
    return [n for n in names if n in ignore or n.endswith(".pyc") or n.endswith(".bak")]


def _is_incremental_enabled() -> bool:
    return (os.getenv(_INCREMENTAL_ENV) or "1").strip().lower() in ("1", "true", "yes", "on")


def _copy_with_hardlinks(src: str, dst: str) -> str:
    """Копирование файла через hardlink; fallback на copy2 при ограничениях ФС."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def _cache_paths(tmp_root: Path) -> tuple[Path, Path, Path]:
    cache_root = tmp_root / _CACHE_DIR_NAME
    template_dir = cache_root / "template"
    meta_path = cache_root / "template_meta.json"
    return cache_root, template_dir, meta_path


def _project_signature(project_root: Path) -> str:
    """Лёгкий отпечаток проекта: path+size+mtime для инвалидации шаблона."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in set(_ignore_for_copy(root, dirs))]
        for name in sorted(files):
            if name.endswith(".pyc") or name.endswith(".bak"):
                continue
            full = Path(root) / name
            rel = full.relative_to(project_root).as_posix()
            try:
                st = full.stat()
            except OSError:
                continue
            h.update(rel.encode("utf-8", errors="ignore"))
            h.update(str(st.st_size).encode("ascii"))
            h.update(str(st.st_mtime_ns).encode("ascii"))
    return h.hexdigest()


def _ensure_template(project_root: Path) -> Path:
    tmp_root = Path(tempfile.gettempdir())
    cache_root, template_dir, meta_path = _cache_paths(tmp_root)
    cache_root.mkdir(parents=True, exist_ok=True)

    sig = _project_signature(project_root)
    current_sig = ""
    try:
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            current_sig = str((meta or {}).get("signature") or "")
    except Exception:
        current_sig = ""

    if template_dir.exists() and current_sig == sig:
        return template_dir

    if template_dir.exists():
        shutil.rmtree(template_dir, ignore_errors=True)
    shutil.copytree(
        project_root,
        template_dir,
        ignore=_ignore_for_copy,
        dirs_exist_ok=False,
        symlinks=False,
    )
    meta_path.write_text(
        json.dumps({"signature": sig, "created_at": time.time()}, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )
    return template_dir


def create_sandbox(project_root: Path) -> Path:
    """
    Создать копию проекта во временной директории.
    Возвращает путь к корню sandbox. Исключены .git, .venv, __pycache__, data и т.д.
    """
    parent = Path(tempfile.gettempdir())
    sandbox_path = parent / f"sandbox_{os.getpid()}_{time.time_ns()}"
    if not _is_incremental_enabled():
        shutil.copytree(
            project_root,
            sandbox_path,
            ignore=_ignore_for_copy,
            dirs_exist_ok=False,
            symlinks=False,
        )
        return sandbox_path

    template_dir = _ensure_template(project_root)
    shutil.copytree(
        template_dir,
        sandbox_path,
        dirs_exist_ok=False,
        symlinks=False,
        copy_function=_copy_with_hardlinks,
    )
    return sandbox_path


def apply_in_sandbox(sandbox_root: Path, relative_path: str, content: str) -> None:
    """Записать content в файл sandbox_root/relative_path."""
    target = (sandbox_root / relative_path).resolve()
    if not str(target).startswith(str(sandbox_root)):
        raise PermissionError(f"Path escapes sandbox: {relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Если файл hardlink на шаблон, отвязываем inode перед записью.
    if target.exists():
        try:
            if target.stat().st_nlink > 1:
                detached = target.with_suffix(target.suffix + ".detached")
                shutil.copy2(target, detached)
                os.replace(detached, target)
        except OSError:
            pass

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
