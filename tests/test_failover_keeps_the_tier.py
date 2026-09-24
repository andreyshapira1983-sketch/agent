"""Отказ провайдера не повод понижать задачу на уровень вниз.

Background: docs/CODE_NOTES.md, "Failover kept the provider and threw away the
tier".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.model_catalog import ComplexityTier, classify_model
from core.model_catalog import peer_model_at_same_tier as _peer_model_at_same_tier


@pytest.fixture(autouse=True)
def _a_catalog_that_knows_both_providers(monkeypatch):
    """Каталог-образец, а не живой каталог установки: 24.09 живой стал только
    DeepSeek (единственный ключ), и тест, читавший его, упал. Правило этого
    теста — «уровень переносится, имя нет» — не зависит от ключей установки."""
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH",
                       str(Path(__file__).parent / "fixtures" / "model_catalog_two_providers.json"))
    monkeypatch.setenv("AGENT_MODEL_CATALOG_TTL_DAYS", "100000")
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "0")


def test_the_measured_downgrade_no_longer_happens():
    """Замер 2026-08-15: каждый вызов планировщика на `claude-sonnet-5`
    переезжал на `gpt-4o-mini`. Standard падал в light, 73 раза за день.
    """
    peer = _peer_model_at_same_tier("claude-sonnet-5", "openai")

    assert peer
    assert classify_model(peer) is ComplexityTier.STANDARD
    assert peer != "gpt-4o-mini"


@pytest.mark.parametrize("model,provider", [
    ("claude-opus-5", "openai"),
    ("claude-haiku-4-5-20251001", "openai"),
    ("gpt-4o-mini", "anthropic"),
    ("gpt-5.6-terra", "anthropic"),
])
def test_the_tier_survives_the_crossing(model: str, provider: str):
    """Формы, под которые правку не подгоняли: оба направления, все три
    уровня. Правило одно — уровень переносится, имя нет.
    """
    from core.model_catalog import tier_model_for

    if not tier_model_for(classify_model(model), provider):
        # Посылка теста — «там, где каталог его знает». Каталог строится из
        # ключей установки; без ключа провайдера в нём нет ни одной его модели
        # (суточный прогон 2026-09-19: в каталоге только openai). Тест здесь
        # падал всегда — и полоса самопочинки откатывала ЛЮБУЮ правку агента
        # в core/model_router.py по «упавшим прицельным тестам».
        pytest.skip(f"каталог этой установки не знает провайдера {provider!r}")
    peer = _peer_model_at_same_tier(model, provider)

    assert peer, "равного не нашлось там, где каталог его знает"
    assert classify_model(peer) is classify_model(model)


def test_an_unknown_provider_falls_back_to_its_own_default():
    """None здесь — прежнее поведение, а не поломка: остаться без ответа хуже,
    чем ответить дефолтом провайдера.
    """
    assert _peer_model_at_same_tier("claude-sonnet-5", "провайдер-которого-нет") is None


def test_no_model_means_no_guess():
    assert _peer_model_at_same_tier(None, "openai") is None
    assert _peer_model_at_same_tier("", "openai") is None


def test_the_router_never_passes_none_again():
    """Вторая половина дороги: найти замену мало, надо её передать.

    До 2026-08-15 здесь стояло `self._llm_factory(nxt, None)`, и подменяющий
    провайдер брал свой дефолт. Кто именно выбирает замену, с тех пор изменилось
    ещё раз — сначала карта уровней, затем замер поверх неё
    (tests/test_which_model_earns_the_role.py). Здесь держится то, что не
    зависит от выбирающего: None сюда больше не уходит.
    """
    import inspect

    from core.model_router import UsageTrackedLLM

    source = inspect.getsource(UsageTrackedLLM._failover_llm)
    assert "self._llm_factory(nxt, None)" not in source
