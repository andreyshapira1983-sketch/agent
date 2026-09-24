"""PDF по ссылке: скан распознаётся, книга не упирается в потолок (слово оператора 24.09).

Следы после 20.09: 33 PDF прочитаны; из неудач по самому PDF — три скана без
текстового слоя («scanned images?») и учебник Erickson (25 055 430 байт) при
потолке 8 МиБ. Распознавание идёт только через песочницу convert_file.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from tools import convert_file, web_fetch


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _fake_programs(argv, cwd: Path, _timeout):
    """pdftoppm кладёт страницу-картинку, tesseract — её текст (как настоящие)."""
    if argv[0] == "pdftoppm":
        (Path(cwd) / "page-1.png").write_bytes(b"png")
    elif argv[0] == "tesseract":
        (Path(cwd) / f"{argv[2]}.txt").write_text("Theorem 1. The FFT needs N log N steps.", encoding="utf-8")
    return 0, ""


def test_a_scanned_pdf_is_recognised_not_refused(monkeypatch) -> None:
    monkeypatch.setattr(web_fetch, "_ocr_runner", _fake_programs)
    text = web_fetch._pdf_text(_blank_pdf())
    assert "Theorem 1. The FFT needs N log N steps." in text
    assert "OCR" in text, "the reader is told the text came from recognition"


def _programs_reading(by_lang: dict[str, str]):
    """Как _fake_programs, но текст страницы зависит от языка распознавания."""
    def run(argv, cwd: Path, _timeout):
        if argv[0] == "pdftoppm":
            (Path(cwd) / "page-1.png").write_bytes(b"png")
        elif argv[0] == "tesseract":
            lang = argv[argv.index("-l") + 1]
            (Path(cwd) / f"{argv[2]}.txt").write_text(by_lang[lang], encoding="utf-8")
        return 0, ""
    return run


def test_an_english_scan_is_read_in_english(monkeypatch) -> None:
    """Живая проверка 24.09 (Cooley–Tukey): rus+eng дал «ап N Х N» — кириллицу
    на месте латиницы; такая улика не сверяется с источником."""
    mixed = "multiply an N-vector by ап N Х N matrix which can be factored into т sparse matrices " * 3
    clean = "multiply an N-vector by an N X N matrix which can be factored into m sparse matrices"
    monkeypatch.setattr(web_fetch, "_ocr_runner", _programs_reading({"rus+eng": mixed, "eng": clean}))
    text = web_fetch._pdf_text(_blank_pdf())
    assert clean in text and "ап N" not in text


def test_a_russian_scan_keeps_both_languages(monkeypatch) -> None:
    russian = "Теорема 1. Быстрое преобразование Фурье требует N log N шагов."
    monkeypatch.setattr(web_fetch, "_ocr_runner", _programs_reading({"rus+eng": russian}))
    assert russian in web_fetch._pdf_text(_blank_pdf())


def test_without_the_sandbox_there_is_no_recognition(monkeypatch) -> None:
    monkeypatch.setattr(convert_file, "sandbox_available", lambda: "no sandbox here")
    with pytest.raises(ValueError, match=r"scanned images.*OCR is unavailable: .*no sandbox here"):
        web_fetch._pdf_text(_blank_pdf())


def test_a_textbook_fits_under_the_pdf_cap() -> None:
    assert web_fetch.PDF_MAX_BYTES >= 25_055_430, "Erickson's Algorithms (24.09) must fit"
