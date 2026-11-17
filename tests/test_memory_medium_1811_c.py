"""Проверка 18.11, средние: ничья и победный счёт не списывают процедуру;
субагент с выданной памятью не держит инструментов, уносящих данные наружу."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.smart_memory import ProceduralMemoryStore, _smoothed_confidence
from core.smart_memory_helpers import _procedure_status_for
from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalMemoryScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
)
from core.subagent_memory_scope import _parse_proposal
from core.subagent_runner import SubAgentRunner
from tools.base import ToolRegistry


def _status(s: int, f: int) -> str:
    return _procedure_status_for(s, _smoothed_confidence(s, f), failure_count=f)


# --- Defect A: needs_review only for a losing record -----------------------
@pytest.mark.parametrize(("s", "f"), [(2, 2), (3, 2), (4, 3), (3, 3), (5, 5)])
def test_a_tie_or_winning_record_is_not_retired(s: int, f: int) -> None:
    assert _status(s, f) == "candidate"


def test_a_clear_winning_record_still_promotes() -> None:
    assert _status(2, 1) == "active"  # conf 0.6, two wins


@pytest.mark.parametrize(("s", "f"), [(0, 1), (1, 2), (2, 3), (3, 5)])
def test_a_losing_record_still_retires(s: int, f: int) -> None:
    assert _status(s, f) == "needs_review"


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


def _proc_row(pid: str, s: int, f: int, status: str) -> dict:
    return {"id": pid, "name": pid, "workflow_key": f"tools:{pid}", "trigger_tags": ["x"],
            "steps": ["Run tool: a"], "success_count": s, "failure_count": f,
            "confidence": 0.1, "status": status}


def test_repair_agrees_with_the_new_rule_and_keeps_deliberate_demotions(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    rows = [
        _proc_row("act32", 3, 2, "active"),        # old rule lowered it to needs_review
        _proc_row("cand22", 2, 2, "candidate"),    # old rule retired it
        _proc_row("nr32", 3, 2, "needs_review"),   # may be a MIR-058 demotion: kept
        _proc_row("act13", 1, 3, "active"),        # losing record: retired
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    store = ProceduralMemoryStore(path)
    store.recompute_legacy_confidence(dry_run=False)
    after = {p.id: p.status for p in store.load()}
    assert after == {"act32": "candidate", "cand22": "candidate",
                     "nr32": "needs_review", "act13": "needs_review"}


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


def test_a_subagent_without_granted_memory_keeps_its_tools(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]
    runner.run_contract(_contract())
    assert set(runner.run.call_args.kwargs["allowed_tools"]) >= _EGRESS


def test_tags_that_match_no_record_grant_nothing_and_keep_tools(tmp_path: Path) -> None:
    _store_with(tmp_path, MemoryRecord(content="другое", tags=["other"], source="agent-auto"))
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]
    runner.run_contract(_contract(memory_scope=CanonicalMemoryScope(read_tags=("budget",))))
    assert set(runner.run.call_args.kwargs["allowed_tools"]) >= _EGRESS


def test_a_proposal_with_memory_read_tags_drops_egress_tools() -> None:
    tools = ["file_read", "web_fetch", "web_search", "rss_fetch", "semantic_scholar_search"]
    with_mem = _parse_proposal("g", {"memory_read_tags": ["budget"], "allowed_tools": tools})
    assert with_mem.tool_scope.allowed_tools == ("file_read",)
    without = _parse_proposal("g", {"memory_read_tags": [], "allowed_tools": tools})
    assert set(without.tool_scope.allowed_tools) == set(tools)
