"""Подтверждённый диагноз с целью в ядре доходит до ветки А — TODO по-прежнему нет.

Background: docs/CODE_NOTES.md, "The ladder opens to the organs it was built for".
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.self_task_producer import (
    _is_diagnosis_target_allowed,
    _task_critic_review,
    produce_coding_task,
)

#: Живой узор трёх охот 2026-08-15: диагноз reasoning_action_mismatch трижды
#: подтверждался целиком и трижды умирал на воротах «core/loop.py — не
#: низкорисковый файл», хотя ветка А кладёт только НОВЫЙ тест в tests/.
_DIAGNOSIS = (
    "Зафиксирован reasoning_action_mismatch: план требовал web_search, "
    "выполнен list_dir; обвязка в core/loop.py."
)
_TEST_LINES = [
    "from core.loop import AgentLoop",
    "def test_reasoning_action_mismatch_is_recorded():",
    "    assert flags(executed=['list_dir'], planned=[]) == ['list_dir']",
]


class _JsonLLM:
    def complete(self, *, system: str, user: str, **_kw) -> str:
        return json.dumps({
            "task_title": "Reproduce the planner/executor mismatch",
            "task_summary": "Add a failing test that reproduces the defect.",
            "impl_path": "core/loop.py",
            "test_path": "tests/test_core_loop_mismatch_repro.py",
            "test_lines": _TEST_LINES,
            "confidence": 0.9,
        })


class _Inbox:
    def __init__(self) -> None:
        self.items: list = []

    def list(self):
        return []

    def add(self, **kw):
        self.items.append(kw)
        return SimpleNamespace(id="ain_core_stageA")


def _candidate(target: str = "core/loop.py"):
    return lambda: SimpleNamespace(
        target_path=target,
        problem_quote=_DIAGNOSIS,
        evidence_ref="verified_diagnosis:trace_x",
    )


def _workspace(tmp_path):
    """A tmp polygon: 2026-08-17 the live run of these tests left receipt
    rows in the REAL data/ journals (workspace=\".\") — the producer now
    writes lesson-injection receipts, so tests must not aim it at home."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "loop.py").write_text(
        "def flags(executed, planned):\n    return executed\n",
        encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_a_core_grounded_diagnosis_is_not_turned_away(tmp_path):
    """Ветка А кладёт только новый тест в tests/ — на этом шаге цель никто не
    редактирует, тест благословляет человек, у Stage B свои ворота. Держать
    диагнозы ядра на воротах значит закрыть лестницу для самых ценных дефектов.
    """
    inbox = _Inbox()

    report = produce_coding_task(
        workspace=_workspace(tmp_path), inbox=inbox, llm=_JsonLLM(),
        task_selector=_candidate(), source_kind="verified_diagnosis",
    )

    assert report.status == "proposed", report.reason
    assert len(inbox.items) == 1


def test_a_todo_candidate_is_still_turned_away_from_core(tmp_path):
    """Улов не отдан: TODO-источнику органы ядра закрыты, как и были."""
    report = produce_coding_task(
        workspace=_workspace(tmp_path), inbox=_Inbox(), llm=_JsonLLM(),
        task_selector=_candidate(),
    )

    assert report.status == "no_task"
    assert "not a low-risk editable" in report.reason


def test_the_critic_accepts_a_core_target_for_a_diagnosis():
    critic = _task_critic_review(
        {
            "impl_path": "core/loop.py",
            "test_path": "tests/test_core_loop_mismatch_repro.py",
            "test_content": "\n".join(_TEST_LINES) + "\n",
            "task_title": "t",
            "confidence": 0.9,
        },
        grounded_target="core/loop.py",
        reader=lambda _p: None,
        confidence_threshold=0.6,
        quote=_DIAGNOSIS,
        source_kind="verified_diagnosis",
    )

    assert critic.decision != "veto", critic.data.get("veto_reasons")


def test_config_and_secrets_stay_closed_even_for_a_diagnosis():
    """Открыты органы ядра, а не всё подряд: гигиена пути и классификатор
    рисков полосы действуют без изъятий.
    """
    assert _is_diagnosis_target_allowed("core/loop.py") is True
    assert _is_diagnosis_target_allowed("config/budget_limits.json") is False
    assert _is_diagnosis_target_allowed("../outside.py") is False
    assert _is_diagnosis_target_allowed("") is False
