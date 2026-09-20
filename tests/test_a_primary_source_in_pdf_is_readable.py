"""Первоисточник лежит в PDF, и читать его надо тоже.

Замер 2026-09-20 по трассам за двое суток: `web_search` отработал 67 раз из
67 успешно, а `web_fetch` — 79 успехов и 28 ошибок. Больше половины всех
ошибок, 15 из 28, — одна и та же:

    PermissionError: content-type 'application/pdf' not in allow-list

Адреса говорят сами за себя: Тьюринг 1936 (cs.virginia.edu), Нётер
(web.stanford.edu), Шеннон 1948, статьи ACM. Агент ищет ПЕРВОИСТОЧНИКИ, а
первоисточники в интернете лежат PDF-ами. Поиск их находит, читалка
отказывается открывать — вот чем «поиск и чтение не соединены».

Список разрешённых типов держит двоичный мусор снаружи и держит его
дальше. PDF — не мусор, а документ, и берётся из него только ТЕКСТ, чистым
питоном (pypdf), без запуска чего бы то ни было. Схема, политика выхода,
предел размера и вычистка секретов остаются как были.

Стенд тот же, что у соседа `tests/test_web_fetch.py`: подставная открывалка
и адрес по IP, чтобы проверка адреса не ходила в DNS.
"""
from __future__ import annotations

import io

import pytest

from tools.web_fetch import ALLOWED_CONTENT_TYPES, PDF_MAX_PAGES, WebFetchTool

_URL = "https://1.1.1.1/shannon1948.pdf"


class _Response:
    def __init__(self, body: bytes, content_type: str = "application/pdf") -> None:
        self.status = 200
        self._buf = io.BytesIO(body)
        self.headers = {"Content-Type": content_type}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def geturl(self) -> str:
        return _URL

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _Opener:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def open(self, _req, timeout=None):
        return self.response


def _pdf_bytes(lines: list[str]) -> bytes:
    """Настоящий PDF, собранный библиотекой, а не подделка из строк.

    Словарь шрифта обязателен: без него `extract_text` возвращает мусор —
    проверено, первая редакция этого теста на том и спотыкалась.
    """
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for line in lines:
        writer.add_blank_page(width=300, height=300)
        page = writer.pages[-1]
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 150 Td ({line}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _fetch(body: bytes, content_type: str = "application/pdf") -> dict:
    return WebFetchTool(
        opener=_Opener(_Response(body, content_type)),
    ).run(url=_URL)


def test_pdf_is_on_the_allow_list() -> None:
    assert "application/pdf" in ALLOWED_CONTENT_TYPES


def test_the_text_of_a_pdf_comes_back() -> None:
    out = _fetch(_pdf_bytes(["Shannon 1948 entropy"]))
    assert "Shannon 1948 entropy" in out["text"]
    assert out["content_type"].startswith("application/pdf")
    assert out["status_code"] == 200


def test_every_page_up_to_the_cap_is_read() -> None:
    out = _fetch(_pdf_bytes(["page one here", "page two here"]))
    assert "page one here" in out["text"]
    assert "page two here" in out["text"]
    assert PDF_MAX_PAGES >= 2


def test_a_pdf_that_cannot_be_read_says_so() -> None:
    """Молчаливая пустота выглядела бы как «страница без содержания»."""
    with pytest.raises(ValueError, match="PDF"):
        _fetch(b"not really a pdf at all")


def test_other_binaries_are_still_refused() -> None:
    for kind in ("image/png", "application/zip", "application/octet-stream"):
        assert kind not in ALLOWED_CONTENT_TYPES
    with pytest.raises(PermissionError, match="allow-list"):
        _fetch(b"\x89PNG\r\n", content_type="image/png")
