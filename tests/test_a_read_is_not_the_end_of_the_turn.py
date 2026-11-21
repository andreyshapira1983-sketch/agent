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

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
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


def test_the_rounds_are_bounded_by_the_failsafe(workspace: Path):
    """Планировщик, который читает без конца, не держит ход вечно."""
    for i in range(10):
        (workspace / f"a{i}.txt").write_text(f"part {i}", encoding="utf-8")
    planner = _ScriptedPlanner([[_src("file_read", {"path": f"a{i}.txt"})] for i in range(10)])
    loop = _loop(workspace, planner, observe=True)
    loop.max_observation_rounds = 4

    loop.run("Что написано в a.txt?")

    assert len(planner.contexts) == 4
    assert _events(loop, "observation_round_skipped")


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


def test_a_turn_over_its_dollar_budget_answers_with_what_it_has(workspace: Path, monkeypatch):
    """Предохранитель денег хода (как per_instance_cost_limit в SWE-agent): круги
    кончаются, ход отвечает по собранному, а не падает и не жжёт дальше."""
    import datetime as _dt

    for i in range(8):
        (workspace / f"a{i}.txt").write_text(f"part {i}", encoding="utf-8")
    later = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)).isoformat()
    (workspace / "data").mkdir(exist_ok=True)
    (workspace / "data" / "model_usage.jsonl").write_text(json.dumps({
        "model": "deepseek-v4-pro", "input_tokens": 2_000_000, "output_tokens": 0,
        "completed_at": later}) + "\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TURN_MAX_USD", "0.5")
    planner = _ScriptedPlanner([[_src("file_read", {"path": f"a{i}.txt"})] for i in range(8)] + [[]])
    loop = _loop(workspace, planner, observe=True)

    answer = loop.run("Собери все части a0..a7.")

    assert len(planner.contexts) == 1
    skipped = _events(loop, "observation_round_skipped")
    assert skipped and "dollar budget" in skipped[-1]["reason"]
    assert answer


def test_one_action_with_the_same_result_four_times_ends_the_rounds(workspace: Path):
    """Ночь 24→25.09: find_in_files «def complete» 7 раз за минуту с одним ответом;
    соседние шаги в кругах были разные, датчик одинаковых кругов молчал.
    OpenHands StuckDetector: пара «действие → наблюдение» 4 раза — стоп."""
    (workspace / "same.txt").write_text("the same answer", encoding="utf-8")
    for i in range(10):
        (workspace / f"a{i}.txt").write_text(f"part {i}", encoding="utf-8")
    rounds = [[_src("file_read", {"path": "same.txt", "start_line": 1, "end_line": 1}),
               _src("file_read", {"path": f"a{i}.txt"})] for i in range(10)]
    planner = _ScriptedPlanner(rounds)
    loop = _loop(workspace, planner, observe=True)

    loop.run("Найди, где определено complete.")

    assert len(planner.contexts) == 4, "четвёртый одинаковый результат одного действия — конец кругов"
    assert "REPEAT: the step" in planner.contexts[2]
    skipped = _events(loop, "observation_round_skipped")
    assert skipped and "one action gave the same result" in skipped[-1]["reason"]
