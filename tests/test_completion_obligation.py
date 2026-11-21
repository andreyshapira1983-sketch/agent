"""S3 — premature completion as an unmet obligation, not a keyword match.

The detector this replaces asked whether the user's wording contained a
tool-ish word. Measured (`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`): **1 of 12**
phrasings that unambiguously demand a tool were recognised, and it fired on
*«объясни разницу между REST и GraphQL»* because `"разниц"` is a diff-tool
keyword.

The replacement asks whether some part of the cycle created a duty to observe
or to run something, and whether that duty was left unmet *without saying so*.

Three sources are wired (`intent`, `plan`, `freshness`). `acceptance_criteria`
is reported as unavailable and draws no conclusion — `not_wired` must never
behave like `no_requirement`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.completion_obligation import (
    UNWIRED_SOURCES,
    evaluate_completion_obligations,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry, ToolResult


@dataclass
class _Step:
    tool: str
    status: str = "pending"
    action_spec: dict = field(default_factory=dict)

    def __post_init__(self):
        self.action_spec = {"tool_name": self.tool}


def _ev(**kw):
    kw.setdefault("question", "")
    kw.setdefault("answer", "")
    return evaluate_completion_obligations(**kw)


# ── source: plan (admitted steps only) ───────────────────────────────────────

def test_an_admitted_observing_step_with_no_result_is_silently_missing():
    res = _ev(question="покажи логи", plan_steps=[_Step("read_logs")])
    assert res.required is True
    assert "plan" in res.requirement_sources
    assert res.missing_requirements == ("tool_execution",)
    assert res.triggered is True


def test_a_policy_block_kept_quiet_does_fire():
    res = _ev(
        question="выполни команду",
        answer="Готово.",
        plan_steps=[_Step("shell_exec", status="failed")],
        denied_tools=["shell_exec"],
        failure_codes=["policy_blocked"],
    )
    assert res.triggered is True


# ── source: freshness ────────────────────────────────────────────────────────


def test_several_sources_are_all_reported():
    res = _ev(
        question="что сейчас в файле core/loop.py",
        plan_steps=[_Step("file_read")],
        realtime_required=True,
    )
    assert set(res.requirement_sources) == {"intent", "plan", "freshness"}
    assert set(res.missing_requirements) == {
        "workspace_observation", "tool_execution", "fresh_evidence",
    }


# ── end to end, through the real loop ────────────────────────────────────────

class _FailingRead(Tool):
    name = "file_read"
    description = "read"
    side_effects = "read"

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"]}

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(status="error", output=None, error="boom")


def _loop(workspace: Path, answer: str, sources=()):
    registry = ToolRegistry()
    registry.register(_FailingRead())
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[answer] * 8),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs",
                           verbose=False),
        planner=FakePlanner(sources=list(sources), reasoning="r"),
        max_replan_attempts=1,
    )
    events: list[tuple[str, dict]] = []
    _orig = loop.log.log
    loop.log.log = lambda et, *a, **k: (
        events.append((et, a[0] if a else {})), _orig(et, *a, **k))[1]
    return loop, events


def test_end_to_end_event_is_emitted_with_the_shadow_verdict(workspace: Path):
    loop, events = _loop(
        workspace,
        "Conclusion: ответ. [general-knowledge]\nConfidence: low",
    )

    loop.run("что в файле core/loop.py")

    payload = next((p for e, p in events if e == "completion_obligation"), None)
    assert payload is not None, "the obligation check must be journalled"
    assert payload["required"] is True
    assert payload["triggered"] is True
    assert payload["requirement_sources"] == ["intent"]
    # `acceptance_criteria` was reported unavailable until 2026-08-02, when the
    # completion contract (MIR-067) wired it. The loop now always supplies a
    # contract, so nothing is unavailable — even for a read-only request like
    # this one, whose contract is legitimately empty. An empty contract is a
    # source that was CONSULTED and owed nothing; that is not the same fact as
    # a source nobody could read, which is why this assertion flipped rather
    # than being deleted.
    assert payload["unavailable_sources"] == []
    assert UNWIRED_SOURCES == ("acceptance_criteria",), (
        "the module still declares the source; only the loop's supply changed"
    )
    # The detector it replaces is carried alongside, not deleted.
    assert "shadow_keyword_detector" in payload
