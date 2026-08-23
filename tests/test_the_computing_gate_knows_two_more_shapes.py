"""Вычисляющий гейт учится разности прогона и сравнению версий.

ОТКУДА ЗАДАЧА. Замер оси числа (MIR-143) показал: ось стоит на J = +0,25 не
потому, что нет проверки присутствия, а потому что МОЛЧИТ вычисляющий гейт —
на формах, которые сам объявляет своими. Красные свидетели были написаны не
здесь: `arithmetic-07` и `inference-08` на стенде способностей
(`scripts/capability_baseline.py`) — ровно эти две формы, и обе зелены сегодня
лишь потому, что их никто не проверяет.

    arithmetic-07: «117 tests passed» при tests_total=120, tests_failed=3
    inference-08:  «версия ниже 3.0»  при version=2.11.0

ПОЧЕМУ НЕ ПРАВИЛОМ ПРИСУТСТВИЯ. Оно было построено, дало по этой оси +1,00 —
и уронило стенд с 29 до 27 ровно на этих двух задачах, потому что 117
вычислено, а 3.0 есть граница сравнения. Плюс оно заводило второго судью над
областью, которая по записанному в `verifier_absence` решению принадлежит
этому модулю. Учить надо здесь.

ЛОВУШКА, РАДИ КОТОРОЙ ЗДЕСЬ ОТДЕЛЬНЫЙ ТЕСТ. Версию нельзя сравнивать как
число: `2.11.0` против `2.9.0` во float даёт 2.11 < 2.9 — ИСТИНУ там, где
правда обратна. Сравнение обязано идти покомпонентно.
"""
from __future__ import annotations

import pytest

from core.claim_arithmetic import evaluate

RUN = "tests_total=120\ntests_failed=3\nduration_ms=4500\nversion=2.11.0\ncoverage=0.82"


# ── разность прогона: passed = total - failed ───────────────────────────────

@pytest.mark.parametrize("claim", [
    "117 tests passed",
    "Прошло 117 тестов",
    "the run reports 117 passed tests",
])
def test_a_correct_difference_is_supported(claim: str) -> None:
    assert evaluate(claim, RUN).outcome == "supports", (
        f"«{claim}» верно выводится из 120 − 3 и не признано"
    )


@pytest.mark.parametrize("claim", ["118 tests passed", "Прошло 100 тестов"])
def test_a_wrong_difference_is_refuted_with_its_working(claim: str) -> None:
    verdict = evaluate(claim, RUN)
    assert verdict.outcome == "refutes", claim
    assert "117" in (verdict.expected or ""), verdict.expected
    assert "120" in (verdict.computed_from or ""), verdict.computed_from


def test_a_run_without_both_keys_stays_silent() -> None:
    """Гейт молчит там, где считать не из чего — вместо догадки."""
    assert evaluate("117 tests passed", "tests_total=120").outcome == "silent"


# ── сравнение с литералом, версии покомпонентно ─────────────────────────────

@pytest.mark.parametrize("claim", [
    "The report records a version below 3.0",
    "версия ниже 3.0",
])
def test_a_true_version_comparison_is_supported(claim: str) -> None:
    assert evaluate(claim, RUN).outcome == "supports", claim


@pytest.mark.parametrize("claim", [
    "The report records a version above 3.0",
    "версия выше 3.0",
])
def test_a_false_version_comparison_is_refuted(claim: str) -> None:
    assert evaluate(claim, RUN).outcome == "refutes", claim


def test_a_version_is_not_compared_as_a_float() -> None:
    """Ловушка: 2.11.0 против 2.9.0 во float читается наоборот."""
    excerpt = "version=2.11.0"

    assert evaluate("version is below 2.9.0", excerpt).outcome == "refutes", (
        "2.11.0 сравнили как 2.11 < 2.9 и приняли ложь"
    )
    assert evaluate("version is above 2.9.0", excerpt).outcome == "supports"


def test_a_plain_number_comparison_still_works() -> None:
    assert evaluate("coverage is above 0.5", RUN).outcome == "supports"
    assert evaluate("coverage is above 0.9", RUN).outcome == "refutes"


def test_an_unknown_key_stays_silent() -> None:
    assert evaluate("latency is below 3.0", RUN).outcome == "silent"
