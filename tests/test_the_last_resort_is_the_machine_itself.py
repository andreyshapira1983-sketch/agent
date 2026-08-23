"""Когда платные ключи мертвы, откат доходит до локальной модели.

ОПЕРАТОРСКАЯ ПОСТАНОВКА (2026-08-23): «если он видит, что ключа нету ни
одного, он должен искать вариант. Но если мы ему дадим этот вариант — это не
автомат; автомат сам должен искать выбор, а долбать — когда выбора нет».

ЧТО БЫЛО ЗАМЕРЕНО. `_PROVIDER_FALLBACK_ORDER` содержал `openai, anthropic,
huggingface`. Провайдер `local` при этом описан полностью: он есть в
`_DEFAULT_PROVIDER_ENV`, его клиент реализован в `core/llm.py` (шесть ветвей),
в репозитории лежат `scripts/install_local_llm.ps1` и `start_local_llm.ps1`, а
`LOCAL_LLM_BASE_URL` и `LOCAL_LLM_MODEL` заданы в живом `.env` этой машины.
Комментарий над цепочкой объясняет, почему исключён `mock`, и про `local` не
говорит НИЧЕГО — то есть это упущение, а не решение.

Следствие: когда все платные ключи отказывали, агент не пробовал модель,
стоящую на его собственной машине. Вариант у него был, дотянуться он не мог.

ГРАНИЦА. `local` идёт ПОСЛЕДНИМ. Он не должен перехватывать работу у
оплаченного провайдера — он последнее средство, а не предпочтение. И он
по-прежнему требует своих переменных: не настроен — не участвует.
"""
from __future__ import annotations

import core.model_router as mr


def _only(monkeypatch, *providers: str) -> None:
    """Оставить учётные данные только у названных провайдеров."""
    for prov, variables in mr._DEFAULT_PROVIDER_ENV.items():
        for var in variables:
            if prov in providers:
                monkeypatch.setenv(var, "configured")
            else:
                monkeypatch.delenv(var, raising=False)


def test_local_is_reached_when_every_paid_key_is_gone(monkeypatch) -> None:
    _only(monkeypatch, "local")

    assert mr._next_failover_provider(["openai", "anthropic", "huggingface"]) == "local", (
        "все платные ключи мертвы, локальная модель настроена на этой машине — "
        "и откат не дошёл до неё: у агента был вариант, которого он не видел"
    )


def test_local_never_outranks_a_paid_provider(monkeypatch) -> None:
    """Последнее средство, а не предпочтение."""
    _only(monkeypatch, "openai", "anthropic", "huggingface", "local")

    assert mr._next_failover_provider([]) == "openai"
    assert mr._next_failover_provider(["openai"]) == "anthropic"
    assert mr._next_failover_provider(["openai", "anthropic"]) == "huggingface"


def test_an_unconfigured_local_stays_out(monkeypatch) -> None:
    """Граница: без своих переменных `local` не участвует, как и раньше."""
    _only(monkeypatch)  # ни у кого нет учётных данных

    assert mr._next_failover_provider(["openai", "anthropic", "huggingface"]) is None


def test_a_half_configured_local_stays_out(monkeypatch) -> None:
    """Один адрес без имени модели — не настроенный провайдер."""
    _only(monkeypatch)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080")

    assert mr._next_failover_provider(["openai", "anthropic", "huggingface"]) is None


def test_mock_is_still_excluded(monkeypatch) -> None:
    """Инвариант, который эта правка не имеет права задеть: `mock` остаётся
    вне цепочки — он входит только через AGENT_ALLOW_MOCK_ROUTING."""
    _only(monkeypatch, "mock")

    assert "mock" not in mr._PROVIDER_FALLBACK_ORDER
    assert mr._next_failover_provider(["openai", "anthropic", "huggingface"]) is None
