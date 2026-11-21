"""Output-contract priority (teaching-harness repair).

A task-specific/structured output contract (e.g. table-only) must take priority
over the generic Conclusion/Facts prose contract. The verifier must not flag an
answer as ``malformed_output`` for lacking generic headers when a task-specific
contract is in force. We can never simultaneously demand "table only" and
mandatory prose sections.
"""

from __future__ import annotations

from pathlib import Path

from core.answer_format import output_contract_requires_headers
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def test_empty_prompt_defaults_to_generic_contract():
    assert output_contract_requires_headers("") is True
    assert output_contract_requires_headers(None) is True


# ── End-to-end: the loop honours a registry/env output-contract override ──────

_TABLE_ANSWER = (
    "| file | class | signature | responsibility |\n"
    "| core/value_review.py | ValueReview | dataclass | holds a verdict |"
)


def _run_loop(tmp_path: Path, answer: str) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[answer]),
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False
        ),
        planner=FakePlanner(sources=[], reasoning="no tools needed"),
    )
    loop.run("inventory the value-review API")
    return loop


def test_loop_accepts_table_answer_under_env_contract_override(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv(
        "AGENT_PROMPT_SYNTHESIZER_SYSTEM",
        "Output ONLY a markdown table: file | class | signature | "
        "responsibility. No prose, no Conclusion, no Facts.",
    )
    loop = _run_loop(tmp_path, _TABLE_ANSWER)
    # Task-specific contract active → the generic headers are not required.
    assert loop._synthesis_expects_contract_headers is False
    assert loop.last_verification.malformed_output is False
