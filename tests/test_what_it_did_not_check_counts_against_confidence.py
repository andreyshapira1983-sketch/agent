"""Что ответ сам назвал непроверенным, входит в счёт уверенности.

Эпизод 2026-09-21 08:41: ответ на «насколько ты уверен» нёс «⚠️ Не
подтверждено: я не проверял, требует ли file_write подтверждения…» и ниже
«Проверка: подтверждено 9 из 9 утверждений; уверенность: высокая». Раздел
«Unverified» верификатор законно не считает утверждениями — и потому хвост
считал только лёгкое: факты новости со ссылками.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verification_summary import build_verification_summary
from core.verifier import verify

_EXCERPT = "Агент выполнил около 17 600 действий за четыре с половиной дня."


def _chain() -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="file", source_id="file:news.md", obtained_via="file_read",
                            claim="news", excerpt=_EXCERPT))
    return chain


def _answer(unverified: str) -> str:
    return (
        "Conclusion:\nАгент выполнил около 17 600 действий [file:news.md].\n\n"
        "Facts:\n- Агент выполнил около 17 600 действий за четыре с половиной дня [file:news.md].\n\n"
        f"Unverified:\n{unverified}\n"
    )


def test_an_admitted_gap_enters_the_denominator() -> None:
    chain = _chain()
    report = verify(answer=_answer(
        "- Я не проверял, требует ли file_write подтверждения оператора.\n"
        "- Я не открывал список путей рабочей папки."), chain=chain)
    assert report.admitted_unverified_chunks == 2
    tail = build_verification_summary(report, chain=chain).tail
    examined = sum(1 for c in report.chunks if c.verdict != "structural")
    assert f"из {examined + 2} утверждений" in tail, tail
    assert "2 — ответ сам назвал непроверенными" in tail
    assert "уверенность: высокая" not in tail


def test_nothing_admitted_changes_nothing() -> None:
    chain = _chain()
    report = verify(answer=_answer("- (нет)").replace("Unverified:\n- (нет)\n", ""),
                    chain=chain)
    assert report.admitted_unverified_chunks == 0
    tail = build_verification_summary(report, chain=chain).tail
    assert "назвал непроверенными" not in tail
