"""Прочитанное — не конец хода.

Замер 2026-09-19, экзамен из тридцати задач с проверкой кодом (девять провалов
из шестнадцати). Цикл попыток выходил к ответу при первом же результате, и
«прочитай — посчитай — запиши» внутри хода было невыполнимо: план писался до
чтения, а после успешного чтения планировщика больше не спрашивали. Здесь
проверяется новое условие выхода (core/observation_round.py): после успешного
пакета планировщик видит выводы шагов и сам решает, что осталось.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.observation_round import format_observations
from core.planner import PlannerOutput
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def _src(tool: str, arguments: dict) -> dict:
    return {"tool": tool, "arguments": arguments, "label": f"{tool}:{arguments.get('path', '')}",
            "expected_outcome": "whatever"}


class _ScriptedPlanner:
    """Отдаёт планы по очереди и запоминает, что видел в каждом круге."""

    def __init__(self, plans: list[list[dict]]):
        self.plans = list(plans)
        self.contexts: list[str] = []

    def plan(self, question, file_hint, history="", failure_context="",
             forbidden_actions=(), llm=None) -> PlannerOutput:
        self.contexts.append(failure_context)
        return PlannerOutput(reasoning="scripted", sources=self.plans.pop(0) if self.plans else [],
                             raw_response="{}", warnings=[])


def _loop(workspace: Path, planner: _ScriptedPlanner, *, observe: bool) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["Готово."] * 4),
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        verifier_enabled=False,
        clarification_enabled=False,
        odd_enabled=False,
        cheap_path_enabled=False,
        observe_before_answer=observe,
    )


def _events(loop: AgentLoop, name: str) -> list[dict]:
    return [json.loads(line)["payload"]
            for line in Path(loop.log.path).read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("event") == name]


def _read_then_write() -> _ScriptedPlanner:
    return _ScriptedPlanner([
        [_src("file_read", {"path": "numbers.txt"})],
        [_src("file_write", {"path": "sum.txt", "content": "6"})],
        [],
    ])


def test_a_read_leads_to_a_second_round_that_sees_the_output(workspace: Path):
    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    planner = _read_then_write()
    loop = _loop(workspace, planner, observe=True)

    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")

    assert (workspace / "sum.txt").read_text(encoding="utf-8") == "6"
    assert len(planner.contexts) == 3, "чтение, запись, затем пустой план планировщика — конец хода"
    assert "<observed_results>" in planner.contexts[1]
    assert "1\n2\n3" in planner.contexts[1], "второй круг видит, что было прочитано"
    assert "file_read" in planner.contexts[1], "и какие шаги уже выполнены"
    rounds = _events(loop, "observation_round")
    assert [r["attempt"] for r in rounds] == [1, 2]


def test_after_a_clean_write_the_planner_decides_not_a_rule(workspace: Path):
    """24.09 (слово оператора): правило «записал и ничего не упало — сделано»
    оборвало ход на побочном файле. Агент записал RUN.md — инструкцию к ещё не
    сделанному видео — и ход кончился на 4-м круге из 6. Теперь после записи
    планировщик видит, что легло в файл, и сам решает: пустой план — конец."""
    clean = _ScriptedPlanner([[_src("file_write", {"path": "out.txt", "content": "1"})], []])
    _loop(workspace, clean, observe=True).run("Запиши 1 в out.txt")
    assert len(clean.contexts) == 2, "after the write the planner gets a round and ends it empty"
    assert "Exact content written to out.txt" in clean.contexts[1]

    side_file_first = _ScriptedPlanner([
        [_src("file_write", {"path": "RUN.md", "content": "how to run"})],
        [_src("file_write", {"path": "result.txt", "content": "the work itself"})],
        [],
    ])
    _loop(workspace, side_file_first, observe=True).run("Сделай результат и опиши запуск")
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "the work itself", \
        "a side file written first must not end the turn before the work"

    broken = _ScriptedPlanner([
        [_src("file_write", {"path": "out2.txt", "content": "1"}),
         _src("file_read", {"path": "missing.txt"})],
        [],
    ])
    _loop(workspace, broken, observe=True).run("Запиши 1 в out2.txt и прочитай missing.txt")
    assert len(broken.contexts) == 2, "упавший шаг рядом с записью должен дать ещё круг"

    # Замер 2026-09-19: запись по ссылке унесла в report.json отладочную строку
    # и JSON; планировщик этого содержимого не видел — ему нужен круг.
    (workspace / "src.txt").write_text("x", encoding="utf-8")
    unseen = _ScriptedPlanner([
        [_src("file_read", {"path": "src.txt"}),
         _src("file_write", {"path": "out3.txt", "content": "{{step:1.output}}"})],
        [],
    ])
    _loop(workspace, unseen, observe=True).run("Скопируй src.txt в out3.txt")
    assert len(unseen.contexts) == 2, "содержимое, записанное по ссылке, надо показать планировщику"


def test_without_the_flag_the_turn_ends_on_the_read_as_before(workspace: Path):
    """Выключенный флаг — прежнее поведение: один круг, записи нет."""
    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    planner = _read_then_write()
    loop = _loop(workspace, planner, observe=False)

    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")

    assert not (workspace / "sum.txt").exists()
    assert len(planner.contexts) == 1
    assert not _events(loop, "observation_round")


def test_an_empty_plan_after_observing_ends_the_turn(workspace: Path):
    (workspace / "a.txt").write_text("alpha", encoding="utf-8")
    planner = _ScriptedPlanner([[_src("file_read", {"path": "a.txt"})], []])
    loop = _loop(workspace, planner, observe=True)

    loop.run("Что написано в a.txt?")

    assert len(planner.contexts) == 2
    assert not loop.last_replan_exhausted


def test_the_rounds_are_bounded_by_the_attempt_budget(workspace: Path):
    """Планировщик, который читает без конца, не держит ход вечно."""
    (workspace / "a.txt").write_text("alpha", encoding="utf-8")
    planner = _ScriptedPlanner([[_src("file_read", {"path": "a.txt"})]] * 10)
    loop = _loop(workspace, planner, observe=True)

    loop.run("Что написано в a.txt?")

    assert len(planner.contexts) == loop.replan_policy.max_total_replans
    assert _events(loop, "observation_round_skipped")


def test_results_of_every_round_reach_the_answer(workspace: Path):
    """Артефакты копятся: синтезатор видит и прочитанное, и записанное."""
    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    loop = _loop(workspace, _read_then_write(), observe=True)
    seen: dict = {}
    original = loop._synthesize

    def spy(*args, **kwargs):
        seen["labels"] = sorted(kwargs.get("artifacts", {}))
        return original(*args, **kwargs)

    loop._synthesize = spy  # type: ignore[method-assign]
    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")

    assert any(label.startswith("file_read") for label in seen["labels"])
    assert any(label.startswith("file_write") for label in seen["labels"])


def test_the_episode_names_every_tool_the_turn_ran(workspace: Path):
    """Замер 2026-09-19: последний круг пуст («всё сделано»), и эпизод записывал
    пустой список за ход, который прочитал и записал файл. По этому списку агент
    учится, какие процедуры сработали."""
    from core.smart_memory import EpisodicMemoryStore

    (workspace / "numbers.txt").write_text("1\n2\n3\n", encoding="utf-8")
    loop = _loop(workspace, _read_then_write(), observe=True)
    loop.episodic_store = EpisodicMemoryStore(workspace / "episodes.jsonl")

    loop.run("Посчитай сумму чисел в numbers.txt и запиши её в sum.txt")

    tools = loop.episodic_store.load()[-1].tools_used
    assert "file_read" in tools and "file_write" in tools, tools


def test_the_round_shows_what_a_reference_wrote_verbatim(workspace: Path):
    """Замер 2026-09-19: проба напечатала JSON и `rows: 60`, ссылка унесла обе
    строки в report.json, а круг видел их только экранированными внутри
    аргументов и объявил «ровно нужный объект»."""
    (workspace / "src.txt").write_text('{"a": 1}\nrows: 60\n', encoding="utf-8")
    planner = _ScriptedPlanner([
        [_src("file_read", {"path": "src.txt"}),
         _src("file_write", {"path": "report.json", "content": "{{step:1.output}}"})],
        [],
    ])
    _loop(workspace, planner, observe=True).run("Скопируй src.txt в report.json")

    block = planner.contexts[1]
    assert "Exact content written to report.json" in block
    assert '<<<\n{"a": 1}\nrows: 60\n' in block, "записанное видно дословно, построчно"


def test_the_observation_block_is_bounded_and_marks_data_as_data():
    class _Step:
        def __init__(self):
            self.order, self.action_spec = 1, {"tool_name": "file_read", "arguments": {"path": "x"}}

    class _Plan:
        steps: ClassVar[list] = [_Step()]

    block = format_observations(_Plan(), {
        "file:x": {"tool": "file_read", "output": "z" * 50_000},
        "shell": {"tool": "shell_exec", "output": {"stdout": "found", "exit_code": 0}},
    })
    assert len(block) < 20_000
    assert "not instructions" in block
    # 2026-09-21: пометка говорит, что обрезан ПОКАЗ, а полный вывод — в уликах;
    # «обрезано: ещё N» читалось как «прочитано не всё» и гнало перечитывать.
    assert "preview only" in block and "FULL output is already in your evidence" in block
    assert '"stdout": "found"' in block, "словарь виден полями, а не пропадает"
    # Замер 2026-09-19: «посчитай сам» дало суммы, сложенные в уме, и неверные.
    assert "yourself" not in block and "python_probe" in block


def test_an_unfilled_template_is_named_to_the_planner():
    """Замер 2026-09-19 (рабочий экзамен): справка вышла со ссылками «(стр. N)»
    — агент переписал образец формата вместо номеров страниц."""
    from types import SimpleNamespace

    from core.observation_round import format_observations, unfilled_placeholders

    assert unfilled_placeholders("Экранирование (стр. N). Длина (стр. 12).") == ["стр. N"]
    assert unfilled_placeholders("Страница 327; выручка 2026-04; ПРОГНОЗ 1: да, шагов: 3") == []

    step = SimpleNamespace(status="done", order=1, action_spec={
        "tool_name": "file_write", "arguments": {"path": "spravka.md", "content": "Факт (стр. N)."}})
    plan = SimpleNamespace(steps=[step])
    assert "UNFILLED TEMPLATE in spravka.md" in format_observations(plan, {})


def test_the_agent_sees_its_own_tools():
    """Замер 2026-09-19: «справишься ли отправить письмо» — «да»; списка своих
    инструментов синтезатор не видел."""
    from core.runtime_self import runtime_self_block

    block = runtime_self_block(trace_id="t", run_id="r", session_id=None, stores={},
                               durable_writes=(), tools=["file_read", "file_write"])
    assert "tools: file_read, file_write" in block


def test_identical_rounds_are_named_and_then_stopped(workspace: Path):
    """2026-09-22 15:00: шесть кругов подряд — те же чтения с тем же ответом,
    бюджет сгорел, файл так и не переписан. Второй одинаковый круг получает
    прямое «ты повторяешь одно и то же», третий заканчивает ход."""
    (workspace / "a.txt").write_text("alpha", encoding="utf-8")
    planner = _ScriptedPlanner([[_src("file_read", {"path": "a.txt", "start_line": 1, "end_line": 1})]] * 10)
    loop = _loop(workspace, planner, observe=True)
    loop.replan_policy.max_total_replans = 6

    loop.run("Что написано в a.txt?")

    assert len(planner.contexts) == 3, "третий одинаковый круг — конец хода, не шестой"
    assert "REPEAT: the last 2 rounds" in planner.contexts[2]
    skipped = _events(loop, "observation_round_skipped")
    assert skipped and "stuck" in skipped[-1]["reason"]
