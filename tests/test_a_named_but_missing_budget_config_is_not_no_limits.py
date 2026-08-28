"""Названный, но отсутствующий файл лимитов — не то же, что «лимитов не задано».

Замер, отвергнутые варианты и границы: H-33 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from core.budget_ledger import BudgetLedger


def test_an_unattended_tick_refuses_to_run_without_a_cap(tmp_path) -> None:
    import agent_tick

    with pytest.raises(agent_tick.BudgetConfigMissing):
        agent_tick._require_budget_config(tmp_path)


def test_the_tick_accepts_a_cap_named_by_the_environment(tmp_path, monkeypatch) -> None:
    """Граница: держать лимиты в другом месте — законный способ, не отказ."""
    import agent_tick

    elsewhere = tmp_path / "где-то" / "limits.json"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(json.dumps({"windows": {"day": {"llm_calls": 5}}}), encoding="utf-8")
    monkeypatch.setenv("AGENT_BUDGET_CONFIG_PATH", str(elsewhere))

    agent_tick._require_budget_config(tmp_path)  # молчит — значит принял


def test_the_library_layer_still_treats_no_config_as_no_limits(tmp_path) -> None:
    """Граница, доказанная 168 красными: библиотека не решает за оператора."""
    ledger = BudgetLedger.from_env(path=tmp_path / "b.jsonl")

    assert ledger.reserve("llm_calls", amount=1, reason="без конфига").allowed


def test_a_present_config_still_bounds_spending(tmp_path) -> None:
    """Контроль: проба обязана уметь показать работающий потолок.

    Без него первый тест краснел бы и на леджере, сломанном насовсем.
    """
    cfg = tmp_path / "limits.json"
    cfg.write_text(json.dumps({"windows": {"day": {"llm_calls": 2}}}), encoding="utf-8")

    ledger = BudgetLedger.from_env(path=tmp_path / "b.jsonl", config_path=cfg)
    assert ledger.reserve("llm_calls", amount=1, reason="первый").allowed
    assert ledger.reserve("llm_calls", amount=1, reason="второй").allowed
    assert not ledger.reserve("llm_calls", amount=1, reason="третий").allowed


def test_the_live_workspace_actually_carries_the_file(tmp_path) -> None:
    """Замер, а не мнение: живой потолок и правда держится на этом файле."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    if not (repo / "config" / "budget_limits.json").exists() and not (
            repo / "data").exists():
        # Чистый клон (CI): config/budget_limits.json в gitignore нарочно.
        # Дома отсутствие файла — авария без потолка; здесь — не живая
        # рабочая область вовсе (data/ тоже нет). 2026-08-28.
        pytest.skip("не живая рабочая область: нет ни config-лимитов, ни data/")
    assert (repo / "config" / "budget_limits.json").exists(), (
        "файла лимитов нет в репозитории — тогда живой агент работает без "
        "денежного потолка, и это надо чинить раньше всего остального"
    )

def test_the_guard_is_actually_wired_into_the_process_entry(tmp_path) -> None:
    """Проверка существует — этого мало; она обязана СТОЯТЬ на пути.

    Ломка обратной правкой показала это буквально: снятие вызова оставило все
    прочие тесты зелёными, потому что они звали функцию напрямую. Зелёный тест
    на функцию не есть доказательство подключения.

    Путь здесь — запуск МОДУЛЯ: плановый безнадзорный тик приходит только через
    `python agent_tick.py`, а `run_tick` зовут изнутри тесты на временных
    папках. Поэтому и проверяется процессом, а не вызовом: иначе тест мерил бы
    не тот вход.
    """
    repo = pathlib.Path(__file__).resolve().parent.parent
    proc = subprocess.run(  # noqa: S603 — свой модуль, аргументы не извне
        [sys.executable, "agent_tick.py", "--workspace", str(tmp_path)],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=180,
        check=False,  # отказ процесса — это и есть предмет проверки
    )

    assert proc.returncode != 0, (
        "тик на рабочем месте без потолка завершился успешно: "
        + (proc.stdout or "")
    )
    assert "budget limits file not found" in (
        (proc.stderr or "") + (proc.stdout or "")
    ), (
        "процесс упал, но не по этой причине — тест закрепил бы чужой отказ: "
        + (proc.stderr or "")
    )
