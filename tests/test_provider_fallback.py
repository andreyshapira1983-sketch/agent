"""Credential-aware provider healing in ``model_router._llm_factory``.

Regression guard for the crash where a routed provider without an API key was
constructed anyway and later blew up deep inside the provider SDK with an
opaque authentication ``TypeError``. The factory now heals that case: it
switches to a credentialed provider, falls back to ``mock`` when explicitly
allowed, or raises a clear, actionable error — never a raw SDK ``TypeError``.
"""
from __future__ import annotations

import pytest

from core.model_router import _llm_factory

_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN")


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    """Start each test from a known state: no keys, no mock/provider overrides."""
    for var in (*_KEYS, "AGENT_PROVIDER", "AGENT_ALLOW_MOCK_ROUTING"):
        monkeypatch.delenv(var, raising=False)


def test_no_credentials_raises_clear_error(monkeypatch):
    with pytest.raises(RuntimeError, match="No API credentials"):
        _llm_factory("anthropic", "claude-sonnet-4-5")


def test_unset_provider_without_keys_raises_clear_error(monkeypatch):
    # Unset provider resolves to the anthropic default; still no key → clear error.
    with pytest.raises(RuntimeError, match="No API credentials"):
        _llm_factory(None, None)


def test_heals_to_openai_when_only_openai_credentialed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Router fell back to anthropic, but only OpenAI has a key → heal to openai.
    llm = _llm_factory("anthropic", "claude-sonnet-4-5")
    assert llm.provider == "openai"


def test_default_route_heals_to_available_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = _llm_factory(None, None)
    assert llm.provider == "openai"


def test_allows_mock_when_flag_set_and_no_keys(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")
    llm = _llm_factory("anthropic", "claude-sonnet-4-5")
    assert llm.provider == "mock"


def test_credentialed_provider_is_left_unchanged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    llm = _llm_factory("anthropic", "claude-sonnet-4-5")
    assert llm.provider == "anthropic"
    assert llm.model == "claude-sonnet-4-5"


def test_mock_provider_needs_no_credentials(monkeypatch):
    llm = _llm_factory("mock", "mock-1")
    assert llm.provider == "mock"


def test_unknown_provider_still_rejected_loudly(monkeypatch):
    # A typo'd provider must not be silently healed away even when a real
    # provider is credentialed; LLM rejects it with ValueError.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(ValueError):
        _llm_factory("nonexistent-provider", None)
