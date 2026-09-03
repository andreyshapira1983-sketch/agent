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
задавало ничто в репозитории. Библиотека хранилище выбирать не должна
(решение 2026-08-29: дефолт в библиотеке дал батарее тестов записать три
выдуманные модели в живой журнал — и при пробе дефолта 2026-09-03 это
повторилось). Дом реестра объявляет РАНТАЙМ в точке входа:
`ensure_roster_home(workspace)` из agent_tick и REPL. Ремонт 2026-09-03 по
слову оператора («строй сам»); был красным, banked strict-xfail.
"""
from __future__ import annotations

import re
from pathlib import Path

from core import llm as llm_module

REPO = Path(__file__).resolve().parent.parent


def test_the_roster_organ_works_when_its_path_is_given(monkeypatch, tmp_path):
    """Зелёный дискриминатор: с заданным путём молчание запоминается и читается."""
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(tmp_path / "roster.jsonl"))

    assert llm_module.is_known_reasoning_model("deepseek", "deepseek-reasoner") is False
    llm_module.remember_reasoning_model("deepseek", "deepseek-reasoner", spent=1200)
    assert llm_module.is_known_reasoning_model("deepseek", "deepseek-reasoner") is True


def test_the_entry_point_gives_the_roster_a_home_in_the_workspace(monkeypatch, tmp_path):
    """Был красным: без переменной окружения реестр был недостижим. Теперь точка
    входа объявляет дом в рабочей области, и он действует для всего процесса."""
    monkeypatch.delenv("AGENT_REASONING_ROSTER", raising=False)
    assert llm_module._roster_path() is None, "библиотека сама хранилище не выбирает"

    home = llm_module.ensure_roster_home(tmp_path)

    assert home == tmp_path / "data" / "reasoning_roster.jsonl"
    assert llm_module._roster_path() == home
    llm_module.remember_reasoning_model("deepseek", "deepseek-reasoner", spent=1200)
    assert llm_module.is_known_reasoning_model("deepseek", "deepseek-reasoner") is True


def test_an_operator_choice_of_home_is_respected(monkeypatch, tmp_path):
    chosen = tmp_path / "elsewhere.jsonl"
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(chosen))

    assert llm_module.ensure_roster_home(tmp_path) == chosen


def test_both_live_entry_points_call_the_wiring():
    """Проверка нерва: функция без вызова из точки входа — та же мёртвая проводка."""
    for rel in ("agent_tick.py", "cli/app.py"):
        source = (REPO / rel).read_text(encoding="utf-8")
        assert re.search(r"\bensure_roster_home\(", source), (
            f"{rel}: точка входа не объявляет дом реестра молчавших"
        )
