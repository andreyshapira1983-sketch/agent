"""Regression: web-egress steps must be dropped from a plan when the question is
pure introspection about the agent's OWN private repo/self.

Trace that motivated this: a question entirely about "your own repository, its
PRs, its architecture, its memory and sub-agents" caused the planner to run
`web_search`, which returned a CNN homepage that was then ingested into the
source registry. The public web cannot answer such questions, so the egress is
dropped at plan-sanitize time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.planner import (
    LLMPlanner,
    _drop_web_lookup_for_introspection,
    _is_self_repo_introspection_question,
    _wants_external_lookup,
)
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.list_dir import ListDirTool
from tools.read_logs import ReadLogsTool
from tools.rss_fetch import RssFetchTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool


TRACE_QUESTION = (
    "Изучи последние изменения в своём репозитории и объясни, какие три свойства "
    "твоего поведения изменились. Найди в своей архитектуре потенциальный баг. "
    "Проанализируй взаимодействие между тобой, твоей памятью и субагентами."
)


def _registry(workspace: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(ListDirTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(WebFetchTool())
    reg.register(RssFetchTool())
    reg.register(ReadLogsTool(workspace_root=workspace))
    return reg


class TestIntrospectionDetector:
    def test_trace_question_is_self_repo_introspection(self) -> None:
        assert _is_self_repo_introspection_question(TRACE_QUESTION) is True

    @pytest.mark.parametrize(
        "question",
        [
            "Проверь свой репозиторий на баги",
            "Что в твоей долговременной памяти про прошлые ошибки?",
            "Найди в своей архитектуре потенциальный баг",
            "What bug is in your architecture?",
            "Summarize what is in your long-term memory",
        ],
    )
    def test_internal_questions_detected(self, question: str) -> None:
        assert _is_self_repo_introspection_question(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "Compare your architecture with AutoGen and MetaGPT",
            "Сравни свою архитектуру с AutoGen",
            "What is the latest news about GPT-5?",
            "Fetch https://example.com and summarize it",
            "Найди научные статьи про LLM агентов на arxiv",
            "What is the capital of France?",
        ],
    )
    def test_external_or_unrelated_questions_not_flagged(self, question: str) -> None:
        assert _is_self_repo_introspection_question(question) is False

    def test_external_comparison_overrides_self_signal(self) -> None:
        # Self-repo term present ("your architecture") but external intent wins.
        q = "Compare your architecture with LangChain"
        assert _wants_external_lookup(q) is True
        assert _is_self_repo_introspection_question(q) is False


class TestDropHelper:
    def test_drops_all_web_egress_keeps_local(self) -> None:
        sources = [
            {"tool": "web_search", "label": "web:x"},
            {"tool": "list_dir", "label": "list_dir:."},
            {"tool": "web_fetch", "label": "web_fetch:y"},
            {"tool": "rss_fetch", "label": "rss_fetch:z"},
            {"tool": "read_logs", "label": "read_logs:latest"},
        ]
        warnings: list[str] = []
        out = _drop_web_lookup_for_introspection(sources, warnings)
        kept = [s["tool"] for s in out]
        assert kept == ["list_dir", "read_logs"]
        assert len(warnings) == 3
        assert all("self-repo introspection" in w for w in warnings)


class TestPlanIntegration:
    def test_web_search_dropped_for_introspection_plan(self, workspace: Path) -> None:
        canned = json.dumps(
            {
                "reasoning": "Investigate the repo.",
                "steps": [
                    {"tool": "web_search", "arguments": {"query": "latest changes PR 47 48 repository"}},
                    {"tool": "list_dir", "arguments": {"path": "."}},
                    {"tool": "read_logs", "arguments": {"last_n": 100}},
                ],
            }
        )
        planner = LLMPlanner(llm=FakeLLM(responses=[canned]), registry=_registry(workspace))
        out = planner.plan(question=TRACE_QUESTION, file_hint=None)

        tools = [s["tool"] for s in out.sources]
        assert "web_search" not in tools
        assert "list_dir" in tools
        assert "read_logs" in tools
        assert any("self-repo introspection" in w for w in out.warnings)

    def test_web_search_kept_for_external_comparison(self, workspace: Path) -> None:
        canned = json.dumps(
            {
                "reasoning": "Compare against external framework.",
                "steps": [
                    {"tool": "web_search", "arguments": {"query": "AutoGen architecture overview"}},
                    {"tool": "list_dir", "arguments": {"path": "."}},
                ],
            }
        )
        planner = LLMPlanner(llm=FakeLLM(responses=[canned]), registry=_registry(workspace))
        out = planner.plan(
            question="Compare your architecture with AutoGen", file_hint=None
        )

        tools = [s["tool"] for s in out.sources]
        assert "web_search" in tools
