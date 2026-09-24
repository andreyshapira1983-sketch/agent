"""DeepSeek поставлен на стартовую линию оси цены — проводка не гниёт.

Заказ оператора 2026-08-28: «положил $5 на deepseek — посмотрим, выберет ли
он его или будет сидеть на chatgpt-ключе». Баланс $5.00 проверен живым
запросом; живая проба через СОБСТВЕННЫЙ клиент агента вернула «Работаю.»
(27 токенов).

Урок этой проводки — заглушка согласилась на всё: первая проба вернула
«[mock-llm response]», потому что диспетчер `_complete_once` не знал
провайдера и молча проваливался в mock. Поймано чтением живого вывода
([[a-mock-agrees-to-anything]]); теперь диспетчер приколочен.

Честно о выборе: межпровайдерское очко сегодня статическое (классы цены),
оба кандидата роли сводок — 'low', ничью решает окно контекста — OpenAI
победит, пока не построен орган измеренной цены за пару (MIR-176, следующий
шаг). Запись в реестре несёт эту правду в notes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import LLM


def test_the_client_builds_a_real_deepseek_backend(monkeypatch) -> None:
    """Красный свидетель ветки: клиент OpenAI-совместимый, адрес deepseek."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")
    llm = LLM(provider="deepseek")

    assert llm.model == "deepseek-flash"
    assert "api.deepseek.com" in str(llm._client.base_url)


def test_the_dispatcher_knows_the_provider() -> None:
    """Заглушка соглашается на всё: провал в mock ловится по исходнику."""
    import inspect

    from core import llm as mod

    src = inspect.getsource(mod.LLM._complete_once)
    assert '"deepseek"' in src, (
        "диспетчер не знает deepseek — вызовы молча уйдут в mock, как в "
        "первой живой пробе 2026-08-28")


def test_the_router_whitelist_admits_deepseek() -> None:
    from core.model_router import SUPPORTED_PROVIDERS

    assert "deepseek" in SUPPORTED_PROVIDERS


def test_the_registry_carries_the_candidate_with_its_honest_note() -> None:
    """Кандидат существует и его запись не врёт о статике выбора."""
    root = Path(__file__).resolve().parent.parent
    registry = root / "config" / "model_registry.json"
    if not registry.exists():
        # Чистый клон (CI): живой реестр моделей в gitignore. Кандидат живёт
        # в рабочей области оператора; тут проверять нечего. 2026-08-28.
        pytest.skip("нет живого config/model_registry.json в этом клоне")
    data = json.loads(registry.read_text(encoding="utf-8"))
    ds = [m for m in data["models"] if m.get("provider") == "deepseek"]

    assert len(ds) == 1
    entry = ds[0]
    assert entry["model"] == "deepseek-chat"
    assert entry["requires_env"] == ["DEEPSEEK_API_KEY"]
    assert "измеренной цены" in entry["notes"], (
        "запись обязана нести правду о статическом межпровайдерском выборе")
