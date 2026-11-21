"""Проверка 18.11, средние: ничья и победный счёт не списывают процедуру;
субагент с выданной памятью не держит инструментов, уносящих данные наружу."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.smart_memory import _smoothed_confidence
from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalMemoryScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
)
from core.subagent_runner import SubAgentRunner
from tools.base import ToolRegistry


def test_one_more_loss_after_a_tie_keeps_it_offered_until_it_loses(tmp_path: Path) -> None:
    """2:2 stays a candidate (offered, can still earn a win); 2:3 retires."""
    from dataclasses import replace

    from core.smart_memory import ProcedureRecord

    proc = ProcedureRecord(id="p", name="n", workflow_key="tools:a", trigger_tags=("x",),
                           steps=("Run tool: a",), success_count=2, failure_count=1,
                           confidence=_smoothed_confidence(2, 1), status="active")
    ep = MagicMock(id="ep-1", question="q")
    tie = proc.with_outcome(ep, "failure")
    assert (tie.success_count, tie.failure_count, tie.status) == (2, 2, "candidate")
    lost = replace(tie).with_outcome(MagicMock(id="ep-2", question="q"), "failure")
    assert lost.status == "needs_review"


# --- Defect B: granted memory removes outward-sending tools -----------------
_EGRESS = {"web_fetch", "web_search", "rss_fetch", "semantic_scholar_search"}


def _runner(tmp_path: Path) -> SubAgentRunner:
    return SubAgentRunner(workspace_root=tmp_path, policy=MagicMock(), model_router=MagicMock(),
                          parent_registry=ToolRegistry(), log_dir=tmp_path)


def _contract(**overrides) -> CanonicalSubagentContract:
    values = {
        "contract_id": "subc_egress", "source": "team_plan", "name": "Auditor",
        "role": "auditor", "objective": "Audit", "outputs": ("findings",),
        "tool_scope": CanonicalToolScope(allowed_tools=(
            "file_read", "run_tests", "web_fetch", "web_search", "rss_fetch",
            "semantic_scholar_search")),
        "budget_scope": CanonicalBudgetScope(max_model_calls=2, max_iterations=1, max_cost_units=3),
        "model_role": "verifier", "risk_level": "low", "approval_required": False,
    }
    values.update(overrides)
    return CanonicalSubagentContract(**values)


def _store_with(tmp_path: Path, *records: MemoryRecord) -> None:
    store = PersistentMemoryStore(tmp_path / "data" / "persistent_memory.jsonl")
    for record in records:
        store.save(record)


def test_a_subagent_holding_granted_memory_gets_no_egress_tools(tmp_path: Path) -> None:
    _store_with(tmp_path, MemoryRecord(content="потолок дня равен 500", tags=["budget"],
                                       source="agent-auto"))
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]
    runner.run_contract(_contract(memory_scope=CanonicalMemoryScope(read_tags=("budget",))))
    kw = runner.run.call_args.kwargs
    assert "потолок дня равен 500" in kw["context"]
    assert not set(kw["allowed_tools"]) & _EGRESS
    assert {"file_read", "run_tests"} <= set(kw["allowed_tools"])


def test_tags_that_match_no_record_grant_nothing_and_keep_tools(tmp_path: Path) -> None:
    _store_with(tmp_path, MemoryRecord(content="другое", tags=["other"], source="agent-auto"))
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]
    runner.run_contract(_contract(memory_scope=CanonicalMemoryScope(read_tags=("budget",))))
    assert set(runner.run.call_args.kwargs["allowed_tools"]) >= _EGRESS
