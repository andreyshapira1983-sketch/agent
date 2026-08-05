"""A step the plan gave no reason for does not run — MIR-015.

Operator, 2026-08-05: "`reasoning_action_mismatch` сейчас только журналируется.
Он должен быть управляющим сигналом: запрещать выполнение необоснованных
инструментов, требовать перепланирование и не позволять записывать такой эпизод
как чистый success/usage_eligible=True."

The demand was right and the detector named in it was not, so the requirement is
connected through a different one. Measured over every `planner` event in
`logs/` — 108 real turns — the keyword sensor `reasoning_action_mismatch` fires
on 44, and the accusations do not survive reading: `file_write` has no entry in
its keyword table at all, so all 5 of its accusations are false while the
rationale says "User wants a new module and test file created"; `list_dir`
demands the literal phrase "list files" where real prose writes "listing actual
directory contents". Blocking on that detector would forbid `file_write` on
nearly every run that creates a file.

What made this fixable: the planner is ALREADY required to state a reason per
step — `"rationale": "<one sentence explaining WHY this step is needed>"` has
been in the output contract all along — and every `sanitize_step` return site
built a fresh dict, so the answer was collected and dropped. The keyword table
was guessing at a fact the code had thrown away.

So `action_without_stated_reason` reads the stated reason. It is a structural
fact with no phrasing to miss and no threshold to tune, and these tests hold the
three consequences the operator asked for: the step does not run, the run is
sent back to plan again, and the episode does not bank as a clean success.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import PlannerOutput
from core.policy import PolicyGate
from core.reasoning_action_check import check_step_justification
from core.replan import ALL_FAILURE_TYPES, DEFAULT_BUDGETS
from core.smart_memory import decide_usage_eligibility, episode_from_agent_cycle
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry


# ---------------------------------------------------------------------------
# The check itself — three states, and the middle one is the only accusation
# ---------------------------------------------------------------------------

def test_a_step_with_a_stated_reason_is_justified():
    report = check_step_justification([
        {"tool": "file_read", "label": "file:a.py", "rationale": "need the real code"},
    ])
    assert not report.has_unjustified
    assert report.justified_tools == ("file_read",)


def test_an_empty_reason_is_an_accusation():
    """The planner produced the step and left the contract's field blank."""
    report = check_step_justification([
        {"tool": "shell_exec", "label": "shell_exec:git", "rationale": "   "},
    ])
    assert report.has_unjustified
    assert report.unjustified_tools == ("shell_exec",)
    assert report.unjustified_labels == ("shell_exec:git",)


def test_a_missing_key_is_not_an_accusation():
    """A step the planner never authored has no contract to break.

    Forced plans and the explicit-file-hint injection build their specs
    directly, bypassing `_validate_steps`. Reading absent as empty would block
    exactly the paths that exist to rescue a turn.
    """
    report = check_step_justification([{"tool": "file_read", "label": "file:hint.md"}])
    assert not report.has_unjustified
    assert report.exempt_tools == ("file_read",)


def test_the_label_says_which_step_not_just_which_tool():
    """Four reads of which one is unreasoned must not send all four back."""
    report = check_step_justification([
        {"tool": "file_read", "label": "file:a.py", "rationale": "compare against b"},
        {"tool": "file_read", "label": "file:b.py", "rationale": ""},
    ])
    assert report.unjustified_labels == ("file:b.py",)
    assert report.justified_tools == ("file_read",)


# ---------------------------------------------------------------------------
# The planner must stop throwing the reason away
# ---------------------------------------------------------------------------

def test_the_planner_keeps_the_reason_it_was_given():
    """`sanitize_step` rebuilds the dict, so carrying it is an explicit act."""
    from core.planner import LLMPlanner

    planner = LLMPlanner.__new__(LLMPlanner)
    planner.registry = ToolRegistry()
    planner.registry.register(_RecordingSearch())
    planner.self_documentation_paths = ()

    sources, _warnings, _dropped = planner._validate_steps(
        [{"tool": "web_search", "arguments": {"query": "x"},
          "rationale": "the question needs current external facts"}],
        file_hint=None,
    )

    assert sources[0]["rationale"] == "the question needs current external facts"


def test_a_planner_step_without_a_reason_carries_the_empty_string():
    """Not an absent key: this one HAS an author, and the author said nothing."""
    from core.planner import LLMPlanner

    planner = LLMPlanner.__new__(LLMPlanner)
    planner.registry = ToolRegistry()
    planner.registry.register(_RecordingSearch())
    planner.self_documentation_paths = ()

    sources, _w, _d = planner._validate_steps(
        [{"tool": "web_search", "arguments": {"query": "x"}}], file_hint=None
    )

    assert sources[0]["rationale"] == ""
    assert check_step_justification(sources).has_unjustified


# ---------------------------------------------------------------------------
# The replan road
# ---------------------------------------------------------------------------

def test_the_failure_type_is_registered_and_demands_a_different_action():
    assert "action_without_stated_reason" in ALL_FAILURE_TYPES
    budget = DEFAULT_BUDGETS["action_without_stated_reason"]
    assert budget.requires_different_action is True
    # 2 means ONE retry in this table (`FailureBudget.max_occurrences` counts
    # the first occurrence too). Pinned because 1 reads like "one chance" and
    # actually means "abort without replanning" — which would deliver the
    # opposite of the instruction that produced this feature.
    assert budget.max_occurrences == 2, (
        "бюджет 1 означал бы прерывание без перепланирования — обратное тому, "
        "что просили"
    )


# ---------------------------------------------------------------------------
# End to end: the tool must not run
# ---------------------------------------------------------------------------

class _RecordingSearch(Tool):
    """Counts its own calls, because 'the step was dropped' is only a claim
    until the thing it would have done is shown not to have happened."""

    name = "web_search"
    description = "deterministic stub that records invocations"
    risk = "read_only"
    calls: list[str] = []

    def run(self, query: str, max_results: int = 3) -> list[dict[str, str]]:
        del max_results
        type(self).calls.append(query)
        return [{"title": "Stub", "url": "https://example.com/x",
                 "snippet": query, "source": "stub"}]

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        return (isinstance(output, list), [])


class _ScriptedPlanner:
    def __init__(self, scripts: list[list[dict[str, Any]]]):
        self.scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    def plan(self, question: str, file_hint: str | None = None, history: str = "",
             failure_context: str = "",
             forbidden_actions: tuple[tuple[str, str], ...] = (),
             llm: Any = None) -> PlannerOutput:
        self.calls.append({"failure_context": failure_context})
        idx = len(self.calls) - 1
        sources = self.scripts[idx] if idx < len(self.scripts) else []
        for src in sources:
            src.setdefault("expected_outcome", "executes the planned step")
        return PlannerOutput(reasoning="scripted", sources=sources,
                             raw_response="", warnings=[])


def _events(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _build_agent(workspace: Path, planner: _ScriptedPlanner,
                 answers: list[str]) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(_RecordingSearch())
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=reg, policy=PolicyGate(reg), llm=FakeLLM(responses=answers),
        logger=logger, planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=3, verifier_enabled=False,
        clarification_gate_enabled=False,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


_ANSWER = (
    "Conclusion: stub answer [web:x].\nFacts: stub [web:x].\n"
    "Sources: stub\nConfidence: low\nUnverified: nothing\nSafety: ok"
)


def test_an_unreasoned_step_never_executes_and_forces_a_replan(workspace: Path):
    """The whole point, in one run: not executed, then re-planned.

    Attempt 1 offers a search with no rationale. Attempt 2 offers the same tool
    WITH one. If blocking works, the tool is called exactly once — on the
    second plan — and the query proves which.
    """
    _RecordingSearch.calls = []
    planner = _ScriptedPlanner([
        [{"tool": "web_search", "arguments": {"query": "unreasoned"},
          "label": "web:unreasoned", "rationale": ""}],
        [{"tool": "web_search", "arguments": {"query": "reasoned"},
          "label": "web:reasoned",
          "rationale": "the question asks for current external facts"}],
    ])
    agent, log_path = _build_agent(workspace, planner, [_ANSWER])

    agent.run("test question")

    assert _RecordingSearch.calls == ["reasoned"], (
        f"необоснованный шаг всё-таки выполнился: {_RecordingSearch.calls}"
    )
    events = _events(log_path)
    blocked = [e for e in events if e["event"] == "unjustified_action_blocked"]
    assert len(blocked) == 1
    assert blocked[0]["payload"]["unjustified_labels"] == ["web:unreasoned"]
    assert len(planner.calls) == 2, "перепланирования не потребовали"
    assert "rationale" in planner.calls[1]["failure_context"].lower(), (
        planner.calls[1]["failure_context"]
    )


def test_a_surviving_justified_step_runs_and_no_replan_is_spent(workspace: Path):
    """The deliberate limit of "require replanning", written down as behaviour.

    When only SOME steps are unreasoned, the unjustified one is prevented —
    which was the whole risk — and the justified ones carry the attempt. Sending
    the turn back to plan again there would discard work that was properly
    argued for and pay a model round for it. The episode still carries the
    signal, so it cannot bank as a clean success either way.
    """
    _RecordingSearch.calls = []
    planner = _ScriptedPlanner([[
        {"tool": "web_search", "arguments": {"query": "kept"}, "label": "web:kept",
         "rationale": "external facts are needed"},
        {"tool": "web_search", "arguments": {"query": "dropped"}, "label": "web:dropped",
         "rationale": ""},
    ]])
    agent, log_path = _build_agent(workspace, planner, [_ANSWER])

    agent.run("test question")

    assert _RecordingSearch.calls == ["kept"], _RecordingSearch.calls
    assert len(planner.calls) == 1, "перепланирование потратили там, где не нужно"
    blocked = [e for e in _events(log_path) if e["event"] == "unjustified_action_blocked"]
    assert blocked[0]["payload"]["unjustified_labels"] == ["web:dropped"]


def test_a_justified_plan_is_left_alone(workspace: Path):
    """The gate must be invisible when the contract is met — no cost, no event."""
    _RecordingSearch.calls = []
    planner = _ScriptedPlanner([
        [{"tool": "web_search", "arguments": {"query": "fine"}, "label": "web:fine",
          "rationale": "external facts are needed here"}],
    ])
    agent, log_path = _build_agent(workspace, planner, [_ANSWER])

    agent.run("test question")

    assert _RecordingSearch.calls == ["fine"]
    assert not [e for e in _events(log_path) if e["event"] == "unjustified_action_blocked"]
    assert len(planner.calls) == 1


# ---------------------------------------------------------------------------
# The episode must not bank as a clean success
# ---------------------------------------------------------------------------

def _episode(**kw):
    base = {"goal": "g", "question": "q", "answer": "a", "tools_used": ["file_read"],
            "source_labels": ["file:x"], "verified_chunks": 3, "unverified_chunks": 0}
    base.update(kw)
    return episode_from_agent_cycle(**base)


def test_the_signal_lowers_a_claim_of_achieved():
    ep = _episode(defect_signals=["action_without_stated_reason"],
                  declared_completion="achieved")
    assert ep.completion_state == "partially_achieved"
    assert ep.completion_override == "action_without_stated_reason"
    assert ep.declared_completion == "achieved", "самооценку не стирают, её опровергают"


def test_the_signal_denies_usage_eligibility():
    ep = _episode(defect_signals=["action_without_stated_reason"],
                  declared_completion="achieved")
    assert decide_usage_eligibility(ep) is False


def test_an_honest_report_is_not_made_worse():
    """One direction only: it lowers a claim, it never punishes candour."""
    ep = _episode(defect_signals=["action_without_stated_reason"],
                  declared_completion="blocked")
    assert ep.completion_state == "blocked"
    assert ep.completion_override is None


def test_the_lesson_tag_cannot_carry_a_defective_run_into_the_usable_pool():
    """Measured cause: 31 of 108 live episodes were admitted by that tag alone.

    `core/self_build_memory.py` tags every self-build episode `lesson`
    unconditionally, so the exemption is not the curated one it was written
    for. Below it, the gate would never run.
    """
    ep = replace(
        _episode(defect_signals=["action_without_stated_reason"],
                 declared_completion="achieved"),
        tags=("lesson", "self-build"),
    )
    assert decide_usage_eligibility(ep) is False


def test_an_honest_failure_with_a_lesson_is_still_admitted():
    """The exemption survives for what it was written for.

    Learning from a failure is its entire purpose. What the gate above removes
    is a run whose own PROCESS was defective — not a run that failed and said so.
    """
    ep = replace(
        _episode(declared_completion="failed", verified_chunks=0,
                 defect_signals=[]),
        tags=("lesson",),
    )
    assert decide_usage_eligibility(ep) is True


def test_the_keyword_sensor_still_decides_nothing():
    """S4 stays an observer, on measured grounds — 44 firings in 108 turns.

    Guarding the boundary in both directions: connecting one detector must not
    quietly promote the one beside it, whose accusations do not survive reading.
    """
    clean = _episode(defect_signals=[], declared_completion="achieved")
    noted = _episode(defect_signals=["reasoning_action_mismatch"],
                     declared_completion="achieved")
    assert noted.completion_state == clean.completion_state
    assert noted.completion_override is None
    assert decide_usage_eligibility(noted) == decide_usage_eligibility(clean) is True
