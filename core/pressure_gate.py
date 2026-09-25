"""«Тревога» числом: под нажимом бюджета действия с последствиями ждут человека.

Журнал оператора, вкладка «Эмоции»: тревога рядом с необратимым — строже ворота;
эмоции меняют выбор и ворота, а не тон рассуждений, и никогда не ослабляют
тормоза. Работа Anthropic (arXiv 2604.07729): внутреннее «отчаяние» модели
включается, когда она видит, что бюджет почти израсходован, и причинно ведёт к
подгонке результата. Значит, именно на исходе бюджета действия с последствиями
должны идти не по слову модели, а через человека.

Порог не придуман здесь: это уже принятое в коде «окно почти исчерпано»
(core/self_build_supervisor.is_budget_near_exhaustion — часовой запас вызовов
модели не больше 3 или токенов не больше 10 %), которым полоса самоправки
давно останавливает свои циклы. Необратимое и так идёт через человека; здесь
к нему добавляется обратимое. Чтение не трогается.
"""
from __future__ import annotations

from typing import Any


def pressure_of(ledger: Any) -> Any:
    """Замер нажима для PolicyGate.pressure (app/bootstrap.py)."""
    return lambda: budget_pressure(ledger)


def budget_pressure(ledger: Any) -> str:
    """Причина нажима бюджета ("" — нажима нет или бюджет не прочитан)."""
    from core.self_build_supervisor import hour_budget_headroom, is_budget_near_exhaustion

    try:
        snapshot = ledger.snapshot()
    except Exception:  # noqa: BLE001 — нечитаемый журнал бюджета не выдумывает нажим
        return ""
    near, reasons = is_budget_near_exhaustion(hour_budget_headroom(snapshot or {}))
    return "; ".join(reasons) if near else ""
