"""Как вывод инструмента выглядит для модели: текст, на который она опирается.

Вынесено из core/answer_format.py: тот модуль — о том, как выглядит ОТВЕТ
модели (контракт вывода, печать для человека, цитаты), а здесь — как выглядит
для модели то, что вернули инструменты: `format_artifact` (результаты
web_search списком, прочитанный файл — через бюджет доказательств, листинг
папки как есть, всё прочее — `str()`) и `number_lines` (нумерация строк
только для запроса; построена и намеренно НЕ подключена — почему, см.
tests/test_the_model_can_see_line_numbers.py и docs/CODE_NOTES.md).
"""
from __future__ import annotations

from typing import Any


def number_lines(content: str, *, original: str | None = None) -> str:
    """Prefix each line with its TRUE 1-based number in *original*, for the
    prompt only.
    """
    lines = (content or "").splitlines()
    if not lines:
        return content or ""
    source = (original or content or "").splitlines()
    width = max(2, len(str(len(source))))

    out: list[str] = []
    cursor = 0
    for line in lines:
        number: int | None = None
        if original is None:
            number = len(out) + 1
        else:
            for idx in range(cursor, len(source)):
                if source[idx] == line:
                    number, cursor = idx + 1, idx + 1
                    break
        gutter = f"{number:>{width}}" if number else " " * width
        out.append(f"{gutter}\t{line}")
    numbered = "\n".join(out)
    return numbered + ("\n" if (content or "").endswith("\n") else "")


def format_artifact(
    tool_name: str | None,
    output: Any,
    *,
    question: str = "",
    self_documentation: bool = False,
) -> str:
    """Render a tool output into a stable string the LLM can ground on."""
    if tool_name == "web_search" and isinstance(output, list):
        if not output:
            return "(no results)"
        lines: list[str] = []
        for r in output:
            title   = r.get("title") or "(no title)"
            url     = r.get("url") or ""
            snippet = r.get("snippet") or ""
            source  = r.get("source") or "duckduckgo"
            lines.append(f"- {title}")
            lines.append(f"  url: {url}")
            if snippet:
                lines.append(f"  snippet: {snippet}")
            lines.append(f"  provider: {source}")
        return "\n".join(lines)
    if tool_name == "file_read" and isinstance(output, str):
        from core.evidence_budget import budget_file_content
        # Строки здесь НЕ нумеруются (number_lines не подключена): номер попал бы в
        # запись свидетельства. Если подключать — до бюджета, иначе номер будет
        # позицией в выдержке, а не в файле.
        return budget_file_content(
            output, question=question, self_documentation=self_documentation,
        )
    if tool_name == "list_dir" and isinstance(output, str):
        return output
    # Fallback: stringify whatever came back.
    return str(output)
