"""Модель, однажды доказавшая молчание, получает пол бюджета в следующем прогоне.

Замер 2026-09-03: три вызова планировщика deepseek-reasoner (V4 Flash в
thinking-режиме) в живом прогоне — 8 400, 10 768, 8 400 выходных токенов;
два из трёх без единого символа ответа. Арифметика однозначна: 8 400 =
1 200 + 2 400 + 4 800 — лестница продолжений от запрошенных 1 200 с потолком
×4. Пол для думающих моделей (8 192 по умолчанию, 16 384 переменной
AGENT_REASONING_TOKEN_FLOOR) НЕ применился ни разу, хотя в
data/reasoning_roster.jsonl deepseek-reasoner записан как молчавший ещё с
эпохи потолка 2048.

Причина: `_roster_path()` читает AGENT_REASONING_ROSTER, а эту переменную не
задаёт ничто в репозитории (.env, agent_tick.py, main.py, scripts). В живом
процессе путь = None, реестр не читается и не пишется, `_reasons_internally()`
= False, `_effective_budget(1200)` = 1200. Ручка пола бесполезна, лестница
1200→2400→4800 ниже 4–8 тысяч токенов мыслей, которые просит промпт выбора
цели, и каждое молчание стоит 70–110 секунд.

Орган «реестр молчавших» РАБОТАЕТ — когда путь задан (зелёный свидетель).
Не работает его подключение по умолчанию (красный). Ремонт не применён:
предлагается путь по умолчанию <workspace>/data/reasoning_roster.jsonl, когда
переменная не задана, — файл там уже лежит.
"""
from __future__ import annotations

import pytest

from core import llm as llm_module


def test_the_roster_organ_works_when_its_path_is_given(monkeypatch, tmp_path):
    """Зелёный дискриминатор: с заданным путём молчание запоминается и читается."""
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(tmp_path / "roster.jsonl"))

    assert llm_module.is_known_reasoning_model("deepseek", "deepseek-reasoner") is False
    llm_module.remember_reasoning_model("deepseek", "deepseek-reasoner", spent=1200)
    assert llm_module.is_known_reasoning_model("deepseek", "deepseek-reasoner") is True


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-09-03 and banked rather than fixed (RED witness "
        "by the operator's word): AGENT_REASONING_ROSTER is set nowhere, so "
        "_roster_path() is None in every live process; the roster is never "
        "consulted, the reasoning floor never applies, and a thinking planner "
        "starts every pick at 1200 tokens with a 4800 ceiling (8400 = "
        "1200+2400+4800, twice in charter_day4_hisdefects). Minimal repair "
        "proposed, not applied: default the path to <workspace>/data/"
        "reasoning_roster.jsonl when the variable is unset. "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_a_live_process_reaches_the_roster_without_an_env_variable(monkeypatch):
    """Красный свидетель: без переменной окружения путь обязан быть, а не None."""
    monkeypatch.delenv("AGENT_REASONING_ROSTER", raising=False)

    path = llm_module._roster_path()

    assert path is not None, (
        "в живом прогоне переменная не задана — реестр молчавших недостижим, "
        "и пол бюджета для думающей модели не применяется никогда"
    )
