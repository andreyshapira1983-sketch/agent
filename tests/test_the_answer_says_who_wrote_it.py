"""Подмена модели обязана быть видна там, где читают ответ.

Background: docs/CODE_NOTES.md, "The answer was not written by the model you
chose".
"""
from __future__ import annotations

from dataclasses import dataclass

from core.degraded_route import substituted_routes, substitution_notice

#: Дословно из живого сеанса 2026-08-15.
_CREDIT_ERROR = (
    "BadRequestError: Error code: 400 - {'type': 'error', 'error': "
    "{'type': 'invalid_request_error', 'message': 'Your credit balance is too "
    "low to access the Anthropic API. Please go to Plans & Billing to upgrade "
    "or purchase credits.'}, 'request_id': 'req_011Ce3wcgRk6yesGnKEVYn6H'}"
)


@dataclass
class _Row:
    role: str
    provider: str
    model: str
    route_reason: str
    status: str
    error: str | None = None
    run_id: str | None = "run_1"


def _measured_session() -> list[_Row]:
    """Ровно то, что было: отказ основной и успех запасной, две роли."""
    rows: list[_Row] = []
    for role in ("planner", "synthesizer"):
        rows.append(_Row(role, "anthropic", "claude-sonnet-5",
                         "complexity:standard:anthropic", "error", _CREDIT_ERROR))
        rows.append(_Row(role, "openai", "gpt-4o-mini",
                         "provider_failover:anthropic->openai", "success"))
    return rows


def test_the_measured_session_is_disclosed():
    """Пять ходов оператор спорил с подменой о её же способностях, потому что
    в ответе об этом не было ни слова.
    """
    notice = substitution_notice(substituted_routes(_measured_session(), run_id="run_1"))

    assert "gpt-4o-mini" in notice
    assert "claude-sonnet-5" in notice


def test_a_substitution_with_no_refusal_this_turn_is_still_disclosed():
    """Роутер мог остаться на запасном после отказа в ПРОШЛОМ ходе. Причину
    назвать нечем, но молчание тут и есть тот самый дефект.
    """
    rows = [_Row("synthesizer", "openai", "gpt-4o-mini",
                 "provider_failover:anthropic->openai", "success")]

    notice = substitution_notice(substituted_routes(rows, run_id="run_1"))

    assert "gpt-4o-mini" in notice
    assert notice.endswith("Качество этого ответа — её, а не основной.")


def test_the_notice_survives_the_printer():
    """Живая поломка 2026-08-15, ПОСЛЕ первой сборки: предупреждение легло в
    черновик (`contributions=[…{'author': 'degraded_route', 'chars': 272}]`,
    `rendered_chars=1556`) и не дошло до печати.

    `format_human_response` собирает ответ по секциям и выбрасывает всё, чего
    не узнала по фиксированному префиксу. На этом уже погибал хвост проверки —
    ради этого и заведён `TAIL_PREFIX`. Тест держит живой путь целиком, потому
    что все семь тестов выше были зелёными, когда оператор не увидел ни слова.
    """
    from core.answer_format import format_human_response

    notice = substitution_notice(substituted_routes(_measured_session(), run_id="run_1"))
    answer = (
        "Conclusion: эпизодная память хранит случаи [general-knowledge]\n"
        "Facts: - процедурная хранит способы [general-knowledge]\n"
        "Sources: none\nConfidence: low\n"
        f"{notice}"
    )

    printed = format_human_response(answer)

    assert "gpt-4o-mini" in printed, "предупреждение снова не дошло до оператора"
    assert "credit balance is too low" in printed


def test_a_provider_skipped_by_health_is_disclosed_without_inventing_a_call():
    rows = [
        _Row(role, "deepseek", "deepseek-chat",
             "provider_unhealthy:openai:3_consecutive_key_errors_within_15m"
             "->deepseek|measured:role", "success")
        for role in ("planner", "synthesizer")
    ]
    routes = substituted_routes(rows, run_id="run_1")
    notice = substitution_notice(routes)
    assert len(routes) == 2
    assert {r.intended for r in routes} == {"openai"}
    assert all(r.refusals == 0 for r in routes)
    assert "deepseek/deepseek-chat" in notice
    assert "3_consecutive_key_errors_within_15m" in notice
    assert "gpt-6-astra" not in notice, "the skipped model is not recorded by this event"
    assert "отказов:" not in notice, "historical health is not a failed call this turn"
