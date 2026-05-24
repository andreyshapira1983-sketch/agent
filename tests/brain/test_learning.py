"""Tests for brain/learning.py — earned-knowledge extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from brain.learning import (
    LearningRecord,
    LearningReport,
    _parse_facts,
    learn_from_outcome,
)
from brain.skills.job import Job, JobStatus
from brain.skills.knowledge import KnowledgeBase
from brain.skills.workflow_runner import JobOutcome, StepRecord


# ════════════════════════════════════════════════════════════════════
# Fakes
# ════════════════════════════════════════════════════════════════════

@dataclass
class FakeSemantic:
    stored: list[dict] = field(default_factory=list)

    def store_fact(self, text: str, metadata: dict | None = None) -> str:
        fid = f"f{len(self.stored)}"
        self.stored.append({"id": fid, "text": text, "metadata": metadata or {}})
        return fid

    def forget_fact(self, fid: str) -> None:
        self.stored = [s for s in self.stored if s["id"] != fid]

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        return []


class ScriptedLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.last_context = None
    def call(self, ctx):
        self.last_context = ctx
        return {"action": "respond", "content": self.content, "confidence": 0.9, "reasoning": ""}


def _outcome(
    *,
    success: bool,
    deliverable: str = "Edited deliverable text.",
    profession_id: str = "text_editor",
) -> JobOutcome:
    final = JobStatus.DELIVERED if success else JobStatus.FAILED
    return JobOutcome(
        job_id="j1",
        profession_id=profession_id,
        final_status=final,
        deliverable_text=deliverable,
        step_records=[
            StepRecord(step_id=0, workflow_id="read", action="tool_call",
                       tool="docx_reader", success=True,
                       output="Original source text."),
        ],
    )


def _job() -> Job:
    return Job(id="j1", brief="please edit", source="email", client_id="c@x")


# ════════════════════════════════════════════════════════════════════
# _parse_facts
# ════════════════════════════════════════════════════════════════════

class TestParseFacts:

    def test_strict_json_array(self):
        out = _parse_facts('["a", "b", "c"]', max_facts=10)
        assert out == ["a", "b", "c"]

    def test_json_with_code_fence(self):
        text = '```json\n["fact one", "fact two"]\n```'
        assert _parse_facts(text, max_facts=10) == ["fact one", "fact two"]

    def test_line_fallback(self):
        text = "- first rule\n- second rule\n* third rule"
        out = _parse_facts(text, max_facts=10)
        assert out == ["first rule", "second rule", "third rule"]

    def test_max_facts_respected(self):
        out = _parse_facts(json.dumps([f"fact{i}" for i in range(20)]), max_facts=5)
        assert len(out) == 5

    def test_dedup_keeps_order(self):
        out = _parse_facts('["a", "A", "b"]', max_facts=10)
        # case-insensitive dedup → "a" wins
        assert out == ["a", "b"]

    def test_empty_input_returns_empty(self):
        assert _parse_facts("", max_facts=10) == []
        assert _parse_facts("   ", max_facts=10) == []


# ════════════════════════════════════════════════════════════════════
# learn_from_outcome
# ════════════════════════════════════════════════════════════════════

class TestLearnFromOutcome:

    def test_success_stores_facts_in_prof_namespace(self):
        llm = ScriptedLLM('["freelancer → фрилансер", "keep tone neutral"]')
        sem = FakeSemantic()
        report = learn_from_outcome(
            _job(), _outcome(success=True),
            llm=llm, semantic_memory=sem,
        )
        assert report.fact_count() == 2
        assert all(r.namespace == "prof:text_editor" for r in report.extracted)
        assert all(r.source == "success" for r in report.extracted)
        assert sem.stored[0]["metadata"]["namespace"] == "prof:text_editor"
        assert sem.stored[0]["metadata"]["outcome"] == "success"

    def test_failure_stores_to_antipatterns(self):
        llm = ScriptedLLM('["do not add new facts", "do not over-edit"]')
        sem = FakeSemantic()
        report = learn_from_outcome(
            _job(), _outcome(success=False),
            llm=llm, semantic_memory=sem,
        )
        assert report.fact_count() == 2
        assert all(r.namespace == "antipatterns:text_editor" for r in report.extracted)
        assert sem.stored[0]["metadata"]["outcome"] == "failure"

    def test_empty_deliverable_skipped(self):
        llm = ScriptedLLM('["should not get here"]')
        sem = FakeSemantic()
        outcome = _outcome(success=True, deliverable="")
        report = learn_from_outcome(_job(), outcome, llm=llm, semantic_memory=sem)
        assert report.fact_count() == 0
        assert "no deliverable" in " ".join(report.notes).lower()

    def test_missing_profession_id_skipped(self):
        llm = ScriptedLLM('["x"]')
        sem = FakeSemantic()
        outcome = _outcome(success=True, profession_id="")
        report = learn_from_outcome(_job(), outcome, llm=llm, semantic_memory=sem)
        assert report.fact_count() == 0

    def test_llm_error_returns_empty_report(self):
        class BoomLLM:
            def call(self, _ctx):
                raise RuntimeError("network down")
        sem = FakeSemantic()
        report = learn_from_outcome(
            _job(), _outcome(success=True),
            llm=BoomLLM(), semantic_memory=sem,
        )
        assert report.fact_count() == 0
        assert any("LLM error" in n for n in report.notes)

    def test_malformed_llm_reply_returns_empty(self):
        llm = ScriptedLLM('{"weird": "shape"}')
        sem = FakeSemantic()
        report = learn_from_outcome(
            _job(), _outcome(success=True),
            llm=llm, semantic_memory=sem,
        )
        # No list, no bullets → 0 facts
        assert report.fact_count() == 0

    def test_knowledge_base_integration_tracks_fact_ids(self, tmp_path):
        llm = ScriptedLLM('["a", "b"]')
        sem = FakeSemantic()
        kb = KnowledgeBase(sem)
        report = learn_from_outcome(
            _job(), _outcome(success=True),
            llm=llm, semantic_memory=sem, knowledge_base=kb,
        )
        assert kb.fact_count("text_editor") == 2
