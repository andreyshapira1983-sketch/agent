"""Обрезанный PDF не читается вообще, а не «наполовину».

Оператор, 2026-09-20, вечер: «Опять PDF-файлы не работают. Не знаю почему».
Час назад PDF был разрешён к чтению (`application/pdf` в списке типов,
текст берётся pypdf) — и всё равно не работал.

Замер на живом адресе: «Attention Is All You Need», https://arxiv.org/pdf/
1706.03762, весит 2 215 244 байта. Общий потолок чтения — 1 МиБ. Файл
обрезался посередине, и pypdf падал:

    EOF marker not found
    ValueError: PDF could not be read: PdfStreamError

Причина не в pypdf. У PDF таблица перекрёстных ссылок лежит в КОНЦЕ файла:
срезав хвост, срезаешь оглавление всего документа, и читать становится
нечего — не «меньше текста», а ноль. Для HTML обрезка безобидна, начало
страницы остаётся. Мерить оба случая одним потолком нельзя, и это та самая
разница, которой не было видно, пока список типов держал PDF снаружи.

Отсюда два правила, и оба проверяются ниже: у PDF свой потолок чтения, а
если документ не влез и в него — ошибка называет ПРИЧИНУ, а не роняет
чужое имя исключения в лицо читателю.
"""
from __future__ import annotations

import io

import pytest

from tools import web_fetch
from tools.web_fetch import WebFetchTool

_URL = "https://1.1.1.1/attention.pdf"


class _Response:
    """Отдаёт ровно столько байт, сколько попросили, и считает запрошенное."""

    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._buf = io.BytesIO(body)
        self.headers = {"Content-Type": "application/pdf"}
        self.asked_for: int | None = None

    def read(self, n: int = -1) -> bytes:
        self.asked_for = n
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


def test_a_pdf_too_large_says_why_instead_of_crashing(monkeypatch) -> None:
    """Не влезло — так и скажи, и назови причину, а не имя чужого сбоя."""
    monkeypatch.setattr(web_fetch, "PDF_MAX_BYTES", 1024)
    tool = WebFetchTool(opener=_Opener(_Response(b"%PDF-1.5" + b"\0" * 4096)),
                        max_bytes=512)
    with pytest.raises(ValueError) as caught:
        tool.run(_URL)
    message = str(caught.value)
    assert "truncated" in message.lower(), message
    assert "PdfStreamError" not in message, message
