"""Цель назвала предмет — значит предмет задан, а не разрешён.

Живой прогон 2026-09-17 (`agent_tick.py --campaign --charter --allow-effects`)
двадцать циклов подряд печатал одно и то же:

    [CHARTER] goal: ... split the oversized module core/self_build_producer.py ...
    [CAMP] campaign_engineering_proposed  status=no_patch, target=core/smart_memory.py

Хартия назвала один файл, руки взялись за другой. Дорога до рук цела:
`_named_target` и `resolve_goal_subject` разрешают имя верно, а
`core/campaign_io.py` передаёт его вниз как `candidate_targets`. Ломается
последний стык: `_manager_from_grounded` кладёт названное в `effective_allowed`,
то есть в список РАЗРЕШЁННОГО, а выбирает всё равно верхушку бэклога.

Разница между «разрешено» и «задано» — это и есть разница между агентом,
который слушает, и агентом, который занят своим. Здесь свидетели на то, что
названный предмет ограничивает выбор, и на то, что при неназванном предмете
бэклог по-прежнему выбирает сам.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.self_build_producer import produce_self_apply_proposal

_ASKED = "core/self_build_producer.py"
_FOUND = "core/redaction.py"


class _LLM:
    """Отвечает заготовками по порядку; помнит, о чём его спрашивали."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, max_tokens: int = 2000,
                 temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "user": user})
        return self.responses.pop(0) if self.responses else "{}"


class _VCS:
    def __init__(self) -> None:
        self.mutations: list[str] = []

    def is_clean(self) -> bool:
        return True

    def create_temp_branch(self, name: str) -> None:  # pragma: no cover - сторож
        self.mutations.append("create_temp_branch")

    def commit(self, message: str) -> str:  # pragma: no cover - сторож
        self.mutations.append("commit")
        return "deadbeef"


class _KillSwitch:
    active = False
    reason = ""


class _Candidate:
    """Наименьшая замена BacklogCandidate — ровно то, что читает управляющий."""

    def __init__(self, target_path: str) -> None:
        self.target_path = target_path
        self.signal_source = "tech_debt"
        self.problem_quote = "grounded: tidy a helper"
        self.evidence_ref = "TECH_DEBT.md:42"


def _builder_ok() -> str:
    return json.dumps({
        "content": "VALUE = 1\n",
        "test_paths": ["tests/test_redaction.py"],
        "test_pattern": "redaction",
        "reason": "small tidy",
        "confidence": 0.9,
    })


def _headroom() -> dict:
    return {"windows": [{"name": "hour", "counters": {
        "llm_calls": {"used": 1, "limit": 100},
        "model_tokens": {"used": 10, "limit": 1000},
    }}]}


def _produce(workspace: Path, *, found: str, asked: str | None, llm: _LLM,
             inbox: ApprovalInbox):
    return produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=_VCS(),
        budget_snapshot=_headroom(),
        kill_switch=_KillSwitch(),
        file_reader=lambda p: "OLD = 0\n" if p in (_FOUND, _ASKED) else None,
        candidate_targets=(asked,) if asked else None,
        grounded_selector=lambda: _Candidate(found),
    )


def test_a_goal_that_names_a_file_is_not_traded_for_another_file(
    workspace: Path,
) -> None:
    """Названный предмет — задание. Верхушка бэклога его не отменяет."""
    inbox = ApprovalInbox(path=None)
    llm = _LLM([_builder_ok()])

    report = _produce(workspace, found=_FOUND, asked=_ASKED, llm=llm, inbox=inbox)

    assert report.target_path != _FOUND, (
        f"цель назвала {_ASKED}, а руки взялись за {report.target_path}"
    )
    assert report.approval_id is None
    assert inbox.list() == []
    # Отказ обязан назвать предмет, о котором просили: молчаливый отказ и
    # молчаливая подмена в журнале неразличимы, а прогон 2026-09-17 и был
    # неразличим двадцать циклов подряд.
    assert _ASKED in (report.reason or ""), report.reason


def test_the_named_file_is_still_worked_on_when_the_backlog_agrees(
    workspace: Path,
) -> None:
    """Положительный контроль: совпало — работа идёт, ограничение не мешает."""
    inbox = ApprovalInbox(path=None)
    llm = _LLM([_builder_ok()])

    report = _produce(workspace, found=_FOUND, asked=_FOUND, llm=llm, inbox=inbox)

    assert report.status == "proposed", report.reason
    assert report.target_path == _FOUND


def test_when_the_goal_names_nothing_the_backlog_still_chooses(
    workspace: Path,
) -> None:
    """Сторож против перечинки: без названного предмета прежний ход цел."""
    inbox = ApprovalInbox(path=None)
    llm = _LLM([_builder_ok()])

    report = _produce(workspace, found=_FOUND, asked=None, llm=llm, inbox=inbox)

    assert report.status == "proposed", report.reason
    assert report.target_path == _FOUND


def test_the_named_file_is_found_below_the_top_of_the_backlog(
    workspace: Path, monkeypatch,
) -> None:
    """Отказ по названному — полдела; кандидат на него надо ещё и найти.

    Свидетели выше подставляют `grounded_selector` и потому обходят отбор
    внутри селектора по умолчанию — ровно тот зазор, на котором ревизия ловила
    меня прошлые круги: обработчик живёт на одном пути, а доказывается на
    другом. Здесь селектор по умолчанию вызывается сам, с бэклогом, где
    названный файл лежит ВТОРЫМ. Без отбора выбор берёт верхушку.
    """
    import core.backlog_selector as sel
    from core.self_build_producer import _default_grounded_selector

    backlog = [_Candidate(_FOUND), _Candidate(_ASKED)]
    monkeypatch.setattr(sel, "load_backlog", lambda *a, **k: list(backlog))

    asked = _default_grounded_selector(
        workspace, only_targets=frozenset({_ASKED}),
    )()
    assert asked is not None and asked.target_path == _ASKED

    # Тот же бэклог без названного предмета отдаёт верхушку — значит зелёный
    # выше добыт отбором, а не тем, что список и так состоял из одного файла.
    assert _default_grounded_selector(workspace)().target_path == _FOUND


def test_a_named_file_absent_from_the_backlog_is_refused_not_swapped(
    workspace: Path, monkeypatch,
) -> None:
    """Пустой отбор — «не нашёл по названному», а не «возьму что было»."""
    import core.backlog_selector as sel
    from core.self_build_producer import _default_grounded_selector

    monkeypatch.setattr(sel, "load_backlog", lambda *a, **k: [_Candidate(_FOUND)])

    assert _default_grounded_selector(
        workspace, only_targets=frozenset({_ASKED}),
    )() is None
