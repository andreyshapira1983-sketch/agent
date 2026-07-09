"""Thin LLM client wrapper.

Single point of contact for the loop. Provider is selected via
AGENT_PROVIDER env var. Supported:
    anthropic   -> Anthropic Messages API (default)
    openai      -> OpenAI Chat Completions
    huggingface -> HuggingFace Inference Router (free with HF_TOKEN)
    mock        -> Deterministic offline stub (no network; for testing)

MVP-14.5c — Usage tracking. Every successful `complete()` increments
`call_count` and accumulates `input_tokens` / `output_tokens` /
`total_tokens` so the audit harness (and future cost-budget hooks)
can see how many API tokens a single `run()` consumed. Token counts
come straight from the provider's `usage` payload — Anthropic uses
`usage.input_tokens` / `usage.output_tokens`, OpenAI-compatible APIs
use `usage.prompt_tokens` / `usage.completion_tokens`. When the
provider doesn't return usage we leave the counters at 0 rather than
guess.
"""
from __future__ import annotations

import os
from typing import Any, Optional


def _provider() -> str:
    return os.getenv("AGENT_PROVIDER", "anthropic").lower().strip()


def _default_model(provider: str) -> str:
    override = os.getenv("AGENT_MODEL")
    if override:
        return override
    if provider == "openai":
        return "gpt-4o-mini"
    if provider == "huggingface":
        return "meta-llama/Llama-3.3-70B-Instruct"
    if provider == "mock":
        return "mock-1"
    return "claude-sonnet-4-5"


DEFAULT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "2048"))

# Provider stop/finish reasons that mean "I ran out of output budget mid-answer"
# rather than "I finished naturally". When we see one of these we can ask the
# model to continue where it left off instead of returning a truncated answer.
_TRUNCATION_REASONS = frozenset({"max_tokens", "length"})


def _auto_continue_enabled() -> bool:
    """Whether truncated answers are auto-continued. Defaults to ON.

    Disable with ``AGENT_AUTO_CONTINUE=0`` (or false/no/off).
    """
    value = os.getenv("AGENT_AUTO_CONTINUE")
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _max_continuations() -> int:
    """Max follow-up rounds when an answer is truncated (default 4).

    A value of 0 disables continuation. Bounded so a misbehaving model can
    never loop forever; the budget governor is the other backstop.
    """
    raw = os.getenv("AGENT_MAX_CONTINUATIONS", "4")
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 4


# Instruction appended for OpenAI-compatible continuation turns. Anthropic does
# not need this — it continues an assistant prefill natively.
_CONTINUE_INSTRUCTION = (
    "Continue the previous response exactly where it stopped. "
    "Output only the continuation — no repetition of earlier text, no preamble, "
    "no explanation."
)


class LLM:
    """Minimal synchronous LLM wrapper with pluggable provider."""

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = (provider or _provider()).lower().strip()
        self.model = model or _default_model(self.provider)
        self._client = self._build_client()
        # Usage counters — incremented by `complete()` after every
        # successful call. Reset via `reset_usage()` between audit
        # scenarios. Counters NEVER throw — when the provider doesn't
        # report `usage` they simply stay flat.
        self.call_count: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        # `last_usage` carries the most recent per-call payload so an
        # audit can attribute spend to a specific scenario without
        # diffing the counters by hand.
        self.last_usage: dict[str, int] = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0
        }

    # ------------------------------------------------------------------
    # Usage helpers
    # ------------------------------------------------------------------

    def reset_usage(self) -> None:
        """Zero every counter. Used by the audit harness between
        scenarios so per-question budgets are independent."""
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last_usage = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0
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

    def _record_usage(self, in_tok: int, out_tok: int) -> None:
        """Internal: bump counters after a successful call. Bad inputs
        (negative or non-int) become 0 — usage tracking must NEVER
        crash a real API call."""
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
        if self.provider == "mock":
            return None
        raise ValueError(
            f"Unsupported AGENT_PROVIDER: {self.provider!r}. "
            "Use 'anthropic', 'openai', 'huggingface', or 'mock'."
        )

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
    ) -> str:
        """Send a single-turn prompt and return the text response.

        `temperature` should be 0.0 for the planner (deterministic JSON) and
        0.5..0.9 for synthesis (a little stylistic freedom).

        Large answers: when the provider stops because it hit ``max_tokens``
        (Anthropic ``stop_reason='max_tokens'`` / OpenAI ``finish_reason=
        'length'``) the answer is auto-continued — the partial text is fed back
        and the model resumes where it left off — until it finishes naturally or
        ``AGENT_MAX_CONTINUATIONS`` rounds are reached. Token usage is summed
        across every round so the budget ledger sees the full spend.
        """
        text, stop_reason = self._complete_once(
            system, user, max_tokens, temperature, prior=None
        )
        # The per-leg provider calls now return RAW text (no .strip()) so that
        # whitespace at a truncation boundary is preserved when legs are
        # concatenated below. Trimming happens once, centrally, on the final
        # answer. Mock text is returned verbatim to keep its deterministic shape.
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
        rounds = 0
        while stop_reason in _TRUNCATION_REASONS and rounds < max_rounds:
            rounds += 1
            cont_text, stop_reason = self._complete_once(
                system, user, max_tokens, temperature, prior=combined
            )
            agg_in += int(self.last_usage.get("input_tokens", 0))
            agg_out += int(self.last_usage.get("output_tokens", 0))
            if not cont_text:
                break
            combined += cont_text

        # Expose the aggregate usage of the whole continuation chain so the
        # budget wrapper records one honest total instead of just the last leg.
        self.last_usage = {
            "input_tokens": agg_in,
            "output_tokens": agg_out,
            "total_tokens": agg_in + agg_out,
        }
        return combined.strip()

    def _complete_once(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        *,
        prior: Optional[str],
    ) -> tuple[str, str]:
        """One provider call. Returns ``(text, stop_reason)``.

        When *prior* is set this is a continuation round: the partial answer so
        far is supplied so the provider resumes instead of restarting.
        """
        if self.provider == "anthropic":
            return self._complete_anthropic(system, user, max_tokens, temperature, prior)
        if self.provider == "openai":
            return self._complete_openai_compatible(
                system, user, max_tokens, temperature, self.model, prior
            )
        if self.provider == "huggingface":
            return self._complete_openai_compatible(
                system, user, max_tokens, temperature, self.model, prior
            )
        return self._complete_mock(system, user, max_tokens), "stop"

    def stream_complete(
        self,
        system: str,
        user: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        on_token: Optional[Any] = None,
    ) -> str:
        """Stream a single-turn prompt, invoking *on_token(text)* for each token chunk.

        Returns the full accumulated text so callers can use it for post-processing
        (verification, memory writes, etc.) exactly like :meth:`complete`.
        Falls back to :meth:`complete` for providers that do not support streaming
        (HuggingFace, mock).

        Args:
            on_token: Optional callable ``(str) -> None`` called for each token
                      chunk as it arrives. Useful for live CLI display
                      (``lambda t: print(t, end="", flush=True)``).
        """
        if self.provider == "anthropic":
            return self._stream_anthropic(system, user, max_tokens, temperature, on_token)
        if self.provider == "openai":
            return self._stream_openai_compatible(system, user, max_tokens, temperature, self.model, on_token)
        # HuggingFace and mock do not support true streaming — fall back to complete.
        return self.complete(system, user, max_tokens, temperature)

    def _stream_anthropic(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        on_token: Optional[Any],
    ) -> str:
        kwargs: dict = dict(
            model=self.model,
            system=system,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
        )
        if self._anthropic_supports_temperature(self.model):
            kwargs["temperature"] = temperature
        accumulated = []
        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                accumulated.append(text)
                if on_token is not None:
                    try:
                        on_token(text)
                    except Exception:  # noqa: BLE001
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
        on_token: Optional[Any],
    ) -> str:
        kwargs: dict = dict(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=True,
            stream_options={"include_usage": True},
        )
        if self._is_o_series(model):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature
        accumulated = []
        in_tok = 0
        out_tok = 0
        for chunk in self._client.chat.completions.create(**kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            text = getattr(delta, "content", None) or ""
            if text:
                accumulated.append(text)
                if on_token is not None:
                    try:
                        on_token(text)
                    except Exception:  # noqa: BLE001
                        pass
            # Final chunk carries usage when stream_options.include_usage=True
            if chunk.usage is not None:
                in_tok = getattr(chunk.usage, "prompt_tokens", 0)
                out_tok = getattr(chunk.usage, "completion_tokens", 0)
        self._record_usage(in_tok, out_tok)
        return "".join(accumulated).strip()

    @staticmethod
    def _anthropic_supports_temperature(model: str) -> bool:
        """claude-opus-4+ and some other gen-4 models deprecated the temperature param."""
        import re
        # Match "claude-*-4" or "claude-*-4-N" patterns (generation 4+)
        return not bool(re.search(r"claude-\w+-4", model.casefold()))

    def _complete_anthropic(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        prior: Optional[str] = None,
    ) -> tuple[str, str]:
        messages: list[dict] = [{"role": "user", "content": user}]
        if prior:
            # Anthropic continues an assistant prefill natively. The prefill must
            # not end with trailing whitespace, so send it rstripped; the raw
            # continuation text is appended to the full answer by the caller.
            messages.append({"role": "assistant", "content": prior.rstrip()})
        kwargs: dict = dict(
            model=self.model,
            system=system,
            max_tokens=max_tokens,
            messages=messages,
        )
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
        """OpenAI models that require max_completion_tokens instead of max_tokens.

        Includes:
        - o-series reasoning models (o1, o3, o4, o3-mini, o4-mini …)
        - GPT-5+ generation (gpt-5.x, gpt-5-…) which share the same API surface
        """
        import re
        m = model.casefold()
        # o-series: o1, o3, o4, o1-mini, o3-mini, o4-mini, …
        if re.search(r"\bo\d", m):
            return True
        # gpt-5 and beyond (major version >= 5)
        match = re.search(r"\bgpt-(\d+)", m)
        if match and int(match.group(1)) >= 5:
            return True
        return False

    def _complete_openai_compatible(
        self,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        model: str,
        prior: Optional[str] = None,
    ) -> tuple[str, str]:
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
                max_completion_tokens=max_tokens,
                messages=messages,
            )
        else:
            response = self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage is not None else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage is not None else 0
        self._record_usage(in_tok, out_tok)
        choice = response.choices[0]
        finish_reason = str(getattr(choice, "finish_reason", "") or "")
        return (choice.message.content or ""), finish_reason

    def _complete_mock(self, system: str, user: str, max_tokens: int) -> str:
        """Deterministic offline stub.

        Two modes (detected from the system prompt):
          - Planner mode: emit a JSON plan picked by simple keyword heuristics.
            Lets the full LLM-planning loop be tested with no API key.
          - Synthesis mode: emit a structured echo so the rest of the loop
            can be verified without any external LLM.
        """
        # Cheap deterministic "tokens": approximate as 1/4 char to give
        # the audit harness non-zero numbers in mock mode.
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
        """Heuristic stand-in for an LLM planner.

        Inspects the user prompt for cues about the question and any file hint,
        then emits a deterministic JSON plan with the same shape a real model
        would return. Good enough to exercise the parser end-to-end offline.
        """
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
