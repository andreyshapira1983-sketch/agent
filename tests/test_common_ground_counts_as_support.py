"""Общая почва считается опорой в ОТЧЁТЕ, а не только в воротах.

Задача 3 субботнего списка. Замер: агент выбрал заказ ВЕРНО — брать разбор
PDF, не брать корректуру иврита, которую уже провалил на деле, — а внизу
стояло «подтверждено 0 из 8 утверждений; уверенность: нулевая», потому что все
опоры были из разговора.

Общая почва (common ground), установленная в разговоре, — законная опора для
дальнейшего, и система это УЖЕ признавала в двух местах:
`core.evidence_support.compute_evidence_support` держит её в числителе, и
ворота обрезки тоже (issue #119). А отчёт наказывал её ДВАЖДЫ: исключал из
числителя и записывал в пробелы. Две разные арифметики на одни и те же куски —
прибор, показывающий слово вместо мира.

Измеренная в литературе беда именно такая: модели «локально правильно
подтверждают сказанное, но потом не пользуются установленной общей почвой
надёжно».

Честность сохранена в словах, а не потеряна в числе: хвост называет, сколько
утверждений стоят на записи разговора, И что это не внешний источник.
"""
from __future__ import annotations

from core.verification_summary import build_verification_summary
from core.verifier_core import VerificationReport


class _Chunk:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.matched_evidence_ids: list[str] = []
        self.text = "x"
        self.annotated = "x"


def _report(dialogue: int = 0, verified: int = 0, unverified: int = 0):
    chunks = ([_Chunk("dialogue_supported")] * dialogue
              + [_Chunk("verified")] * verified
              + [_Chunk("unverified")] * unverified)
    return VerificationReport(
        chunks=chunks,
        total_chunks=len(chunks),
        verified_chunks=verified,
        unverified_chunks=unverified,
        cited_but_unmatched_chunks=0,
        self_declared_chunks=0,
        structural_chunks=0,
        annotated_answer="x",
        fully_unverified=(verified == 0 and dialogue == 0),
        chain_was_empty=False,
        dialogue_supported_chunks=dialogue,
    )


def test_an_answer_standing_on_the_conversation_is_not_reported_as_groundless() -> None:
    """Тот самый замеренный случай: восемь опор из разговора."""
    tail = build_verification_summary(_report(dialogue=8)).tail
    assert "подтверждено 8 из 8" in tail
    assert "нулевая" not in tail


def test_the_tail_still_says_it_is_not_an_external_source() -> None:
    """Число выросло, честность осталась В СЛОВАХ.

    Опора на разговор — законная, но НЕ внешнее подтверждение. Молча
    приравнять её к улике значило бы построить ещё один врущий прибор.
    """
    tail = build_verification_summary(_report(dialogue=8)).tail
    assert "по записи этого разговора" in tail
    assert "не внешним источником" in tail


def test_the_conversation_is_counted_once_not_twice() -> None:
    """Опора из разговора уходит из пробелов, когда входит в числитель.

    До 2026-09-23 она была и в пробелах, и вне числителя — наказана дважды.
    """
    tail = build_verification_summary(_report(dialogue=8, unverified=3)).tail
    assert "подтверждено 8 из 11" in tail
    assert "без подтверждения: 3" in tail


def test_an_answer_without_any_support_is_not_softened() -> None:
    """Правка НЕ ослабляет отчёт там, где опоры нет вовсе."""
    tail = build_verification_summary(_report(unverified=5)).tail
    assert "подтверждено 0 из 5" in tail
    assert "нулевая" in tail


def test_external_and_dialogue_support_add_up() -> None:
    tail = build_verification_summary(_report(dialogue=4, verified=4)).tail
    assert "подтверждено 8 из 8" in tail
    assert "из них 4 — по записи этого разговора" in tail
