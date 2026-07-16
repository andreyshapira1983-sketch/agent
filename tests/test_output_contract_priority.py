"""Output-contract priority (teaching-harness repair).

A task-specific/structured output contract (e.g. table-only) must take priority
over the generic Conclusion/Facts prose contract. The verifier must not flag an
answer as ``malformed_output`` for lacking generic headers when a task-specific
contract is in force. We can never simultaneously demand "table only" and
mandatory prose sections.
"""

from __future__ import annotations

from pathlib import Path

from core.evidence import ProvenanceChain, make_evidence
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.loop_helpers import output_contract_requires_headers
from core.policy import PolicyGate
from core.verifier import verify
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _chain() -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(
        make_evidence(
            kind="file",
            source_id="file:core/value_review.py",
            obtained_via="file_read",
            claim="reviewed file",
            excerpt="class ValueReview: ...",
        )
    )
    return chain


_TABLE_ONLY_CONTRACT = (
    "You are an API-inventory assistant. Output ONLY a markdown table with "
    "columns: file | class/function | signature | responsibility. No prose, "
    "no Conclusion, no Facts sections."
)


def test_generic_contract_detected_from_system_answer():
    from core.loop_helpers import SYSTEM_ANSWER

    assert output_contract_requires_headers(SYSTEM_ANSWER) is True


def test_task_specific_contract_disables_generic_headers():
    assert output_contract_requires_headers(_TABLE_ONLY_CONTRACT) is False


def test_empty_prompt_defaults_to_generic_contract():
    assert output_contract_requires_headers("") is True
    assert output_contract_requires_headers(None) is True


def test_verifier_flags_missing_headers_under_generic_contract():
    answer = "core/value_review.py | ValueReview | dataclass | holds a verdict"
    report = verify(answer=answer, chain=_chain())
    assert report.malformed_output is True


def test_verifier_allows_table_only_answer_under_task_specific_contract():
    answer = (
        "| file | class | signature | responsibility |\n"
        "| core/value_review.py | ValueReview | dataclass | holds a verdict |"
    )
    report = verify(answer=answer, chain=_chain(), expects_contract_headers=False)
    assert report.malformed_output is False


def test_verifier_still_flags_when_headers_expected_but_absent_table():
    answer = (
        "| file | class | signature | responsibility |\n"
        "| core/value_review.py | ValueReview | dataclass | holds a verdict |"
    )
    report = verify(answer=answer, chain=_chain(), expects_contract_headers=True)
    assert report.malformed_output is True


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


def test_loop_flags_table_answer_under_default_generic_contract(tmp_path: Path):
    loop = _run_loop(tmp_path, _TABLE_ANSWER)
    # Default generic contract is in force → a bare table is malformed.
    assert loop._synthesis_expects_contract_headers is True
    assert loop.last_verification.malformed_output is True


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
