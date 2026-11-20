"""Thin LLM client wrapper."""
from __future__ import annotations

import inspect
import os
from typing import Any

# Registry of models that spend output budget on reasoning (core/reasoning_roster.py); read and extended here, names re-exported for entry points and tests.
from core.reasoning_roster import (  # noqa: F401
    ROSTER_RELPATH,
    _roster_key,
    _roster_path,
    ensure_roster_home,
    is_known_reasoning_model,
    remember_reasoning_model,
)


def _provider() -> str:
    return os.getenv("AGENT_PROVIDER", "anthropic").lower().strip()


def _default_model(provider: str) -> str:
    if provider == "local":
        return os.getenv("LOCAL_LLM_MODEL", "qwen-local")

    override = os.getenv("AGENT_MODEL")
    if override:
        return override
    if provider == "deepseek":
        # Official names: deepseek-flash and deepseek-v4-pro; "deepseek-chat" survives only as a Flash alias and may be removed any day.
        return "deepseek-flash"
    if provider == "openai":
        return "gpt-4o-mini"
    if provider == "huggingface":
        return "meta-llama/Llama-3.3-70B-Instruct"
    if provider == "mock":
        return "mock-1"
    return "claude-sonnet-4-5"


DEFAULT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "2048"))


# Provider stop/finish reasons meaning the output budget ran out mid-answer; the model can be asked to continue instead of returning a truncated answer.
_TRUNCATION_REASONS = frozenset({"max_tokens", "length"})

# OpenAI reasoning models (o-series, gpt-5+) spend max_completion_tokens on internal reasoning first, so a budget sized for the answer
# alone can return content=None with finish_reason='length'. For these models the requested budget is a FLOOR, raised so reasoning and
# answer fit; other models keep the exact budget. Parsed defensively: a malformed value degrades to the default, never raises at import.
def _reasoning_token_floor() -> int:
    try:
        return int(str(os.getenv("AGENT_REASONING_TOKEN_FLOOR", "8192")).strip())
    except (TypeError, ValueError):
        return 8192


_REASONING_TOKEN_FLOOR = _reasoning_token_floor()

#: Valid OpenAI reasoning-effort levels; anything else degrades to "unset".
_REASONING_EFFORT_LEVELS = frozenset({"minimal", "low", "medium", "high", "max"})


def _reasoning_effort_kwargs() -> dict[str, str]:
    """reasoning_effort kwargs for gpt-5+/o-series calls from AGENT_OPENAI_REASONING_EFFORT; unset or invalid gives {}; read per call (no restart)."""
    level = (os.getenv("AGENT_OPENAI_REASONING_EFFORT", "") or "").strip().lower()
    return {"reasoning_effort": level} if level in _REASONING_EFFORT_LEVELS else {}


def accepted_flags(fn: Any, flags: dict[str, bool]) -> dict[str, bool]:
    """The subset of flags that fn can take as keywords; asked of the signature because retrying on TypeError would re-send a paid request."""
    if not flags:
        return flags
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return flags
    return {name: value for name, value in flags.items() if name in params}


# Providers whose chat API has response_format={"type": "json_object"}. DeepSeek needs "json" and the shape in the prompt and may return
# EMPTY content (parse_json names that empty_model_output).
_JSON_MODE_PROVIDERS = frozenset({"deepseek", "openai"})


def _deepseek_thinking_kwargs() -> dict[str, Any]:
    """DeepSeek thinking mode from AGENT_DEEPSEEK_THINKING (enabled/disabled); both current models think by default and thinking spends the same
    max_tokens as the answer; unset or invalid gives {}; read per call."""
    mode = (os.getenv("AGENT_DEEPSEEK_THINKING", "") or "").strip().lower()
    return {"extra_body": {"thinking": {"type": mode}}} if mode in {"enabled", "disabled"} else {}


# How far a continuation round may escalate the per-leg budget when the previous leg came back empty; bounded so a silent model cannot
# drive unbounded spend (AGENT_MAX_CONTINUATIONS is the other backstop); clamped at 1.
def _continue_escalation_cap() -> int:
    try:
        return max(1, int(str(os.getenv("AGENT_CONTINUE_ESCALATION_CAP", "4")).strip()))
    except (TypeError, ValueError):
        return 4


_CONTINUE_ESCALATION_CAP = _continue_escalation_cap()


def _auto_continue_enabled() -> bool:
    """Whether truncated answers are auto-continued (default on; disable with AGENT_AUTO_CONTINUE=0/false/no/off)."""
    value = os.getenv("AGENT_AUTO_CONTINUE")
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _max_continuations() -> int:
    """Max follow-up rounds when an answer is truncated (default 4; 0 disables); bounded so a misbehaving model cannot loop forever."""
    raw = os.getenv("AGENT_MAX_CONTINUATIONS", "4")
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 4


# Instruction appended for OpenAI-compatible continuation turns; Anthropic continues an assistant prefill natively.
_CONTINUE_INSTRUCTION = (
    "Continue the previous response exactly where it stopped. "
    "Output only the continuation — no repetition of earlier text, no preamble, "
    "no explanation."
)


class LLM:
    """Minimal synchronous LLM wrapper with pluggable provider."""

    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = (provider or _provider()).lower().strip()
        self.model = model or _default_model(self.provider)
        self._client = self._build_client()
        # Usage counters, incremented by complete() after every successful call and reset via reset_usage(); they never throw (flat when the provider reports no usage).
        self.call_count: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        # last_usage: the most recent per-call payload, so an audit can attribute spend to a scenario.
        self.last_usage: dict[str, int | None] = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            # Prompt-cache input tokens from the provider; None means NOT MEASURED, which differs from 0.
            "cache_hit_tokens": None, "cache_miss_tokens": None,
        }
        #: Whether the last answer stopped on the token limit; lets a caller that declined continuation tell a short answer from a broken one.
        self.last_answer_was_truncated: bool = False


    def reset_usage(self) -> None:
        """Zero every counter (the audit harness calls it between scenarios)."""
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last_usage = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cache_hit_tokens": None, "cache_miss_tokens": None,
        }

    def usage_summary(self) -> dict[str, Any]:
        """Snapshot of cumulative usage. Safe to read at any time."""
        return {
            "provider": self.provider,
            "model": self.model,
            "call_count": self.call_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }

    @staticmethod
    def _opt_tokens(value: Any) -> int | None:
        """Token count, or None when the provider did not report it (None = not measured, 0 = measured and nothing cached)."""
        if value is None:
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _agg_cache(
        hit: int | None, miss: int | None, leg: dict[str, Any]
    ) -> tuple[int | None, int | None]:
        """Add a leg's cache numbers to the running totals; a silent leg does not reset them, and the sum stays None until some leg reports a real number."""
        def _add(acc: int | None, key: str) -> int | None:
            value = LLM._opt_tokens(leg.get(key))
            if value is None:
                return acc
            return value if acc is None else acc + value
        return _add(hit, "cache_hit_tokens"), _add(miss, "cache_miss_tokens")

    def _record_usage(
        self,
        in_tok: int,
        out_tok: int,
        cache_hit: Any = None,
        cache_miss: Any = None,
    ) -> None:
        """Internal: bump counters after a successful call; bad inputs become 0 (usage tracking must never crash a call)."""
        try:
            in_int = max(0, int(in_tok))
            out_int = max(0, int(out_tok))
        except (TypeError, ValueError):
            in_int = 0
            out_int = 0
        self.call_count += 1
        self.input_tokens += in_int
        self.output_tokens += out_int
        self.last_usage = {
            "input_tokens": in_int,
            "output_tokens": out_int,
            "total_tokens": in_int + out_int,
            "cache_hit_tokens": self._opt_tokens(cache_hit),
            "cache_miss_tokens": self._opt_tokens(cache_miss),
        }

    def _build_client(self):
        if self.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        if self.provider == "openai":
            from openai import OpenAI
            return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        if self.provider == "huggingface":
            from openai import OpenAI
            return OpenAI(
                base_url="https://router.huggingface.co/v1",
                api_key=os.getenv("HF_TOKEN"),
            )
        if self.provider == "deepseek":
            # OpenAI-compatible API (price-axis experiment).
            from openai import OpenAI
            return OpenAI(
                base_url="https://api.deepseek.com",
                api_key=os.getenv("DEEPSEEK_API_KEY"),
            )
        if self.provider == "local":
            from openai import OpenAI

            try:
                timeout = float(os.getenv("LOCAL_LLM_TIMEOUT", "60"))
            except (TypeError, ValueError):
                timeout = 60.0
            if timeout <= 0:
                timeout = 60.0
            return OpenAI(
                base_url=os.getenv(
                    "LOCAL_LLM_BASE_URL",
                    "http://127.0.0.1:1234/v1",
                ),
                api_key=os.getenv("LOCAL_LLM_API_KEY", "lm-studio"),
                timeout=timeout,
            )
        if self.provider == "mock":
            return None
        raise ValueError(
            f"Unsupported AGENT_PROVIDER: {self.provider!r}. "
            "Use 'anthropic', 'openai', 'deepseek', 'huggingface', "
            "'local', or 'mock'."
        )

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        *,
        allow_continuation: bool = True,
        json_object: bool = False,
    ) -> str:
        """Send a single-turn prompt and return the text; json_object=True asks a provider with a JSON mode for a valid JSON object (see _JSON_MODE_PROVIDERS), elsewhere it changes nothing."""
        text, stop_reason = self._complete_once(
            system, user, max_tokens, temperature, prior=None,
            json_object=json_object,
        )
        self.last_answer_was_truncated = stop_reason in _TRUNCATION_REASONS
        # Truncated with nothing to show: the whole budget went to internal reasoning. That behaviour, not a name table, is what the roster records.
        if not text and self.last_answer_was_truncated:
            remember_reasoning_model(self.provider, self.model, spent=max_tokens)
        if not allow_continuation:
            return text if self.provider == "mock" else text.strip()
        # Per-leg provider calls return RAW text so whitespace at a truncation boundary survives concatenation; trimming happens once on the final answer. Mock text is verbatim.
        if self.provider == "mock":
            return text
        if not _auto_continue_enabled():
            return text.strip()

        max_rounds = _max_continuations()
        if max_rounds <= 0:
            return text.strip()

        combined = text
        agg_in = int(self.last_usage.get("input_tokens", 0))
        agg_out = int(self.last_usage.get("output_tokens", 0))
        # Cache over the whole continuation chain: None until some leg reports numbers, else silent legs would look like a measured zero.
        agg_hit, agg_miss = self._agg_cache(None, None, self.last_usage)
        rounds = 0
        # Budget for the NEXT leg: after a truncated leg that returned nothing, an identical request fails the same way, so escalate.
        # Seeded from the EFFECTIVE budget (reasoning models are lifted to _REASONING_TOKEN_FLOOR), else identical calls repeat until doubling overtakes the floor.
        round_budget = self._effective_budget(max_tokens)
        budget_ceiling = max(round_budget, round_budget * _CONTINUE_ESCALATION_CAP)
        last_leg_empty = not text
        while stop_reason in _TRUNCATION_REASONS and rounds < max_rounds:
            # Escalate on EVERY truncated leg, not only an empty one: a leg that wrote text and still hit the ceiling proves the budget cannot
            # finish the answer, and re-asking at the same number re-sends the whole prompt for nothing.
            if last_leg_empty and round_budget >= budget_ceiling:
                # Already at the ceiling and still nothing came back: stop paying.
                break
            round_budget = min(round_budget * 2, budget_ceiling)
            rounds += 1
            cont_text, stop_reason = self._complete_once(
                system, user, round_budget, temperature, prior=combined
            )
            agg_in += int(self.last_usage.get("input_tokens", 0))
            agg_out += int(self.last_usage.get("output_tokens", 0))
            agg_hit, agg_miss = self._agg_cache(agg_hit, agg_miss, self.last_usage)
            last_leg_empty = not cont_text
            if cont_text:
                combined += cont_text
            elif stop_reason not in _TRUNCATION_REASONS:
                # Empty and NOT truncated: the model has nothing more to add; escalating would not help.
                break

        # Expose the aggregate usage of the whole continuation chain so the budget wrapper records one honest total.
        self.last_usage = {
            "input_tokens": agg_in,
            "output_tokens": agg_out,
            "total_tokens": agg_in + agg_out,
            "cache_hit_tokens": agg_hit,
            "cache_miss_tokens": agg_miss,
        }
        # Still truncated here means the rounds ran out, not that the model finished: the caller holds an answer that stops mid-sentence.
        self.last_answer_was_truncated = stop_reason in _TRUNCATION_REASONS
        return combined.strip()

    def _complete_once(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        *,
        prior: str | None,
        json_object: bool = False,
    ) -> tuple[str, str]:
        """One provider call; returns (text, stop_reason); with prior set it is a continuation round that resumes the partial answer."""
        if self.provider == "anthropic":
            return self._complete_anthropic(system, user, max_tokens, temperature, prior)
        if self.provider in {"openai", "deepseek", "huggingface", "local"}:
            return self._complete_openai_compatible(
                system, user, max_tokens, temperature, self.model, prior,
                json_object=json_object,
            )
        return self._complete_mock(system, user, max_tokens), "stop"

    def stream_complete(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        on_token: Any | None = None,
    ) -> str:
        """Stream a single-turn prompt, invoking on_token(text) for each token chunk."""
        if self.provider == "anthropic":
            return self._stream_anthropic(system, user, max_tokens, temperature, on_token)
        if self.provider in {"openai", "local"}:
            return self._stream_openai_compatible(
                system, user, max_tokens, temperature, self.model, on_token
            )
        # HuggingFace and mock do not support true streaming — fall back to complete.
        return self.complete(system, user, max_tokens, temperature)

    def _stream_anthropic(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        on_token: Any | None,
    ) -> str:
        kwargs: dict = {
            "model": self.model,
            "system": system,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if self._anthropic_supports_temperature(self.model):
            kwargs["temperature"] = temperature
        accumulated = []
        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                accumulated.append(text)
                if on_token is not None:
                    try:
                        on_token(text)
                    except Exception:  # noqa: BLE001, S110 — a token consumer must never break the stream
                        pass
            # Capture final usage from the completed message
            final_msg = stream.get_final_message()
        usage = getattr(final_msg, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage is not None else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage is not None else 0
        self._record_usage(in_tok, out_tok)
        return "".join(accumulated).strip()

    def _stream_openai_compatible(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        model: str,
        on_token: Any | None,
    ) -> str:
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._is_o_series(model):
            kwargs["max_completion_tokens"] = max_tokens
            kwargs.update(_reasoning_effort_kwargs())
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature
        accumulated = []
        in_tok = 0
        out_tok = 0
        cache_hit = None
        cache_miss = None
        for chunk in self._client.chat.completions.create(**kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            text = getattr(delta, "content", None) or ""
            if text:
                accumulated.append(text)
                if on_token is not None:
                    try:
                        on_token(text)
                    except Exception:  # noqa: BLE001, S110 — a token consumer must never break the stream
                        pass
            # Final chunk carries usage when stream_options.include_usage=True
            if chunk.usage is not None:
                in_tok = getattr(chunk.usage, "prompt_tokens", 0)
                out_tok = getattr(chunk.usage, "completion_tokens", 0)
                cache_hit = getattr(chunk.usage, "prompt_cache_hit_tokens", None)
                cache_miss = getattr(chunk.usage, "prompt_cache_miss_tokens", None)
        self._record_usage(in_tok, out_tok, cache_hit, cache_miss)
        return "".join(accumulated).strip()

    @staticmethod
    def _anthropic_generation(model: str) -> int | None:
        """The Claude generation in model, or None when the name predates it.

        Read as a number and compared, never spelled as a literal digit (a literal ages into a bug on the next generation).
        Gen-3 names (claude-<gen>-<minor>-<family>-<date>) return None; gen-4+ names are claude-<family>-<gen>[-<minor>][-<date>];
        letters are required where the family belongs, else the minor version would be read as the generation.
        """
        import re
        match = re.search(r"claude-[a-z]+-(\d+)", model.casefold())
        return int(match.group(1)) if match else None

    @classmethod
    def _anthropic_supports_temperature(cls, model: str) -> bool:
        """Whether model still accepts temperature (generation 4 dropped it; sending it gives HTTP 400)."""
        generation = cls._anthropic_generation(model)
        return generation is None or generation < 4

    @classmethod
    def _anthropic_supports_prefill(cls, model: str) -> bool:
        """Whether model accepts a trailing assistant message to continue from (native prefill); generation 5 rejects it and the request must end with a user message."""
        generation = cls._anthropic_generation(model)
        return generation is None or generation < 5

    def _complete_anthropic(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        prior: str | None = None,
    ) -> tuple[str, str]:
        messages: list[dict] = [{"role": "user", "content": user}]
        if prior:
            # The partial answer goes back either way, or the model restarts. Trailing whitespace is rejected only on a trailing assistant turn
            needs_closing_turn = not self._anthropic_supports_prefill(self.model)
            # (a prefill); once a user turn follows, boundary whitespace must survive so the continuation is glued at the right place.
            messages.append({
                "role": "assistant",
                "content": prior if needs_closing_turn else prior.rstrip(),
            })
            if needs_closing_turn:
                # These models reject a trailing assistant turn, so ask for the continuation explicitly.
                messages.append({"role": "user", "content": _CONTINUE_INSTRUCTION})
        kwargs: dict = {
            "model": self.model,
            "system": system,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if self._anthropic_supports_temperature(self.model):
            kwargs["temperature"] = temperature
        message = self._client.messages.create(**kwargs)
        parts = [block.text for block in message.content if hasattr(block, "text")]
        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage is not None else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage is not None else 0
        self._record_usage(in_tok, out_tok)
        stop_reason = str(getattr(message, "stop_reason", "") or "")
        return "".join(parts), stop_reason

    @staticmethod
    def _is_o_series(model: str) -> bool:
        """OpenAI models that require max_completion_tokens: o-series reasoning models and GPT-5+."""
        import re
        m = model.casefold()
        # o-series: o1, o3, o4, o1-mini, o3-mini, o4-mini, …
        if re.search(r"\bo\d", m):
            return True
        # gpt-5 and beyond (major version >= 5)
        match = re.search(r"\bgpt-(\d+)", m)
        return bool(match and int(match.group(1)) >= 5)

    def _reasoning_budget(self, max_tokens: int) -> int:
        """Effective token budget for an OpenAI reasoning model; never lowers the caller's budget."""
        floor = _REASONING_TOKEN_FLOOR
        if floor <= 0:
            return max_tokens
        return max(max_tokens, floor)

    def _reasons_internally(self) -> bool:
        """Does this model spend the output budget on thinking before speaking? The name table answers for known models; the roster for every model that ever proved it by going silent."""
        if self.provider in {"openai", "huggingface", "local"} and self._is_o_series(self.model):
            return True
        return is_known_reasoning_model(self.provider, self.model)

    def _effective_budget(self, max_tokens: int) -> int:
        """Tokens the next leg will really be allowed to spend."""
        if self._reasons_internally():
            return self._reasoning_budget(max_tokens)
        return max_tokens

    def _complete_openai_compatible(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        model: str,
        prior: str | None = None,
        *,
        json_object: bool = False,
    ) -> tuple[str, str]:
        # JSON mode on the first leg only: a continuation leg resumes the text
        # of an unfinished object, which is not a JSON object on its own.
        format_kwargs = (
            {"response_format": {"type": "json_object"}}
            if json_object and prior is None and self.provider in _JSON_MODE_PROVIDERS
            else {}
        )
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if prior:
            # OpenAI-compatible APIs have no native assistant-prefill continue,
            # so replay the partial answer and ask the model to resume from it.
            messages.append({"role": "assistant", "content": prior})
            messages.append({"role": "user", "content": _CONTINUE_INSTRUCTION})
        # o-series reasoning models: use max_completion_tokens, no temperature param
        if self._is_o_series(model):
            response = self._client.chat.completions.create(
                model=model,
                max_completion_tokens=self._reasoning_budget(max_tokens),
                messages=messages,
                **_reasoning_effort_kwargs(),
                **format_kwargs,
            )
        else:
            response = self._client.chat.completions.create(
                model=model,
                # Floor for a model from the thinking roster from the first request, else a small budget is empty and paid twice.
                max_tokens=self._effective_budget(max_tokens),
                temperature=temperature,
                messages=messages,
                **(_deepseek_thinking_kwargs() if self.provider == "deepseek" else {}),
                **format_kwargs,
            )
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage is not None else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage is not None else 0
        # Prompt cache: DeepSeek (and compatibles) send these fields beside prompt_tokens; without them cached input looks like full price and spend is overstated.
        cache_hit = getattr(usage, "prompt_cache_hit_tokens", None) if usage is not None else None
        cache_miss = getattr(usage, "prompt_cache_miss_tokens", None) if usage is not None else None
        self._record_usage(in_tok, out_tok, cache_hit, cache_miss)
        choice = response.choices[0]
        finish_reason = str(getattr(choice, "finish_reason", "") or "")
        return (choice.message.content or ""), finish_reason

    def _complete_mock(self, system: str, user: str, max_tokens: int) -> str:
        """Deterministic offline stub: planner mode (keyword-heuristic JSON plan) or synthesis mode (structured echo); no API key needed."""
        # Cheap deterministic "tokens" (1/4 char) so the audit harness gets non-zero numbers in mock mode.
        in_tok = (len(system) + len(user)) // 4
        if "PLANNER_MODE" in system or "the planner of an autonomous agent" in system:
            text = self._mock_plan(user)
            self._record_usage(in_tok, len(text) // 4)
            return text

        snippet = user[:200].replace("\n", " ")
        text = (
            "[mock-llm response]\n"
            f"system_chars={len(system)} user_chars={len(user)} max_tokens={max_tokens}\n"
            f"user_preview={snippet}\n"
            "answer=This is a deterministic stub. Replace AGENT_PROVIDER with a real backend "
            "(anthropic | openai | huggingface) and set the matching API key in .env to get a real answer."
        )
        self._record_usage(in_tok, len(text) // 4)
        return text

    @staticmethod
    def _mock_plan(user: str) -> str:
        """Heuristic stand-in for an LLM planner."""
        import json as _json
        import re as _re

        # Only inspect the question line — NOT the full prompt — so that
        # planner-scaffolding words like "current_date" don't trigger cues.
        file_hint_match = _re.search(r"file hint[:\s]*([^\n]+)", user, flags=_re.IGNORECASE)
        file_hint = file_hint_match.group(1).strip() if file_hint_match else ""
        if file_hint.lower() in {"none", "(none)", "-"}:
            file_hint = ""

        question_match = _re.search(r"question[:\s]*([^\n]+)", user, flags=_re.IGNORECASE)
        question = question_match.group(1).strip() if question_match else user.strip()
        qtext = question.lower()

        web_cues = (
            "internet", "news", "today", "latest", "current",
            "search the web", "интернет", "новост", "сегодня",
            "актуальн", "последн", "найди в",
        )
        file_cues = (
            "this file", "the file", "in the file", "section",
            "файл", "в файле", "раздел",
        )
        compare_cues = ("compare", "vs", "versus", "differ", "сравн", "сопостав")

        wants_web = any(c in qtext for c in web_cues)
        wants_file = bool(file_hint) and any(c in qtext for c in file_cues)
        wants_compare = any(c in qtext for c in compare_cues) and bool(file_hint)

        steps: list[dict] = []
        if wants_compare:
            steps.append({"tool": "file_read", "arguments": {"path": file_hint}, "rationale": "mock: compare -> read file"})
            steps.append({"tool": "web_search", "arguments": {"query": question, "max_results": 5}, "rationale": "mock: compare -> web"})
            reasoning = "Mock heuristic detected a compare-question with a file hint."
        elif wants_file:
            steps.append({"tool": "file_read", "arguments": {"path": file_hint}, "rationale": "mock: question references file"})
            reasoning = "Mock heuristic: question references the provided file."
        elif wants_web:
            steps.append({"tool": "web_search", "arguments": {"query": question, "max_results": 5}, "rationale": "mock: question needs external info"})
            reasoning = "Mock heuristic: question needs external info."
        elif file_hint and len(question) > 5:
            steps.append({"tool": "file_read", "arguments": {"path": file_hint}, "rationale": "mock: file is hinted and question is substantive"})
            reasoning = "Mock heuristic: file hint present, defaulting to file_read."
        else:
            reasoning = "Mock heuristic: general-knowledge question, no tools needed."

        return _json.dumps({"reasoning": reasoning, "steps": steps}, ensure_ascii=False)
