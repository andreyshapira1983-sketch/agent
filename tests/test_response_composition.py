"""A decider's contribution reaches the operator, or is recorded as superseded.

The measured defect: on a `replan_exhausted` turn with thin evidence, three
subsystems logged that they had changed the answer and only one of them had.
`output_policy` added a caveat that the verdict was unreliable, the B-1
clarification gate added three questions — the gate exists precisely because
*when the loop is stuck, the mature response is to ASK* — and low-evidence
truncation then rebuilt the answer from scratch. The 426 characters that shipped
contained neither. They were not overruled by a decision; they were overwritten,
because they were bytes in the same variable as the claims.

`ResponseDraft` separates the two kinds of content. Truncation may delete claims
— that is its job — and cannot touch a notice, because a notice is not a claim
and no verdict about the evidence makes it untrue.

Both halves are pinned here: the composed output for the untruncated path must
stay byte-identical to what the sequential mutation produced (otherwise this is
a rewrite, not a fix), and the notices must survive the truncated path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.response_draft import ResponseDraft
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry, ToolResult

# ── the object ───────────────────────────────────────────────────────────────


def test_a_notice_already_in_the_body_is_not_duplicated():
    draft = ResponseDraft(body="text with CAVEAT inside")

    assert draft.add_notice(author="x", channel="prepend", text="CAVEAT") is False
    assert draft.render() == "text with CAVEAT inside"


def test_blank_and_duplicate_notices_are_skipped():
    draft = ResponseDraft(body="BODY")
    assert draft.add_notice(author="x", channel="prepend", text="") is False
    assert draft.add_notice(author="x", channel="prepend", text="   ") is False
    assert draft.add_notice(author="x", channel="prepend", text="N") is True
    assert draft.add_notice(author="y", channel="prepend", text="N") is False


def test_a_body_channel_notice_is_refused():
    draft = ResponseDraft(body="BODY")
    with pytest.raises(ValueError, match="not a notice channel"):
        draft.add_notice(author="x", channel="body", text="claims")  # type: ignore[arg-type]


# ── end to end, through the real loop ────────────────────────────────────────

class _AlwaysFails(Tool):
    name = "web_search"
    description = "search"
    side_effects = "read"

    @property
    def schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(status="error", output=None, error="upstream 503")


_LONG_UNSUPPORTED = "\n".join(
    ["Conclusion:", "Курс доллара сейчас около 80 рублей."]
    + [f"- Фактор {i}: влияние примерно {i * 3} процентов." for i in range(1, 10)]
    + ["Confidence: high"]
)


def _stuck_loop(workspace: Path) -> tuple[AgentLoop, list[tuple[str, dict]]]:
    """A loop that exhausts its replan budget on a realtime question.

    Realtime intent with an empty chain is what makes evidence *expected*, so
    the truncation gate can fire — the combination the defect needs, and the
    one the deciders are designed for.
    """
    registry = ToolRegistry()
    registry.register(_AlwaysFails())
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_LONG_UNSUPPORTED] * 10),
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
        ),
        planner=FakePlanner(
            sources=[{
                "tool": "web_search",
                "arguments": {"query": "курс"},
                "label": "web_search:курс",
                "expected_outcome": "hits",
            }],
            reasoning="r",
        ),
        max_replan_attempts=1,
    )
    events: list[tuple[str, dict]] = []
    _orig = loop.log.log

    def _spy(event_type, *a, **k):
        events.append((event_type, a[0] if a else {}))
        return _orig(event_type, *a, **k)

    loop.log.log = _spy  # type: ignore[method-assign]
    return loop, events


def _payload(events: list[tuple[str, dict]], name: str) -> dict | None:
    return next((p for e, p in events if e == name), None)


def test_truncation_does_not_delete_the_clarifying_questions(workspace: Path):
    loop, events = _stuck_loop(workspace)

    answer = loop.run("какой сейчас курс доллара сегодня")

    enforcement = _payload(events, "answer_enforcement")
    clarification = _payload(events, "clarification_gate")
    assert enforcement and enforcement["outcome"] == "insufficient_evidence"
    assert enforcement["applied"] is True, "the truncation must still fire"
    assert clarification, "the gate must still fire on a stuck loop"

    for question in clarification["questions"]:
        assert question in answer, (
            "a clarifying question the loop decided to ask was deleted by a "
            "decider that judges claims, not questions"
        )


def test_truncation_does_not_delete_the_output_policy_warning(workspace: Path):
    loop, events = _stuck_loop(workspace)

    answer = loop.run("какой сейчас курс доллара сегодня")

    policy = _payload(events, "output_policy")
    assert policy and policy["applied"] is True
    for warning in policy["warnings"]:
        assert warning in answer
