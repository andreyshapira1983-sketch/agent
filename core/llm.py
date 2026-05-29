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
        """
        if self.provider == "anthropic":
            return self._complete_anthropic(system, user, max_tokens, temperature)
        if self.provider == "openai":
            return self._complete_openai_compatible(system, user, max_tokens, temperature, self.model)
        if self.provider == "huggingface":
            return self._complete_openai_compatible(system, user, max_tokens, temperature, self.model)
        return self._complete_mock(system, user, max_tokens)

    def _complete_anthropic(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        message = self._client.messages.create(
            model=self.model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": user}],
        )
        parts = [block.text for block in message.content if hasattr(block, "text")]
        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage is not None else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage is not None else 0
        self._record_usage(in_tok, out_tok)
        return "".join(parts).strip()

    def _complete_openai_compatible(self, system: str, user: str, max_tokens: int, temperature: float, model: str) -> str:
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage is not None else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage is not None else 0
        self._record_usage(in_tok, out_tok)
        return (response.choices[0].message.content or "").strip()

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
