"""
tools/builtins/image_reader.py — Чтение изображений (PNG, JPG, WEBP, BMP, GIF, TIFF)

Использует Pillow (PIL) — чистый Python, без внешних бинарников.
OCR (extract_text) — опционально через pytesseract (требует Tesseract binary).

Actions:
    get_metadata  — размер, формат, режим цвета, DPI, цветовая модель
    extract_text  — OCR: извлечь текст с изображения (требует pytesseract + Tesseract)
    describe      — человекочитаемое описание изображения (размер, цвета, яркость)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
_MAX_FILE_SIZE_MB = 100


class ImageReaderTool(ToolBase):
    """
    Читает изображения и извлекает метаданные или текст (OCR).

    params:
        file_path (str): Путь к файлу изображения
        action    (str, optional): get_metadata | extract_text | describe
                                   (по умолчанию: get_metadata)
        lang      (str, optional): Язык для OCR — e.g. "rus", "eng", "rus+eng"
                                   (по умолчанию: "rus+eng")
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_reader",
            description=(
                "Читает изображения (PNG, JPG, WEBP, BMP, GIF, TIFF). "
                "Возвращает метаданные, описание или OCR-текст."
            ),
            parameters={
                "file_path": "str — путь к файлу изображения",
                "action":    "str (optional) — get_metadata | extract_text | describe",
                "lang":      "str (optional, default 'rus+eng') — язык OCR (Tesseract коды)",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return self._fail("Pillow не установлен. Запустите: pip install Pillow")

        file_path = params.get("file_path", "")
        if not file_path:
            return self._fail("Параметр 'file_path' обязателен")

        path = Path(str(file_path))
        if not path.exists():
            return self._fail(f"Файл не найден: {file_path}")
        if not path.is_file():
            return self._fail(f"Это не файл: {file_path}")
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            return self._fail(
                f"Неподдерживаемый формат: {path.suffix!r}. "
                f"Поддерживается: {sorted(_SUPPORTED_EXTENSIONS)}"
            )

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > _MAX_FILE_SIZE_MB:
            return self._fail(
                f"Файл слишком большой: {size_mb:.1f} МБ (макс. {_MAX_FILE_SIZE_MB})"
            )

        action = params.get("action", "get_metadata")
        t0 = time.perf_counter()

        try:
            from PIL import Image

            img = Image.open(str(path))

            if action == "get_metadata":
                exif_data: dict[str, Any] = {}
                try:
                    raw_exif = img._getexif()  # type: ignore[attr-defined]
                    if raw_exif:
                        from PIL.ExifTags import TAGS
                        for tag_id, value in raw_exif.items():
                            tag = TAGS.get(tag_id, str(tag_id))
                            # Берём только читаемые значения
                            if isinstance(value, (str, int, float, tuple)):
                                exif_data[tag] = str(value)
                except Exception:
                    pass  # EXIF недоступен или сломан

                width, height = img.size
                result = {
                    "file_path":   str(path),
                    "format":      img.format or path.suffix.upper().lstrip("."),
                    "mode":        img.mode,
                    "width_px":    width,
                    "height_px":   height,
                    "megapixels":  round(width * height / 1_000_000, 2),
                    "file_size_kb": round(path.stat().st_size / 1024, 1),
                    "exif":        exif_data,
                }
                # DPI если есть
                dpi = img.info.get("dpi")
                if dpi:
                    result["dpi"] = dpi

                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(output=result, duration_ms=round(elapsed, 2))

            elif action == "describe":
                width, height = img.size
                megapixels = round(width * height / 1_000_000, 2)

                # Конвертируем в RGB для анализа цветов
                try:
                    rgb = img.convert("RGB")
                    # Уменьшаем для быстрого анализа
                    small = rgb.resize((100, 100))
                    pixels = list(small.getdata())
                    avg_r = sum(p[0] for p in pixels) / len(pixels)
                    avg_g = sum(p[1] for p in pixels) / len(pixels)
                    avg_b = sum(p[2] for p in pixels) / len(pixels)
                    brightness = (avg_r + avg_g + avg_b) / 3

                    if brightness > 200:
                        tone = "светлое"
                    elif brightness > 128:
                        tone = "среднее"
                    elif brightness > 60:
                        tone = "тёмное"
                    else:
                        tone = "очень тёмное"

                    dominant = "красный" if avg_r >= avg_g and avg_r >= avg_b else (
                        "зелёный" if avg_g >= avg_r and avg_g >= avg_b else "синий"
                    )
                    color_info = f"яркость {brightness:.0f}/255, преобладает {dominant}"
                except Exception:
                    color_info = "анализ цвета недоступен"
                    tone = "неизвестно"

                description = (
                    f"Изображение {width}×{height} пикселей ({megapixels} МП), "
                    f"формат {img.format or path.suffix.upper().lstrip('.')}, "
                    f"цветовая модель {img.mode}. "
                    f"Тональность: {tone} ({color_info}). "
                    f"Размер файла: {round(path.stat().st_size / 1024, 1)} КБ."
                )

                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(
                    output=description,
                    duration_ms=round(elapsed, 2),
                    width=width,
                    height=height,
                )

            elif action == "extract_text":
                try:
                    import pytesseract
                except ImportError:
                    return self._fail(
                        "pytesseract не установлен. Запустите: pip install pytesseract\n"
                        "Также нужен Tesseract-OCR binary: https://github.com/UB-Mannheim/tesseract/wiki"
                    )

                lang = params.get("lang", "rus+eng")

                # Конвертируем в RGB/L для лучшего OCR
                if img.mode not in {"RGB", "L"}:
                    img = img.convert("RGB")

                text = pytesseract.image_to_string(img, lang=lang)
                text = text.strip()

                elapsed = (time.perf_counter() - t0) * 1000
                return self._ok(
                    output=text,
                    duration_ms=round(elapsed, 2),
                    lang=lang,
                    chars=len(text),
                )

            else:
                return self._fail(
                    f"Неизвестное действие: {action!r}. "
                    "Допустимо: get_metadata | extract_text | describe"
                )

        except Exception as exc:
            return self._fail(f"Ошибка чтения изображения: {exc}")
