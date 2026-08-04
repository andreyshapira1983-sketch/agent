"""Автономный отчёт о тестах обязан говорить, какой код проверялся.

Прогон `:auto-run` 2026-08-04 доложил «passed=6646 failed=1», а в чистой
копии тот же набор зелёный. Ответить «почему» было нечем: инструмент
отпечаток кода возвращает (PR #301), но демон складывал в отчёт только шесть
полей и отпечаток выбрасывал — в журнале `code_state: None`.

Тот самый путь, где агент судит о себе САМ, оставался без ответа на вопрос
«какой код я проверял». Здесь это закрепляется.
"""
from __future__ import annotations

from pathlib import Path

from core.autonomous_runtime import AutonomousRuntime, AutonomousTask
from core.models import ToolResult


class _FakeTool:
    """Инструмент, который отдаёт ровно то, что отдаёт настоящий run_tests."""

    name = "run_tests"

    def invoke(self, call):
        return ToolResult(
            tool_call_id="tc-fake",
            status="success",
            output={
                "exit_code": 0,
                "timed_out": False,
                "passed": 10,
                "failed": 0,
                "errors": 0,
                "failed_tests": [],
                "code_state": {
                    # Не настоящий путь: значение поля в отпечатке. Строка
                    # вида «/tmp/...» тут выглядела для bandit как небезопасный
                    # временный файл (замечание гейта #302).
                    "workspace": "workspace-under-test",
                    "commit": "abc1234",
                    "branch": "main",
                    "matches_shared": False,
                    "files_newer_than_index": 3,
                },
            },
        )


class _FakeRegistry:
    def get(self, name: str):
        assert name == "run_tests"
        return _FakeTool()


class _FakeAgent:
    def __init__(self):
        self.registry = _FakeRegistry()


def test_the_report_carries_the_code_state(tmp_path: Path):
    runtime = AutonomousRuntime.__new__(AutonomousRuntime)
    runtime.agent = _FakeAgent()

    report = runtime._task_tests(AutonomousTask(kind="tests", description="health"))

    assert "code_state" in report.details, (
        "автономный отчёт молчит о том, какой код проверялся — «упал тест» "
        "снова неотличимо от «упал в устаревшей копии»"
    )
    assert report.details["code_state"]["commit"] == "abc1234"
    assert report.details["code_state"]["matches_shared"] is False
