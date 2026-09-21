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


def test_an_unverified_section_saying_nothing_counts_nothing() -> None:
    """2026-09-21, «пересчитай функции»: «Ничего — подтверждено AST» считалось
    непроверенным пунктом, и уверенность занижалась при пустом разделе."""
    report = verify(answer=_answer(
        "Ничего — количество и имена функций подтверждены прямым измерением AST."),
        chain=_chain())
    assert report.admitted_unverified_chunks == 0


def test_nothing_admitted_changes_nothing() -> None:
    chain = _chain()
    report = verify(answer=_answer("- (нет)").replace("Unverified:\n- (нет)\n", ""),
                    chain=chain)
    assert report.admitted_unverified_chunks == 0
    tail = build_verification_summary(report, chain=chain).tail
    assert "назвал непроверенными" not in tail


def test_a_parenthesis_with_its_own_counts_is_not_an_enumeration() -> None:
    """2026-09-21, «испорченный файл»: верное «5 и 5» с пояснением в скобках
    получило [claim-refuted] — скобка прочитана как перечень из двух."""
    from core.verifier_utils import enumeration_count_reason

    claim = ("Маркер A встречается в файле 5 раз, маркер B — 5 раз (по данным поиска — "
             "10 совпадений, из них 5 строк с A и 5 с B).")
    assert enumeration_count_reason(claim) is None
    assert enumeration_count_reason("Нашёл 3 файла (a.py, b.py).") is not None


def test_dialogue_support_is_named_in_the_tail() -> None:
    """2026-09-21, «помнишь разговор?»: хвост «0 из 11, нулевая» читался как ответ
    без опоры, хотя все утверждения стояли на записи беседы (MIR-028: это не
    внешнее подтверждение, но и не пустота — назвать)."""
    from types import SimpleNamespace

    from core.verifier_models import ClaimChunk, VerificationReport

    chunks = tuple(ClaimChunk(text=f"c{i}", citations=(), matched_evidence_ids=(),
                              verdict="dialogue_supported") for i in range(3))
    report = VerificationReport(total_chunks=3, verified_chunks=0, unverified_chunks=0,
                                cited_but_unmatched_chunks=0, self_declared_chunks=0,
                                structural_chunks=0, chunks=chunks, annotated_answer="",
                                fully_unverified=True, chain_was_empty=False,
                                dialogue_supported_chunks=3)
    tail = build_verification_summary(report, chain=None, vector=SimpleNamespace()).tail
    assert "из них 3 — по записи этого разговора" in tail
