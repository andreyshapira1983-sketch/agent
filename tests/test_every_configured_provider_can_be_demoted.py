"""У КАЖДОГО провайдера из цепочки отката долговечный отказ демотирует его.

ВНЕШНЕЕ УТВЕРЖДЕНИЕ. Поле называет это классификационным разрывом 429: логика
повторов врёт, когда «повторяемое» и «неповторяемое» различают по строке
ошибки, а строку писал один провайдер. Наш собственный MIR-132 — тот же класс,
замеренный здесь: одна пустая касса опрашивалась 391 раз за четверо суток,
потому что ничего не помнило исход между процессами.

ПРИМЕНИМОСТЬ. Починка MIR-132 завела словарь долговечных отказов по фразам
OpenAI и Anthropic. В цепочке отката три провайдера, и у всех трёх ключ задан.
Третий — huggingface — говорит другими словами.

ЗАМЕР ДО ПОЧИНКИ. «You have exceeded your monthly included credits» и
«Authorization header is invalid» не совпадали ни с одним маркером: провайдер
оставался «здоровым» и опрашивался бы на каждом вызове до конца месяца —
ровно тот отказ, ради которого MIR-132 и заводили, на другом провайдере.

ПОЧЕМУ ТАБЛИЦА, А НЕ ТРИ СТРОКИ. Дыра не в строках, а в том, что словарь
провайдер-зависим, а хранится провайдер-нейтрально: покрытие для нового
провайдера никто не доказывает. Таблица ниже держит по каждому провайдеру
цепочки и его долговечные, и его ПРЕХОДЯЩИЕ фразы — вторые обязаны НЕ
демотировать, иначе здоровый провайдер уходит в двухчасовой отдых из-за лимита
частоты, чего словарь избегает намеренно.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.model_router import _PROVIDER_FALLBACK_ORDER
from core.model_usage import ModelUsageLedger

#: (провайдер, фраза, должен ли демотировать) — по документации самих провайдеров.
CASES: tuple[tuple[str, str, bool], ...] = (
    ("openai", "Error code: 429 - You exceeded your current quota, please check your plan and billing details", True),
    ("openai", "insufficient_quota: You have run out of credits", True),
    ("openai", "Incorrect API key provided: sk-***", True),
    ("openai", "Rate limit reached for gpt-4o-mini in organization org-x on requests per min (RPM)", False),
    ("anthropic", "Your credit balance is too low to access the Anthropic API", True),
    ("anthropic", "invalid x-api-key: authentication_error", True),
    ("anthropic", "Number of request tokens has exceeded your per-minute rate limit", False),
    ("huggingface", "You have exceeded your monthly included credits for Inference Providers. Subscribe to PRO to get 20x more monthly included credits.", True),
    ("huggingface", "Authorization header is invalid, use 'Bearer API_TOKEN'", True),
    ("huggingface", "Model is currently loading, estimated time 20s", False),
)


def _unhealthy_after(provider: str, message: str) -> object:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ModelUsageLedger(Path(tmp) / "usage.jsonl")
        for i in range(4):
            stamp = (now - timedelta(minutes=4 - i)).isoformat()
            ledger.record(
                role="planner", provider=provider, model="m", route_reason="r",
                cost_tier="light", status="error", input_tokens=0, output_tokens=0,
                estimated=False, started_at=stamp, completed_at=stamp,
                duration_ms=1, error=message,
            )
        return ledger.provider_unhealthy(provider)


@pytest.mark.parametrize(("provider", "message", "durable"), CASES)
def test_a_durable_failure_parks_the_provider_and_a_transient_one_does_not(
    provider: str, message: str, durable: bool
) -> None:
    got = bool(_unhealthy_after(provider, message))
    if durable:
        assert got, (
            f"{provider}: «{message[:60]}» — долговечный отказ прочитан как "
            "здоровье, провайдер будет опрашиваться на каждом вызове"
        )
    else:
        assert not got, (
            f"{provider}: «{message[:60]}» — преходящий отказ отправил здорового "
            "провайдера в двухчасовой отдых"
        )


def test_every_provider_in_the_fallback_order_has_evidence() -> None:
    """Новый провайдер в цепочке не наследует покрытие соседа молча."""
    covered = {provider for provider, _, _ in CASES}
    missing = [p for p in _PROVIDER_FALLBACK_ORDER if p not in covered]
    assert not missing, (
        f"в цепочке отката есть провайдеры без единой замеренной фразы: {missing} — "
        "их долговечные отказы не проверял никто"
    )
