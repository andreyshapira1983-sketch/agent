"""Прошлый результат шага отдаётся вместо вызова, только если мир не менялся.

Замер 2026-09-19 (рабочий экзамен): агент верно исправил pricing.py, прогнал
тесты снова — и получил из кэша красный прогон, снятый ДО правки; увидев его,
откатил верную правку. Утром тот же кэш отдавал старое numbers.txt после того,
как заказчик дописал строки между ходами. См. `core/cache_freshness.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.cache_freshness import cache_stamp
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.models import PlanStep
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def test_only_reads_of_an_unchanged_world_are_cacheable(tmp_path: Path):
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    first = cache_stamp("file_read", {"path": "a.txt"}, tmp_path)
    assert first is not None
    (tmp_path / "a.txt").write_text("one and two", encoding="utf-8")
    assert cache_stamp("file_read", {"path": "a.txt"}, tmp_path) != first
    assert cache_stamp("web_fetch", {"url": "https://example.org"}, tmp_path) == "remote"
    for tool in ("run_tests", "python_probe", "find_in_files", "list_dir", "file_write", "current_time"):
        assert cache_stamp(tool, {"path": "a.txt"}, tmp_path) is None, tool


def _loop(ws: Path) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=ws))
    return AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=[]),
        logger=TraceLogger(new_trace_id(), ws / "logs", verbose=False),
        planner=FakePlanner(sources=[]), approval_provider=AutoApprover(default="approve"),
        memory=WorkingMemory(),
    )


def _read(loop: AgentLoop, n: int) -> dict:
    step = PlanStep(plan_id="p", order=n, expected_outcome="read", action_spec={
        "type": "tool_call", "tool_name": "file_read", "arguments": {"path": "numbers.txt"}})
    return loop._execute_step(step)


def test_a_file_changed_between_reads_is_read_again(tmp_path: Path):
    (tmp_path / "numbers.txt").write_text("1\n2\n", encoding="utf-8")
    loop = _loop(tmp_path)
    assert "2" in _read(loop, 1)["output"]
    again = _read(loop, 2)
    assert "served from working-memory cache" in again.get("issues", []), "неизменный файл — из кэша"

    (tmp_path / "numbers.txt").write_text("1\n2\n3000\n", encoding="utf-8")
    fresh = _read(loop, 3)
    assert "3000" in fresh["output"], "дописанные строки обязаны быть видны"
    events = [json.loads(line)["event"] for line in Path(loop.log.path).read_text(encoding="utf-8").splitlines()]
    assert "memory_cache_stale" in events
