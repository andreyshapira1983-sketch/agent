"""
Tools for reading/writing files and listing dirs. Paths relative to project root; security via allowed paths.
Guardrails для self-modifying агента:
- Удаление файлов запрещено (пустой content при перезаписи — reject).
- Перезапись не может удалять больше MAX_RELATIVE_REMOVAL (30%) объёма файла — иначе reject.
- Перед каждой записью в существующий файл создаётся backup .bak (обязательно).
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path
from datetime import datetime, timezone

from src.tools.base import tool_schema
from src.tools.registry import register

# Лимит: нельзя удалять больше этой доли файла при перезаписи (0.30 = 30%).
MAX_RELATIVE_REMOVAL = 0.30
# Лимит по diff: нельзя менять (удалять + добавлять) больше этой доли строк файла (0.30 = 30%).
MAX_LINES_CHANGED_RATIO = 0.30
# Жёсткий лимит: патч не может менять больше этого числа строк (добавления + удаления).
MAX_PATCH_LINES = 200

# Self-protection: эти пути агент никогда не должен менять (механизм защиты).
PROTECTED_PATHS = (
    "src/main.py",
    "src/core/",
    "src/tools/impl/file_tools.py",
    "src/tools/impl/patch_request_tool.py",
    "src/governance/",
    "src/hitl/",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _strict_mode_enabled() -> bool:
    return (os.environ.get("AGENT_STRICT_MODE") or "0").strip().lower() in ("1", "true", "yes", "on")


def _has_pytest_warnings(output: str) -> bool:
    low = (output or "").lower()
    return "warning summary" in low or " warnings " in low or "warning:" in low


def _resolve(path: str) -> Path:
    root = _project_root()
    p = (root / path).resolve()
    if not str(p).startswith(str(root)):
        raise PermissionError(f"Path outside project: {path}")
    return p


def _normalized_path(path: str) -> str:
    """Путь относительно корня, с forward slashes для сравнения с PROTECTED_PATHS."""
    root = _project_root()
    p = (root / path).resolve()
    s = str(p)
    if s.startswith(str(root)):
        rel = p.relative_to(root)
        return rel.as_posix()
    return path.replace("\\", "/")


def _is_protected(path: str) -> bool:
    """Проверка: путь в списке защищённых (агент не должен менять системные файлы)."""
    norm = _normalized_path(path)
    for prefix in PROTECTED_PATHS:
        if prefix.endswith("/"):
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return True
        elif norm == prefix or norm.startswith(prefix + "/"):
            return True
    return False


def _read_file(path: str) -> str:
    return _resolve(path).read_text(encoding="utf-8", errors="replace")


def _write_file(path: str, content: str) -> str:
    from src.hitl.audit_log import audit
    if _is_protected(path):
        return f"Error: path is protected. Agent must not modify {path} (self-protection)."
    try:
        from src.governance.patch_guard import can_patch, record_patch
        allowed, reason = can_patch(_normalized_path(path))
        if not allowed:
            return f"Error: {reason}"
    except Exception:
        pass
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    old_text: str | None = None
    if p.exists() and p.is_file():
        old_text = p.read_text(encoding="utf-8", errors="replace")
        old_len = len(old_text)
        new_len = len(content or "")
        # Guardrail 1: удаление файлов запрещено
        if new_len == 0:
            return "Error: file deletion is forbidden. Refused to overwrite with empty content. Improve the file, do not delete it."
        # Guardrail 2: нельзя удалять больше MAX_RELATIVE_REMOVAL (30%) объёма
        if old_len > 50 and new_len < (1.0 - MAX_RELATIVE_REMOVAL) * old_len:
            pct = (1 - new_len / old_len) * 100
            return f"Error: refused to overwrite {path}: patch removes {pct:.0f}% of file (max allowed {MAX_RELATIVE_REMOVAL*100:.0f}%). New size {new_len} vs original {old_len}. Make smaller, incremental changes."
        # Guardrail 3: diff-based — нельзя менять больше MAX_LINES_CHANGED_RATIO строк (добавления + удаления)
        old_lines = old_text.splitlines()
        new_lines = (content or "").splitlines()
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        total_changed = removed + added
        old_count = len(old_lines) or 1
        if old_count > 10 and total_changed > old_count * MAX_LINES_CHANGED_RATIO:
            pct = (total_changed / old_count) * 100
            return f"Error: refused — patch changes {total_changed} lines ({pct:.0f}% of {old_count}). Max allowed {MAX_LINES_CHANGED_RATIO*100:.0f}%. Make smaller, incremental changes."
        if total_changed > MAX_PATCH_LINES:
            return f"Error: refused — patch changes {total_changed} lines. Hard limit is {MAX_PATCH_LINES} lines. Split into smaller patches."
    had_backup = False
    # Syntax gate: для .py файлов проверка синтаксиса до записи
    if old_text is not None and p.suffix == ".py":
        try:
            import ast
            ast.parse(content)
        except SyntaxError as e:
            return f"Error: syntax error in {path}. Refused to write.\n{e!s}"
    strict_mode = _strict_mode_enabled()
    # Sandbox: при перезаписи (или strict_mode) и включённом test gate — применяем патч в копии проекта, pytest в sandbox; в основной проект пишем только при успехе
    test_gate = os.environ.get("WRITE_FILE_TEST_GATE", "1") != "0"
    if (old_text is not None or strict_mode) and test_gate:
        from src.evolution.sandbox import run_in_sandbox, cleanup_sandbox
        root = _project_root()
        rel_path = _normalized_path(path)
        ok, msg, sandbox_path = run_in_sandbox(root, rel_path, content, timeout=90)
        if not ok:
            return f"Error: tests failed in sandbox. No changes written to {path}.\n\n{msg}"
        if strict_mode and _has_pytest_warnings(msg):
            if sandbox_path is not None:
                cleanup_sandbox(sandbox_path)
            return f"Error: strict mode rejected write to {path}: pytest warnings detected in sandbox."
        # Успех в sandbox — пишем в основной проект
        bak = p.with_suffix(p.suffix + ".bak")
        if old_text is not None:
            bak.write_text(old_text, encoding="utf-8")
            had_backup = True
        p.write_text(content, encoding="utf-8")
        audit("write_file", {"path": path, "content_len": len(content)})
        try:
            from src.governance.patch_guard import record_patch
            record_patch(_normalized_path(path))
        except Exception:
            pass
        if sandbox_path is not None:
            cleanup_sandbox(sandbox_path)
        return f"Written {len(content)} chars to {path} (sandbox tests passed; backup: {path}.bak)"
    # Без test gate или новый файл — пишем сразу
    if old_text is not None:
        bak = p.with_suffix(p.suffix + ".bak")
        bak.write_text(old_text, encoding="utf-8")
        had_backup = True
    p.write_text(content, encoding="utf-8")
    audit("write_file", {"path": path, "content_len": len(content)})
    try:
        from src.governance.patch_guard import record_patch
        record_patch(_normalized_path(path))
    except Exception:
        pass
    return f"Written {len(content)} chars to {path}" + (f" (backup: {path}.bak)" if had_backup else "")


def _propose_file_edit(path: str, new_content: str) -> str:
    """
    Показать diff и сохранить в pending; не писать в файл.
    Guardrail: protected paths, >30% removal, >30% lines changed, MAX_PATCH_LINES, looping guard.
    """
    from src.hitl.audit_log import audit
    if _is_protected(path):
        return f"Error: path is protected. Agent must not modify {path} (self-protection)."
    try:
        from src.governance.patch_guard import can_patch
        allowed, reason = can_patch(_normalized_path(path))
        if not allowed:
            return f"Error: {reason}"
    except Exception:
        pass
    p = _resolve(path)
    old_lines = []
    if p.exists() and p.is_file():
        old_lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        if not (new_content or "").strip():
            return "Error: file deletion is forbidden. Proposed content is empty. Improve the file, do not delete it."
    new_lines = (new_content or "").splitlines(keepends=True)
    if not new_lines and new_content:
        new_lines = [new_content]
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path + " (new)", lineterm=""))
    diff_text = "".join(diff) if diff else "(no diff or new file)"
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    old_count = len(old_lines) or 1
    total_changed = removed + added
    # Guardrail: запрет патча, удаляющего >30% строк
    if old_count > 10 and removed > old_count * MAX_RELATIVE_REMOVAL:
        pct = (removed / old_count) * 100
        return f"Error: rejected — patch removes {removed} lines ({pct:.0f}% of {old_count}). Max allowed removal is {MAX_RELATIVE_REMOVAL*100:.0f}%. Make smaller, incremental changes."
    # Guardrail: запрет патча, меняющего >30% строк (добавления + удаления)
    if old_count > 10 and total_changed > old_count * MAX_LINES_CHANGED_RATIO:
        pct = (total_changed / old_count) * 100
        return f"Error: rejected — patch changes {total_changed} lines ({pct:.0f}% of {old_count}). Max allowed change is {MAX_LINES_CHANGED_RATIO*100:.0f}%. Make smaller, incremental changes."
    if total_changed > MAX_PATCH_LINES:
        return f"Error: rejected — patch changes {total_changed} lines. Hard limit is {MAX_PATCH_LINES} lines. Split into smaller patches."
    double_confirm = removed > 5
    # Сохранить предложение в pending
    pending_dir = _project_root() / "config" / "pending_patches"
    pending_dir.mkdir(parents=True, exist_ok=True)
    safe_name = path.replace("/", "_").replace("\\", "_")[:60]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    patch_file = pending_dir / f"{ts}_{safe_name}.patch"
    patch_file.write_text(f"# Diff for {path}\n# Apply with write_file after user confirmation.\n\n{diff_text}\n\n# New content (for apply):\n---\n{new_content}", encoding="utf-8")
    audit("propose_file_edit", {"path": path, "patch": patch_file.name, "lines_removed": removed})
    try:
        from src.communication.telegram_alerts import send_autonomous_event
        send_autonomous_event(f"📝 [Автономное действие] Предложено изменение файла: {path} — {patch_file.name}")
    except Exception:
        pass
    out = f"Diff saved to config/pending_patches/{patch_file.name}. Show user this diff; only call write_file after confirmation.\n\n"
    if double_confirm:
        out += f"⚠ ВНИМАНИЕ: удаляется {removed} строк (>5). Требуется ДВОЙНОЕ подтверждение пользователя перед write_file.\n\n"
    out += f"--- DIFF ---\n{diff_text}"
    return out


def _list_dir(path: str) -> str:
    p = _resolve(path)
    if not p.is_dir():
        return f"Not a directory: {path}"
    names = [x.name for x in p.iterdir()]
    return "\n".join(sorted(names)) if names else "(empty)"


def _describe_workspace(path: str = ".", max_depth: int = 3, max_entries: int = 200) -> str:
    from src.environment.filesystem import describe_tree

    target = _resolve(path or ".")
    return describe_tree(str(target), max_depth=max_depth, max_entries=max_entries)


def register_file_tools() -> None:
    root = str(_project_root())
    from src.security.policy import set_allowed_paths
    from src.environment.filesystem import set_allowed_roots
    set_allowed_paths([root])
    set_allowed_roots([root])

    register(
        "read_file",
        tool_schema(
            "read_file",
            "Read contents of a file. Path relative to project root (e.g. src/main.py or tests/test_foo.py).",
            {"path": {"type": "string", "description": "Path relative to project root"}},
            required=["path"],
        ),
        _read_file,
    )
    register(
        "write_file",
        tool_schema(
            "write_file",
            "Write content to a file. File deletion forbidden. Changes that remove >30% of file are rejected. Backup .bak created before overwrite. Use propose_file_edit first, then write_file after user confirmed.",
            {
                "path": {"type": "string", "description": "Path relative to project root"},
                "content": {"type": "string", "description": "File content (UTF-8)"},
            },
            required=["path", "content"],
        ),
        _write_file,
    )
    register(
        "propose_file_edit",
        tool_schema(
            "propose_file_edit",
            "Propose file change (no writing). Patches that delete file or remove >30% of lines are rejected. Diff saved to pending; call write_file only after user confirms.",
            {
                "path": {"type": "string", "description": "Path relative to project root"},
                "new_content": {"type": "string", "description": "Proposed new file content"},
            },
            required=["path", "new_content"],
        ),
        _propose_file_edit,
    )
    register(
        "list_dir",
        tool_schema(
            "list_dir",
            "List directory contents. Path relative to project root (e.g. src or tests).",
            {"path": {"type": "string", "description": "Path relative to project root"}},
            required=["path"],
        ),
        _list_dir,
    )
    register(
        "describe_workspace",
        tool_schema(
            "describe_workspace",
            "Show a compact recursive tree of files and directories so the agent can quickly see what exists and where it lives in the project.",
            {
                "path": {"type": "string", "description": "Directory relative to project root", "default": "."},
                "max_depth": {"type": "integer", "description": "Recursion depth", "default": 3},
                "max_entries": {"type": "integer", "description": "Maximum number of listed entries", "default": 200},
            },
        ),
        _describe_workspace,
    )
