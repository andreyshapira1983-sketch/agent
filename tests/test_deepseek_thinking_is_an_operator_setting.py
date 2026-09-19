"""DeepSeek's thinking mode is the operator's setting, not the provider's default.

2026-09-19: DeepSeek serves two models, deepseek-flash and deepseek-v4-pro, both
thinking by default; thinking spends the same max_tokens as the answer. The
operator chose Pro for answers at the non-thinking price."""
from __future__ import annotations

from types import SimpleNamespace

from core.llm import LLM, _deepseek_thinking_kwargs


def test_unset_or_garbage_leaves_the_provider_default(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DEEPSEEK_THINKING", raising=False)
    assert _deepseek_thinking_kwargs() == {}
    monkeypatch.setenv("AGENT_DEEPSEEK_THINKING", "maybe")
    assert _deepseek_thinking_kwargs() == {}


def test_the_setting_reaches_only_deepseek_calls(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DEEPSEEK_THINKING", "disabled")
    seen: list[dict] = []

    def create(**kw):
        seen.append(kw)
        msg = SimpleNamespace(content="391")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=5, completion_tokens=1))

    for provider in ("deepseek", "openai"):
        llm = LLM.__new__(LLM)
        llm.provider, llm.model = provider, "deepseek-v4-pro" if provider == "deepseek" else "gpt-4o-mini"
        llm.call_count = llm.input_tokens = llm.output_tokens = 0
        llm.last_usage = {}
        llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        assert llm.complete("s", "17*23?", max_tokens=50, allow_continuation=False) == "391"
    assert seen[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "extra_body" not in seen[1]
