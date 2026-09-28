"""A plan is not a claim about the world.

Genre recognition: markers like "шаг", "сначала", "предлагаю", "план"
make a text a plan. A plan does not need evidence. A statement about
the world (a report, a claim) DOES need evidence, even if it looks
structured.
"""

import pytest

from core.low_evidence_policy import is_evidence_expected

CASES = [
    # A plan, even one carrying numbers as its parameters -> no evidence expected.
    pytest.param(
        "Шаг 1: ставлю порог 0.55 при длине 40. Шаг 2: окно 32 символа.",
        False,
        id="real_plan_with_parameters",
    ),
    # A state report -> evidence expected.
    pytest.param(
        "Реестр показывает конфликты источников.",
        True,
        id="state_report_needs_evidence",
    ),
    # A plan marker present -> no evidence.
    pytest.param(
        "Сначала соберу данные, потом сделаю вывод.",
        False,
        id="plan_marker_snachala",
    ),
    pytest.param(
        "Предлагаю проверить гипотезу на выборке.",
        False,
        id="plan_marker_predlagayu",
    ),
    pytest.param(
        "План: прочитать лог, затем исправить ошибку.",
        False,
        id="plan_marker_plan",
    ),
    # A claim about the world -> evidence expected.
    pytest.param(
        "Сервер вернул 500 на всех запросах.",
        True,
        id="world_claim_server_500",
    ),
    pytest.param(
        "Покрытие тестами составляет 42 процента.",
        True,
        id="world_claim_coverage",
    ),
    pytest.param(
        "Вчера было три инцидента в проде.",
        True,
        id="world_claim_incidents",
    ),
    pytest.param(
        "Модель не прошла валидацию на новых данных.",
        True,
        id="world_claim_validation",
    ),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_plan_is_not_a_claim_about_the_world(text: str, expected: bool):
    """The real evidence gate, given only the answer: its genre alone decides."""
    assert is_evidence_expected(answer=text) is expected
