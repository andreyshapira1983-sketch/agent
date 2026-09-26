"""Красная проверка — сбой, а не «круг прошёл без ошибок».

След 27.09 (trace_66e414ca…): 11 красных patch_check подряд, счётчик сбоев пуст,
планировщику каждый круг писали «The previous plan ran WITHOUT errors», а
единственный совет был «перепиши файл». Решение замечания ruff S603 лежало в
своём же коде (`# noqa: S603` в tools/convert_file.py) — его ни разу не искали.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tests.test_a_read_is_not_the_end_of_the_turn import _ScriptedPlanner, _src
from tools.base import Tool, ToolRegistry

#: Вывод patch_check из следа 27.09, укороченный.
_RED = [
    {"verdict": "red", "why": "tests are not green", "applied": True, "errors": [],
     "tests_output": "E       AttributeError: module 'tools.web_fetch' has no attribute '_shutil'\n"
                     "1 failed, 166 passed in 4.40s",
     "ruff": "S607 Starting a process with a partial executable path\nFound 1 error."},
    {"verdict": "red", "why": "tests are not green", "applied": True, "errors": [],
     "tests_output": "E       AttributeError: module 'tools.web_fetch' has no attribute 'subprocess'\n"
                     "1 failed, 166 passed in 4.40s",
     "ruff": "S603 `subprocess` call: check for execution of untrusted input\nFound 1 error."},
    {"verdict": "green", "why": "", "applied": True, "errors": [], "tests_output": "167 passed", "ruff": ""},
]


class _PatchCheck(Tool):
    name = "patch_check"
    description = "replays the patch in a copy and runs its tests"
    risk = "read_only"

    def __init__(self) -> None:
        self.outputs = list(_RED)

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return self.outputs.pop(0)


def _loop(workspace: Path, planner: _ScriptedPlanner) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(_PatchCheck())
    return AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=["Готово."] * 4),
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=planner, approval_provider=AutoApprover(default="approve"),
        verifier_enabled=False, clarification_enabled=False, odd_enabled=False,
        cheap_path_enabled=False, observe_before_answer=True,
    )


def test_a_red_check_is_counted_named_and_sends_the_agent_to_look_it_up(workspace: Path):
    check = _src("patch_check", {"path": "proposals/selffix/pdf/edits.txt", "full": False})
    planner = _ScriptedPlanner([[check], [check], [check], []])
    loop = _loop(workspace, planner)

    loop.run("Доведи правку proposals/selffix/pdf/edits.txt до зелёного")

    events = [json.loads(line) for line in Path(loop.log.path).read_text(encoding="utf-8").splitlines()]
    counts = [e["payload"]["failure_counts_so_far"] for e in events if e["event"] == "replan_attempt"]
    assert counts[0] == {"verify_failed": 1}, "первая красная проверка — это сбой в счётчике"
    assert counts[1] == {"verify_failed": 2}

    first, second, after_green = planner.contexts[1], planner.contexts[2], planner.contexts[3]
    assert "WITHOUT errors" not in first, "красный круг не называется чистым"
    assert "came back RED" in first and "has no attribute '_shutil'" in first
    assert "STILL RED" not in first, "одна красная — ещё не повод искать"

    assert "STILL RED" in second and "ruff: S603" in second
    assert "find_in_files" in second and "web_search" in second, "сначала найти, как это уже решено"
    assert second.index("<observed_results>") < second.index("STILL RED"), \
        "предупреждение внутри блока: иначе показ перестаёт быть дословным"

    assert "WITHOUT errors" in after_green and "STILL RED" not in after_green
