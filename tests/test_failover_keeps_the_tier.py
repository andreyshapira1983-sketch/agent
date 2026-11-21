"""Отказ провайдера не повод понижать задачу на уровень вниз.

Background: docs/CODE_NOTES.md, "Failover kept the provider and threw away the
tier".
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_no_model_means_no_guess():
    assert _peer_model_at_same_tier(None, "openai") is None
    assert _peer_model_at_same_tier("", "openai") is None
