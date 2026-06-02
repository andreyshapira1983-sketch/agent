"""Shared test fixtures and helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.planner import PlannerOutput


class FakeLLM:
    """A drop-in LLM stand-in.

    Records every `complete(...)` call and returns canned responses
    from `responses` in order. Falls back to an empty JSON object so
    a forgotten queue doesn't crash the planner.
    """

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        # Loop builds Action with side_effects="read" if tool_name is set,
        # which doesn't depend on .provider, but other code paths may peek.
        self.provider = "fake"
        self.model = "fake-1"

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class FakePlanner:
    """A Planner stand-in that emits whatever sources the test gives it.

    Bypasses `LLMPlanner._sanitize_step`, which is exactly what we want when
    we need to verify the loop's defenses (policy gate, registry lookup) on
    plans the real planner would never produce.
    """

    def __init__(self, sources: list[dict[str, Any]] | None = None, reasoning: str = "fake-plan"):
        self.sources: list[dict[str, Any]] = list(sources or [])
        self.reasoning = reasoning
        self.calls: list[dict[str, Any]] = []

    def plan(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
        llm=None,
    ) -> PlannerOutput:
        self.calls.append(
            {
                "question": question,
                "file_hint": file_hint,
                "history": history,
                "failure_context": failure_context,
                "forbidden_actions": forbidden_actions,
            }
        )
        # MVP-12: if the loop forbade a (tool, args) pair, the real
        # planner would drop it. FakePlanner emulates that by filtering
        # `self.sources` against the forbidden set on EACH call.
        import json as _json

        forbidden_set = set(forbidden_actions)
        sources_out: list[dict[str, Any]] = []
        for src in self.sources:
            tool = src.get("tool")
            args = src.get("arguments") or {}
            try:
                canonical = _json.dumps(args, sort_keys=True, ensure_ascii=False)
            except TypeError:
                canonical = ""
            if isinstance(tool, str) and canonical and (tool, canonical) in forbidden_set:
                continue
            sources_out.append(src)
        return PlannerOutput(
            reasoning=self.reasoning,
            sources=sources_out,
            raw_response="",
            warnings=[],
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An isolated workspace directory."""
    return tmp_path
