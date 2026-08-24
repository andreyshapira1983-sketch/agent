"""Выбор цели от хартии — платный вызов, и он обязан считаться деньгами.

ИСТОРИЧЕСКИЙ КЛАСС (H-27, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Семейство «внешний эффект произошёл, а его учёт — нет»: банковский перевод без
проводки, отправленное письмо без записи в очереди. Здесь эффект — оплаченный
вызов модели, а учёт — суточный и недельный потолок в `budget_ledger`.

ЖИВОЙ ЗАМЕР 2026-08-24 по `data/`. Резервов `llm_calls` — 1364, строк расхода —
1379. Разрыв в 15 строк не объясняется историей (строк старше первого резерва
ноль) и весь лежит в openai: 659 записей против 644 резервов, и ровно те же 15
строк отсутствуют среди `model_cost_units`. По дням разрыв стоит только
19 августа (+13) и 20-го (+2); отказов anthropic в те дни ноль, то есть
переключение провайдера ни при чём — а 14–17-го, при 391 отказе, счета сошлись
точь-в-точь.

ПРИЧИНА. `agent_tick` строил леджер для выбора цели своей строкой, мимо
`app/bootstrap.py`, где леджер получает `budget_ledger=`. И резерв, и запись
стоимости стоят под `if self.budget_ledger is not None`, поэтому вызов писал
строку расхода и оставался невидим для потолков.

ПОЧЕМУ ЭТО ПЕРЕЖИЛО ПРЕДЫДУЩУЮ ПОЧИНКУ. Комментарий на месте правки называет
вскрытие 19 августа: тогда нашли вызовы ВОВСЕ без леджера и подключили леджер
расхода. Измеряли видимость в учёте — её и починили; вторую половину, видимость
для потолка, никто не мерил. Класс «покрытие списали у соседа».
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core.model_usage import ModelBudgetExceeded


def _workspace(tmp_path: pathlib.Path, *, calls_limit: int) -> pathlib.Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "budget_limits.json").write_text(
        json.dumps({"windows": {"day": {"llm_calls": calls_limit}}}),
        encoding="utf-8",
    )
    return tmp_path


def _exhaust(workspace: pathlib.Path, *, calls: int) -> None:
    """Израсходовать суточный потолок ЧЕРЕЗ сам леджер, а не подделкой файла.

    Строка бюджета несёт хеш целостности; написанная руками, она была бы
    отвергнута чтением, и тест позеленел бы по чужой причине.
    """
    from core.budget_ledger import BudgetLedger

    ledger = BudgetLedger.from_env(
        path=workspace / "data" / "budget_ledger.jsonl",
        config_path=workspace / "config" / "budget_limits.json",
    )
    for _ in range(calls):
        ledger.reserve("llm_calls", amount=1, reason="предыдущий расход")


def test_the_charter_goal_router_is_stopped_by_an_exhausted_cap(tmp_path) -> None:
    import agent_tick

    ws = _workspace(tmp_path, calls_limit=2)
    _exhaust(ws, calls=2)

    router = agent_tick._charter_goal_router(ws)
    ledger = router.usage_ledger

    assert ledger.budget_ledger is not None, (
        "леджер выбора цели не несёт бюджета — вызов невидим для потолка"
    )
    with pytest.raises(ModelBudgetExceeded):
        ledger.assert_can_start(
            role="planner", provider="openai", model="gpt",
            system="выбери цель", user="хартия", max_output_tokens=200,
            cost_tier="medium",
        )


def test_the_same_router_still_allows_a_call_inside_the_cap(tmp_path) -> None:
    """Контроль: проба обязана уметь показывать положительный исход.

    Без него зелёный первый тест ничего не значил бы — он краснел бы и на
    сломанном леджере, и на любой посторонней ошибке построения.
    """
    import agent_tick

    ws = _workspace(tmp_path, calls_limit=50)
    _exhaust(ws, calls=1)

    router = agent_tick._charter_goal_router(ws)
    router.usage_ledger.assert_can_start(
        role="planner", provider="openai", model="gpt",
        system="выбери цель", user="хартия", max_output_tokens=200,
        cost_tier="medium",
    )
