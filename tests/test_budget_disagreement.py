"""MIR-073, half 2 — the orchestrator SEES the plan-vs-budget contradiction.

Measured live (2026-08-03, operator's self-opinion run): the planner planned
`file_read(core/loop.py)`, the total evidence budget starved that block to the
50-char floor — and nothing anywhere said the two deciders had just
contradicted each other; the journal's `subsystem_disagreement` channel (built
exactly for «органы системы противоречат друг другу») stayed silent because
no detector looked at the budget.

Contract:

* `detect_budget_starvation` is pure: (label, kept, original) tuples in,
  zero or more `subsystem_disagreement` payloads out;
* a planned source squeezed to a sliver (kept/original ≤ 5%) is a
  disagreement; a mild trim is not;
* the demoted memory block is exempt — memory paying first is the DESIGN,
  not a contradiction;
* the loop journals each payload as `subsystem_disagreement` (logging only,
  per the operator's sensor policy — no behaviour change).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.subsystem_disagreement import detect_budget_starvation
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

# ── the pure detector ────────────────────────────────────────────────────────

def test_a_starved_planned_source_is_a_disagreement():
    events = detect_budget_starvation(
        [("file:core/loop.py", 50, 12204)],
        planned_labels={"file:core/loop.py"},
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "planner_vs_evidence_budget"
    assert ev["label"] == "file:core/loop.py"
    assert ev["kept_chars"] == 50 and ev["original_chars"] == 12204


def test_a_mild_trim_is_not_a_disagreement():
    events = detect_budget_starvation(
        [("file:a.py", 3200, 12000)],
        planned_labels={"file:a.py"},
    )
    assert events == []


def test_demoted_memory_starvation_is_the_design_not_a_contradiction():
    events = detect_budget_starvation(
        [("long_term_memory", 50, 8000)],
        planned_labels={"long_term_memory"},
        memory_label="long_term_memory",
    )
    assert events == []


def test_an_unplanned_label_is_ignored():
    events = detect_budget_starvation(
        [("stray", 50, 9000)],
        planned_labels={"file:a.py"},
    )
    assert events == []


# ── wired through the real loop ──────────────────────────────────────────────

def _events(p: Path) -> list[dict]:
    out: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def test_a_broken_detector_is_journaled_not_swallowed(workspace: Path, monkeypatch):
    """Review round #286: a failure inside the starvation detector must not
    break the turn — and must not vanish silently either."""
    import core.subsystem_disagreement as sd

    def _boom(*a, **k):
        raise RuntimeError("детектор сломан")

    monkeypatch.setattr(sd, "detect_budget_starvation", _boom)
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    (workspace / "a.txt").write_text("а" * 2900, encoding="utf-8")
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=["ответ [file:a.txt]."]),
        logger=logger,
        planner=FakePlanner([
            {"tool": "file_read", "arguments": {"path": "a.txt"},
             "label": "file:a.txt", "expected_outcome": "reads the file"},
        ]),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=True,
    )
    answer = agent.run("что в файле a.txt?", file_hint="a.txt")
    assert answer  # the turn still completes
    events = _events(workspace / "logs" / f"{trace_id}.jsonl")
    errors = [e for e in events if e.get("event") == "subsystem_disagreement_error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["error_type"] == "RuntimeError"


def test_the_loop_journals_the_contradiction(workspace: Path, monkeypatch):
    """Budget forced so tight that even fair shares collapse to the absolute
    floor: the journal must carry `planner_vs_evidence_budget` for the starved
    planned file — the orchestrator no longer says «противоречия не вижу»."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    (workspace / "a.txt").write_text("а" * 2900, encoding="utf-8")
    (workspace / "b.txt").write_text("б" * 2900, encoding="utf-8")
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=["ответ по файлам [file:a.txt]."]),
        logger=logger,
        planner=FakePlanner([
            {"tool": "file_read", "arguments": {"path": "a.txt"},
             "label": "file:a.txt", "expected_outcome": "reads the file"},
            {"tool": "file_read", "arguments": {"path": "b.txt"},
             "label": "file:b.txt", "expected_outcome": "reads the file"},
        ]),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=True,
    )
    agent.run("что в файлах a.txt и b.txt?", file_hint="a.txt")
    events = _events(workspace / "logs" / f"{trace_id}.jsonl")
    disagreements = [
        e for e in events
        if e.get("event") == "subsystem_disagreement"
        and e.get("payload", {}).get("kind") == "planner_vs_evidence_budget"
    ]
    assert disagreements, "оркестратор обязан журналировать противоречие план↔бюджет"
    labels = {d["payload"]["label"] for d in disagreements}
    assert labels & {"file:a.txt", "file:b.txt"}
