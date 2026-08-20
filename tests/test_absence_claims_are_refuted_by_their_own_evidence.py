"""«Этого там нет» — проверяемое утверждение, если источник процитирован.

ЖИВОЙ СЛУЧАЙ 2026-08-10, прогон `run_f785f6e4`. Агент написал:

    В `core/smart_memory.py` отсутствует класс, метод или поле с именем
    "lesson" — концепция "verified negative lesson" не реализована.

и сослался на `core/smart_memory.py`. В файле **38 вхождений** `lesson`, включая
поле `lessons` отчёта консолидации и правило допуска по тегу. Утверждение
опровергается собственной уликой.

СЛЕДУЮЩИЙ СЛОЙ ОШИБКИ, названный оператором. Литеральный гейт научил не
выдумывать ИМЕНА. Ошибка поднялась на уровень выше: имя не выдумано, выдумано
ОТСУТСТВИЕ. «Неполный поиск» превращается в «доказательство отсутствия», и весь
вердикт прогона (`FAILURE-TO-LESSON PATH IS BROKEN`) построен на этом.

ОДНОСТОРОННОСТЬ, и она здесь принципиальна. Присутствие предмета в выдержке
ОПРОВЕРГАЕТ утверждение об отсутствии. Отсутствие в выдержке не доказывает
ничего: выдержка усечена. Поэтому гейт умеет только отнимать `verified`, как и
четыре соседних, и молчит везде, где предмет не назван явно.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


def _verdicts(claim: str, excerpt: str, source_id: str = "core/probe.py") -> list[str]:
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file", source_id=source_id, obtained_via="test",
        claim="", excerpt=excerpt, confidence=0.9,
    ))
    answer = (
        f"Conclusion: {claim} [file:{source_id}]\n"
        f"Facts:\n- {claim} [file:{source_id}]\n"
        f"Sources:\n1. file:{source_id} - источник\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(answer=answer, chain=chain, user_question="проверь")
    return [c.verdict for c in report.chunks if c.verdict != "structural"]


def test_the_live_specimen_is_refuted() -> None:
    """ГЛАВНОЕ: ровно то утверждение, на котором построен ложный вердикт."""
    verdicts = _verdicts(
        'В файле отсутствует поле с именем "lesson".',
        "def _capped_lessons(...):\n    lessons: tuple[str, ...] = ()\n",
        "core/smart_memory.py",
    )
    assert "verified" not in verdicts, (
        f"утверждение об отсутствии подтверждено уликой, где предмет есть: {verdicts}"
    )


@pytest.mark.parametrize("claim", [
    'Механизм `extract_lesson` не реализован.',
    'Поля "usage_eligible" в схеме нет.',
    'No `lesson` tag exists in this module.',
    'В модуле не найдено `procedure_credit_allowed`.',
])
def test_other_absence_shapes_are_refuted(claim: str) -> None:
    """Класс, а не одна формулировка: русский, английский, разные маркеры."""
    excerpt = (
        "def decide_usage_eligibility(ep):\n"
        '    if "lesson" in ep.tags: return True\n'
        "def procedure_credit_allowed(ep): ...\n"
        "def extract_lesson(ep): ...\n"
        "usage_eligible: bool | None = None\n"
    )
    assert "verified" not in _verdicts(claim, excerpt)


def test_a_true_absence_claim_is_neither_refuted_nor_certified() -> None:
    """ПРЕДОХРАНИТЕЛЬ жив, но третьим состоянием, а не сертификатом.

    Опасение, ради которого этот тест писался, в силе: честное «этого здесь
    нет» наказывать нельзя, иначе агент разучится говорить о границах. Гейт
    опровержения на нём по-прежнему молчит.

    Но и `verified` тут быть не может, и это следует из соседнего теста
    `test_the_gate_only_subtracts`: выдержка усечена по построению. Здесь она
    в две строки — из двух строк не выводится, чего в файле нет. С 2026-08-15
    такое утверждение получает `topic_supported_but_claim_unverified`:
    источник по теме, сертификата у утверждения нет. Это не наказание — в
    `unverified_chunks` оно не попадает, и ответ неподтверждённым не считается
    (docs/CODE_NOTES.md, «Absence was certified by a resolved citation»).
    """
    verdicts = _verdicts(
        'В файле отсутствует поле с именем `quantum_flux`.',
        "def decide_usage_eligibility(ep): ...\nusage_eligible: bool | None = None\n",
    )
    assert "refuted" not in verdicts, f"честное отсутствие объявлено ложью: {verdicts}"
    assert "verified" not in verdicts, f"отсутствие сертифицировано выдержкой: {verdicts}"
    assert "topic_supported_but_claim_unverified" in verdicts


def test_a_positive_claim_is_untouched() -> None:
    """Гейт срабатывает только на УТВЕРЖДЕНИИ ОБ ОТСУТСТВИИ."""
    verdicts = _verdicts(
        'Файл определяет `usage_eligible` как поле схемы.',
        "usage_eligible: bool | None = None\n",
    )
    assert "verified" in verdicts


def test_prose_absence_without_a_named_subject_is_silent() -> None:
    """Без названного предмета судить не о чем — как и в соседних гейтах.

    «Здесь нет обработки ошибок» — суждение о смысле, а не о строке; ловить
    его сверкой подстрок значило бы выдумать судью.
    """
    verdicts = _verdicts(
        "В этом модуле нет настоящей обработки ошибок.",
        "def decide_usage_eligibility(ep): ...\n",
    )
    # Гейт ОПРОВЕРЖЕНИЯ молчит: названного предмета нет, судить не о чем.
    # Сертификата тоже нет — это утверждение об отсутствии, а выдержкой
    # отсутствие не доказывается ни при каком предмете.
    assert "refuted" not in verdicts
    assert "verified" not in verdicts


def test_the_gate_only_subtracts() -> None:
    """Присутствие опровергает отсутствие; обратное неверно.

    Выдержка усечена по построению, поэтому «предмета в выдержке нет» не
    доказывает, что его нет в файле. Гейт обязан молчать в эту сторону.
    """
    from core.verifier_absence import absence_refuted_by_excerpt

    assert absence_refuted_by_excerpt('нет поля "lesson"', "lessons = ()")
    assert not absence_refuted_by_excerpt('нет поля "lesson"', "совсем другой текст")
    assert not absence_refuted_by_excerpt("поле lesson определено", "lessons = ()")
