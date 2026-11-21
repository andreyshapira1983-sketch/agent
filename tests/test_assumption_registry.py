"""Tests for Layer 5 — Assumption Registry.

Coverage:
  - Assumption model defaults / field validation
  - extract_from_question: language, python_version, scope, run-command
  - extract_from_plan: per-tool heuristics, deduplication, .py extension
  - AssumptionRegistry: register, register_many, active filter, mark_verified,
    to_prompt_block, to_log_payload
  - AssumptionStore: save, save_many, load_by_run, load_recent,
    load_recent_runs, corrupted-line resilience, empty file
  - AgentLoop integration: accepts assumption_store param, populates
    last_assumptions after run, emits assumption events, no-store path safe
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.assumption_registry import (
    Assumption,
    AssumptionRegistry,
    AssumptionStore,
    extract_from_plan,
)

# ============================================================
# Helpers
# ============================================================

def _make_step(tool: str, **args) -> dict:
    return {"tool": tool, "arguments": args}


def _store(tmp_path: Path) -> AssumptionStore:
    return AssumptionStore(tmp_path / "assumptions.jsonl")


# ============================================================
# TestExtractFromPlan
# ============================================================

class TestExtractFromPlan:
    def test_file_read_py_format(self):
        sources = [_make_step("file_read", path="main.py")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "file_format" in cats
        fmt = next(a for a in result if a.category == "file_format")
        assert "python" in fmt.text.lower() or "syntactically" in fmt.text.lower()


# ============================================================
# TestAssumptionRegistry
# ============================================================

class TestAssumptionRegistry:
    def test_mark_verified_not_found(self):
        reg = AssumptionRegistry()
        result = reg.mark_verified("nonexistent_id", verified=True)
        assert result is False


    def test_to_prompt_block_confirmed_tag(self):
        reg = AssumptionRegistry()
        a = reg.register("verified assumption")
        reg.mark_verified(a.id, verified=True)
        block = reg.to_prompt_block()
        assert "[confirmed]" in block

    def test_to_prompt_block_skips_contradicted(self):
        reg = AssumptionRegistry()
        a = reg.register("contradicted")
        reg.mark_verified(a.id, verified=False)
        block = reg.to_prompt_block()
        assert block == ""


# ============================================================
# TestAssumptionStore
# ============================================================

class TestAssumptionStore:
    def test_save_many_empty_returns_zero(self, tmp_path):
        store = _store(tmp_path)
        n = store.save_many([])
        assert n == 0


    def test_load_recent_runs_groups_by_run(self, tmp_path):
        store = _store(tmp_path)
        store.save(Assumption(text="A", run_id="run_1"))
        store.save(Assumption(text="B", run_id="run_2"))
        store.save(Assumption(text="C", run_id="run_1"))
        grouped = store.load_recent_runs(n=2)
        assert "run_1" in grouped or "run_2" in grouped


    def test_missing_file_returns_empty(self, tmp_path):
        store = AssumptionStore(tmp_path / "nonexistent" / "assumptions.jsonl")
        assert store.load_by_run("anything") == []
        assert store.load_recent(10) == []


# ============================================================
# TestAgentLoopAssumptionIntegration
# ============================================================

class TestAgentLoopAssumptionIntegration:
    """Black-box tests: verify AgentLoop accepts AssumptionStore and
    exposes last_assumptions after a run."""

    def _make_agent(self, tmp_path, with_store=True):
        """Build a minimal AgentLoop with mocked I/O."""
        from core.logger import TraceLogger
        from core.loop import AgentLoop
        from core.memory import WorkingMemory
        from core.planner import LLMPlanner
        from core.policy import PolicyGate
        from tools.base import ToolRegistry

        class _FakeLLM:
            provider = "mock"
            model = "mock-model"
            def complete(self, system, user, **kw):
                return "Conclusion: done.\nSources:\n1. [general-knowledge]\nConfidence: high\nUnverified: nothing"
            def stream(self, system, user, **kw):
                yield "Conclusion: done.\nSources:\n1. [general-knowledge]\nConfidence: high\nUnverified: nothing"

        llm = _FakeLLM()
        logger = TraceLogger(
            trace_id="trace_test_001",
            log_dir=tmp_path,
            verbose=False,
        )
        registry = ToolRegistry()
        policy = PolicyGate(registry)
        memory = WorkingMemory()
        planner = LLMPlanner(llm=llm, registry=registry)
        store = _store(tmp_path) if with_store else None

        return AgentLoop(
            registry=registry,
            policy=policy,
            llm=llm,
            logger=logger,
            planner=planner,
            memory=memory,
            assumption_store=store,
        )


    def test_plan_assumptions_extracted(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path)
        mock_plan = PlannerOutput(
            reasoning="will read a file",
            sources=[{
                "tool": "file_read",
                "arguments": {"path": "README.md"},
                "label": "read_readme",
                "expected_outcome": "Non-empty UTF-8 text from the file.",
            }],
            raw_response="",
            warnings=[],
        )
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            agent.run("Show me the README file content please")
        assert agent.last_assumptions is not None
        cats = [a.category for a in agent.last_assumptions.assumptions]
        assert "file_encoding" in cats


    def test_last_assumptions_run_id_matches_trace(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path)
        mock_plan = PlannerOutput(reasoning="noop", sources=[], raw_response="", warnings=[])
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            agent.run("Проверь python скрипт и запусти тесты пожалуйста")
        if agent.last_assumptions:
            for a in agent.last_assumptions.assumptions:
                assert a.run_id == "trace_test_001"


# ---------------------------------------------------------------------------
# Layer connections
# ---------------------------------------------------------------------------

class TestLayerConnections:
    """Tests for cross-layer bridges: Layer 4→5 and Layer 2→5."""

    # ------------------------------------------------------------------ L4→5


    # ------------------------------------------------------------------ L2→5

    def test_restore_from_store_marks_ids_as_restored(self):
        """Assumptions loaded from store must be in _restored_ids."""
        reg = AssumptionRegistry(run_id="r1")
        a = Assumption(run_id="r1", text="Test.", category="general",
                       source="heuristic", confidence=0.80)
        reg.restore_from_store([a])
        assert a.id in reg._restored_ids
        assert a in reg.assumptions


    def test_store_not_duplicated_on_second_run(self, tmp_path):
        """If store already has assumptions for run_id, restore_from_store
        prevents them being written again."""
        store = _store(tmp_path)
        a = Assumption(run_id="run_abc", text="Existing.", source="heuristic")
        store.save(a)

        # Simulate new run with same run_id
        reg = AssumptionRegistry(run_id="run_abc")
        prior = store.load_by_run("run_abc")
        reg.restore_from_store(prior)

        # Add one new assumption
        new_a = Assumption(run_id="run_abc", text="Fresh.", source="question")
        reg.register_many([new_a])

        # Only save new ones
        saved_count = store.save_many(reg.new_assumptions)
        assert saved_count == 1

        # Total in store: 2 (original + new), not 3
        all_loaded = store.load_by_run("run_abc")
        assert len(all_loaded) == 2


# ============================================================
# Security / validation tests (audit fixes)
# ============================================================

class TestAssumptionSecurityValidation:
    """Tests for security fixes applied after deep audit."""

    # ---- XML injection in to_prompt_block --------------------------------


    # ---- confidence validator -------------------------------------------

    def test_confidence_above_one_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="confidence"):
            Assumption(text="test", confidence=1.5)

    def test_confidence_below_zero_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="confidence"):
            Assumption(text="test", confidence=-0.1)


    # ---- source literal --------------------------------------------------


    # ---- text validator --------------------------------------------------

    def test_empty_text_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="empty"):
            Assumption(text="")

    def test_whitespace_only_text_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="empty"):
            Assumption(text="   ")

    def test_text_too_long_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="too long"):
            Assumption(text="x" * 501)
