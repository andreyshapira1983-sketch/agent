"""
Защитный слой: один вызов LLM только для генерации (unified diff или контент).
Агент читает файл, применяет/валидирует, вызывает propose_file_edit.
См. AGENT_ARCHITECTURE.md: Agent → LLM (только генерация) → валидация → действие.
"""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

from src.tools.base import tool_schema
from src.tools.impl.file_tools import MAX_PATCH_LINES, _is_protected
from src.tools.registry import register
from src.tools.orchestrator import run_tool

MAX_RELATIVE_REMOVAL = 0.30
MAX_LINES_CHANGED_RATIO = 0.30

# Предпочтительно: LLM возвращает unified diff — тогда нельзя переписать весь файл.
PATCH_SYSTEM_DIFF = """You are a patch generator. Output ONLY a unified diff (like "diff -u") for the file.
Rules:
- Output only the diff: lines starting with " ", "-", "+". Optional header ---/+++ and @@ hunk headers.
- Do not delete the entire file. Make small, targeted changes (few lines).
- No explanation, no markdown, no code fences — only the diff text.
Language: match the user's language for any comments in the changed lines."""

PATCH_SYSTEM = """You are a patch generator. Your only job is to output the new full file content.
Rules: Do not delete the file. Do not remove more than a small part of the file. Do not add explanations, markdown, or code fences — output only the raw file content.
Language: match the user's language for any comments."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _normalize_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/").lstrip("/")


def _resolve_patch_target(path: str) -> tuple[str | None, str | None]:
    """Resolve a user-provided relative path to an existing file in the workspace."""
    normalized = _normalize_path(path)
    if not normalized:
        return None, "Error: path and user_goal are required."
    root = _project_root()
    direct = (root / normalized).resolve()
    if str(direct).startswith(str(root)) and direct.exists() and direct.is_file():
        return direct.relative_to(root).as_posix(), None

    basename = Path(normalized).name
    if not basename:
        return None, f"Error: file not found for path: {path}"

    suffix_matches: list[str] = []
    normalized_lower = normalized.lower()
    for candidate in root.rglob(basename):
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(root).as_posix()
        rel_lower = rel.lower()
        if rel_lower == normalized_lower or rel_lower.endswith(normalized_lower) or rel_lower.endswith("/" + normalized_lower):
            suffix_matches.append(rel)

    unique_matches = list(dict.fromkeys(suffix_matches))
    if len(unique_matches) == 1:
        return unique_matches[0], None
    if len(unique_matches) > 1:
        options = ", ".join(unique_matches[:5])
        return None, f"Error: ambiguous path '{path}'. Matching files: {options}"
    return None, f"Error: file not found for path: {path}"


def _apply_unified_diff(old_content: str, diff_text: str) -> str | None:
    """
    Применить unified diff к содержимому. Возвращает новое содержимое или None при ошибке.
    Парсит hunks @@ -start,count +start,count @@ и применяет -/+ к строкам.
    """
    raw = diff_text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    if not raw:
        return None
    old_lines = old_content.splitlines(keepends=True)
    if not old_lines and old_content:
        old_lines = [old_content if old_content.endswith("\n") else old_content + "\n"]
    diff_lines = raw.splitlines(keepends=True)
    new_lines: list[str] = []
    old_idx = 0
    k = 0
    while k < len(diff_lines):
        line = diff_lines[k]
        m = re.match(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", line.strip())
        if m:
            old_start = int(m.group(1)) - 1
            old_len = int(m.group(2) or 1)
            k += 1
            while old_idx < old_start and old_idx < len(old_lines):
                new_lines.append(old_lines[old_idx])
                old_idx += 1
            hunk_new: list[str] = []
            for _ in range(old_len):
                if k >= len(diff_lines):
                    old_idx += 1
                    continue
                pl = diff_lines[k]
                if pl.startswith("-") and not pl.startswith("---"):
                    k += 1
                    old_idx += 1
                    continue
                if pl.startswith("+") and not pl.startswith("+++"):
                    hunk_new.append(pl[1:] if pl[1:].endswith("\n") else pl[1:] + "\n")
                    k += 1
                    continue
                if pl.startswith(" "):
                    hunk_new.append(pl[1:] if len(pl) > 1 else pl)
                    k += 1
                    old_idx += 1
                    continue
                k += 1
                old_idx += 1
            new_lines.extend(hunk_new)
            continue
        if line.startswith("---") or line.startswith("+++"):
            k += 1
            continue
        k += 1
    while old_idx < len(old_lines):
        new_lines.append(old_lines[old_idx])
        old_idx += 1
    result = "".join(new_lines)
    return result if result or not old_content else None


def _call_llm_for_diff(path: str, current_content: str, user_goal: str) -> str:
    """Один вызов LLM: просим unified diff вместо полного файла."""
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
    if not key:
        return ""
    client = OpenAI(api_key=key)
    user_msg = f"File path: {path}\n\nUser goal: {user_goal}\n\nCurrent file content (for context):\n---\n{current_content[:8000]}\n---\nOutput ONLY the unified diff (lines starting with space, -, +, and @@ headers). No full file, no explanation."
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PATCH_SYSTEM_DIFF},
            {"role": "user", "content": user_msg},
        ],
    )
    if not r.choices or not r.choices[0].message.content:
        return ""
    return r.choices[0].message.content.strip()


def _call_llm_for_content(path: str, current_content: str, user_goal: str) -> str:
    """Один вызов LLM: цель пользователя + текущий файл → новое содержимое."""
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
    if not key:
        return ""
    client = OpenAI(api_key=key)
    user_msg = f"File path: {path}\n\nUser goal: {user_goal}\n\nCurrent file content:\n---\n{current_content}\n---\nOutput only the new file content (no explanation, no markdown fences)."
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PATCH_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    if not r.choices or not r.choices[0].message.content:
        return ""
    text = r.choices[0].message.content.strip()
    # Убрать возможные markdown-блоки кода
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _validate_patch_result(old_content: str, new_content: str, path: str) -> str | None:
    """Валидация: не пусто, не >30% удаления, не >30% строк изменено. Возвращает None если ок, иначе строку ошибки."""
    if not new_content.strip():
        return "Error: rejected — patch would delete file. File deletion is forbidden."
    old_len = len(old_content)
    new_len = len(new_content)
    if old_len > 50 and new_len < (1.0 - MAX_RELATIVE_REMOVAL) * old_len:
        pct = (1 - new_len / old_len) * 100
        return f"Error: rejected — patch removes {pct:.0f}% of file (max {MAX_RELATIVE_REMOVAL*100:.0f}%). Make smaller changes."
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    total_changed = removed + added
    old_count = len(old_lines) or 1
    if old_count > 10 and total_changed > old_count * MAX_LINES_CHANGED_RATIO:
        pct = (total_changed / old_count) * 100
        return f"Error: rejected — patch changes {total_changed} lines ({pct:.0f}% of {old_count}). Max {MAX_LINES_CHANGED_RATIO*100:.0f}%. Make smaller, incremental changes."
    if total_changed > MAX_PATCH_LINES:
        return f"Error: rejected — patch changes {total_changed} lines. Hard limit is {MAX_PATCH_LINES} lines. Split into smaller patches."
    return None


def _request_patch(path: str, user_goal: str) -> str:
    """
    Локально: read_file → LLM (unified diff или контент) → валидация (diff-based + lines changed) → propose_file_edit.
    Сначала пробуем запросить у LLM unified diff (безопаснее); при неудаче — полный контент.
    """
    if not (path or "").strip() or not (user_goal or "").strip():
        return "Error: path and user_goal are required."
    path = path.strip()
    user_goal = user_goal.strip()
    resolved_path, resolve_error = _resolve_patch_target(path)
    if resolve_error:
        return resolve_error
    path = resolved_path or path
    if _is_protected(path):
        return f"Error: path is protected. Agent must not modify {path} (self-protection)."
    try:
        current = run_tool("read_file", {"path": path})
    except Exception as e:
        return f"Error reading file: {e!s}"
    if not current or current.startswith("Error"):
        return f"Cannot read file: {current[:200]}"
    new_content = ""
    # 1) Пробуем unified diff (LLM не может переписать весь файл)
    diff_raw = _call_llm_for_diff(path, current, user_goal)
    if diff_raw and ("@@" in diff_raw or diff_raw.strip().startswith("-") or diff_raw.strip().startswith("+")):
        applied = _apply_unified_diff(current, diff_raw)
        if applied is not None:
            new_content = applied
    # 2) Fallback: полный контент
    if not new_content:
        new_content = _call_llm_for_content(path, current, user_goal)
    if not new_content:
        return "Error: LLM returned empty content. Try a clearer goal."
    err = _validate_patch_result(current, new_content, path)
    if err:
        return err
    try:
        return run_tool("propose_file_edit", {"path": path, "new_content": new_content})
    except Exception as e:
        return f"Error calling propose_file_edit: {e!s}"


def register_patch_request_tool() -> None:
    register(
        "request_patch",
        tool_schema(
            "request_patch",
            "Safe code change: you provide path and user goal; agent reads file, asks LLM once for new content, validates (no deletion, max 30% removal), then proposes patch. Use this for any user-requested file/code change instead of calling write_file or propose_file_edit directly.",
            {
                "path": {"type": "string", "description": "File path relative to project root (e.g. src/main.py)"},
                "user_goal": {"type": "string", "description": "What the user wants (e.g. add a comment, fix the bug, refactor X)"},
            },
            required=["path", "user_goal"],
        ),
        _request_patch,
    )
