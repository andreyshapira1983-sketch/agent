"""Запись файла несёт свой текст, а не то, что прочитано в том же плане.

Разговор 2026-09-22 на сервере: пять записей агента легли выводом чтения —
skill.py получил окно книги с номерами строк («[Erickson_Algorithms.txt lines
5300-5420 of 21363]»), proposals/.../patch.md дважды получил текст модуля
(.py → .md), а попытку описать правку съела сама поломка. Подсказка
планировщику учила ровно этому примером «file_read → file_write
content={{step:1.output}}». Диагноз и замысел проверки — агента; правило
уточнено, чтобы не сломать законное: вывод команды в файл и копию целого
файла того же вида (ради неё ссылки и делались, замер 2026-08-31).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import PlanStep
from core.policy import PolicyGate
from core.step_references import UnresolvedStepReference, reject_content_reference
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool

REF = "{{" + "step:1.output" + "}}"  # собрано из кусков: сам файл теста — тоже текст
WINDOW = "[loop_step_execution.py lines 340-430 of 1122]\n340:         self,\n"


def _check(tool: str, path: str, output: str, target: str) -> None:
    reject_content_reference("file_write", {"path": target, "content": REF},
                             {"1": (tool, path)}, {"1": output})


@pytest.mark.parametrize(("tool", "path", "output", "target"), [
    ("file_read", "knowledge_library/cs/txt/Erickson_Algorithms.txt",
     "[Erickson_Algorithms.txt lines 5300-5420 of 21363]\n5300: x\n",
     "proposals/skills/edit_distance/skill.py"),                     # 22.09 skill.py
    ("file_read", "core/referent_resolver.py", '"""Referent resolution…',
     "proposals/selffix/referent_trap/patch.md"),                     # 22.09 09:48
    ("file_read", "core/loop_step_execution.py", WINDOW,
     "proposals/selffix/step_ref_content/patch.md"),                  # 22.09 10:12
    ("web_fetch", "", "page text", "notes.md"),
    ("find_in_files", "", "a.py:3: hit", "notes.md"),
])
def test_what_was_read_does_not_replace_the_writers_text(tool, path, output, target) -> None:
    with pytest.raises(UnresolvedStepReference, match="Write your own text"):
        _check(tool, path, output, target)


@pytest.mark.parametrize(("tool", "path", "output", "target"), [
    ("python_probe", "", "871\n", "sum.txt"),                  # вывод команды — законно
    ("shell_exec", "", "ok\n", "out.log"),
    ("file_read", "source.txt", "измеренное содержимое", "copy.txt"),  # копия того же вида
    ("file_read", "src.txt", '{"a": 1}\nrows: 60\n', "report.json"),  # «скопируй src.txt в report.json»
])
def test_a_command_output_or_a_same_kind_copy_is_carried(tool, path, output, target) -> None:
    _check(tool, path, output, target)


def test_only_file_write_content_is_checked() -> None:
    reject_content_reference("file_read", {"path": REF}, {"1": ("file_read", "a.py")}, {"1": "x"})
    reject_content_reference("file_write", {"path": "a.md", "content": "свой текст"},
                             {"1": ("file_read", "a.py")}, {"1": "x"})


def _step(tool_name: str, arguments: dict, order: int) -> PlanStep:
    return PlanStep(plan_id="plan_x", order=order, expected_outcome="whatever",
                    action_spec={"type": "tool_call", "tool_name": tool_name, "arguments": arguments})


def test_the_loop_refuses_the_live_case_and_writes_nothing(workspace: Path) -> None:
    """Через настоящий исполнитель: .py прочитан, .md не записан, причина названа."""
    (workspace / "module.py").write_text('"""модуль"""\n', encoding="utf-8")
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    loop = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=[]),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False),
        planner=FakePlanner(sources=[]), approval_provider=AutoApprover(default="approve"),
    )
    steps = [_step("file_read", {"path": "module.py"}, 1),
             _step("file_write", {"path": "patch.md", "content": REF}, 2)]

    results = loop._execute_steps_parallel(steps)

    _step_obj, outcome, trigger = results[1]
    assert outcome is None and trigger is not None
    assert "Write your own text" in trigger.reason
    assert not (workspace / "patch.md").exists(), "прочитанное не должно лечь на диск"
