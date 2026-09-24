"""Каталог моделей знает DeepSeek — единственного поставщика (24.09).

Каталог держал только список OpenAI, снятый 18.09; опроса DeepSeek не было,
хотя ключ есть только у него. Официальные имена на 24.09 — deepseek-flash и
deepseek-v4-pro (GET /models); «deepseek-chat» из списка исчез.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import core.model_catalog as mc


class _Client:
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        assert base_url == "https://api.deepseek.com"
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(
            data=[SimpleNamespace(id="deepseek-flash"), SimpleNamespace(id="deepseek-v4-pro")]))


def test_deepseek_is_asked_and_classified(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_Client))
    catalog = mc.discover_catalog(["deepseek"], api_keys={"deepseek": "k"})
    tiers = {m["id"]: m["tier"] for m in catalog["providers"]["deepseek"]["models"]}
    assert tiers == {"deepseek-flash": "light", "deepseek-v4-pro": "standard"}
    assert "unreachable" not in catalog


def test_a_deepseek_key_makes_deepseek_a_refresh_target(monkeypatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert mc._credentialed_providers() == ["deepseek"]


def test_the_default_deepseek_model_is_an_official_name(monkeypatch) -> None:
    from core.llm import _default_model
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    assert _default_model("deepseek") == "deepseek-flash"
