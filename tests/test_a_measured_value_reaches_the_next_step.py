"""Прочитанное доезжает до записи — иначе модель вынуждена сочинять.

Замер 2026-08-31/09-01, повторён на четырёх разных задачах: плана «прочитай
файл и запиши прочитанное» не существовало. Аргументы всех шагов фиксируются
при планировании, поэтому в `file_write` уезжала строка
``<content read from .agent_drafts/...>`` — сторож заглушек её отвергал, и
агент вывел: «перенос данных возможен только когда содержимое целиком лежит в
самом плане».

Последствие было не в одном сломанном шаге, а в поведении: не имея права
опереться на прочитанное, модель ВЫНУЖДЕНА была угадывать — схему записи,
сигнатуру функции, содержимое файла. Часть того, что выглядело как «выдумывает
вместо того, чтобы посмотреть», оказалась невозможностью посмотреть и записать
в одном плане.

Здесь проверяется транспорт: ссылка `{{step:<id|order>.output}}` делает шаг
зависимым, разрешается ПОСЛЕ измерения источника и никогда не подставляет
правдоподобное вместо измеренного.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import PlanStep
from core.placeholder_text import looks_like_unfilled_content
from core.policy import PolicyGate
from core.step_references import (
    UnresolvedStepReference,
    has_step_reference,
    referenced_steps,
    resolve_step_references,
)
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def test_a_whole_argument_keeps_the_measured_type():
    outputs = {"0": ["первая", "вторая"]}

    resolved = resolve_step_references({"items": "{{step:0.output}}"}, outputs)

    assert resolved["items"] == ["первая", "вторая"], "список обязан остаться списком"


def test_a_reference_inside_text_is_substituted():
    outputs = {"read": "содержимое файла"}

    resolved = resolve_step_references(
        {"content": "начало\n{{step:read.output}}\nконец"}, outputs)

    assert resolved["content"] == "начало\nсодержимое файла\nконец"


def test_references_are_found_at_any_depth():
    arguments = {
        "path": "core/loop.py",
        "payload": {"nested": ["{{step:2.output}}", "плоский текст"]},
        "content": "{{step:read_a.output}}",
    }

    assert has_step_reference(arguments)
    assert referenced_steps(arguments) == ("2", "read_a")


def test_arguments_without_references_are_untouched():
    arguments = {"path": "core/loop.py", "content": "обычный текст"}

    assert not has_step_reference(arguments)
    assert resolve_step_references(arguments, {}) == arguments


def test_a_missing_source_is_an_error_not_a_guess():
    """Главное свойство: неразрешённая ссылка НЕ дорисовывается."""
    with pytest.raises(UnresolvedStepReference) as excinfo:
        resolve_step_references({"content": "{{step:7.output}}"}, {"0": "есть"})

    assert "no result" in str(excinfo.value)
    assert "'0'" in str(excinfo.value), "причина обязана назвать, что было известно"


def test_the_reference_form_is_not_mistaken_for_a_placeholder():
    """Транспорт не воюет со сторожем заглушек: форма ссылки ему не родня."""
    assert not looks_like_unfilled_content("{{step:0.output}}")
    assert looks_like_unfilled_content("<content read from .agent_drafts/x.py>")


def test_an_empty_output_is_still_a_measurement():
    """Пустой вывод — это измеренный факт, а не отсутствие результата."""
    resolved = resolve_step_references({"content": "{{step:0.output}}"}, {"0": ""})

    assert resolved["content"] == ""


# ===========================================================
# Путь внутрь вывода — ссылка, которую нельзя разрешить (ход 34)
# ===========================================================


def test_a_path_into_the_output_is_a_named_error_not_a_literal():
    """Экзамен 2026-09-05, ход 34: планировщик написал
    `{{step:1.output.live_trace_id}}`; форма не совпала с контрактом, строка
    уехала в `read_logs` буквально, и агент два хода объяснял не ту ошибку.
    Теперь шаг признаётся зависимым и проваливается с названным правилом."""
    arguments = {"trace_id": "{{step:1.output.live_trace_id}}"}

    assert has_step_reference(arguments)
    with pytest.raises(UnresolvedStepReference) as excinfo:
        resolve_step_references(arguments, {"1": {"live_trace_id": "trace_x"}})

    message = str(excinfo.value)
    assert "not a supported form" in message
    assert "'.live_trace_id'" in message
    assert "{{step:1.output}}" in message, "правило обязано быть названо"


def test_an_index_into_the_output_is_rejected_the_same_way():
    with pytest.raises(UnresolvedStepReference):
        resolve_step_references({"x": "{{step:0.output[0]}}"}, {"0": ["a"]})


# ===========================================================
# Проза о синтаксисе — не ссылка (экзамен 2026-09-05, ход 22)
# ===========================================================


def test_prose_naming_the_reference_form_stays_prose():
    """В objective субагента стояло «ссылок вида {{step:N.output}}» — «N» не
    шаг, а буква, и шаг, которому нужно было лишь рассказать о синтаксисе,
    провалился целиком. Когда план известен, такая форма — текст."""
    arguments = {"objective": "найди обработку ссылок вида {{step:N.output}} в коде"}

    resolved = resolve_step_references(arguments, {"1": "x"}, plan_steps={"1", "2"})

    assert resolved == arguments


def test_a_dangling_numbered_reference_is_still_an_error():
    """Номер, которого нет в плане, — не проза, а висячая ссылка: раньше
    ровно такая легла на диск 17-байтовым файлом (ходы 12–13)."""
    with pytest.raises(UnresolvedStepReference):
        resolve_step_references(
            {"content": "{{step:9.output}}"}, {"1": "x"}, plan_steps={"1", "2"},
        )


def test_without_a_plan_every_form_is_a_reference():
    """Старые вызовы, не знающие плана, остаются строгими."""
    with pytest.raises(UnresolvedStepReference):
        resolve_step_references({"objective": "вида {{step:N.output}}"}, {"1": "x"})


# ===========================================================
# Транспорт внутри исполнителя шагов: обе ветки, не одна
# ===========================================================


class TestReferencesReachTheEffectPath:
    """Замер 2026-09-05 (экзамен, ходы 12–13): два ремонта подряд записали в
    дерево файл из 17 байт — буквальную строку `{{step:4.output}}`. Резолвер
    существовал и работал, но вызывался только на ветке чтений; ветка, куда
    попадает любой план с `file_write`, гнала шаги сырыми. Ровно те планы,
    которым подстановка нужна, её не получали.
    """

    def _loop(self, workspace: Path) -> AgentLoop:
        registry = ToolRegistry()
        registry.register(FileReadTool(workspace_root=workspace))
        registry.register(FileWriteTool(workspace_root=workspace))
        return AgentLoop(
            registry=registry,
            policy=PolicyGate(registry),
            llm=FakeLLM(responses=[]),
            logger=TraceLogger(
                trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
            ),
            planner=FakePlanner(sources=[]),
            approval_provider=AutoApprover(default="approve"),
        )

    @staticmethod
    def _step(tool_name: str, arguments: dict, order: int) -> PlanStep:
        return PlanStep(
            plan_id="plan_x",
            order=order,
            action_spec={"type": "tool_call", "tool_name": tool_name, "arguments": arguments},
            expected_outcome="whatever",
        )

    def test_what_was_read_is_what_gets_written(self, workspace: Path):
        (workspace / "source.txt").write_text("измеренное содержимое", encoding="utf-8")
        loop = self._loop(workspace)
        steps = [
            self._step("file_read", {"path": "source.txt"}, 1),
            self._step("file_write", {"path": "copy.txt", "content": "{{step:1.output}}"}, 2),
        ]

        results = loop._execute_steps_parallel(steps)

        assert all(outcome is not None for _, outcome, _ in results), results
        assert (workspace / "copy.txt").read_text(encoding="utf-8") == "измеренное содержимое"

    def test_a_step_without_a_source_is_not_executed(self, workspace: Path):
        """Обещание докстринга: неразрешённая ссылка — шаг НЕ исполняется.

        До 2026-09-05 шаг получал `status="failed"` и всё равно запускался с
        буквальной строкой в аргументах — так плейсхолдер и попал на диск.
        """
        loop = self._loop(workspace)
        steps = [
            self._step("file_read", {"path": "source.txt"}, 1),
            self._step("file_write", {"path": "copy.txt", "content": "{{step:9.output}}"}, 2),
        ]

        results = loop._execute_steps_parallel(steps)

        step, outcome, trigger = results[1]
        assert outcome is None
        assert step.status == "failed"
        assert trigger is not None and "{{step:9.output}}" in trigger.reason
        assert trigger.code == "tool_error"
        assert not (workspace / "copy.txt").exists(), "плейсхолдер не должен лечь на диск"

    def test_the_read_only_path_keeps_the_same_promise(self, workspace: Path):
        (workspace / "a.txt").write_text("a", encoding="utf-8")
        loop = self._loop(workspace)
        ran: list[str] = []

        def record(step: PlanStep):
            ran.append(step.action_spec["arguments"]["path"])
            return step, {"tool": "file_read", "output": "a", "label": "x", "issues": []}, None

        loop._run_step_parallel = record  # type: ignore[assignment]
        steps = [
            self._step("file_read", {"path": "a.txt"}, 1),
            self._step("file_read", {"path": "{{step:9.output}}"}, 2),
        ]

        results = loop._execute_steps_parallel(steps)

        assert ran == ["a.txt"], "шаг с неразрешённой ссылкой не должен был запускаться"
        assert results[1][1] is None and results[1][2] is not None

    def test_prose_about_the_syntax_reaches_the_tool(self, workspace: Path):
        """Экзамен 2026-09-05, ход 22: шаг с «ссылок вида {{step:N.output}}» в
        objective провалился как неразрешённая ссылка. Здесь план известен,
        «N» — не шаг, и текст доезжает до инструмента нетронутым."""
        (workspace / "a.txt").write_text("a", encoding="utf-8")
        loop = self._loop(workspace)
        seen: list[dict] = []

        def record(step: PlanStep):
            seen.append(dict(step.action_spec["arguments"]))
            return step, {"tool": "file_read", "output": "a", "label": "x", "issues": []}, None

        loop._run_step_parallel = record  # type: ignore[assignment]
        prose = "найди обработку ссылок вида {{step:N.output}}; контекст: {{step:1.output}}"
        steps = [
            self._step("file_read", {"path": "a.txt"}, 1),
            self._step("file_read", {"path": prose}, 2),
        ]

        results = loop._execute_steps_parallel(steps)

        assert results[1][2] is None, results[1][2]
        assert seen[1]["path"] == "найди обработку ссылок вида {{step:N.output}}; контекст: a"

    def test_a_substituted_context_is_fitted_not_refused(self, workspace: Path):
        """Экзамен 2026-09-05, сессия exam_h2: подставленный в `context`
        субагента вывод двух шагов превысил 2000 символов, инструмент отказал
        («'context' exceeds 2000 characters»), и три зависимых шага не
        исполнились. Шаблон санитайзер обрезал бы — измеренное обязано пройти
        те же правила."""
        from tools.spawn_subagent import _MAX_CONTEXT_LEN

        (workspace / "big.txt").write_text("x" * (_MAX_CONTEXT_LEN + 500), encoding="utf-8")
        loop = self._loop(workspace)
        seen: list[dict] = []

        def record(step: PlanStep):
            seen.append(dict(step.action_spec["arguments"]))
            output = (workspace / "big.txt").read_text(encoding="utf-8")
            return step, {"tool": step.action_spec["tool_name"], "output": output, "label": "x", "issues": []}, None

        loop._run_step_parallel = record  # type: ignore[assignment]
        steps = [
            self._step("file_read", {"path": "big.txt"}, 1),
            self._step(
                "spawn_subagent",
                {"role": "R", "objective": "o", "context": "Event: {{step:1.output}}"},
                2,
            ),
        ]

        results = loop._execute_steps_parallel(steps)

        assert results[1][2] is None, results[1][2]
        assert len(seen[1]["context"]) == _MAX_CONTEXT_LEN
        assert seen[1]["context"].startswith("Event: xxx")
        events = (workspace / "logs").glob("*.jsonl")
        text = "".join(p.read_text(encoding="utf-8") for p in events)
        assert "step_arguments_fitted" in text, "обрезка обязана быть видна в журнале"
