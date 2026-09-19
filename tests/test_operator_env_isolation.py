"""Операторское окружение не должно решать, красный тест или зелёный.

Прогон 2026-08-04: `AGENT_TIER_PROVIDERS_STANDARD` из `.env` оператора
протекала в тесты и роняла тест роутинга у него одного. Список изоляции в
conftest вёлся руками и отстал от кода роутера.

Инвариант: каждая переменная, которую читает роутер, либо нейтрализуется,
либо стоит в исключениях с причиной.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.conftest import _OPERATOR_MODEL_ENV_PREFIXES, _OPERATOR_MODEL_ENV_VARS

_ROUTER = Path(__file__).resolve().parents[1] / "core" / "model_router.py"

#: Роутер их читает, но тесты не снимают — осознанно. Начнёт ронять прогон
#: у оператора — место такой переменной в списке нейтрализации, не здесь.
_DELIBERATELY_NOT_NEUTRALISED = frozenset({
    # Имена ролей: роутер достраивает из них `AGENT_<РОЛЬ>_PROVIDER/_MODEL`.
    "AGENT_ANSWER", "AGENT_MEMORY", "AGENT_MEMORY_SUMMARY", "AGENT_PLANNER",
    "AGENT_SYNTHESIZER", "AGENT_VERIFIER", "AGENT_REPAIR", "AGENT_REPAIR_PROPOSAL",
    # Флаги поведения роутера, а не выбора модели оператором.
    "AGENT_MODEL_ALLOW_UNAVAILABLE", "AGENT_MODEL_SELECTION_POLICY",
    "AGENT_PROVIDER_FAILOVER",
})


def _router_env_names() -> set[str]:
    """Имена в кавычках — так их пишет код; по всему тексту ловились бы
    упоминания из комментариев. Голые префиксы — заготовки, не имена."""
    text = _ROUTER.read_text(encoding="utf-8", errors="replace")
    return {
        name for name in re.findall(r"[\"'](AGENT_[A-Z_]+)[\"']", text)
        if name not in _OPERATOR_MODEL_ENV_PREFIXES
    }


def test_every_routing_variable_is_neutralised_or_listed_as_an_exception():
    """Список изоляции не имеет права молча отстать от кода роутера."""
    known = set(_OPERATOR_MODEL_ENV_VARS) | _DELIBERATELY_NOT_NEUTRALISED
    unaccounted = sorted(_router_env_names() - known)

    assert not unaccounted, (
        "роутер читает эти переменные, а тесты их не нейтрализуют и не "
        "объявили исключением — операторский .env будет решать судьбу "
        f"прогона: {unaccounted}"
    )


def test_the_exception_list_does_not_rot():
    """Исключение, которого роутер уже не читает, — мёртвая запись."""
    stale = sorted(_DELIBERATELY_NOT_NEUTRALISED - _router_env_names())

    assert not stale, f"исключения указывают на переменные, которых нет: {stale}"


@pytest.mark.parametrize("var", ["AGENT_TIER_PROVIDERS_STANDARD", "AGENT_MODEL_TIER_STANDARD"])
def test_the_operator_variable_is_gone_by_the_time_a_test_runs(var: str):
    """Дословная проверка: к телу теста переменной уже нет."""
    assert var not in os.environ, (
        f"{var} дожила до тела теста — прогон зависит от машины оператора"
    )
