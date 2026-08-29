"""The failover chain can reach the funded provider (DeepSeek).

Born 2026-08-29, marathon night: OpenAI ran out of money mid-shift and every
goal cycle died through the whole failover chain — openai -> anthropic (no
balance) -> huggingface (key errors) -> local (no server) — while DeepSeek,
the one provider with a live topped-up balance ($5, operator, 2026-08-28),
was never tried.  Two mechanical holes, each pinned here:

- ``_DEFAULT_PROVIDER_ENV`` had no "deepseek" entry, so the router treated
  the provider as credential-less even with DEEPSEEK_API_KEY set;
- ``_PROVIDER_FALLBACK_ORDER`` did not list "deepseek" at all.

The operator's word: money goes where the agent can actually spend it —
DeepSeek follows openai in the preference order because it is the cheap
rescuer, not the default brain (role defaults stay untouched).
"""
from __future__ import annotations

import pytest

from core import model_router as mr


class TestDeepseekCredentials:
    def test_env_map_knows_deepseek(self):
        assert "deepseek" in mr._DEFAULT_PROVIDER_ENV
        assert mr._DEFAULT_PROVIDER_ENV["deepseek"] == ("DEEPSEEK_API_KEY",)

    def test_credentialed_when_key_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        assert mr._provider_has_credentials("deepseek") is True

    def test_not_credentialed_when_key_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert mr._provider_has_credentials("deepseek") is False


class TestFailoverOrder:
    def test_deepseek_sits_between_openai_and_anthropic(self):
        order = mr._PROVIDER_FALLBACK_ORDER
        assert "deepseek" in order
        assert order.index("openai") < order.index("deepseek") < order.index(
            "anthropic"
        )

    def test_dead_openai_fails_over_to_funded_deepseek(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        assert mr._next_failover_provider(["openai"]) == "deepseek"

    def test_without_key_the_chain_skips_deepseek_gracefully(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert mr._next_failover_provider(["openai"]) == "anthropic"
