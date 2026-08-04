"""Операторское окружение не должно решать, красный тест или зелёный.

Автономный прогон 2026-08-04 доложил «passed=6646 failed=1»; в CI и в чистой
копии тот же набор зелёный. Дважды подряд я объяснил это «устаревшей рабочей
копией» — и оба раза ошибся. Причина другая: в `.env` оператора задана
`AGENT_TIER_PROVIDERS_STANDARD`, она протекает в тесты, и
`test_model_router_for_task_standard_equals_for_role` падает у него, оставаясь
зелёным везде ещё.

Корень — в `tests/conftest.py`: список `_OPERATOR_MODEL_ENV_VARS` ведётся
руками и отстал от того, что реально читает `core/model_router.py`. Такой
список дрейфует молча: переменную добавили в код — про тесты забыли.

Здесь закрепляется сам инвариант, а не отдельная переменная: всё, что роутер
читает из окружения, обязано нейтрализоваться перед тестом.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.conftest import _OPERATOR_MODEL_ENV_VARS

_ROUTER = Path(__file__).resolve().parents[1] / "core" / "model_router.py"

#: Семейства переменных, которые оператор задаёт в .env и которые меняют
#: выбор модели. Именно они ломали прогон у оператора.
_ROUTING_PREFIXES = ("AGENT_TIER_PROVIDERS_", "AGENT_MODEL_TIER_")


def _router_env_names() -> set[str]:
    """Полные имена переменных роутинга, которые упоминает сам роутер.

    Голые префиксы (`AGENT_TIER_PROVIDERS_` в f-строке) отбрасываем: это не
    имена, а заготовки, из которых роутер собирает имя яруса.
    """
    text = _ROUTER.read_text(encoding="utf-8", errors="replace")
    return {
        name for name in re.findall(r"AGENT_[A-Z_]+", text)
        if name.startswith(_ROUTING_PREFIXES) and name not in _ROUTING_PREFIXES
    }


def test_every_routing_variable_the_router_reads_is_neutralised():
    """Список изоляции не имеет права отставать от кода роутера."""
    missing = sorted(_router_env_names() - set(_OPERATOR_MODEL_ENV_VARS))

    assert not missing, (
        "роутер читает эти переменные, а тесты их не нейтрализуют — "
        f"операторский .env будет решать судьбу прогона: {missing}"
    )


@pytest.mark.parametrize("var", ["AGENT_TIER_PROVIDERS_STANDARD", "AGENT_MODEL_TIER_STANDARD"])
def test_the_operator_variable_is_gone_by_the_time_a_test_runs(var: str):
    """Дословная проверка: к телу теста переменной уже нет."""
    assert var not in os.environ, (
        f"{var} дожила до тела теста — прогон зависит от машины оператора"
    )
