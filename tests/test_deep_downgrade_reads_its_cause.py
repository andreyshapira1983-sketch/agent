"""«Причина отсутствует» — не то же самое, что «причина не годится».

ЖИВОЙ СЛУЧАЙ 2026-08-10. Самый тяжёлый запрос сессии проехал с
`route_reason=deep_downgraded:missing_reason`. Код читается как сбой учёта, а
означал он штатное: оператор глубокий тир не запрашивал вовсе.

Модуль уже применил этот принцип к состоянию каталога моделей — там сказано
прямо: «один и тот же исход, разная инструкция; сообщить первое, когда верно
второе, стоит расследования». К самой причине эскалации принцип применён не
был: `reason=None` и `reason="banana"` давали один код.
"""
from __future__ import annotations

from core.deep_escalation import DeepEscalationRequest, evaluate_deep_escalation


def _request(**kw) -> DeepEscalationRequest:
    base = {
        "role": "planner",
        "deep_model_available": True,
        "budget_ok": True,
        "expected_output": None,
    }
    base.update(kw)
    return DeepEscalationRequest(**base)


def test_nobody_asked_is_not_reported_as_a_broken_request() -> None:
    """ГЛАВНОЕ: штатный путь не имеет права выглядеть неисправностью."""
    decision = evaluate_deep_escalation(_request(reason=None))
    assert decision.effective_tier != "deep"
    assert decision.route_reason == "deep_downgraded:not_requested", (
        f"штатное отсутствие запроса доложено как {decision.route_reason!r}"
    )


def test_an_unusable_reason_is_still_reported_as_such() -> None:
    """Ломка наоборот: развести два случая — не значит замолчать второй."""
    decision = evaluate_deep_escalation(_request(reason="просто хочу опус"))
    assert decision.effective_tier != "deep"
    assert decision.route_reason == "deep_downgraded:unknown_reason"


def test_an_empty_string_counts_as_not_requested() -> None:
    """Пустая строка приходит от разбора ввода и означает то же, что None."""
    assert evaluate_deep_escalation(_request(reason="  ")).route_reason == (
        "deep_downgraded:not_requested"
    )


def test_a_valid_reason_still_passes() -> None:
    """ПРЕДУСЛОВИЕ: ворота не заклинило в положении «понизить»."""
    from core.deep_escalation import ACTIVE_REASONS, EXPECTED_OUTPUTS

    decision = evaluate_deep_escalation(_request(
        reason=sorted(ACTIVE_REASONS)[0],
        expected_output=sorted(EXPECTED_OUTPUTS)[0],
    ))
    assert decision.effective_tier == "deep"
