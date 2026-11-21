"""LLM client — provider routing and the `mock` provider's deterministic plans.

The `mock` provider is what makes the entire test suite hermetic. If its
heuristics drift, dozens of integration tests will quietly route differently.
This file pins the mock behaviour down: each cue → each tool.

The real provider clients (anthropic, openai, huggingface) are not exercised
here — they require network and a real API key. We only verify that:
  - construction with provider='mock' works without any API key set
  - construction with an unknown provider rejects loudly
  - `complete()` in mock mode returns a deterministic string of the right shape
"""
from __future__ import annotations

import importlib
import json

import pytest

import core.llm as llm_module
from core.llm import (
    _CONTINUE_INSTRUCTION,
    LLM,
    _auto_continue_enabled,
    _default_model,
    _max_continuations,
)


def _make_planner_prompt(question: str, file_hint: str = "(none)") -> str:
    """Match the exact shape `LLMPlanner._build_user_prompt` produces.

    `LLM._mock_plan` parses `file hint:` and `question:` lines from this.
    """
    return (
        "current_date: 2026-01-01\n"
        f"file hint: {file_hint}\n"
        "registered tools: file_read, web_search\n"
        "\n"
        f"question: {question}\n"
        "\n"
        "Return your JSON plan now."
    )


PLANNER_SYSTEM = "You are the planner of an autonomous agent. PLANNER_MODE."


# ============================================================
# Provider routing
# ============================================================

class TestProviderRouting:
    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported AGENT_PROVIDER"):
            LLM(provider="nonexistent-provider")


    def test_agent_model_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "mock")
        monkeypatch.setenv("AGENT_MODEL", "custom-model-42")
        llm = LLM()
        assert llm.model == "custom-model-42"

    def test_local_default_model_and_timeout(self, monkeypatch):
        monkeypatch.delenv("AGENT_MODEL", raising=False)
        monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen-local")
        monkeypatch.setenv("LOCAL_LLM_TIMEOUT", "12.5")
        captured: dict = {}

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
        # Import path used inside _build_client: `from openai import OpenAI`
        import openai as openai_mod

        monkeypatch.setattr(openai_mod, "OpenAI", _FakeOpenAI)

        llm = LLM(provider="local")
        assert llm.provider == "local"
        assert llm.model == "qwen-local"
        assert captured["timeout"] == 12.5
        assert "1234" in captured["base_url"]


# ============================================================
# Mock planner mode — deterministic heuristic routing
# ============================================================

class TestMockPlannerMode:
    def _plan(self, question: str, file_hint: str = "(none)") -> dict:
        llm = LLM(provider="mock")
        raw = llm.complete(
            system=PLANNER_SYSTEM,
            user=_make_planner_prompt(question, file_hint),
            temperature=0.0,
        )
        return json.loads(raw)


    # --- web_search branch ---
    @pytest.mark.parametrize(
        "question",
        [
            "what's the news today?",
            "current price of oil",
            "search the web for python releases",
            "найди в интернете последние новости",
            "что сегодня актуально",
        ],
    )
    def test_web_cues_pick_web_search(self, question):
        plan = self._plan(question)
        tools = [s["tool"] for s in plan["steps"]]
        assert tools == ["web_search"]
        assert plan["steps"][0]["arguments"]["max_results"] == 5

    # --- file_read branch ---
    @pytest.mark.parametrize(
        "question",
        [
            "summarise this file",
            "what's in the file?",
            "опиши раздел этого файла",
        ],
    )
    def test_file_cues_pick_file_read(self, question):
        plan = self._plan(question, file_hint="doc.txt")
        tools = [s["tool"] for s in plan["steps"]]
        assert tools == ["file_read"]
        assert plan["steps"][0]["arguments"]["path"] == "doc.txt"

    # --- compare branch ---
    @pytest.mark.parametrize(
        "question",
        [
            "compare this file with the latest news",
            "vs the web",
            "сравни файл с актуальными данными",
        ],
    )
    def test_compare_cues_pick_both(self, question):
        plan = self._plan(question, file_hint="doc.txt")
        tools = [s["tool"] for s in plan["steps"]]
        assert "file_read" in tools
        assert "web_search" in tools

    # --- file-hint fallback (substantive question, file present, no cues) ---
    def test_file_hint_with_substantive_question_defaults_to_file_read(self):
        plan = self._plan("explain the policy details", file_hint="doc.txt")
        tools = [s["tool"] for s in plan["steps"]]
        assert tools == ["file_read"]


    # --- output shape contract ---
    def test_mock_plan_always_returns_valid_json_object(self):
        llm = LLM(provider="mock")
        for q in ["Hi", "What's new?", "summarise the file"]:
            raw = llm.complete(
                system=PLANNER_SYSTEM,
                user=_make_planner_prompt(q, file_hint="x.txt"),
                temperature=0.0,
            )
            parsed = json.loads(raw)  # must not raise
            assert isinstance(parsed, dict)
            assert "reasoning" in parsed
            assert "steps" in parsed
            assert isinstance(parsed["steps"], list)


# ============================================================
# Usage tracking (MVP-14.5c) — the foundation of "did this run
# burn an API key without a good reason?"
# ============================================================

class TestUsageTracking:
    def test_complete_accumulates_tokens(self):
        llm = LLM(provider="mock")
        llm.complete(system="a" * 40, user="b" * 40)
        first = llm.usage_summary()
        assert first["input_tokens"] > 0
        assert first["output_tokens"] > 0
        llm.complete(system="c" * 40, user="d" * 40)
        second = llm.usage_summary()
        # Strictly grew (we appended a second call).
        assert second["input_tokens"] > first["input_tokens"]
        assert second["output_tokens"] > first["output_tokens"]
        assert second["total_tokens"] == (
            second["input_tokens"] + second["output_tokens"]
        )


    def test_reset_usage_zeroes_everything(self):
        llm = LLM(provider="mock")
        llm.complete(system="a" * 100, user="b" * 100)
        llm.complete(system="c" * 100, user="d" * 100)
        assert llm.call_count == 2
        assert llm.input_tokens > 0
        llm.reset_usage()
        s = llm.usage_summary()
        assert s["call_count"] == 0
        assert s["input_tokens"] == 0
        assert s["output_tokens"] == 0
        assert s["total_tokens"] == 0
        assert llm.last_usage == {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            # None, а не 0: после сброса замера кэша НЕТ, и это не то же
            # самое, что «замерили и попало ноль».
            "cache_hit_tokens": None, "cache_miss_tokens": None,
        }

    def test_cache_numbers_from_the_provider_reach_last_usage(self):
        """Поставщик сообщил про кэш — числа должны дойти до журнала.

        Замерено 2026-09-23: 84% входа планировщика — неизменная шапка, и
        DeepSeek берёт за неё в 30 раз меньше. Пока эти поля не читались,
        расход выглядел втрое больше настоящего. Прибор, который не видит
        разницы между попаданием и промахом, показывает слово, а не мир.
        """
        llm = LLM(provider="mock")
        llm._record_usage(10_000, 500, cache_hit=9_800, cache_miss=200)
        assert llm.last_usage["cache_hit_tokens"] == 9_800
        assert llm.last_usage["cache_miss_tokens"] == 200
        # И обратно: молчание поставщика — это НЕ ноль.
        llm._record_usage(10_000, 500)
        assert llm.last_usage["cache_hit_tokens"] is None
        assert llm.last_usage["cache_miss_tokens"] is None
        # Мусор вместо числа тоже не должен превращаться в ноль или падать.
        llm._record_usage(1, 1, cache_hit="сколько-то", cache_miss=-5)
        assert llm.last_usage["cache_hit_tokens"] is None
        assert llm.last_usage["cache_miss_tokens"] == 0

    def test_continuation_chain_keeps_the_cache_numbers(self, monkeypatch):
        """Кэш всех отрезков продолжения доходит до итогового last_usage.

        Итог цепочки `complete()` собирает заново, и кэш теряется именно там.
        """
        monkeypatch.delenv("AGENT_AUTO_CONTINUE", raising=False)
        monkeypatch.delenv("AGENT_MAX_CONTINUATIONS", raising=False)
        llm = _scripted_openai_llm([
            ("AAAA", 9_300, 5, "length",
             {"prompt_cache_hit_tokens": 9_000, "prompt_cache_miss_tokens": 300}),
            ("BBBB", 9_120, 4, "length",
             {"prompt_cache_hit_tokens": 9_000, "prompt_cache_miss_tokens": 120}),
            # Молчащий отрезок не обнуляет накопленное.
            ("CCCC", 50, 3, "stop"),
        ])
        assert llm.complete(system="s", user="u") == "AAAABBBBCCCC"
        assert llm.last_usage["cache_hit_tokens"] == 18_000
        assert llm.last_usage["cache_miss_tokens"] == 420

    def test_record_usage_tolerates_bad_inputs(self):
        """Usage tracking must NEVER crash a real API call."""
        llm = LLM(provider="mock")
        # Direct call with bad inputs — should clamp to 0, not raise.
        llm._record_usage(-5, -10)
        assert llm.call_count == 1
        assert llm.input_tokens == 0
        assert llm.output_tokens == 0
        llm._record_usage("not-a-number", None)
        assert llm.call_count == 2
        assert llm.input_tokens == 0


# ============================================================
# Pure helpers — model classification and default selection
# ============================================================

class TestModelHelpers:
    @pytest.mark.parametrize(
        "provider,expected",
        [
            ("openai", "gpt-4o-mini"),
            ("huggingface", "meta-llama/Llama-3.3-70B-Instruct"),
            ("mock", "mock-1"),
            ("anthropic", "claude-sonnet-4-5"),
        ],
    )
    def test_default_model_per_provider(self, provider, expected, monkeypatch):
        monkeypatch.delenv("AGENT_MODEL", raising=False)
        assert _default_model(provider) == expected

    def test_default_model_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("AGENT_MODEL", "my-model")
        assert _default_model("openai") == "my-model"


# ============================================================
# Provider client construction (no network — clients are lazy)
# ============================================================


class TestBuildClient:
    def test_huggingface_client_built_with_token(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf-test")
        llm = LLM(provider="huggingface")
        assert llm._client is not None
        # HF routes through the OpenAI-compatible router base URL.
        assert "huggingface" in str(getattr(llm._client, "base_url", ""))


# ============================================================
# Real provider call paths exercised with FAKE clients (no network)
#
# These close the "never-run seam": the token-parsing + usage-recording
# logic inside _complete_anthropic / _complete_openai_compatible and the
# streaming variants was only ever exercised by a live API call. We inject
# fake clients that reproduce the SDK response shapes so the seam runs
# deterministically offline.
# ============================================================

class _Usage:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Block:
    def __init__(self, text):
        self.text = text


class _AnthropicMessage:
    def __init__(self, text, in_tok, out_tok):
        self.content = [_Block(text)]
        self.usage = _Usage(input_tokens=in_tok, output_tokens=out_tok)


class _FakeAnthropicMessages:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.last_create_kwargs = kwargs
        return _AnthropicMessage("hello from claude", 11, 7)

    def stream(self, **kwargs):
        self._parent.last_stream_kwargs = kwargs
        return _FakeAnthropicStream(["he", "llo"], 12, 8)


class _FakeAnthropicStream:
    def __init__(self, chunks, in_tok, out_tok):
        self.text_stream = chunks
        self._final = _AnthropicMessage("".join(chunks), in_tok, out_tok)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._final


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeAnthropicMessages(self)
        self.last_create_kwargs = None
        self.last_stream_kwargs = None


def _anthropic_llm(model="claude-sonnet-4-5"):
    llm = LLM(provider="mock")
    llm.provider = "anthropic"
    llm.model = model
    llm._client = _FakeAnthropicClient()
    return llm


class TestAnthropicCallPath:
    def test_complete_keeps_temperature_for_gen3_model(self):
        llm = _anthropic_llm("claude-3-5-sonnet-20241022")
        llm.complete(system="s", user="u", temperature=0.4)
        assert llm._client.last_create_kwargs["temperature"] == 0.4

    def test_stream_accumulates_tokens_and_records_usage(self):
        llm = _anthropic_llm()
        seen = []
        out = llm.stream_complete(
            system="s", user="u", on_token=seen.append
        )
        assert out == "hello"
        assert seen == ["he", "llo"]
        assert llm.last_usage == {
            "input_tokens": 12, "output_tokens": 8, "total_tokens": 20,
            # Поставщик не сообщил про кэш — значит НЕ ИЗМЕРЕНО, не ноль.
            "cache_hit_tokens": None, "cache_miss_tokens": None,
        }

    def test_stream_on_token_error_is_swallowed(self):
        llm = _anthropic_llm()

        def boom(_):
            raise RuntimeError("callback failed")

        # A failing on_token must not break streaming.
        out = llm.stream_complete(system="s", user="u", on_token=boom)
        assert out == "hello"

    def test_stream_keeps_temperature_for_gen3_model(self):
        llm = _anthropic_llm("claude-3-5-sonnet-20241022")
        llm.stream_complete(system="s", user="u", temperature=0.6)
        assert llm._client.last_stream_kwargs["temperature"] == 0.6


class _OAIMessage:
    def __init__(self, content):
        self.message = _Usage(content=content)


class _OAIResponse:
    def __init__(self, content, in_tok, out_tok):
        self.choices = [_OAIMessage(content)]
        self.usage = _Usage(prompt_tokens=in_tok, completion_tokens=out_tok)


class _OAIDelta:
    def __init__(self, content):
        self.delta = _Usage(content=content)


class _OAIChunk:
    def __init__(self, content, usage=None):
        self.choices = [_OAIDelta(content)] if content is not None else []
        self.usage = usage


class _FakeCompletions:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.last_kwargs = kwargs
        if kwargs.get("stream"):
            return iter([
                _OAIChunk("he"),
                _OAIChunk("llo"),
                _OAIChunk(None, usage=_Usage(prompt_tokens=5, completion_tokens=3)),
            ])
        return _OAIResponse("hello from gpt", 9, 4)


class _FakeChat:
    def __init__(self, parent):
        self.completions = _FakeCompletions(parent)


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = _FakeChat(self)
        self.last_kwargs = None


def _openai_llm(model="gpt-4o-mini"):
    llm = LLM(provider="mock")
    llm.provider = "openai"
    llm.model = model
    llm._client = _FakeOpenAIClient()
    return llm


class TestOpenAICallPath:
    def test_complete_o_series_uses_max_completion_tokens_no_temperature(self):
        llm = _openai_llm("o3-mini")
        llm.complete(system="s", user="u", temperature=0.5)
        kw = llm._client.last_kwargs
        assert "max_completion_tokens" in kw
        assert "max_tokens" not in kw
        assert "temperature" not in kw

    def test_stream_accumulates_and_records_final_chunk_usage(self):
        llm = _openai_llm()
        seen = []
        out = llm.stream_complete(
            system="s", user="u", on_token=seen.append
        )
        assert out == "hello"
        assert seen == ["he", "llo"]
        assert llm.last_usage == {
            "input_tokens": 5, "output_tokens": 3, "total_tokens": 8,
            "cache_hit_tokens": None, "cache_miss_tokens": None,
        }

    def test_stream_o_series_branch_sets_max_completion_tokens(self):
        llm = _openai_llm("o4-mini")
        llm.stream_complete(system="s", user="u")
        kw = llm._client.last_kwargs
        assert "max_completion_tokens" in kw
        assert "max_tokens" not in kw

    def test_stream_on_token_error_is_swallowed(self):
        llm = _openai_llm()

        def boom(_):
            raise RuntimeError("callback failed")

        out = llm.stream_complete(system="s", user="u", on_token=boom)
        assert out == "hello"


class TestHuggingFaceFallsBackToComplete:
    def test_stream_complete_falls_back_for_huggingface(self):
        # huggingface has no native streaming → stream_complete delegates to
        # complete(). We point it at a fake openai-compatible client.
        llm = LLM(provider="mock")
        llm.provider = "huggingface"
        llm.model = "meta-llama/Llama-3.3-70B-Instruct"
        llm._client = _FakeOpenAIClient()
        out = llm.stream_complete(system="s", user="u")
        # Fell back to the non-streaming completions path.
        assert out == "hello from gpt"
        assert llm._client.last_kwargs.get("stream") is None


# ============================================================
# Plan A — large answers are auto-continued instead of truncated
#
# When a provider stops on its output cap (Anthropic
# stop_reason='max_tokens' / OpenAI finish_reason='length') complete()
# feeds the partial answer back and resumes until a natural stop or the
# AGENT_MAX_CONTINUATIONS cap. Usage is summed across every leg.
# ============================================================


class _ScriptedAnthropicMessages:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text, in_tok, out_tok, stop = self._script.pop(0)
        msg = _AnthropicMessage(text, in_tok, out_tok)
        msg.stop_reason = stop
        return msg


class _ScriptedAnthropicClient:
    def __init__(self, script):
        self.messages = _ScriptedAnthropicMessages(script)


def _scripted_anthropic_llm(script, model="claude-sonnet-4-5"):
    llm = LLM(provider="mock")
    llm.provider = "anthropic"
    llm.model = model
    llm._client = _ScriptedAnthropicClient(script)
    return llm


class _ScriptedChoice:
    def __init__(self, content, finish_reason):
        self.message = _Usage(content=content)
        self.finish_reason = finish_reason


class _ScriptedOAIResponse:
    def __init__(self, content, in_tok, out_tok, finish_reason, cache=None):
        self.choices = [_ScriptedChoice(content, finish_reason)]
        self.usage = _Usage(prompt_tokens=in_tok, completion_tokens=out_tok, **(cache or {}))


class _ScriptedCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, in_tok, out_tok, finish, *cache = self._script.pop(0)
        return _ScriptedOAIResponse(content, in_tok, out_tok, finish, *cache)


class _ScriptedChat:
    def __init__(self, script):
        self.completions = _ScriptedCompletions(script)


class _ScriptedOpenAIClient:
    def __init__(self, script):
        self.chat = _ScriptedChat(script)
        self.last_kwargs = None


def _scripted_openai_llm(script, model="gpt-4o-mini"):
    llm = LLM(provider="mock")
    llm.provider = "openai"
    llm.model = model
    llm._client = _ScriptedOpenAIClient(script)
    return llm


class TestLargeOutputContinuation:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("AGENT_AUTO_CONTINUE", raising=False)
        # Learned state is state: a roster left by an earlier test would
        # answer "this model reasons" and lift the budget under a test
        # that is asking what an UNKNOWN model gets.
        monkeypatch.delenv("AGENT_REASONING_ROSTER", raising=False)
        monkeypatch.delenv("AGENT_MAX_CONTINUATIONS", raising=False)

    # --- config helpers ---------------------------------------------------


    def test_auto_continue_disabled(self, monkeypatch):
        monkeypatch.setenv("AGENT_AUTO_CONTINUE", "0")
        assert _auto_continue_enabled() is False

    def test_max_continuations_default_and_override(self, monkeypatch):
        assert _max_continuations() == 4
        monkeypatch.setenv("AGENT_MAX_CONTINUATIONS", "2")
        assert _max_continuations() == 2
        monkeypatch.setenv("AGENT_MAX_CONTINUATIONS", "garbage")
        assert _max_continuations() == 4

    # --- anthropic --------------------------------------------------------


    def test_gen5_continuation_ends_on_a_user_message(self):
        """Generation 5 refuses a trailing assistant turn — ask, don't prefill.

        Observed live: continuing a truncated claude-opus-5 reply raised HTTP
        400 ("the conversation must end with a user message") and the whole
        call was lost. The partial answer is still replayed, but the request
        closes with an explicit instruction to resume.
        """
        llm = _scripted_anthropic_llm(
            [("AAAA", 10, 5, "max_tokens"), ("BBBB", 3, 4, "end_turn")],
            model="claude-opus-5",
        )
        out = llm.complete(system="s", user="u")
        assert out == "AAAABBBB"

        second = llm._client.messages.calls[1]["messages"]
        assert second[-1]["role"] == "user", (
            "a gen-5 request that ends on an assistant turn is rejected outright"
        )
        assert second[-1]["content"] == _CONTINUE_INSTRUCTION
        # The partial answer must still be carried back, or the model restarts.
        assert second[-2] == {"role": "assistant", "content": "AAAA"}


    def test_gen5_replay_keeps_boundary_whitespace(self):
        """Only a PREFILL may not end in whitespace — history may, and must.

        `complete` keeps each leg unstripped precisely so a word split across
        the truncation boundary rejoins correctly. In the gen-5 shape the
        partial answer is followed by a user turn, so it is ordinary history
        and the restriction does not apply: trimming it here would discard the
        boundary at the one point that decides where the continuation lands.
        """
        llm = _scripted_anthropic_llm(
            [("the quick brown \n", 10, 5, "max_tokens"), ("fox", 3, 4, "end_turn")],
            model="claude-opus-5",
        )
        llm.complete(system="s", user="u")

        replayed = llm._client.messages.calls[1]["messages"][-2]
        assert replayed == {"role": "assistant", "content": "the quick brown \n"}


    # --- openai -----------------------------------------------------------


    # --- caps / disable ---------------------------------------------------


    def test_disabled_by_env_returns_truncated(self, monkeypatch):
        monkeypatch.setenv("AGENT_AUTO_CONTINUE", "0")
        llm = _scripted_openai_llm([
            ("AAAA", 5, 5, "length"),
            ("BBBB", 5, 5, "stop"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAA"
        assert len(llm._client.chat.completions.calls) == 1

    def test_zero_cap_disables_continuation(self, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_CONTINUATIONS", "0")
        llm = _scripted_anthropic_llm([
            ("AAAA", 5, 5, "max_tokens"),
            ("BBBB", 5, 5, "end_turn"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAA"
        assert len(llm._client.messages.calls) == 1


    def test_empty_and_finished_continuation_breaks_immediately(self):
        # Empty AND not truncated means the model genuinely has nothing left to
        # add. Escalating the budget cannot help, so stop at once.
        llm = _scripted_anthropic_llm([
            ("AAAA", 5, 5, "max_tokens"),
            ("", 1, 0, "end_turn"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAA"
        assert len(llm._client.messages.calls) == 2

    def test_escalation_never_exceeds_ceiling(self, monkeypatch):
        monkeypatch.setenv("AGENT_CONTINUE_ESCALATION_CAP", "2")
        importlib.reload(llm_module)
        try:
            llm = llm_module.LLM(provider="mock")
            llm.provider = "anthropic"
            llm.model = "claude-sonnet-4-5"
            llm._client = _ScriptedAnthropicClient([
                ("", 1, 0, "max_tokens"),
                ("", 1, 0, "max_tokens"),
                ("", 1, 0, "max_tokens"),
                ("", 1, 0, "max_tokens"),
            ])
            llm.complete(system="s", user="u", max_tokens=100)
            budgets = [c["max_tokens"] for c in llm._client.messages.calls]
            assert max(budgets) <= 200
        finally:
            monkeypatch.delenv("AGENT_CONTINUE_ESCALATION_CAP", raising=False)
            importlib.reload(llm_module)

    # --- boundary whitespace preservation --------------------------------


class TestStructuredAnswersAreNotBlindlyStitched:
    """Continuation cannot resume a JSON string, and corrupts it silently.

    Three live builder replies died this way on 2026-08-04 (preserved under
    `logs/self_build_rejects/`). The evidence is the escaping style flipping
    mid-answer at the leg boundary: before it, `\\\\n` and `\\\\"` (escaped,
    inside a JSON string); after it, real newlines and bare `"` — the model
    resumed writing plain code, not knowing it stood inside a JSON string.
    The three break positions (32 326 / 32 440 / 37 678 chars) all sit at
    2.0–2.35 chars per token from the start of a 16 000-token first leg.

    Each of those runs paid for up to four extra legs to produce garbage, and
    the veto that followed blamed the target file (MIR-083).
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("AGENT_AUTO_CONTINUE", raising=False)
        # Learned state is state: a roster left by an earlier test would
        # answer "this model reasons" and lift the budget under a test
        # that is asking what an UNKNOWN model gets.
        monkeypatch.delenv("AGENT_REASONING_ROSTER", raising=False)
        monkeypatch.delenv("AGENT_MAX_CONTINUATIONS", raising=False)

    #: Shaped like the real failure: leg 1 is inside a JSON string, leg 2 is not.
    _LEG_1 = '{"content": "def f():\\n    return 1\\n'
    _LEG_2 = '"}\n        raise\n'

    def test_a_structured_call_stops_at_the_first_leg(self):
        llm = _scripted_anthropic_llm([
            (self._LEG_1, 10, 5, "max_tokens"),
            (self._LEG_2, 3, 4, "end_turn"),
        ])

        out = llm.complete(system="s", user="u", allow_continuation=False)

        assert out == self._LEG_1.strip()
        assert len(llm._client.messages.calls) == 1, (
            "a second leg was requested and paid for"
        )

    def test_a_structured_call_reports_it_was_truncated(self):
        """Truncated must be distinguishable from "the model wrote nonsense"."""
        llm = _scripted_anthropic_llm([(self._LEG_1, 10, 5, "max_tokens")])

        llm.complete(system="s", user="u", allow_continuation=False)

        assert llm.last_answer_was_truncated is True


class TestReasoningTokenBudget:
    """OpenAI reasoning models spend the budget on thinking before answering.

    `max_completion_tokens` covers internal reasoning AND visible output, so a
    budget sized for the answer alone can be consumed entirely by reasoning --
    the API returns success with `content=None`. The client therefore treats
    the caller's request as a floor for these models only.
    """


    def test_floor_of_zero_disables_the_raise(self, monkeypatch):
        monkeypatch.setenv("AGENT_REASONING_TOKEN_FLOOR", "0")
        importlib.reload(llm_module)
        try:
            llm = llm_module.LLM(provider="mock")
            llm.provider = "openai"
            llm.model = "gpt-5.6-terra"
            llm._client = _ScriptedOpenAIClient([("{}", 5, 5, "stop")])
            llm.complete(system="s", user="u", max_tokens=1024)
            assert (
                llm._client.chat.completions.calls[0]["max_completion_tokens"] == 1024
            )
        finally:
            monkeypatch.delenv("AGENT_REASONING_TOKEN_FLOOR", raising=False)
            importlib.reload(llm_module)
class TestEscalationStartsFromTheEffectiveBudget:
    """Escalation must double what a leg REALLY costs, not what it asked for.

    `_complete_openai_compatible` silently lifts a reasoning model's request to
    `_REASONING_TOKEN_FLOOR`. Seeding the continuation loop from the smaller
    requested number made every early "escalation" collapse back onto the same
    floor, so the client paid for byte-identical failed calls and then hit its
    ceiling without ever spending more than leg 1 already had.
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("AGENT_AUTO_CONTINUE", raising=False)
        # Learned state is state: a roster left by an earlier test would
        # answer "this model reasons" and lift the budget under a test
        # that is asking what an UNKNOWN model gets.
        monkeypatch.delenv("AGENT_REASONING_ROSTER", raising=False)
        monkeypatch.delenv("AGENT_MAX_CONTINUATIONS", raising=False)

    @staticmethod
    def _budgets(llm, key):
        return [c[key] for c in llm._client.chat.completions.calls]


    def test_reasoning_escalation_still_respects_a_ceiling(self):
        llm = _scripted_openai_llm(
            [(None, 10, 10, "length")] * 8, model="gpt-5.6-terra"
        )
        llm.complete(system="s", user="u", max_tokens=1024)

        budgets = self._budgets(llm, "max_completion_tokens")
        ceiling = llm_module._REASONING_TOKEN_FLOOR * llm_module._CONTINUE_ESCALATION_CAP
        assert max(budgets) <= ceiling
        assert len(budgets) <= 1 + _max_continuations()


class TestBudgetEnvVarsFailSoft:
    """A malformed env var must degrade to the default, never break import.

    `_max_continuations()` and `core.planner._plan_max_tokens()` already work
    this way; a bare `int(os.getenv(...))` at module scope would instead raise
    `ValueError` during import and take the whole process down.
    """

    @staticmethod
    def _reload_with(monkeypatch, name, value):
        monkeypatch.setenv(name, value)
        try:
            importlib.reload(llm_module)
            return llm_module
        finally:
            monkeypatch.delenv(name, raising=False)

    def test_malformed_floor_falls_back(self, monkeypatch):
        try:
            mod = self._reload_with(monkeypatch, "AGENT_REASONING_TOKEN_FLOOR", "not-a-number")
            assert mod._REASONING_TOKEN_FLOOR == 8192
        finally:
            importlib.reload(llm_module)

    def test_malformed_cap_falls_back(self, monkeypatch):
        try:
            mod = self._reload_with(monkeypatch, "AGENT_CONTINUE_ESCALATION_CAP", "oops")
            assert mod._CONTINUE_ESCALATION_CAP == 4
        finally:
            importlib.reload(llm_module)
