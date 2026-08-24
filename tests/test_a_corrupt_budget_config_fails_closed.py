"""Испорченный конфиг лимитов останавливает агента, а не открывает ему кошелёк.

ИСТОРИЧЕСКИЙ КЛАСС (H-14, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
CrowdStrike, 19 июля 2024: файл канала конфигурации, разобранный компонентом
уровня ядра, положил миллионы машин по всему миру. Урок двойной — конфиг
является входом не менее опасным, чем сеть, и компонент, который его читает,
должен решить заранее, падать ему или продолжать.

ЗАМЕР 2026-08-24, с ДОКАЗАННЫМ контролем (на здоровых файлах сборка проходит):
`budget_limits.json` и `model_registry.json`, испорченные обрезкой, мусором
или опустошением, роняют `build_agent` с `ValueError`. `model_catalog.json`
переживает порчу — у него есть запасной путь.

ПОЧЕМУ ЭТО НЕ ЧИНИТСЯ СНИСХОДИТЕЛЬНОСТЬЮ. Для лимитов расхода отказ и есть
верное поведение: работать без границы трат опаснее, чем не работать вовсе.
Здесь закрепляется именно направление отказа — закрытое, а не открытое.
Снисходительный разбор («не смогли прочитать — считаем, что лимитов нет»)
превратил бы порчу файла в неограниченные траты, и это ровно та подмена,
ради которой класс и заведён.

ЧТО ОСТАЁТСЯ ОТКРЫТЫМ И ЗАПИСАНО ОТДЕЛЬНО. Падение происходит на СБОРКЕ, то
есть после отметки `tick_start`: демон, у которого испорчен конфиг, будет
падать каждый тик и при этом читаться как `alive` (MIR-135). Составной риск
принадлежит той записи, а не этой; здесь — только направление отказа.
"""
from __future__ import annotations

import json

import pytest


def _load(tmp_path, text: str):
    from core.budget_ledger import _load_budget_config as load_budget_config

    path = tmp_path / "budget_limits.json"
    path.write_text(text, encoding="utf-8")
    return load_budget_config(path)


@pytest.mark.parametrize("corruption", [
    '{"day": ',          # обрезано на середине
    "<<<МУСОР>>>",       # не json вовсе
    "",                  # пусто
    "null",              # валидный json, но не объект
])
def test_a_corrupt_budget_config_raises_instead_of_defaulting(tmp_path, corruption) -> None:
    with pytest.raises(Exception) as caught:
        _load(tmp_path, corruption)

    assert "budget" in str(caught.value).lower(), (
        f"отказ не называет предмет: {caught.value!r}"
    )


def test_a_valid_config_is_still_loaded(tmp_path) -> None:
    """Граница: контроль, доказывающий, что тест выше видит именно порчу."""
    config = _load(tmp_path, json.dumps({
        "windows": {"day": {"agent_runs": 10, "cost_units": 100}},
    }))

    assert config["windows"]["day"]["agent_runs"] == 10
