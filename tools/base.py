"""Tool abstraction and registry.

A Tool is a callable unit with a risk label and a typed `run` method.
The registry resolves names to instances for the loop.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Literal

from core.models import ToolCall, ToolResult

Risk = Literal["read_only", "reversible", "irreversible", "external"]


def require_ascii_identifier(value: str, *, role: str) -> str:
    """Reject non-ASCII text where this codebase expects an identifier.

    Programming identifiers in this agent — file paths, shell argv
    elements, memory tags — MUST be ASCII. Russian (or any other
    non-ASCII text) belongs in *content*: the bytes inside a file, the
    body of a memory note, a web search query. Mixing the two creates
    encoding surprises on Windows (cp1251 → UTF-8 mojibake) and breaks
    cross-platform pipelines (cmd.exe argv handling, subprocess env,
    older filesystems).

    `role` is a short human-readable label used in the exception message
    (e.g. "file_write path", "shell_exec argv[1]"). Raises
    `PermissionError` so the tool / sanitiser surfaces a uniform
    rejection that the policy gate already knows how to log.
    """
    if not isinstance(value, str):
        raise PermissionError(
            f"{role} must be a string, got {type(value).__name__}"
        )
    if not value.isascii():
        # Build a small hint: show the first offending character so the
        # user / LLM can see what to fix, but never echo the entire
        # value (it might be long or contain unrelated data).
        bad = next((ch for ch in value if ord(ch) > 127), "")
        raise PermissionError(
            f"{role} must be ASCII-only; non-ASCII character "
            f"{bad!r} (U+{ord(bad):04X}) is not allowed. "
            "Use English identifiers (e.g. 'hello.txt' instead of 'привет.txt'); "
            "Russian or other languages remain fine inside file content, "
            "memory notes, and search queries."
        )
    return value


#: Сколько настоящих имён показать в подсказке к отсутствующему пути.
_MAX_HINT_NAMES = 8


def truncated_name_hint(workspace_root, target) -> str:
    """Подсказка к отсутствующему пути: сначала ПРОДОЛЖЕНИЯ имени, потом список.

    Замер 2026-09-23, живой прогон: агент искал `logs/trace_8bdd8f1f06036d05e`
    и получил «Not found» без объяснения. Полное имя следа —
    `trace_8bdd8f1f06036d05e7746397e8703202.jsonl`, и в журналах агента оно
    лежит в ЧЕТЫРЁХ разных длинах: 18 упоминаний урезаны до 19 знаков, 9 — до
    25, 191 — целиком без расширения, 1 — целиком с расширением. Урезанный
    шестнадцатеричный хвост невозможно отличить от целого на глаз, поэтому
    агент берёт обрывок и упирается в «файла нет».

    Перечислять содержимое папки здесь мало: в `logs/` лежит 370 следов, и
    первые восемь по алфавиту ничего не подсказывают. Решает поиск
    ПРОДОЛЖЕНИЯ: если в папке есть ровно несколько имён, начинающихся с
    данного, их и надо назвать — тогда отказ становится починимым в том же
    ходе, а не потерянным шагом.

    Ошибка, которая не называет причину, заставляет угадывать, и агент
    угадывает неверно — тот же довод, что у двери памяти и у ворот улик.
    """
    from pathlib import Path

    try:
        root = Path(workspace_root).resolve()
        target = Path(target)
        parent = target.parent if target.parent != target else root
        if not parent.is_absolute():
            parent = root / parent
        parent = parent.resolve()
        parent.relative_to(root)
    except (OSError, ValueError):
        return ""
    if not parent.is_dir():
        return ""

    stem = target.name
    try:
        names = sorted(p.name for p in parent.iterdir() if not p.name.startswith("."))
    except OSError:
        return ""
    if not names:
        return ""

    if stem:
        starts = [n for n in names if n.startswith(stem) and n != stem]
        if starts:
            shown = ", ".join(starts[:_MAX_HINT_NAMES])
            more = "" if len(starts) <= _MAX_HINT_NAMES else f" (+{len(starts) - _MAX_HINT_NAMES})"
            return (
                f". Похоже на УРЕЗАННОЕ имя: в этой папке есть {len(starts)} "
                f"имя(ён), начинающихся с '{stem}' — {shown}{more}. "
                "Возьми полное имя целиком; обрывок шестнадцатеричного "
                "хвоста от целого не отличается на вид."
            )

    # Продолжений нет — подсказки нет. Список содержимого папки здесь НЕ
    # строится нарочно: он уже есть у `file_read._nearest_dir_hint`, и вторая
    # его редакция другими словами — два прибора об одном, ровно тот класс,
    # который мы весь день и разбираем. У каждого помощника одна работа.
    return ""


def normalize_slug(text: str) -> str:
    """Lowercase ASCII slug: non-alphanumeric runs become one hyphen."""
    import re

    if not isinstance(text, str):
        raise TypeError(f"normalize_slug expects str, got {type(text).__name__}")
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug or "untitled"


class Tool(ABC):
    name: str
    description: str
    risk: Risk = "read_only"
    #: Контракт аргументов словами — имена, типы, допустимые значения. Пусто
    #: значит «контракт не объявлен», и планировщик угадывает (замер
    #: 2026-09-03: memory_bank, шесть неверных вызовов подряд).
    arguments: str = ""

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """Execute the tool. Must raise on failure."""
        raise NotImplementedError

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        """Argument-dependent risk classification (§5 Action Risk & Reversibility).

        Most tools have a fixed risk class — `file_read` is always
        `read_only`, `web_search` is always `read_only`. But some tools
        (`file_write` is the canonical case) cross a trust boundary
        depending on what they're asked to do: writing a new file is
        reversible, *overwriting* an existing file is not.

        Default: return the static `self.risk`. Override to inspect
        `arguments` and pick a stricter (or laxer) class. The PolicyGate
        calls this method instead of reading `risk` directly.
        """
        return self.risk

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        """Tool Result Validation (§5).

        Returns (is_ok, issues):
          - is_ok=True  -> downstream may consume the output (warnings may still be present)
          - is_ok=False -> hard failure; loop must treat as verification error
          - issues      -> human-readable notes (empty list if perfectly clean)

        Default policy: any truthy output is OK, falsy -> hard fail.
        Tools should override for semantic checks (schema, freshness, emptiness, etc.).
        """
        if output is None:
            return False, ["output is None"]
        if isinstance(output, (str, list, dict)) and len(output) == 0:
            return False, ["empty output"]
        return True, []

    def execution_status(self, output: Any) -> str:
        """Did the work this tool was asked to do actually succeed?

        Defaults to `success` for any tool whose `run` returned without
        raising — for most tools, returning IS succeeding. A tool that can
        complete its own call while the work fails overrides this: shell
        commands are the case that forced it, where the subprocess starting
        and the command working are different facts (MIR-010).

        Distinct from `validate_output`, which asks whether the returned
        object is well-formed. A correct report of a failure is valid.
        """
        return "success"

    def invoke(self, call: ToolCall) -> ToolResult:
        """Wrap `run` with timing and error capture, returning a ToolResult."""
        started = time.perf_counter()
        try:
            output = self.run(**call.arguments)
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = ToolResult(
                tool_call_id=call.id,
                status="success" if self.execution_status(output) == "success" else "error",
                output=output,
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 — every tool failure becomes an error ToolResult
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = ToolResult(
                tool_call_id=call.id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
            )
        try:
            from core.tool_receipts import record_tool_invoke_receipt

            record_tool_invoke_receipt(self, call, result)
        except Exception:  # noqa: BLE001, S110 — reason stated above
            pass  # receipts must never break tool execution
        return result


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry")
        return self._tools[name]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def describe(self) -> str:
        return "\n".join(
            f"- {t.name} ({t.risk}): {t.description}"
            + (f" Arguments: {t.arguments}" if getattr(t, "arguments", "") else "")
            for t in self._tools.values()
        )

    def argument_contracts(self, *, hidden: frozenset[str] = frozenset()) -> str:
        """Контракты аргументов объявивших их инструментов — для планировщика."""
        lines = [
            f"- {t.name}: {t.arguments}"
            for t in self._tools.values()
            if getattr(t, "arguments", "") and t.name not in hidden
        ]
        return "\n".join(lines)
