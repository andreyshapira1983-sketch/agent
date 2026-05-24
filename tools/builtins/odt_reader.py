"""
tools/builtins/odt_reader.py — Чтение документов OpenDocument (.odt, .ods, .odp)

Использует odfpy (чистый Python, без LibreOffice).

ODT  = OpenDocument Text (текстовые документы, как .docx)
ODS  = OpenDocument Spreadsheet (таблицы, как .xlsx)
ODP  = OpenDocument Presentation (презентации, как .pptx)

Actions (для всех типов):
    extract_text   — весь текст документа
    get_metadata   — автор, название, дата создания, тип документа
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_SUPPORTED = {".odt", ".ods", ".odp", ".odg"}
_MAX_FILE_SIZE_MB = 50
_MAX_CHARS_OUTPUT = 100_000


class OdtReaderTool(ToolBase):
    """
    Читает OpenDocument-файлы (.odt, .ods, .odp, .odg).

    params:
        file_path  (str): Путь к файлу
        action     (str, optional): extract_text | get_metadata
                                    (по умолчанию: extract_text)
        max_chars  (int, optional): Максимум символов (по умолчанию 20000)
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="odt_reader",
            description=(
                "Читает текст и метаданные из OpenDocument файлов (.odt, .ods, .odp, .odg). "
                "Работает без LibreOffice."
            ),
            parameters={
                "file_path": "str — путь к .odt/.ods/.odp файлу",
                "action":    "str (optional) — extract_text | get_metadata",
                "max_chars": "int (optional, default 20000) — максимум символов",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        t0 = time.perf_counter()
        try:
            import odf  # noqa: F401  (odfpy)
        except ImportError:
            return self._fail(
                "odfpy не установлен. Запустите: pip install odfpy"
            )

        file_path = params.get("file_path", "")
        if not file_path:
            return self._fail("Параметр 'file_path' обязателен")

        path = Path(str(file_path))
        if not path.exists():
            return self._fail(f"Файл не найден: {file_path}")
        if path.suffix.lower() not in _SUPPORTED:
            return self._fail(
                f"Неподдерживаемый формат: {path.suffix!r}. "
                f"Поддерживается: {', '.join(sorted(_SUPPORTED))}"
            )

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > _MAX_FILE_SIZE_MB:
            return self._fail(
                f"Файл слишком большой: {size_mb:.1f} МБ (лимит {_MAX_FILE_SIZE_MB} МБ)"
            )

        action = str(params.get("action", "extract_text")).lower()
        if action not in ("extract_text", "get_metadata"):
            return self._fail(
                f"Неизвестное действие: {action!r}. "
                "Доступно: extract_text | get_metadata"
            )

        try:
            from odf.opendocument import load as odf_load  # type: ignore
            doc = odf_load(str(path))
            dur_ms = (time.perf_counter() - t0) * 1000

            if action == "get_metadata":
                return self._get_metadata(doc, path, dur_ms)
            else:
                return self._extract_text(doc, path, params, dur_ms)

        except Exception as exc:
            return self._fail(f"Ошибка чтения {path.suffix}: {exc}")

    # ------------------------------------------------------------------
    def _extract_text(self, doc: Any, path: Path, params: dict, dur_ms: float) -> ToolResult:
        from odf import text as odf_text, table as odf_table  # type: ignore
        from odf.element import Element  # type: ignore

        max_chars = int(params.get("max_chars", 20_000))
        max_chars = min(max(1, max_chars), _MAX_CHARS_OUTPUT)

        lines: list[str] = []

        def _collect(element: Any) -> None:
            """Рекурсивно собирает текст из элементов ODF."""
            if hasattr(element, "qname"):
                tag = element.qname[1] if element.qname else ""
                if tag in ("p", "h"):
                    # Параграф или заголовок — собрать в одну строку
                    text_parts: list[str] = []
                    _collect_inline(element, text_parts)
                    line = "".join(text_parts).strip()
                    if line:
                        lines.append(line)
                    return
                elif tag == "table-row":
                    row_cells: list[str] = []
                    for child in element.childNodes:
                        cell_parts: list[str] = []
                        _collect_inline(child, cell_parts)
                        row_cells.append("".join(cell_parts).strip())
                    row_text = " | ".join(row_cells)
                    if row_text.replace("|", "").strip():
                        lines.append(row_text)
                    return

            if hasattr(element, "childNodes"):
                for child in element.childNodes:
                    _collect(child)

        def _collect_inline(element: Any, parts: list[str]) -> None:
            """Собирает текст внутри параграфа (учитывая вложенные spans)."""
            if hasattr(element, "data"):
                parts.append(element.data)
            if hasattr(element, "childNodes"):
                for child in element.childNodes:
                    _collect_inline(child, parts)

        body = doc.body
        if body:
            _collect(body)

        text = "\n".join(lines)
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        return self._ok(
            text,
            duration_ms=round(dur_ms, 2),
            file_type=path.suffix.lower(),
            chars_returned=len(text),
            truncated=truncated,
            file_size_kb=round(path.stat().st_size / 1024, 1),
        )

    # ------------------------------------------------------------------
    def _get_metadata(self, doc: Any, path: Path, dur_ms: float) -> ToolResult:
        try:
            meta = doc.meta
        except AttributeError:
            meta = None

        info: dict[str, Any] = {
            "file_type":     path.suffix.lower(),
            "file_size_kb":  round(path.stat().st_size / 1024, 1),
        }

        if meta is not None:
            def _get(tag: str) -> str | None:
                """Берёт первый элемент с нужным тегом из мета."""
                try:
                    from odf.meta import (  # type: ignore
                        InitialCreator, CreationDate, Generator,
                        DocumentStatistic,
                    )
                    from odf.dc import (  # type: ignore
                        Title, Creator, Description, Subject, Date,
                    )
                    tag_map = {
                        "title":       Title,
                        "creator":     Creator,
                        "description": Description,
                        "subject":     Subject,
                        "date":        Date,
                        "created":     CreationDate,
                        "initial_creator": InitialCreator,
                        "generator":   Generator,
                    }
                    cls = tag_map.get(tag)
                    if cls is None:
                        return None
                    elems = meta.getElementsByType(cls)
                    if elems:
                        raw = elems[0]
                        if hasattr(raw, "firstChild") and raw.firstChild:
                            return str(raw.firstChild)
                    return None
                except Exception:
                    return None

            info.update({
                "title":           _get("title"),
                "creator":         _get("creator"),
                "initial_creator": _get("initial_creator"),
                "subject":         _get("subject"),
                "description":     _get("description"),
                "created":         _get("created"),
                "modified":        _get("date"),
                "generator":       _get("generator"),
            })

        return self._ok(info, duration_ms=round(dur_ms, 2))
