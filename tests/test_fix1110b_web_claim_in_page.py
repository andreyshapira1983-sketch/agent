"""Утверждение, чей литерал дословно стоит в скачанной странице, подтверждается — и только оно."""
from __future__ import annotations

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.verifier import verify

_URL = "https://www.python.org/downloads/"
_NAV = "Python Books \n Python Essays \n Community \n Diversity \n Mailing Lists \n IRC \n Forums \n " * 20
_BODY = (" Download the latest source release \n Download Python 3.15.2 \n"
         " Python version \n Maintenance status \n 3.15 \n bugfix \n 2026-10-09 \n"
         " Release version \n Release date \n Python 3.15.2 \n Oct. 25, 2026 \n Release notes \n")
_TAIL = " Python 3.12.1 \n Dec. 8, 2023 \n Download \n Release notes \n" * 40


def _chain(text: str) -> ProvenanceChain:
    ev = evidence_from_tool_result(
        tool_name="web_fetch", arguments={"url": _URL}, status="success",
        output={"url": _URL, "text": text, "content_hash": "ab" * 32,
                "fetched_at": "2026-11-10T13:20:54+00:00"})
    assert ev is not None
    return ProvenanceChain([ev])


def _verdict(claim: str, text: str) -> str:
    answer = f"Conclusion:\n{claim} [web:{_URL}].\n"
    report = verify(answer=answer, chain=_chain(text))
    assert report.total_chunks == 1, report.annotated_answer
    return report.chunks[0].verdict


def test_the_page_is_longer_than_the_stored_excerpt() -> None:
    page = _NAV + _BODY + _TAIL
    ev = _chain(page).evidences[0]
    assert page.index("3.15.2") > 800 and "3.15.2" not in ev.excerpt
    assert "source_text" not in ev.to_dict(), "полный текст не идёт на диск"


def test_a_version_standing_deep_in_the_page_is_verified() -> None:
    page = _NAV + _BODY + _TAIL
    assert _verdict("Последняя стабильная версия Python — 3.15.2", page) == "verified"


def test_a_version_absent_from_the_page_is_not_verified() -> None:
    page = _NAV + _BODY + _TAIL
    assert "3.15.7" not in page
    assert _verdict("Последняя стабильная версия Python — 3.15.7", page) != "verified"


def test_a_version_the_page_has_only_as_part_of_another_is_not_verified() -> None:
    page = _NAV + _BODY.replace("3.15.2", "3.15.20") + _TAIL
    assert _verdict("Последняя стабильная версия Python — 3.15.2", page) != "verified"


def test_the_find_header_is_the_agents_words_not_the_page() -> None:
    page = "[find '3.15.7': no match in 9000 chars; page head follows]\n" + _NAV + _BODY + _TAIL
    assert _verdict("Последняя стабильная версия Python — 3.15.7", page) != "verified"


def test_a_page_about_something_else_does_not_verify_by_topic() -> None:
    page = _NAV + " Python is a programming language. Download Python today. \n" * 30
    assert _verdict("Последняя стабильная версия Python — 3.15.2", page) != "verified"


def test_a_short_page_is_judged_as_before() -> None:
    assert _verdict("Последняя стабильная версия Python — 3.15.2", _BODY) == "verified"
    assert _verdict("Последняя стабильная версия Python — 3.15.7", _BODY) != "verified"
