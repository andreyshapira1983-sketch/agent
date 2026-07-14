"""Live provider/key failover in ``UsageTrackedLLM.complete``.

When a routed provider's key runs out of money, hits its rate limit, or is
rejected for auth reasons *mid-call*, the tracked LLM transparently rebuilds the
client on the next credentialed provider and retries, rather than crashing.

These tests exercise the seam directly (no real network / keys) by injecting a
fake ledger and a fake ``llm_factory`` that returns scripted clients per
provider.
"""
from __future__ import annotations

import pytest

from core.model_router import (
    ModelRoute,
    UsageTrackedLLM,
    _is_local_unavailable_error,
    _is_switch_key_error,
    _next_failover_provider,
)

_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (*_KEYS, "AGENT_PROVIDER", "AGENT_PROVIDER_FAILOVER"):
        monkeypatch.delenv(var, raising=False)


class _RateLimitError(Exception):
    status_code = 429


class _AuthError(Exception):
    pass


class _FakeLedger:
    """Minimal ledger capturing the calls ``UsageTrackedLLM`` makes."""

    def __init__(self):
        self.records: list[dict] = []
        self.starts: list[dict] = []

    def assert_can_start(self, **kwargs):
        return None

    def log_start(self, **kwargs):
        self.starts.append(kwargs)

    def record(self, **kwargs):
        self.records.append(kwargs)


class _FakeLLM:
    def __init__(self, provider, *, raises=None, output="ok"):
        self.provider = provider
        self.model = f"{provider}-model"
        self._raises = raises
        self._output = output
        self.calls = 0

    def complete(self, *, system, user, max_tokens, temperature):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._output


def _route(provider="openai"):
    return ModelRoute(role="planner", provider=provider, model=f"{provider}-model", reason="test")


def _tracked(primary, factory, ledger):
    return UsageTrackedLLM(
        primary,
        role="planner",
        route=_route("openai"),
        cost_tier="deep",
        ledger=ledger,
        llm_factory=factory,
    )


# --- classifier --------------------------------------------------------------

def test_is_switch_key_error_status_code():
    assert _is_switch_key_error(_RateLimitError("429 Too Many Requests"))


def test_is_switch_key_error_text_quota():
    assert _is_switch_key_error(Exception("Error: insufficient_quota, please add billing"))


def test_is_switch_key_error_text_auth():
    assert _is_switch_key_error(Exception("invalid api key provided"))


def test_non_switch_key_error_not_matched():
    assert not _is_switch_key_error(ValueError("content policy violation"))
    assert not _is_switch_key_error(TimeoutError("connection reset"))


def test_is_local_unavailable_error_timeout_and_connection():
    assert _is_local_unavailable_error(TimeoutError("timed out"))
    assert _is_local_unavailable_error(ConnectionError("connection refused"))
    assert _is_local_unavailable_error(ConnectionRefusedError("refused"))


def test_is_local_unavailable_error_sdk_class_names():
    class APIConnectionError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    assert _is_local_unavailable_error(APIConnectionError("failed to connect"))
    assert _is_local_unavailable_error(APITimeoutError("request timed out"))


def test_is_local_unavailable_error_rejects_content_errors():
    assert not _is_local_unavailable_error(ValueError("content policy violation"))
    assert not _is_local_unavailable_error(RuntimeError("invalid json in model output"))


# --- next provider selection -------------------------------------------------

def test_next_failover_provider_skips_uncredentialed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    # openai not credentialed → skipped; anthropic chosen.
    assert _next_failover_provider(tried=[]) == "anthropic"


def test_next_failover_provider_skips_tried(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    assert _next_failover_provider(tried=["openai"]) == "anthropic"


def test_next_failover_provider_none_when_no_creds():
    assert _next_failover_provider(tried=[]) is None


# --- end-to-end failover -----------------------------------------------------

def test_failover_switches_to_next_credentialed_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    primary = _FakeLLM("openai", raises=_RateLimitError("rate limit exceeded"))
    backup = _FakeLLM("anthropic", output="done")

    def factory(provider, model):
        assert provider == "anthropic"
        return backup

    ledger = _FakeLedger()
    tracked = _tracked(primary, factory, ledger)

    out = tracked.complete(system="s", user="u")

    assert out == "done"
    assert primary.calls == 1 and backup.calls == 1
    # one error (openai) then one success (anthropic, stamped as failover)
    statuses = [r["status"] for r in ledger.records]
    assert statuses == ["error", "success"]
    assert ledger.records[0]["provider"] == "openai"
    assert ledger.records[1]["provider"] == "anthropic"
    assert ledger.records[1]["route_reason"] == "provider_failover:openai->anthropic"
    # the wrapper now points at the working provider
    assert tracked.provider == "anthropic"


def test_non_switch_key_error_does_not_failover(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    primary = _FakeLLM("openai", raises=ValueError("content policy violation"))
    called = {"n": 0}

    def factory(provider, model):
        called["n"] += 1
        return _FakeLLM(provider, output="should-not-be-used")

    ledger = _FakeLedger()
    tracked = _tracked(primary, factory, ledger)

    with pytest.raises(ValueError, match="content policy"):
        tracked.complete(system="s", user="u")
    assert called["n"] == 0
    assert [r["status"] for r in ledger.records] == ["error"]


def test_all_providers_exhausted_raises_and_records_each(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    primary = _FakeLLM("openai", raises=_RateLimitError("429"))
    backup = _FakeLLM("anthropic", raises=_AuthError("unauthorized: invalid api key"))

    def factory(provider, model):
        return backup

    ledger = _FakeLedger()
    tracked = _tracked(primary, factory, ledger)

    with pytest.raises(_AuthError):
        tracked.complete(system="s", user="u")
    # openai + anthropic both attempted; huggingface has no creds → stop.
    assert [r["status"] for r in ledger.records] == ["error", "error"]
    assert {r["provider"] for r in ledger.records} == {"openai", "anthropic"}


def test_failover_disabled_by_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("AGENT_PROVIDER_FAILOVER", "0")

    primary = _FakeLLM("openai", raises=_RateLimitError("429"))

    def factory(provider, model):  # pragma: no cover - must not be called
        raise AssertionError("factory should not be called when failover disabled")

    ledger = _FakeLedger()
    tracked = _tracked(primary, factory, ledger)

    with pytest.raises(_RateLimitError):
        tracked.complete(system="s", user="u")
    assert [r["status"] for r in ledger.records] == ["error"]


def test_no_factory_means_no_failover(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    primary = _FakeLLM("openai", raises=_RateLimitError("429"))
    ledger = _FakeLedger()
    tracked = UsageTrackedLLM(
        primary,
        role="planner",
        route=_route("openai"),
        cost_tier="deep",
        ledger=ledger,
        llm_factory=None,
    )

    with pytest.raises(_RateLimitError):
        tracked.complete(system="s", user="u")
    assert [r["status"] for r in ledger.records] == ["error"]


def test_successful_call_unchanged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    primary = _FakeLLM("openai", output="hello")

    def factory(provider, model):  # pragma: no cover - not reached
        raise AssertionError("no failover on success")

    ledger = _FakeLedger()
    tracked = _tracked(primary, factory, ledger)

    assert tracked.complete(system="s", user="u") == "hello"
    assert [r["status"] for r in ledger.records] == ["success"]
    assert ledger.records[0]["route_reason"] == "test"


# --- local downtime failover -------------------------------------------------

def _tracked_local(primary, factory, ledger):
    return UsageTrackedLLM(
        primary,
        role="memory_summary",
        route=ModelRoute(
            role="memory_summary",
            provider="local",
            model="qwen-local",
            reason="env:AGENT_MEMORY",
        ),
        cost_tier="free",
        ledger=ledger,
        llm_factory=factory,
    )


def test_local_connection_error_failovers_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    primary = _FakeLLM("local", raises=ConnectionError("connection refused"))
    backup = _FakeLLM("openai", output="cloud-ok")

    def factory(provider, model):
        assert provider == "openai"
        assert model is None
        return backup

    ledger = _FakeLedger()
    tracked = _tracked_local(primary, factory, ledger)

    out = tracked.complete(system="s", user="u")

    assert out == "cloud-ok"
    assert primary.calls == 1 and backup.calls == 1
    assert [r["status"] for r in ledger.records] == ["error", "success"]
    assert ledger.records[0]["provider"] == "local"
    assert ledger.records[1]["provider"] == "openai"
    assert ledger.records[1]["route_reason"] == "provider_failover:local->openai"
    assert tracked.provider == "openai"


def test_local_timeout_failovers_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")

    primary = _FakeLLM("local", raises=TimeoutError("request timed out"))
    backup = _FakeLLM("openai", output="after-timeout")

    def factory(provider, model):
        assert provider == "openai"
        return backup

    ledger = _FakeLedger()
    tracked = _tracked_local(primary, factory, ledger)

    assert tracked.complete(system="s", user="u") == "after-timeout"
    assert ledger.records[1]["route_reason"] == "provider_failover:local->openai"


def test_openai_timeout_does_not_failover(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

    primary = _FakeLLM("openai", raises=TimeoutError("request timed out"))
    called = {"n": 0}

    def factory(provider, model):
        called["n"] += 1
        return _FakeLLM(provider, output="should-not-be-used")

    ledger = _FakeLedger()
    tracked = _tracked(primary, factory, ledger)

    with pytest.raises(TimeoutError):
        tracked.complete(system="s", user="u")
    assert called["n"] == 0
    assert [r["status"] for r in ledger.records] == ["error"]


def test_local_failover_disabled_by_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    monkeypatch.setenv("AGENT_PROVIDER_FAILOVER", "0")

    primary = _FakeLLM("local", raises=ConnectionError("connection refused"))

    def factory(provider, model):  # pragma: no cover
        raise AssertionError("factory should not be called when failover disabled")

    ledger = _FakeLedger()
    tracked = _tracked_local(primary, factory, ledger)

    with pytest.raises(ConnectionError):
        tracked.complete(system="s", user="u")
    assert [r["status"] for r in ledger.records] == ["error"]
