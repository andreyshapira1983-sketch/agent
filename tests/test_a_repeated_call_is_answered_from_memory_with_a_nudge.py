"""A repeated search or computation in an unchanged world is served from memory with a nudge (live run 26.09: 29 of 299 calls were repeats)."""
from __future__ import annotations

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
from tools.find_in_files import FindInFilesTool


def test_a_search_of_one_file_is_stamped_like_a_read(tmp_path: Path) -> None:
    (tmp_path / "book.txt").write_text("Theorem 5.1\n", encoding="utf-8")
    first = cache_stamp("find_in_files", {"path": "book.txt", "query": "Theorem"}, tmp_path)
    assert first is not None
    (tmp_path / "book.txt").write_text("Theorem 5.1\nTheorem 5.2\n", encoding="utf-8")
    assert cache_stamp("find_in_files", {"path": "book.txt", "query": "Theorem"}, tmp_path) != first
    assert cache_stamp("find_in_files", {"path": ".", "query": "Theorem"}, tmp_path) is None, \
        "папка меняется без смены своего времени — не из кэша"


def test_a_computation_is_fresh_only_until_the_next_effect(tmp_path: Path) -> None:
    code = {"code": "print(1 + 1)"}
    assert cache_stamp("python_probe", code, tmp_path) is None, "без счёта действий — не из кэша"
    assert cache_stamp("python_probe", code, tmp_path, effects=3) == cache_stamp("python_probe", code, tmp_path, 3)
    assert cache_stamp("python_probe", code, tmp_path, effects=3) != cache_stamp("python_probe", code, tmp_path, 4)


def _loop(ws: Path) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=ws))  # the loop takes the workspace root from it
    registry.register(FindInFilesTool(workspace_root=ws))
    return AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=[]),
        logger=TraceLogger(new_trace_id(), ws / "logs", verbose=False),
        planner=FakePlanner(sources=[]), approval_provider=AutoApprover(default="approve"),
        memory=WorkingMemory(),
    )


def _search(loop: AgentLoop, n: int) -> dict:
    step = PlanStep(plan_id="p", order=n, expected_outcome="found", action_spec={
        "type": "tool_call", "tool_name": "find_in_files",
        "arguments": {"path": "book.txt", "query": "Theorem"}})
    return loop._execute_step(step)


def test_the_same_search_twice_comes_from_memory_with_a_nudge(tmp_path: Path) -> None:
    (tmp_path / "book.txt").write_text("Theorem 5.1\n", encoding="utf-8")
    loop = _loop(tmp_path)
    _search(loop, 1)

    again = _search(loop, 2)

    assert any("повтор" in i for i in again.get("issues", [])), "повтор без подсказки"
    (tmp_path / "book.txt").write_text("Theorem 5.1\nTheorem 5.2\n", encoding="utf-8")
    assert "5.2" in str(_search(loop, 3)["output"]), "изменённый файл обязан искаться заново"
