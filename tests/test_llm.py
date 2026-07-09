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

import json

import pytest

from core.llm import LLM, _default_model


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
    def test_mock_construction_needs_no_api_key(self):
        llm = LLM(provider="mock")
        assert llm.provider == "mock"
        assert llm.model == "mock-1"

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported AGENT_PROVIDER"):
            LLM(provider="nonexistent-provider")

    def test_explicit_provider_overrides_env(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
        # Explicit kwarg must win.
        llm = LLM(provider="mock")
        assert llm.provider == "mock"

    def test_env_provider_is_picked_up_when_no_kwarg(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "mock")
        llm = LLM()
        assert llm.provider == "mock"

    def test_agent_model_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROVIDER", "mock")
        monkeypatch.setenv("AGENT_MODEL", "custom-model-42")
        llm = LLM()
        assert llm.model == "custom-model-42"


# ============================================================
# Mock synthesis mode (non-planner system prompt)
# ============================================================

class TestMockSynthesisMode:
    def test_synthesis_mode_returns_deterministic_echo(self):
        llm = LLM(provider="mock")
        out = llm.complete(
            system="You are a synthesizer.",
            user="hello world",
            temperature=0.5,
        )
        # The mock echo carries diagnostic hints — pin them so future
        # refactors don't silently change the shape integration tests rely on.
        assert "[mock-llm response]" in out
        assert "system_chars=" in out
        assert "user_chars=" in out
        assert "user_preview=hello world" in out

    def test_synthesis_truncates_user_preview_to_200_chars(self):
        llm = LLM(provider="mock")
        long_user = "x" * 500
        out = llm.complete(system="synth", user=long_user)
        # The preview is exactly 200 chars; the marker `user_preview=` is
        # followed by at most 200 'x' characters then a newline.
        assert "user_preview=" + ("x" * 200) + "\n" in out
        assert ("x" * 201) not in out


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

    # --- empty plan branch ---
    def test_general_question_no_file_no_web_cues_returns_empty_plan(self):
        plan = self._plan("What is 2+2?")
        assert plan["steps"] == []

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

    def test_file_hint_with_too_short_question_keeps_empty_plan(self):
        # Very short question: heuristic stays out of file_read fallback.
        plan = self._plan("ok", file_hint="doc.txt")
        assert plan["steps"] == []

    # --- false-positive guard (the bug that motivated the heuristic fix) ---
    def test_word_today_INSIDE_scaffolding_does_not_trigger_web(self):
        # "current_date: 2026-01-01" must NOT be interpreted as a web cue
        # just because it contains the word "current". The mock searches the
        # question line only, not the whole prompt. Regression test for the
        # bug fixed during MVP-3.
        llm = LLM(provider="mock")
        raw = llm.complete(
            system=PLANNER_SYSTEM,
            user=_make_planner_prompt("Hi"),
            temperature=0.0,
        )
        plan = json.loads(raw)
        assert plan["steps"] == [], (
            "scaffolding tokens (current_date, registered tools) must not "
            "leak into cue detection"
        )

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
    def test_counters_start_at_zero(self):
        llm = LLM(provider="mock")
        s = llm.usage_summary()
        assert s["call_count"] == 0
        assert s["input_tokens"] == 0
        assert s["output_tokens"] == 0
        assert s["total_tokens"] == 0
        assert s["provider"] == "mock"
        assert s["model"] == "mock-1"

    def test_complete_increments_call_count(self):
        llm = LLM(provider="mock")
        llm.complete(system="x", user="y")
        assert llm.call_count == 1
        llm.complete(system="x", user="y")
        llm.complete(system="x", user="y")
        assert llm.call_count == 3

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

    def test_last_usage_reflects_latest_call(self):
        llm = LLM(provider="mock")
        llm.complete(system="short", user="short")
        first_last = dict(llm.last_usage)
        llm.complete(system="a" * 200, user="b" * 200)
        second_last = dict(llm.last_usage)
        # last_usage carries ONLY the most recent call, not the sum.
        assert second_last["input_tokens"] > first_last["input_tokens"]
        assert second_last["total_tokens"] == (
            second_last["input_tokens"] + second_last["output_tokens"]
        )
        # Cumulative counter is the SUM of both calls, not just the
        # last one — sanity-check the two views don't get confused.
        s = llm.usage_summary()
        assert s["input_tokens"] == (
            first_last["input_tokens"] + second_last["input_tokens"]
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
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0
        }

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

    def test_usage_summary_includes_provider_and_model(self):
        llm = LLM(provider="mock")
        s = llm.usage_summary()
        assert "provider" in s
        assert "model" in s
        assert s["provider"] == "mock"

    def test_mock_planner_path_also_tracks(self):
        """Both branches of `_complete_mock` must record usage."""
        llm = LLM(provider="mock")
        llm.complete(
            system=PLANNER_SYSTEM,
            user=_make_planner_prompt("Hi"),
            temperature=0.0,
        )
        assert llm.call_count == 1
        assert llm.last_usage["input_tokens"] > 0
        assert llm.last_usage["output_tokens"] > 0


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

    @pytest.mark.parametrize(
        "model,supported",
        [
            ("claude-3-5-sonnet-20241022", True),   # gen-3 → temperature allowed
            ("claude-sonnet-4-5", False),           # gen-4 → temperature dropped
            ("claude-opus-4-1", False),
        ],
    )
    def test_anthropic_supports_temperature(self, model, supported):
        assert LLM._anthropic_supports_temperature(model) is supported

    @pytest.mark.parametrize(
        "model,is_o",
        [
            ("o1", True),
            ("o3-mini", True),
            ("o4-mini", True),
            ("gpt-5", True),
            ("gpt-5.1-mini", True),
            ("gpt-4o-mini", False),   # the 'o' in 4o is not a word-boundary o-series
            ("gpt-4o", False),
            ("gpt-3.5-turbo", False),
        ],
    )
    def test_is_o_series(self, model, is_o):
        assert LLM._is_o_series(model) is is_o


# ============================================================
# Provider client construction (no network — clients are lazy)
# ============================================================

class TestBuildClient:
    def test_openai_client_built_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        llm = LLM(provider="openai")
        assert llm._client is not None

    def test_huggingface_client_built_with_token(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf-test")
        llm = LLM(provider="huggingface")
        assert llm._client is not None
        # HF routes through the OpenAI-compatible router base URL.
        assert "huggingface" in str(getattr(llm._client, "base_url", ""))

    def test_anthropic_client_built_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        llm = LLM(provider="anthropic")
        assert llm._client is not None


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
    def test_complete_parses_text_and_records_usage(self):
        llm = _anthropic_llm()
        out = llm.complete(system="sys", user="hi", temperature=0.3)
        assert out == "hello from claude"
        assert llm.call_count == 1
        assert llm.last_usage == {
            "input_tokens": 11, "output_tokens": 7, "total_tokens": 18
        }

    def test_complete_drops_temperature_for_gen4_model(self):
        llm = _anthropic_llm("claude-sonnet-4-5")
        llm.complete(system="s", user="u", temperature=0.9)
        # gen-4 models must not receive the temperature kwarg.
        assert "temperature" not in llm._client.last_create_kwargs

    def test_complete_keeps_temperature_for_gen3_model(self):
        llm = _anthropic_llm("claude-3-5-sonnet-20241022")
        llm.complete(system="s", user="u", temperature=0.4)
        assert llm._client.last_create_kwargs["temperature"] == 0.4

    def test_stream_accumulates_tokens_and_records_usage(self):
        llm = _anthropic_llm()
        seen = []
        out = llm.stream_complete(
            system="s", user="u", on_token=lambda t: seen.append(t)
        )
        assert out == "hello"
        assert seen == ["he", "llo"]
        assert llm.last_usage == {
            "input_tokens": 12, "output_tokens": 8, "total_tokens": 20
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
    def test_complete_parses_content_and_records_usage(self):
        llm = _openai_llm()
        out = llm.complete(system="s", user="u", temperature=0.5)
        assert out == "hello from gpt"
        assert llm.last_usage == {
            "input_tokens": 9, "output_tokens": 4, "total_tokens": 13
        }
        # Non-o-series model uses max_tokens + temperature.
        assert "max_tokens" in llm._client.last_kwargs
        assert llm._client.last_kwargs["temperature"] == 0.5

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
            system="s", user="u", on_token=lambda t: seen.append(t)
        )
        assert out == "hello"
        assert seen == ["he", "llo"]
        assert llm.last_usage == {
            "input_tokens": 5, "output_tokens": 3, "total_tokens": 8
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

import pytest

from core.llm import _auto_continue_enabled, _max_continuations


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
    def __init__(self, content, in_tok, out_tok, finish_reason):
        self.choices = [_ScriptedChoice(content, finish_reason)]
        self.usage = _Usage(prompt_tokens=in_tok, completion_tokens=out_tok)


class _ScriptedCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, in_tok, out_tok, finish = self._script.pop(0)
        return _ScriptedOAIResponse(content, in_tok, out_tok, finish)


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
        monkeypatch.delenv("AGENT_MAX_CONTINUATIONS", raising=False)

    # --- config helpers ---------------------------------------------------

    def test_auto_continue_default_on(self):
        assert _auto_continue_enabled() is True

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

    def test_anthropic_continues_until_natural_stop(self):
        llm = _scripted_anthropic_llm([
            ("AAAA", 10, 5, "max_tokens"),
            ("BBBB", 3, 4, "end_turn"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAABBBB"
        assert len(llm._client.messages.calls) == 2
        # second call carried the partial answer back as an assistant prefill
        second = llm._client.messages.calls[1]["messages"]
        assert second[-1]["role"] == "assistant"
        assert second[-1]["content"] == "AAAA"
        # usage is summed across both legs
        assert llm.last_usage == {
            "input_tokens": 13, "output_tokens": 9, "total_tokens": 22
        }

    def test_anthropic_no_continuation_on_natural_stop(self):
        llm = _scripted_anthropic_llm([("AAAA", 10, 5, "end_turn")])
        out = llm.complete(system="s", user="u")
        assert out == "AAAA"
        assert len(llm._client.messages.calls) == 1

    # --- openai -----------------------------------------------------------

    def test_openai_continues_until_natural_stop(self):
        llm = _scripted_openai_llm([
            ("AAAA", 9, 4, "length"),
            ("BBBB", 2, 3, "stop"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAABBBB"
        assert len(llm._client.chat.completions.calls) == 2
        # second call replays the partial answer + a continue instruction
        msgs = llm._client.chat.completions.calls[1]["messages"]
        assert msgs[-2]["role"] == "assistant"
        assert msgs[-2]["content"] == "AAAA"
        assert msgs[-1]["role"] == "user"
        assert llm.last_usage == {
            "input_tokens": 11, "output_tokens": 7, "total_tokens": 18
        }

    # --- caps / disable ---------------------------------------------------

    def test_respects_max_continuations_cap(self, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_CONTINUATIONS", "1")
        llm = _scripted_openai_llm([
            ("AAAA", 5, 5, "length"),
            ("BBBB", 5, 5, "length"),   # still truncated, but cap=1 stops here
            ("CCCC", 5, 5, "stop"),     # must never be reached
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAABBBB"
        assert len(llm._client.chat.completions.calls) == 2

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

    def test_empty_continuation_breaks_loop(self):
        # A model that reports truncation but returns no further text must not
        # loop; the chain stops and returns what we have.
        llm = _scripted_anthropic_llm([
            ("AAAA", 5, 5, "max_tokens"),
            ("", 1, 0, "max_tokens"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "AAAA"
        assert len(llm._client.messages.calls) == 2

    # --- boundary whitespace preservation --------------------------------

    def test_anthropic_continuation_preserves_boundary_whitespace(self):
        # A truncation boundary must not merge lines/tokens: trailing whitespace
        # of an earlier leg and leading whitespace of a later leg must survive so
        # a faithfully-continued answer reads correctly.
        llm = _scripted_anthropic_llm([
            ("Line one\n\n", 10, 5, "max_tokens"),
            ("Line two", 3, 4, "end_turn"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "Line one\n\nLine two"

    def test_openai_continuation_preserves_boundary_whitespace(self):
        llm = _scripted_openai_llm([
            ("the quick brown ", 9, 4, "length"),
            ("fox jumps", 2, 3, "stop"),
        ])
        out = llm.complete(system="s", user="u")
        assert out == "the quick brown fox jumps"

