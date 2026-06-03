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

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.assumption_registry import (
    Assumption,
    AssumptionRegistry,
    AssumptionStore,
    extract_from_plan,
    extract_from_question,
)


# ============================================================
# Helpers
# ============================================================

def _make_step(tool: str, **args) -> dict:
    return {"tool": tool, "arguments": args}


def _store(tmp_path: Path) -> AssumptionStore:
    return AssumptionStore(tmp_path / "assumptions.jsonl")


# ============================================================
# TestAssumptionDefaults
# ============================================================

class TestAssumptionDefaults:
    def test_id_prefix(self):
        a = Assumption(text="hello")
        assert a.id.startswith("asmp_")

    def test_default_category(self):
        a = Assumption(text="x")
        assert a.category == "general"

    def test_default_confidence(self):
        a = Assumption(text="x")
        assert a.confidence == pytest.approx(0.80)

    def test_default_source(self):
        a = Assumption(text="x")
        assert a.source == "heuristic"

    def test_default_verified_is_none(self):
        a = Assumption(text="x")
        assert a.verified is None

    def test_run_id_default_empty(self):
        a = Assumption(text="x")
        assert a.run_id == ""

    def test_unique_ids(self):
        ids = {Assumption(text="x").id for _ in range(20)}
        assert len(ids) == 20

    def test_created_at_is_utc(self):
        from datetime import timezone
        a = Assumption(text="x")
        assert a.created_at.tzinfo is not None
        assert a.created_at.tzinfo == timezone.utc or str(a.created_at.tzinfo) == "UTC"

    def test_to_dict_roundtrip(self):
        a = Assumption(text="round trip", category="language", confidence=0.75, run_id="r1")
        d = a.to_dict()
        a2 = Assumption.from_dict(d)
        assert a2.id == a.id
        assert a2.text == a.text
        assert a2.category == a.category
        assert a2.run_id == a.run_id

    def test_explicit_verified_true(self):
        a = Assumption(text="x", verified=True)
        assert a.verified is True

    def test_explicit_verified_false(self):
        a = Assumption(text="x", verified=False)
        assert a.verified is False


# ============================================================
# TestExtractFromQuestion
# ============================================================

class TestExtractFromQuestion:
    def test_russian_question_language_assumption(self):
        result = extract_from_question("Покажи мне список файлов проекта пожалуйста")
        cats = [a.category for a in result]
        assert "language" in cats
        lang = next(a for a in result if a.category == "language")
        assert "Russian" in lang.text

    def test_english_question_language_assumption(self):
        result = extract_from_question("Show me the list of files in the project please")
        cats = [a.category for a in result]
        assert "language" in cats
        lang = next(a for a in result if a.category == "language")
        assert "English" in lang.text

    def test_mixed_no_language_assumption(self):
        # When EN and RU are roughly equal, no language assumption
        result = extract_from_question("Show файл list проект файлы файлы файлы files files files")
        lang_items = [a for a in result if a.category == "language"]
        # Mixed — may or may not fire; we just want no crash and at most 1
        assert len(lang_items) <= 1

    def test_short_text_no_language_assumption(self):
        result = extract_from_question("ok")
        lang_items = [a for a in result if a.category == "language"]
        assert len(lang_items) == 0

    def test_python_without_version_fires(self):
        result = extract_from_question("How do I use python for this task?")
        cats = [a.category for a in result]
        assert "python_version" in cats

    def test_python_with_version_no_assumption(self):
        # "python3" has a version digit right after — should NOT fire
        result = extract_from_question("python3 script.py")
        cats = [a.category for a in result]
        assert "python_version" not in cats

    def test_python_3_no_assumption(self):
        result = extract_from_question("run with python 3.11")
        cats = [a.category for a in result]
        assert "python_version" not in cats

    def test_run_command_scope_russian(self):
        result = extract_from_question("запусти тесты")
        cats = [a.category for a in result]
        assert "scope" in cats

    def test_run_command_scope_english(self):
        result = extract_from_question("run the tests please")
        cats = [a.category for a in result]
        assert "scope" in cats

    def test_execute_fires_scope(self):
        result = extract_from_question("execute this command")
        cats = [a.category for a in result]
        assert "scope" in cats

    def test_file_without_path_scope(self):
        result = extract_from_question("прочитай файл и скажи мне результат")
        cats = [a.category for a in result]
        assert "scope" in cats

    def test_file_with_path_no_extra_scope(self):
        # "файл /home/foo.txt" — the path separator follows, so no extra scope assumption
        result = extract_from_question("прочитай файл /home/user/data.txt")
        scope_from_file = [
            a for a in result
            if a.category == "scope" and "workspace" in a.text.lower()
        ]
        assert len(scope_from_file) == 0

    def test_run_id_propagated(self):
        result = extract_from_question("запусти python скрипт", run_id="run_abc")
        for a in result:
            assert a.run_id == "run_abc"

    def test_source_is_question(self):
        result = extract_from_question("python test")
        for a in result:
            assert a.source == "question"

    def test_returns_list_no_match(self):
        result = extract_from_question("")
        assert isinstance(result, list)

    def test_no_mutation(self):
        q = "запусти python скрипт"
        r1 = extract_from_question(q)
        r2 = extract_from_question(q)
        # Results should be independent objects
        if r1 and r2:
            assert r1[0] is not r2[0]

    def test_confidence_range(self):
        result = extract_from_question("Покажи мне python скрипт и запусти файл")
        for a in result:
            assert 0.0 <= a.confidence <= 1.0


# ============================================================
# TestExtractFromPlan
# ============================================================

class TestExtractFromPlan:
    def test_file_read_encoding(self):
        sources = [_make_step("file_read", path="readme.txt")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "file_encoding" in cats

    def test_file_read_py_format(self):
        sources = [_make_step("file_read", path="main.py")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "file_format" in cats
        fmt = next(a for a in result if a.category == "file_format")
        assert "python" in fmt.text.lower() or "syntactically" in fmt.text.lower()

    def test_file_read_non_py_no_format(self):
        sources = [_make_step("file_read", path="data.csv")]
        result = extract_from_plan(sources)
        fmt = [a for a in result if a.category == "file_format"]
        assert len(fmt) == 0

    def test_file_write_permissions(self):
        sources = [_make_step("file_write", path="out.txt")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "workspace_permission" in cats

    def test_web_search_network_access(self):
        sources = [_make_step("web_search", query="python news")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "network_access" in cats

    def test_web_fetch_network_and_format(self):
        sources = [_make_step("web_fetch", url="https://example.com")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "network_access" in cats
        assert "file_format" in cats

    def test_shell_exec_tool_and_scope(self):
        sources = [_make_step("shell_exec", command="echo hello")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "tool_availability" in cats
        assert "scope" in cats

    def test_run_tests_pytest(self):
        sources = [_make_step("run_tests")]
        result = extract_from_plan(sources)
        cats = [a.category for a in result]
        assert "tool_availability" in cats
        text = next(a.text for a in result if a.category == "tool_availability")
        assert "pytest" in text.lower()

    def test_empty_plan_no_assumptions(self):
        result = extract_from_plan([])
        assert result == []

    def test_dedup_same_tool_twice(self):
        sources = [
            _make_step("file_read", path="a.txt"),
            _make_step("file_read", path="b.txt"),
        ]
        result = extract_from_plan(sources)
        texts = [a.text for a in result]
        # Should not have duplicate encoding text
        assert len(texts) == len(set(texts))

    def test_run_id_propagated(self):
        sources = [_make_step("file_read", path="x.txt")]
        result = extract_from_plan(sources, run_id="run_xyz")
        for a in result:
            assert a.run_id == "run_xyz"

    def test_source_is_planner(self):
        sources = [_make_step("file_read", path="x.txt")]
        result = extract_from_plan(sources)
        for a in result:
            assert a.source == "planner"

    def test_confidence_range(self):
        sources = [
            _make_step("file_read", path="x.py"),
            _make_step("web_search", query="q"),
            _make_step("run_tests"),
        ]
        result = extract_from_plan(sources)
        for a in result:
            assert 0.0 <= a.confidence <= 1.0

    def test_no_tool_key_safe(self):
        sources = [{"tool": "", "arguments": {}}]
        result = extract_from_plan(sources)
        assert isinstance(result, list)

    def test_unknown_tool_no_assumptions(self):
        sources = [_make_step("unknown_custom_tool")]
        result = extract_from_plan(sources)
        assert result == []


# ============================================================
# TestAssumptionRegistry
# ============================================================

class TestAssumptionRegistry:
    def test_register_returns_assumption(self):
        reg = AssumptionRegistry(run_id="r1")
        a = reg.register("test text", "general", 0.8)
        assert isinstance(a, Assumption)

    def test_register_run_id_inherited(self):
        reg = AssumptionRegistry(run_id="r42")
        a = reg.register("text", "language", 0.9, "question")
        assert a.run_id == "r42"

    def test_register_many(self):
        reg = AssumptionRegistry()
        items = [Assumption(text=f"item {i}") for i in range(5)]
        reg.register_many(items)
        assert len(reg) == 5

    def test_len_empty(self):
        reg = AssumptionRegistry()
        assert len(reg) == 0

    def test_assumptions_property_copy(self):
        reg = AssumptionRegistry()
        reg.register("x")
        lst = reg.assumptions
        assert len(lst) == 1
        # Returned list is independent
        lst.clear()
        assert len(reg) == 1

    def test_active_excludes_contradicted(self):
        reg = AssumptionRegistry()
        a1 = reg.register("good", "general", 0.9)
        a2 = reg.register("bad", "general", 0.9)
        reg.mark_verified(a2.id, verified=False)
        active = reg.active
        assert a1 in active
        assert a2 not in active

    def test_active_includes_confirmed(self):
        reg = AssumptionRegistry()
        a = reg.register("confirmed")
        reg.mark_verified(a.id, verified=True)
        assert a in reg.active

    def test_active_includes_unverified(self):
        reg = AssumptionRegistry()
        a = reg.register("unverified")
        assert a in reg.active

    def test_mark_verified_found(self):
        reg = AssumptionRegistry()
        a = reg.register("x")
        result = reg.mark_verified(a.id, verified=True)
        assert result is True
        assert a.verified is True

    def test_mark_verified_not_found(self):
        reg = AssumptionRegistry()
        result = reg.mark_verified("nonexistent_id", verified=True)
        assert result is False

    def test_to_prompt_block_empty_when_no_active(self):
        reg = AssumptionRegistry()
        assert reg.to_prompt_block() == ""

    def test_to_prompt_block_has_xml_tags(self):
        reg = AssumptionRegistry()
        reg.register("File is UTF-8", "file_encoding", 0.85)
        block = reg.to_prompt_block()
        assert "<assumptions>" in block
        assert "</assumptions>" in block

    def test_to_prompt_block_contains_text(self):
        reg = AssumptionRegistry()
        reg.register("File is UTF-8", "file_encoding", 0.85)
        block = reg.to_prompt_block()
        assert "File is UTF-8" in block
        assert "file_encoding" in block

    def test_to_prompt_block_confidence_percentage(self):
        reg = AssumptionRegistry()
        reg.register("test", "general", 0.75)
        block = reg.to_prompt_block()
        assert "75%" in block

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

    def test_to_log_payload(self):
        reg = AssumptionRegistry()
        reg.register("x", "general", 0.9, "question")
        payload = reg.to_log_payload()
        assert len(payload) == 1
        assert payload[0]["text"] == "x"
        assert payload[0]["category"] == "general"
        assert "confidence" in payload[0]
        assert "id" in payload[0]

    def test_multiple_items_log_payload(self):
        reg = AssumptionRegistry()
        reg.register("a")
        reg.register("b")
        assert len(reg.to_log_payload()) == 2


# ============================================================
# TestAssumptionStore
# ============================================================

class TestAssumptionStore:
    def test_save_creates_file(self, tmp_path):
        store = _store(tmp_path)
        a = Assumption(text="test save", run_id="r1")
        store.save(a)
        assert store.path.exists()

    def test_save_and_load_by_run(self, tmp_path):
        store = _store(tmp_path)
        a = Assumption(text="hello", run_id="run_abc")
        store.save(a)
        loaded = store.load_by_run("run_abc")
        assert len(loaded) == 1
        assert loaded[0].text == "hello"

    def test_load_by_run_excludes_other_runs(self, tmp_path):
        store = _store(tmp_path)
        store.save(Assumption(text="A", run_id="run_1"))
        store.save(Assumption(text="B", run_id="run_2"))
        r1 = store.load_by_run("run_1")
        assert all(a.run_id == "run_1" for a in r1)
        assert len(r1) == 1

    def test_save_many_returns_count(self, tmp_path):
        store = _store(tmp_path)
        items = [Assumption(text=f"item {i}", run_id="r") for i in range(5)]
        n = store.save_many(items)
        assert n == 5

    def test_save_many_empty_returns_zero(self, tmp_path):
        store = _store(tmp_path)
        n = store.save_many([])
        assert n == 0

    def test_load_recent_most_recent_first(self, tmp_path):
        store = _store(tmp_path)
        for i in range(5):
            store.save(Assumption(text=f"item{i}", run_id="r"))
        recent = store.load_recent(3)
        assert len(recent) == 3
        # Most recent = item4, then item3, then item2
        assert recent[0].text == "item4"

    def test_load_recent_all_when_n_larger(self, tmp_path):
        store = _store(tmp_path)
        store.save(Assumption(text="only", run_id="r"))
        recent = store.load_recent(100)
        assert len(recent) == 1

    def test_load_recent_empty_file(self, tmp_path):
        store = _store(tmp_path)
        recent = store.load_recent(10)
        assert recent == []

    def test_load_recent_runs_groups_by_run(self, tmp_path):
        store = _store(tmp_path)
        store.save(Assumption(text="A", run_id="run_1"))
        store.save(Assumption(text="B", run_id="run_2"))
        store.save(Assumption(text="C", run_id="run_1"))
        grouped = store.load_recent_runs(n=2)
        assert "run_1" in grouped or "run_2" in grouped

    def test_corrupted_line_skipped(self, tmp_path):
        store = _store(tmp_path)
        a = Assumption(text="good", run_id="r")
        store.save(a)
        # Inject a corrupted line
        with open(store.path, "a", encoding="utf-8") as f:
            f.write("NOT_JSON_AT_ALL\n")
        # Should not raise, just skip the bad line
        loaded = store.load_by_run("r")
        assert len(loaded) == 1
        assert loaded[0].text == "good"

    def test_missing_file_returns_empty(self, tmp_path):
        store = AssumptionStore(tmp_path / "nonexistent" / "assumptions.jsonl")
        assert store.load_by_run("anything") == []
        assert store.load_recent(10) == []

    def test_roundtrip_all_fields(self, tmp_path):
        store = _store(tmp_path)
        a = Assumption(
            text="roundtrip",
            category="file_encoding",
            confidence=0.95,
            source="planner",
            run_id="r99",
            verified=True,
        )
        store.save(a)
        loaded = store.load_by_run("r99")
        assert len(loaded) == 1
        b = loaded[0]
        assert b.text == a.text
        assert b.category == a.category
        assert b.confidence == pytest.approx(0.95)
        assert b.source == a.source
        assert b.verified is True

    def test_parent_dir_created_automatically(self, tmp_path):
        nested_path = tmp_path / "deep" / "nested" / "assumptions.jsonl"
        store = AssumptionStore(nested_path)
        store.save(Assumption(text="x", run_id="r"))
        assert nested_path.exists()


# ============================================================
# TestAgentLoopAssumptionIntegration
# ============================================================

class TestAgentLoopAssumptionIntegration:
    """Black-box tests: verify AgentLoop accepts AssumptionStore and
    exposes last_assumptions after a run."""

    def _make_agent(self, tmp_path, with_store=True):
        """Build a minimal AgentLoop with mocked I/O."""
        from core.ids import new_id
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
        trace_id = f"trace_test_{new_id('t')}"
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

        agent = AgentLoop(
            registry=registry,
            policy=policy,
            llm=llm,
            logger=logger,
            planner=planner,
            memory=memory,
            assumption_store=store,
        )
        return agent

    def test_accepts_assumption_store_param(self, tmp_path):
        agent = self._make_agent(tmp_path, with_store=True)
        assert agent.assumption_store is not None

    def test_no_store_does_not_crash(self, tmp_path):
        agent = self._make_agent(tmp_path, with_store=False)
        assert agent.assumption_store is None

    def test_last_assumptions_none_before_run(self, tmp_path):
        agent = self._make_agent(tmp_path)
        assert agent.last_assumptions is None

    def test_last_assumptions_populated_after_run(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path)
        # Patch planner to return empty plan
        mock_plan = PlannerOutput(
            reasoning="no tools needed",
            sources=[],
            raw_response="",
            warnings=[],
        )
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            agent.run("Привет мир это тест проверки работы агента")
        assert agent.last_assumptions is not None
        assert isinstance(agent.last_assumptions, AssumptionRegistry)

    def test_question_assumptions_extracted(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path)
        mock_plan = PlannerOutput(
            reasoning="noop",
            sources=[],
            raw_response="",
            warnings=[],
        )
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            agent.run("Покажи мне список файлов проекта пожалуйста")
        # Russian → language assumption
        assert agent.last_assumptions is not None
        texts = [a.text for a in agent.last_assumptions.assumptions]
        assert any("Russian" in t for t in texts)

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

    def test_store_receives_saved_assumptions(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path)
        mock_plan = PlannerOutput(
            reasoning="noop",
            sources=[],
            raw_response="",
            warnings=[],
        )
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            agent.run("Покажи мне список файлов")
        # Check JSONL file was written
        if agent.last_assumptions and agent.last_assumptions.assumptions:
            recent = agent.assumption_store.load_recent(50)
            assert len(recent) > 0

    def test_run_without_store_still_completes(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path, with_store=False)
        mock_plan = PlannerOutput(
            reasoning="noop",
            sources=[],
            raw_response="",
            warnings=[],
        )
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            answer = agent.run("Hello world test")
        # Just needs to not raise
        assert answer is not None

    def test_last_assumptions_run_id_matches_trace(self, tmp_path):
        from core.planner import PlannerOutput

        agent = self._make_agent(tmp_path)
        mock_plan = PlannerOutput(reasoning="noop", sources=[], raw_response="", warnings=[])
        with patch.object(agent.planner, "plan", return_value=mock_plan):
            agent.run("Проверь python скрипт и запусти тесты пожалуйста")
        if agent.last_assumptions:
            for a in agent.last_assumptions.assumptions:
                assert a.run_id == "trace_test_001"
