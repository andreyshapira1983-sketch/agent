"""
Core Intelligence: receives interpreted input, calls LLM, coordinates planning/memory/knowledge/tools.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency in some test envs
    OpenAI = None  # type: ignore[assignment]

from src.core.prompt import get_system_prompt
from src.tools.registry import list_tools
from src.tools.orchestrator import run_tool

# Use OPENAI_API_KEY (set from OPEN_KEY_API in main/env)
_client: OpenAI | None = None

MAX_TOOL_ROUNDS = 10
_READING_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "reading_log.json"


def _get_llm_timeout_sec() -> float:
    """Timeout для одного LLM вызова (сек), configurable через LLM_CALL_TIMEOUT_SEC."""
    try:
        return max(10.0, float((os.getenv("LLM_CALL_TIMEOUT_SEC") or "90").strip()))
    except Exception:
        return 90.0


def _client_get() -> OpenAI:
    global _client
    if _client is None:
        if OpenAI is None:
            raise RuntimeError("openai package is not available")
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY (or OPEN_KEY_API in .env) is not set")
        _client = OpenAI(api_key=key)
    return _client


def _openai_tools() -> list[dict[str, Any]]:
    """Tool list in OpenAI API format."""
    return list_tools()


def _run_tool_calls(tool_calls: list[Any]) -> list[dict[str, Any]]:
    """Execute tool calls and return list of tool result messages."""
    out: list[dict[str, Any]] = []
    for tc in tool_calls:
        tid = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
        fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
        if not fn:
            continue
        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else "") or ""
        args_s = getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else "{}")
        try:
            args = json.loads(args_s) if isinstance(args_s, str) else (args_s or {})
        except json.JSONDecodeError:
            args = {}
        result = run_tool(str(name), args)
        out.append({"role": "tool", "tool_call_id": tid, "content": str(result)})
    return out


def chat(
    messages: list[dict[str, Any]],
    system_extra: str = "",
    model: str = "gpt-4o-mini",
    use_tools: bool = True,
) -> str:
    """Single LLM call with system + messages. If use_tools, runs tool loop. Returns assistant text."""
    system = get_system_prompt(system_extra)
    if len(system) > _MAX_SYSTEM_CHARS:
        system = system[:_MAX_SYSTEM_CHARS - 50] + "\n\n[Системный контекст обрезан из-за лимита модели.]"
    full: list[dict[str, Any]] = [{"role": "system", "content": system}]
    full.extend(messages)
    client = _client_get()
    tools = _openai_tools() if use_tools else []
    llm_timeout = _get_llm_timeout_sec()
    for _ in range(MAX_TOOL_ROUNDS):
        kwargs: dict[str, Any] = {"model": model, "messages": full}
        if tools:
            kwargs["tools"] = tools
        kwargs["timeout"] = llm_timeout
        try:
            r = client.chat.completions.create(**kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                return f"LLM timeout: модель не ответила за {llm_timeout:.0f} c."
            raise
        if not r.choices:
            return ""
        msg = r.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return (msg.content or "").strip()
        full.append(msg)
        full.extend(_run_tool_calls(tool_calls))
    return (full[-1].get("content") or "Слишком много шагов инструментов.").strip()


def _recent_feedback_extra() -> str:
    """Краткая сводка последней обратной связи для контекста агента."""
    try:
        from src.learning.feedback import get_recent_feedback
        items = get_recent_feedback(5)
        if not items:
            return ""
        lines = []
        for i in items:
            r = i.get("rating")
            s = f"- запрос: «{str(i.get('request', ''))[:80]}» → ответ: «{str(i.get('response', ''))[:60]}»"
            if r is not None:
                s += f" (оценка: {r})"
            lines.append(s)
        return "Последние обмены (для учёта опыта):\n" + "\n".join(lines)
    except Exception:
        return ""


def _derived_rules_extra() -> str:
    """Выведенные из feedback правила: агент их видит в контексте и учитывает в ответах."""
    try:
        from src.learning.rules_safety import get_rules
        rules = get_rules(limit=20, min_score=0.5)
        if not rules:
            return ""
        lines = [r.get("rule", "")[:300] for r in rules if r.get("rule")]
        if not lines:
            return ""
        return "Выведенные правила (соблюдай в ответах):\n" + "\n".join(f"- {s}" for s in lines)
    except Exception:
        return ""


def _current_status_extra() -> str:
    """Текущее состояние агента: последняя цель цикла, последнее действие — чтобы отвечать на «что делаешь?», «чем занят?»."""
    try:
        from src.hitl.audit_log import get_audit_tail
        tail = get_audit_tail(80)
        last_goal = ""
        last_action = ""
        for e in reversed(tail):
            if e.get("action") == "autonomous_cycle_end":
                d = e.get("details") or {}
                last_goal = str(d.get("goal") or "").strip()
                break
        for e in reversed(tail):
            if e.get("action") == "autonomous_act":
                d = e.get("details") or {}
                last_action = f"{d.get('tool', '')} (успех: {d.get('success', '')})"
                break
        if not last_goal and not last_action:
            return ""
        parts = []
        if last_goal:
            parts.append(f"последняя цель автономного цикла: {last_goal}")
        if last_action:
            parts.append(f"последнее действие: {last_action}")
        return "Твоё текущее состояние (отвечай на «что делаешь?», «чем занят?» отсюда): " + "; ".join(parts) + "."
    except Exception:
        return ""


def _reading_memory_extra(limit: int = 3) -> str:
    """Короткая сводка накопленного изученного контента из reading_log."""
    try:
        if not _READING_LOG_PATH.exists():
            return ""
        raw = json.loads(_READING_LOG_PATH.read_text(encoding="utf-8"))
        entries = raw.get("entries") or []
        if not entries:
            return ""
        last = entries[-max(1, min(limit, 10)):]
        lines: list[str] = []
        for item in reversed(last):
            title = str(item.get("title") or item.get("url") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not title and not summary:
                continue
            if summary:
                lines.append(f"- {title[:80]}: {summary[:180]}")
            else:
                lines.append(f"- {title[:120]}")
        if not lines:
            return ""
        return "Память изученного (используй в ответах и планировании):\n" + "\n".join(lines)
    except Exception:
        return ""


# Лимиты контекста, чтобы не превысить maximum context length модели (400 ошибка)
_MAX_SYSTEM_CHARS = 52_000
_MAX_CONTEXT_MESSAGES = 12
_MAX_MESSAGE_CONTENT_CHARS = 2_000
_MAX_TOTAL_CONTEXT_CHARS = 28_000


def _truncate_messages_for_context(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Приоритизированное обрезание контекста: важные и свежие сообщения сохраняются первыми."""
    if not msgs:
        return []

    # Базовый набор: не более N последних сообщений
    out = msgs[-_MAX_CONTEXT_MESSAGES:] if len(msgs) > _MAX_CONTEXT_MESSAGES else list(msgs)
    prepared: list[tuple[int, str, str]] = []
    for idx, m in enumerate(out):
        role = str(m.get("role") or "user")
        content = (m.get("content") or "").strip()
        if len(content) > _MAX_MESSAGE_CONTENT_CHARS:
            content = content[: _MAX_MESSAGE_CONTENT_CHARS - 20] + "… [обрезано]"
        prepared.append((idx, role, content))

    if not prepared:
        return []

    def _score(item: tuple[int, str, str]) -> float:
        idx, role, content = item
        recency = (idx + 1) / max(len(prepared), 1)
        role_weight = 2.5 if role == "user" else (1.5 if role == "assistant" else 1.0)
        low = content.lower()
        semantic_bonus = 0.0
        if "[intent:" in low:
            semantic_bonus += 1.8
        if any(k in low for k in ("ошиб", "error", "patch", "quality", "цель", "goal", "разрешаю")):
            semantic_bonus += 1.2
        return recency + role_weight + semantic_bonus

    ranked = sorted(prepared, key=_score, reverse=True)
    selected: list[tuple[int, str, str]] = []
    total = 0
    for item in ranked:
        _, _, content = item
        if total + len(content) > _MAX_TOTAL_CONTEXT_CHARS and selected:
            continue
        selected.append(item)
        total += len(content)

    # Гарантия: самое последнее сообщение не теряем
    if prepared[-1] not in selected:
        last = prepared[-1]
        if total + len(last[2]) <= _MAX_TOTAL_CONTEXT_CHARS:
            selected.append(last)

    selected.sort(key=lambda x: x[0])
    return [{"role": role, "content": content} for _, role, content in selected]


def process_user_input(
    user_text: str,
    context_messages: list[dict[str, str]] | None = None,
    model: str = "gpt-4o-mini",
) -> str:
    """
    Process user message: build messages (with optional context), call LLM with tools, return reply.
    Context is previous dialogue for short-term memory; recent feedback is added to system extra.
    Контекст и системный промпт ограничены, чтобы не превысить лимит модели (400).
    """
    messages: list[dict[str, Any]] = []
    if context_messages:
        messages = _truncate_messages_for_context(context_messages)
    user_content = (user_text or "").strip()
    if len(user_content) > _MAX_MESSAGE_CONTENT_CHARS * 2:
        user_content = user_content[: _MAX_MESSAGE_CONTENT_CHARS * 2 - 30] + "… [сообщение обрезано]"
    messages.append({"role": "user", "content": user_content})
    system_extra = _recent_feedback_extra()
    rules_block = _derived_rules_extra()
    if rules_block:
        system_extra = (system_extra + "\n\n" + rules_block).strip()
    status_block = _current_status_extra()
    if status_block:
        system_extra = (system_extra + "\n\n" + status_block).strip()
    learned_block = _reading_memory_extra(limit=3)
    if learned_block:
        system_extra = (system_extra + "\n\n" + learned_block).strip()
    return chat(messages, model=model, system_extra=system_extra)
