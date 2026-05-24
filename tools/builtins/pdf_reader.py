"""
tools/builtins/pdf_reader.py — Чтение PDF-файлов

Brain использует этот инструмент когда нужно извлечь текст или метаданные из PDF.
Использует pypdf (чистый Python, без внешних бинарников).

Actions:
    extract_text  — извлечь текст (весь документ или конкретную страницу)
    get_metadata  — заголовок, автор, дата, количество страниц, размер
    get_page_count — только количество страниц
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_MAX_FILE_SIZE_MB = 50
_MAX_CHARS_OUTPUT = 100_000


class PdfReaderTool(ToolBase):
    """
    Читает PDF-файлы и извлекает текст или метаданные.

    params:
        file_path (str): Путь к PDF-файлу
        action    (str, optional): extract_text | get_metadata | get_page_count
                                   (по умолчанию: extract_text)
        page      (int, optional): Номер страницы (с 1). Если не указан — весь документ.
        max_chars (int, optional): Максимум символов в ответе (по умолчанию 10000)
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="pdf_reader",
            description="Извлечь текст и метаданные из PDF-файла (без внешних зависимостей)",
            parameters={
                "file_path": "str — путь к PDF-файлу",
                "action":    "str (optional) — extract_text | get_metadata | get_page_count",
                "page":      "int (optional) — номер страницы (с 1), по умолчанию все",
                "max_chars": "int (optional, default 10000) — максимум символов в ответе",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return self._fail("pypdf не установлен. Запустите: pip install pypdf")

        file_path = params.get("file_path", "")
        if not file_path:
            return self._fail("Параметр 'file_path' обязателен")

        path = Path(str(file_path))
        if not path.exists():
            return self._fail(f"Файл не найден: {file_path}")
        if not path.is_file():
            return self._fail(f"Это не файл: {file_path}")
        if path.suffix.lower() != ".pdf":
            return self._fail(f"Файл должен быть .pdf, получено: {path.suffix!r}")

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > _MAX_FILE_SIZE_MB:
            return self._fail(
                f"Файл слишком большой: {size_mb:.1f} МБ (максимум {_MAX_FILE_SIZE_MB} МБ)"
            )

        action = params.get("action", "extract_text")
        t0 = time.perf_counter()

        try:
            import pypdf

            reader = pypdf.PdfReader(str(path))

            if action == "get_page_count":
                count = len(reader.pages)
                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(output=count, duration_ms=round(elapsed, 2))

            elif action == "get_metadata":
                meta = reader.metadata or {}
                result = {
                    "pages":       len(reader.pages),
                    "title":       str(meta.get("/Title", "")).strip(),
                    "author":      str(meta.get("/Author", "")).strip(),
                    "subject":     str(meta.get("/Subject", "")).strip(),
                    "creator":     str(meta.get("/Creator", "")).strip(),
                    "file_path":   str(path),
                    "file_size_kb": round(path.stat().st_size / 1024, 1),
                }
                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(output=result, duration_ms=round(elapsed, 2))

            elif action == "extract_text":
                max_chars = int(params.get("max_chars", 10_000))
                max_chars = min(max(100, max_chars), _MAX_CHARS_OUTPUT)

                page_param = params.get("page")
                total_pages = len(reader.pages)

                if page_param is not None:
                    page_idx = int(page_param) - 1
                    if page_idx < 0 or page_idx >= total_pages:
                        return self._fail(
                            f"Страница {page_param} вне диапазона (1–{total_pages})"
                        )
                    text = reader.pages[page_idx].extract_text() or ""
                else:
                    parts: list[str] = []
                    for pg in reader.pages:
                        extracted = pg.extract_text() or ""
                        parts.append(extracted)
                    text = "\n\n".join(parts)

                truncated = False
                if len(text) > max_chars:
                    text = text[:max_chars]
                    truncated = True

                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(
                    output=text,
                    duration_ms=round(elapsed, 2),
                    pages=total_pages,
                    chars_returned=len(text),
                    truncated=truncated,
                )

            else:
                return self._fail(
                    f"Неизвестное действие: {action!r}. "
                    "Допустимо: extract_text | get_metadata | get_page_count"
                )

        except Exception as exc:
            return self._fail(f"Ошибка чтения PDF: {exc}")
