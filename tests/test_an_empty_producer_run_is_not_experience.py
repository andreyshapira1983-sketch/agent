"""Пустой ход производителя заявок не банкуется как опыт дважды.

Замер 2026-09-22: в опыте 300 записей, из них 275 — «self-build-produce» со
статусами no_grounded_target (193) и no_patch (82). Они вытеснили ВСЮ
настоящую работу: сегодня ноль эпизодов с книгами и ноль успехов, поэтому
драйвы компетентности вечно показывали «успешной задачи по области ни разу»,
а учёба всегда перебивала самопочинку.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.self_build_memory import record_self_build_episode
from core.smart_memory import EpisodicMemoryStore


def _agent(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(episodic_store=EpisodicMemoryStore(tmp_path / "episodes.jsonl"))


def test_the_same_empty_run_is_banked_once(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    result = {"status": "no_grounded_target", "reason": "split target 'core/x.py' is not editable"}

    assert record_self_build_episode(agent, kind="self-build-produce", result=result) is True
    assert record_self_build_episode(agent, kind="self-build-produce", result=result) is False

    assert len(agent.episodic_store.load()) == 1


def test_real_work_is_still_banked_every_time(tmp_path: Path) -> None:
    agent = _agent(tmp_path)
    result = {"status": "proposed", "target_path": "core/x.py", "approval_id": "ain_1"}

    assert record_self_build_episode(agent, kind="self-build-produce", result=result) is True
    assert record_self_build_episode(agent, kind="self-build-produce", result=result) is True

    assert len(agent.episodic_store.load()) == 2
