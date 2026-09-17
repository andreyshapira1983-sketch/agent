"""Проверенная автономная починка обязана оставлять урок, и урок обязан читаться.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находка 10: петля обучения не
замкнута. Замер по живому коду до этой правки:

* `record_rules_from_result` (core/self_build_rules.py) звалась РОВНО из одного
  места — `cli/commands_self_apply.py:83`, то есть только когда человек сам
  набрал `:self-apply-run`. Автономный путь применения
  (`drain_rule_approved_proposals`) не звал её никогда;
* извлекалось из исхода ровно одно узкое правило — `cannot import name 'X'
  from 'M'` — и только при `status == "rolled_back"`. Принятая починка не
  оставляла НИЧЕГО;
* `core/self_repair.py:_write_repair_lesson` на безнадзорном пути недостижим:
  без `approval_provider` починка обрывается раньше.

Итог: агент мог откатить одну и ту же негодную правку сколько угодно раз, и
десятый откат ничем не отличался от первого. Это и есть «ошибки не
накапливаются в знание».

Договор, который здесь проверяется:

    сбой -> диагноз -> попытка починки -> проверка -> устойчивый урок
    -> следующая похожая задача этот урок ЧИТАЕТ

Урок обязан нести происхождение: исходный сбой, что изменено, чем проверено,
принято или откачено, и на что распространяется. Урок без происхождения —
мнение, а не знание.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / DEFAULT_APPROVAL_INBOX_PATH)


def _grant(inbox: ApprovalInbox, *, runs_per_day: int = 5):
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary=f"standing effects grant: {runs_per_day} runs/day",
        risk="irreversible",
        payload={"max_runs_per_day": runs_per_day},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    return inbox.approve(item.id)


def _pending_document(inbox: ApprovalInbox, workspace: Path, name: str):
    payload = build_self_apply_payload(
        files=[{"path": f"knowledge/doctrine/future/{name}.md", "content": "# заметка\n"}],
        reason=f"черновик {name}",
        origin="test",
        workspace=workspace,
    )
    return inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply: {name}.md",
        risk="reversible",
        payload=payload,
    )


class _Lane:
    """Подделка полосы с заданным исходом: на диск ничего не пишет."""

    def __init__(self, status: str, *, reason: str = "", tests: tuple[str, ...] = ("tests",)) -> None:
        self.status = status
        self.reason = reason
        self.tests = tests
        self.item_ids: list[str] = []

    def __call__(self, **kwargs: Any) -> dict:
        item_id = kwargs["item_id"]
        self.item_ids.append(item_id)
        proposal = kwargs["inbox"].get(item_id)
        files = [f["path"] for f in (proposal.payload or {}).get("files", [])]
        return {
            "proposal_id": item_id,
            "origin": "test",
            "status": self.status,
            "reason": self.reason,
            "files_changed": files,
            "tests_run": list(self.tests),
            "rollback_status": "restored" if self.status == "rolled_back" else "none",
            "commit_hash": "abc123" if self.status == "committed_local" else None,
        }


def _install(monkeypatch: Any, lane: _Lane) -> _Lane:
    import core.self_apply_bridge as bridge

    monkeypatch.setattr(bridge, "run_approved_self_apply", lane)
    return lane


def _lessons(workspace: Path) -> list:
    from core.self_build_rules import LessonStore, default_lessons_path

    return LessonStore(default_lessons_path(workspace)).load()


def test_a_rolled_back_autonomous_apply_leaves_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Откат без человека за клавиатурой обязан оставить машиночитаемый след.

    Красный свидетель: до правки автономный путь не звал запись уроков вовсе,
    поэтому после отката на диске не появлялось ничего.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "alpha")
    _install(monkeypatch, _Lane("rolled_back", reason="ImportError: cannot import name 'X' from 'core.y'"))

    drain_rule_approved_proposals(workspace, dry_run=False)

    lessons = _lessons(workspace)
    assert len(lessons) == 1, "откат прошёл, урока нет — опыт снова потерян"
    assert lessons[0].outcome == "rolled_back"
    assert lessons[0].origin == "rule_approved_apply", (
        "урок обязан называть, КТО его добыл: путь без человека или CLI"
    )


def test_an_accepted_autonomous_apply_leaves_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Принятая починка — тоже опыт: «так сработало» знание не меньшее, чем «так нет»."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "beta")
    _install(monkeypatch, _Lane("committed_local"))

    drain_rule_approved_proposals(workspace, dry_run=False)

    lessons = _lessons(workspace)
    assert len(lessons) == 1
    assert lessons[0].outcome == "accepted"


def test_a_lesson_carries_its_provenance(workspace: Path, monkeypatch: Any) -> None:
    """Пять полей происхождения, и ни одно не пустое.

    Урок без происхождения нельзя ни проверить, ни отозвать: непонятно, из
    какого сбоя он вырос и чем подтверждён.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "gamma")
    _install(monkeypatch, _Lane("rolled_back", reason="тесты упали: 3 failed", tests=("tests/test_x.py",)))

    drain_rule_approved_proposals(workspace, dry_run=False)

    lesson = _lessons(workspace)[0]
    assert lesson.failure, "исходный сбой не записан"
    assert lesson.change, "что именно менялось — не записано"
    assert lesson.verification, "чем проверено — не записано"
    assert lesson.outcome in {"accepted", "rolled_back"}
    assert lesson.scope, "область применимости пуста — урок не с чем сопоставить"
    assert "knowledge/doctrine/future/gamma.md" in lesson.scope
    assert "tests/test_x.py" in lesson.verification


def test_a_later_similar_change_consumes_the_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Петля замыкается здесь: второй заход по тому же адресу читает урок.

    Без этого откат ничему не учит — следующий тик подаёт ту же правку, полоса
    снова гоняет батарею, снова откатывает, и так до конца суток.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "delta")
    lane = _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))

    drain_rule_approved_proposals(workspace, dry_run=False)
    assert len(lane.item_ids) == 1

    # Тот же адрес, новая заявка — ровно та ситуация, которую урок описывает.
    _pending_document(inbox, workspace, "delta")
    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out["applied"] == 0, "урок записан и не прочитан — это не память, а архив"
    assert out["refused"] == 1
    assert len(lane.item_ids) == 1, "полоса запущена повторно по уже отвергнутому адресу"


def test_a_different_target_is_not_shadowed_by_the_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Урок узок по построению: он про свой адрес, а не про всякую работу.

    Иначе один откат остановил бы петлю целиком, и «обучение» стало бы
    выключателем.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "epsilon")
    lane = _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))
    drain_rule_approved_proposals(workspace, dry_run=False)

    lane.status = "committed_local"
    _pending_document(inbox, workspace, "zeta")
    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out["applied"] == 1, "чужой адрес заблокирован уроком про другой файл"


def test_the_lesson_store_is_machine_readable(workspace: Path, monkeypatch: Any) -> None:
    """Урок хранится строками JSON, а не прозой: его обязан читать код."""
    import json

    from core.rule_approved_apply import drain_rule_approved_proposals
    from core.self_build_rules import default_lessons_path

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "eta")
    _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))
    drain_rule_approved_proposals(workspace, dry_run=False)

    lines = default_lessons_path(workspace).read_text(encoding="utf-8").splitlines()
    assert lines, "файл уроков пуст"
    row = json.loads(lines[0])
    for field in ("failure", "change", "verification", "outcome", "scope", "origin"):
        assert field in row, f"в машинной записи нет поля {field}"


def test_an_import_rollback_still_yields_its_hard_rule(
    workspace: Path, monkeypatch: Any
) -> None:
    """Прежнее узкое правило не потеряно: урок добавился рядом, а не вместо.

    `keep_importable` — единственное правило, которое Критик умеет применять
    детерминированно; заменить его прозой значило бы разменять проверяемое
    на общее.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals
    from core.self_build_rules import RuleStore, default_rules_path

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "theta")
    _install(monkeypatch, _Lane(
        "rolled_back",
        reason="ImportError: cannot import name 'observe' from 'core.success_check'",
    ))

    drain_rule_approved_proposals(workspace, dry_run=False)

    rules = RuleStore(default_rules_path(workspace)).load()
    assert [r.symbol for r in rules] == ["observe"]
    assert rules[0].target == "core/success_check.py"
