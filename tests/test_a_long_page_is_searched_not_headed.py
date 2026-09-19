"""A long web page is searched for what the task needs, not shown by its head.

Web-knowledge chain, 2026-09-20: the RFC 9114 page was fetched whole
(146 111 chars), yet the model saw ~1.8k — paragraphs were picked by the words
of the RUSSIAN question, the page is English, nothing matched, and the head
(title and version list) came back. The same happened to the Goedel page the
evening before (1.8k of 124k). A book has find_in_files; a page had nothing.
`web_fetch(find=...)` returns the text around each match.
"""
from __future__ import annotations

from core.step_sanitizer import sanitize_step
from tests.test_web_fetch import _opener_with, _resolver_for
from tools.web_fetch import WebFetchTool, find_windows

_PAGE = ("HTTP/3 RFC 9114 header and version list. " * 60
         + "Section 6. Stream Mapping: HTTP/3 relies on QUIC stream multiplexing, "
           "so a lost packet on one stream does not block the others. "
         + "Filler about unrelated topics. " * 200)


def test_the_windows_hold_the_match_and_say_where_they_are() -> None:
    shown = find_windows(_PAGE, "stream multiplexing|multiplex")
    assert shown.startswith("[find 'stream multiplexing|multiplex': ")
    assert "relies on QUIC stream multiplexing, so a lost packet" in shown
    assert len(shown) < len(_PAGE) // 2, "окна, а не вся страница"


def test_no_match_is_said_and_the_page_is_kept() -> None:
    shown = find_windows(_PAGE, "flow control credit")
    assert shown.startswith("[find 'flow control credit': no match in ")
    assert shown.endswith(_PAGE)


def test_the_tool_returns_the_windows_and_the_full_length() -> None:
    body = f"<html><body><p>{_PAGE}</p></body></html>".encode()
    tool = WebFetchTool(opener=_opener_with(body), resolver=_resolver_for("1.1.1.1"))
    out = tool.run(url="https://example.com/rfc9114", find="stream multiplexing")
    assert "relies on QUIC stream multiplexing" in out["text"][:1500], "совпадение в начале текста, куда смотрит модель"
    assert out["full_length"] > len(out["text"]) and out["find"] == "stream multiplexing"
    plain = WebFetchTool(opener=_opener_with(body), resolver=_resolver_for("1.1.1.1")).run(
        url="https://example.com/rfc9114")
    assert "find" not in plain and plain["text"].startswith("HTTP/3 RFC 9114 header"), "без find — как раньше"


def test_the_sanitizer_lets_find_through() -> None:
    warnings: list[str] = []
    step = sanitize_step("web_fetch", {"url": "https://www.rfc-editor.org/rfc/rfc9114",
                                       "find": " stream multiplexing "}, None, 0, warnings)
    assert step is not None and step["arguments"] == {"url": "https://www.rfc-editor.org/rfc/rfc9114",
                                                      "find": "stream multiplexing"}
    bare = sanitize_step("web_fetch", {"url": "https://www.rfc-editor.org/rfc/rfc9114"}, None, 1, warnings)
    assert bare is not None and bare["arguments"] == {"url": "https://www.rfc-editor.org/rfc/rfc9114"}
