"""Субагент видит только ту память, что названа в его контракте.

Замер, отвергнутые варианты и границы: MIR-156 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.subagent_contract import (
    CanonicalBudgetScope,
    CanonicalMemoryScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
)
from core.subagent_runner import SubagentContractRefused, SubAgentRunner
from tools.base import ToolRegistry


def _runner(tmp_path: Path) -> SubAgentRunner:
    return SubAgentRunner(
        workspace_root=tmp_path,
        policy=MagicMock(),
        model_router=MagicMock(),
        parent_registry=ToolRegistry(),
        log_dir=tmp_path,
    )


def _contract(**overrides) -> CanonicalSubagentContract:
    values = {
        "contract_id": "subc_mem",
        "source": "team_plan",
        "name": "RepositoryAuditor",
        "role": "auditor",
        "objective": "Audit the repository",
        "outputs": ("findings",),
        "tool_scope": CanonicalToolScope(allowed_tools=("file_read",)),
        "budget_scope": CanonicalBudgetScope(
            max_model_calls=2, max_iterations=1, max_cost_units=3,
        ),
        "model_role": "verifier",
        "risk_level": "low",
        "approval_required": False,
    }
    values.update(overrides)
    return CanonicalSubagentContract(**values)


def _store_with(tmp_path: Path, *records: MemoryRecord) -> None:
    store = PersistentMemoryStore(tmp_path / "data" / "persistent_memory.jsonl")
    for record in records:
        store.save(record)


def test_a_granted_tag_reaches_the_subagent(tmp_path: Path) -> None:
    """Красный свидетель: контракт с разрешённой меткой ОТВЕРГАЛСЯ целиком.

    Субагент не получал даже той памяти, которая ему выдана: слой заявки права
    расписывал, исполнитель отвечал «не поддерживается».
    """
    _store_with(
        tmp_path,
        MemoryRecord(content="потолок дня равен 500", tags=["budget"], source="agent-auto"),
    )
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]

    runner.run_contract(
        _contract(memory_scope=CanonicalMemoryScope(read_tags=("budget",))),
        context="исходный контекст",
    )

    passed = runner.run.call_args.kwargs["context"]
    assert "потолок дня равен 500" in passed
    assert "исходный контекст" in passed, "проекция не должна съедать прежний контекст"


def test_an_ungranted_record_never_appears(tmp_path: Path) -> None:
    """Граница: выдана одна метка — вторая не видна.

    Без этой проверки «проекция» удовлетворялась бы передачей всей памяти.
    """
    _store_with(
        tmp_path,
        MemoryRecord(content="потолок дня равен 500", tags=["budget"], source="agent-auto"),
        MemoryRecord(content="пароль оператора", tags=["secret"], source="user-explicit"),
    )
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]

    runner.run_contract(
        _contract(memory_scope=CanonicalMemoryScope(read_tags=("budget",))),
        context="",
    )

    passed = runner.run.call_args.kwargs["context"]
    assert "потолок дня равен 500" in passed
    assert "пароль оператора" not in passed


def test_a_write_grant_is_still_refused(tmp_path: Path) -> None:
    """Право ЗАПИСИ не выдано и этой правкой не выдаётся.

    Разрешение читать и разрешение писать — разные полномочия; вторая половина
    требует пути «находка -> проверка -> продвижение», которого нет.
    """
    runner = _runner(tmp_path)

    with pytest.raises(SubagentContractRefused, match="write"):
        runner.run_contract(
            _contract(memory_scope=CanonicalMemoryScope(write_tags=("lesson",))),
            context="",
        )


def test_no_grant_means_no_memory_at_all(tmp_path: Path) -> None:
    """Умолчание остаётся прежним: без названной метки субагент беспамятен."""
    _store_with(
        tmp_path,
        MemoryRecord(content="что-то важное", tags=["budget"], source="agent-auto"),
    )
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]

    runner.run_contract(_contract(), context="только это")

    assert runner.run.call_args.kwargs["context"] == "только это"


def test_an_empty_store_adds_nothing(tmp_path: Path) -> None:
    """Граница: разрешение есть, показывать нечего — пустого блока не будет."""
    runner = _runner(tmp_path)
    runner.run = MagicMock(return_value=object())  # type: ignore[method-assign]

    runner.run_contract(
        _contract(memory_scope=CanonicalMemoryScope(read_tags=("budget",))),
        context="только это",
    )

    assert runner.run.call_args.kwargs["context"] == "только это"
