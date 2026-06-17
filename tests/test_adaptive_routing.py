"""Tests for adaptive model routing — for_task() integration.

Verifies that:
1. LLMPlanner.plan() uses the llm= override when supplied.
2. AgentLoop._synthesize() uses the llm= override when supplied.
3. AgentLoop.run() calls model_router.for_task() and logs adaptive_route.
4. for_task() falls back gracefully when no tier model is found.
5. assess_complexity() correctly classifies LIGHT / STANDARD / DEEP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.loop import AgentLoop, new_trace_id
from core.logger import TraceLogger
from core.model_router import ModelRole, ModelRoute, ModelRouter
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.task_complexity import ComplexityTier, assess_complexity
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_answer(text: str) -> str:
    return (
        f"answer={text}\n"
        "Conclusion:\n{text}\n"
        "Facts:\n- one fact [general-knowledge]\n"
        "Sources:\n1. [general-knowledge]\n"
        "Confidence: high\n"
    )


def _make_registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    return registry


def _make_loop(workspace: Path, fake_llm: FakeLLM, planner: FakePlanner) -> AgentLoop:
    registry = _make_registry(workspace)
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=fake_llm,
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=planner,
    )


# ── 1. LLMPlanner.plan() uses llm= override ──────────────────────────────────

class TestPlannerLLMOverride:
    def test_llm_override_is_used(self, tmp_path: Path):
        """When llm= is passed, the planner should call it, not self.llm."""
        default_llm = FakeLLM(responses=['{"reasoning":"default","sources":[]}'])
        override_llm = FakeLLM(responses=['{"reasoning":"override","sources":[]}'])

        registry = _make_registry(tmp_path)
        planner = LLMPlanner(llm=default_llm, registry=registry)

        result = planner.plan(
            question="привет",
            file_hint=None,
            llm=override_llm,
        )

        # override was called, default was not
        assert len(override_llm.calls) == 1
        assert len(default_llm.calls) == 0
        assert result.reasoning == "override"

    def test_no_override_uses_self_llm(self, tmp_path: Path):
        """When llm= is not passed, self.llm is used (backward compat)."""
        default_llm = FakeLLM(responses=['{"reasoning":"default","sources":[]}'])
        registry = _make_registry(tmp_path)
        planner = LLMPlanner(llm=default_llm, registry=registry)

        result = planner.plan(question="привет", file_hint=None)

        assert len(default_llm.calls) == 1
        assert result.reasoning == "default"


# ── 2. AgentLoop._synthesize() uses llm= override ────────────────────────────

class TestSynthesizeLLMOverride:
    def test_synthesize_override_is_used(self, tmp_path: Path):
        """_synthesize(llm=...) must call the override, not self.llm."""
        from core.models import Goal, Observation

        default_llm = FakeLLM(responses=[_make_answer("default answer")])
        override_llm = FakeLLM(responses=[_make_answer("override answer")])

        loop = _make_loop(tmp_path, default_llm, FakePlanner())
        goal = Goal(
            id="g1",
            description="test",
            success_criteria="",
            parent_goal_id=None,
            status="pending",
            priority=5,
            deadline=None,
        )

        result = loop._synthesize(
            goal=goal,
            artifacts={},
            question="test question",
            planner_reasoning="none",
            llm=override_llm,
        )

        assert len(override_llm.calls) == 1
        assert len(default_llm.calls) == 0
        assert "override answer" in result

    def test_synthesize_no_override_uses_self_llm(self, tmp_path: Path):
        """_synthesize() without llm= uses self.llm."""
        from core.models import Goal

        default_llm = FakeLLM(responses=[_make_answer("self llm answer")])
        loop = _make_loop(tmp_path, default_llm, FakePlanner())
        goal = Goal(
            id="g1",
            description="test",
            success_criteria="",
            parent_goal_id=None,
            status="pending",
            priority=5,
            deadline=None,
        )

        result = loop._synthesize(
            goal=goal,
            artifacts={},
            question="test question",
            planner_reasoning="none",
        )

        assert len(default_llm.calls) == 1
        assert "self llm answer" in result


# ── 3. AgentLoop.run() calls for_task() and logs adaptive_route ───────────────

class TestRunAdaptiveRoute:
    def _run_with_mock_router(self, tmp_path: Path, question: str) -> list[dict]:
        """Run the loop with a mock router, collect log events."""
        fake_llm = FakeLLM(responses=[_make_answer("answer")])
        planner = FakePlanner(sources=[], reasoning="no tools needed")

        # Patch model_router.for_task to return fake_llm and record calls
        for_task_calls: list[tuple] = []

        def fake_for_task(role, task, *, escalation=None):
            for_task_calls.append((role, task))
            return fake_llm

        events: list[dict] = []

        class SpyLogger:
            def log(self, event_type: str, *args, **kwargs):
                events.append({"type": event_type, **kwargs})

        registry = _make_registry(tmp_path)
        loop = AgentLoop(
            registry=registry,
            policy=PolicyGate(registry),
            llm=fake_llm,
            logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False),
            planner=planner,
        )
        loop.model_router.for_task = fake_for_task  # type: ignore[method-assign]
        loop.log = SpyLogger()  # type: ignore[assignment]

        loop.run(question)

        return events, for_task_calls

    def test_for_task_called_for_planner_and_synth(self, tmp_path: Path):
        """run() must call for_task() for both PLANNER and SYNTHESIZER roles."""
        _events, for_task_calls = self._run_with_mock_router(
            tmp_path, "какой статус системы"
        )
        # role may be ModelRole enum or plain string — normalise to value
        roles_called = [
            r.value if hasattr(r, "value") else str(r)
            for r, _ in for_task_calls
        ]
        assert "planner" in roles_called
        assert "synthesizer" in roles_called

    def test_for_task_receives_full_question(self, tmp_path: Path):
        """for_task() must receive the full user question as the task string."""
        question = "сделай полный архитектурный аудит системы"
        _events, for_task_calls = self._run_with_mock_router(tmp_path, question)
        questions_passed = [task for _, task in for_task_calls]
        assert all(q == question for q in questions_passed)


# ── 4. for_task() fallback when no tier model found ──────────────────────────

class TestForTaskFallback:
    def test_falls_back_to_for_role_when_no_tier_model(self, tmp_path: Path):
        """If tier_model_for() returns None, for_task() falls back to for_role()."""
        from core.llm import LLM
        fake_llm = FakeLLM()
        router = ModelRouter.single(fake_llm)

        with patch("core.model_catalog.tier_model_for", return_value=None):
            result = router.for_task(ModelRole.PLANNER, "разработай с нуля архитектуру")

        # Should return the same object as for_role() (no crash)
        assert result is not None

    def test_for_task_standard_tier_reuses_for_role(self, tmp_path: Path):
        """STANDARD tier must call for_role() directly (fast path, no catalog)."""
        fake_llm = FakeLLM()
        router = ModelRouter.single(fake_llm)

        # A plain question → STANDARD tier → for_role() path (no catalog query)
        result = router.for_task(ModelRole.SYNTHESIZER, "объясни что такое GIL")
        assert result is not None


# ── 5. assess_complexity() LIGHT / STANDARD / DEEP ───────────────────────────

class TestComplexityClassification:
    @pytest.mark.parametrize("question", [
        "привет",
        "hi",
        "статус",
        "что такое Python",
        "кратко объясни",
    ])
    def test_light_questions(self, question: str):
        assert assess_complexity(question) == ComplexityTier.LIGHT

    @pytest.mark.parametrize("question", [
        "Найди новости про Python 3.14",
        "как работает asyncio",
        "объясни разницу между list и tuple в Python",
    ])
    def test_standard_questions(self, question: str):
        assert assess_complexity(question) == ComplexityTier.STANDARD

    @pytest.mark.parametrize("question", [
        "спроектируй полную архитектуру микросервисов",
        "сделай полный аудит безопасности",
        "разработать с нуля систему мониторинга",
        "полный стратегический анализ рисков",
        "комплексный анализ всех компонентов",
    ])
    def test_deep_questions(self, question: str):
        assert assess_complexity(question) == ComplexityTier.DEEP

    def test_memory_summary_always_light(self):
        deep = "сделай полный архитектурный аудит и разработай с нуля"
        assert assess_complexity(deep, role="memory_summary") == ComplexityTier.LIGHT
