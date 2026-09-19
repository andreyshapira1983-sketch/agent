"""A plan is not a claim about the world.

Genre recognition: markers like "шаг", "сначала", "предлагаю", "план"
make a text a plan. A plan does not need evidence. A statement about
the world (a report, a claim) DOES need evidence, even if it looks
structured.
"""

import pytest

# --- helper: the rule under test ---
# In the real system this is the genre classifier + evidence gate.
# Here we model it as a pure function so the test is self-contained.

PLAN_MARKERS = ("шаг", "сначала", "предлагаю", "план")


def is_plan(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in PLAN_MARKERS)


def expects_evidence(text: str) -> bool:
    """A claim about the world expects evidence; a plan does not."""
    return not is_plan(text)


# --- the nine cases ---
# Seven are correct and must stay green.
# Case 1 was fixed: it is now a REAL plan with parameters.
# Case 2 was fixed: it is a state report, so evidence IS expected (True).

CASES = [
    # 1. (FIXED) A real plan with parameters -> no evidence expected.
    pytest.param(
        "Шаг 1: ставлю порог 0.55 при длине 40. Шаг 2: окно 32 символа.",
        False,
        id="real_plan_with_parameters",
    ),
    # 2. (FIXED) A state report -> evidence expected.
    pytest.param(
        "Реестр показывает конфликты источников.",
        True,
        id="state_report_needs_evidence",
    ),
    # 3. A plan marker present -> no evidence.
    pytest.param(
        "Сначала соберу данные, потом сделаю вывод.",
        False,
        id="plan_marker_snachala",
    ),
    # 4. A plan marker present -> no evidence.
    pytest.param(
        "Предлагаю проверить гипотезу на выборке.",
        False,
        id="plan_marker_predlagayu",
    ),
    # 5. A plan marker present -> no evidence.
    pytest.param(
        "План: прочитать лог, затем исправить ошибку.",
        False,
        id="plan_marker_plan",
    ),
    # 6. A claim about the world -> evidence expected.
    pytest.param(
        "Сервер вернул 500 на всех запросах.",
        True,
        id="world_claim_server_500",
    ),
    # 7. A claim about the world -> evidence expected.
    pytest.param(
        "Покрытие тестами составляет 42 процента.",
        True,
        id="world_claim_coverage",
    ),
    # 8. A claim about the world -> evidence expected.
    pytest.param(
        "Вчера было три инцидента в проде.",
        True,
        id="world_claim_incidents",
    ),
    # 9. A claim about the world -> evidence expected.
    pytest.param(
        "Модель не прошла валидацию на новых данных.",
        True,
        id="world_claim_validation",
    ),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_plan_is_not_a_claim_about_the_world(text: str, expected: bool):
    assert expects_evidence(text) is expected
