"""MIR-060, один доказанный класс: ссылка разрешилась — литерал не появился.

ЭКСПЕРИМЕНТ 2026-08-10, `experiments/mir060_adversarial_test.py`, шесть
состязательных случаев против настоящего `core.verifier.verify`. Пять из шести
воспроизвели дефект: ссылка разрешается, истина «нет», вердикт `verified`.

УКУС. Единственная мутация — `core/verifier_core.py:214`, `strict_ok = True` →
`False` — перевела ВСЕ ШЕСТЬ, включая истинный контроль, в
`topic_supported_but_claim_unverified`. Значит истинность утверждения не
устанавливает ничто: вердикт целиком держится на умолчании, выданном за
разрешение ссылки, а три существующих гейта (независимость памяти, арифметика,
статистические цифры) умеют только отнимать и ни разу не сработали.

ЧТО ЧИНИТСЯ ЗДЕСЬ. Четвёртый гейт того же вида: отличительные ЛИТЕРАЛЫ
утверждения — идентификаторы моделей и прогонов, SHA, пути — обязаны
встречаться в цитируемой улике. Это ровно тот вред, который наблюдался живьём:
ответ назвал `claude-3-7-sonnet-20250219`, телеметрия того же прогона —
`claude-sonnet-4-5`, и верификация доложила 24 из 24 подтверждённых.

ЧТО НЕ ЧИНИТСЯ И НЕ ОБЪЯВЛЯЕТСЯ ПОЧИНЕННЫМ. Случаи 3–6 эксперимента — чужой
референт, протухшее состояние, усиление «настроен»→«работает», отрицание — этот
гейт не ловит и не может: там нужен вывод следования, а не совпадение строк.
Замерено, а не предположено (см. матрицу в эксперименте).

ГОЛЫЕ ЧИСЛА НАМЕРЕННО ВНЕ ОБРАЗЦА: счёт, сумма и сравнение принадлежат
`evaluate_claim_arithmetic`, и второй судья над той же областью спорил бы с
первым.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify


def _chain(excerpt: str, source_id: str, kind: str = "file") -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind=kind, source_id=source_id, obtained_via="test",
        claim="", excerpt=excerpt, confidence=0.9,
    ))
    return chain


def _answer(claim: str, citation: str) -> str:
    return (
        f"Conclusion: {claim} [{citation}]\n"
        f"Facts:\n- {claim} [{citation}]\n"
        f"Sources:\n1. {citation} - источник\n"
        "Confidence: high\nUnverified: nothing\n"
    )


def _verdicts(claim: str, excerpt: str, source_id: str = "core/probe.py") -> list[str]:
    report = verify(
        answer=_answer(claim, f"file:{source_id}"),
        chain=_chain(excerpt, source_id),
        user_question="проверь утверждение",
    )
    return [c.verdict for c in report.chunks if c.verdict != "structural"]


def test_the_live_harm_is_no_longer_verified() -> None:
    """ГЛАВНОЕ: ровно тот случай, что прошёл проверку 24 из 24."""
    verdicts = _verdicts(
        "Архитектура использует claude-3-7-sonnet-20250219.",
        "model=claude-sonnet-4-5 provider=anthropic role=planner",
    )
    assert "verified" not in verdicts, (
        f"модель, которой нет в улике, снова подтверждена: {verdicts}"
    )


def test_a_foreign_run_identifier_is_no_longer_verified() -> None:
    """Второй живой предмет: идентификатор чужого прогона."""
    verdicts = _verdicts(
        "В прогоне run_860e7ef70936901 записано 92 события.",
        "run_id=run_adcfda2fc0bfd50 events=92",
    )
    assert "verified" not in verdicts


def test_the_true_control_stays_verified() -> None:
    """ПРЕДУСЛОВИЕ: гейт не превращается в «ничего не подтверждать».

    Мутация показала, что при `strict_ok=False` истинный контроль тоже
    перестаёт подтверждаться. Починка обязана этого НЕ делать.
    """
    verdicts = _verdicts(
        "Архитектура использует claude-sonnet-4-5.",
        "model=claude-sonnet-4-5 provider=anthropic role=planner",
    )
    assert "verified" in verdicts, f"истинное утверждение потеряно: {verdicts}"


def test_a_matching_path_stays_verified() -> None:
    verdicts = _verdicts(
        "Модуль core/loop.py реализует цикл ответа.",
        "core/loop.py: цикл наблюдения и ответа",
    )
    assert "verified" in verdicts


def test_a_foreign_path_is_not_verified() -> None:
    verdicts = _verdicts(
        "Модуль core/ghost_module.py реализует цикл ответа.",
        "core/loop.py: цикл наблюдения и ответа",
    )
    assert "verified" not in verdicts


def test_prose_without_literals_is_untouched() -> None:
    """Гейт молчит там, где судить нечего — как и арифметический."""
    verdicts = _verdicts(
        "Компонент работает штатно и обрабатывает запросы.",
        "Component C\nStatus: configured\nEnabled: true",
    )
    assert "verified" in verdicts


def test_counting_claims_stay_with_arithmetic() -> None:
    """Голые числа не отбираются у арифметики: два судьи спорили бы."""
    verdicts = _verdicts(
        "В файле 3 функции.",
        "def a():\n    pass\ndef b():\n    pass\ndef c():\n    pass",
    )
    assert "verified" in verdicts


@pytest.mark.parametrize("claim,excerpt", [
    ("Ветка собрана на 20698b1e4c29f", "HEAD=aff6be7c1d2e3f4"),
    ("Файл config/model_catalog.json просрочен", "config/models.yaml: fresh"),
    ("Запись AWS_SECRET_KEY отсутствует", "GITHUB_TOKEN отсутствует"),
])
def test_other_absent_literals_are_caught(claim: str, excerpt: str) -> None:
    """Опровержение: класс, а не три заученных предмета."""
    assert "verified" not in _verdicts(claim, excerpt)


def test_the_gate_only_subtracts() -> None:
    """Дисциплина всех четырёх гейтов: отнимать можно, добавлять нельзя.

    Утверждение без разрешённой ссылки не становится подтверждённым оттого,
    что его литералы случайно совпали с какой-то уликой в цепочке.
    """
    report = verify(
        answer=(
            "Conclusion: core/loop.py реализует цикл.\n"
            "Facts:\n- core/loop.py реализует цикл.\n"
            "Sources:\n1. none - нет\nConfidence: low\nUnverified: всё\n"
        ),
        chain=_chain("core/loop.py: цикл", "core/loop.py"),
        user_question="что делает модуль",
    )
    assert "verified" not in [c.verdict for c in report.chunks]
