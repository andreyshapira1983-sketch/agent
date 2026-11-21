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


#: Профиль памяти, ОТКРЫВАЮЩИЙ сток урока. Ровно тот, что даёт полномочие
#: песочницы (`core/burn_in_sandbox.SANDBOX_DURABLE_SINKS`). До ревизии PR #333
#: тесты этого файла доказывали запись урока под обычным стоячим грантом, то
#: есть доказывали ровно то, на что ревизия и указала: урок шёл мимо политики
#: памяти. Сам факт управляемости проверяется отдельными свидетелями ниже; эти
#: тесты говорят о СОДЕРЖАНИИ урока и потому приходят с открытым стоком.
_OPEN_SINKS = frozenset({"procedure", "knowledge"})


def _drain(workspace: Path, **kwargs: Any) -> dict:
    from core.rule_approved_apply import drain_rule_approved_proposals

    kwargs.setdefault("durable_writes", _OPEN_SINKS)
    return drain_rule_approved_proposals(workspace, dry_run=False, **kwargs)


def test_a_rolled_back_autonomous_apply_leaves_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Откат без человека за клавиатурой обязан оставить машиночитаемый след.

    Красный свидетель: до правки автономный путь не звал запись уроков вовсе,
    поэтому после отката на диске не появлялось ничего.
    """

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "alpha")
    _install(monkeypatch, _Lane("rolled_back", reason="ImportError: cannot import name 'X' from 'core.y'"))

    _drain(workspace)

    lessons = _lessons(workspace)
    assert len(lessons) == 1, "откат прошёл, урока нет — опыт снова потерян"
    assert lessons[0].outcome == "rolled_back"
    assert lessons[0].origin == "rule_approved_apply", (
        "урок обязан называть, КТО его добыл: путь без человека или CLI"
    )


def test_a_verified_candidate_leaves_a_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Проверенный кандидат — тоже опыт: «так сработало» знание не меньшее, чем «так нет».

    Слово исхода именно `verified_candidate`, а не `accepted`: в этот момент
    полоса сделала локальный commit и вернула дерево на исходную ветку, то
    есть работающий агент остался на прежнем коде. Принятие — отдельное
    событие и делает его `core/burn_in_supervisor.adopt_offer`
    (ревизия PR #333, дефект 5).
    """

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "beta")
    _install(monkeypatch, _Lane("committed_local"))

    _drain(workspace)

    lessons = _lessons(workspace)
    assert len(lessons) == 1
    assert lessons[0].outcome == "verified_candidate"


def test_a_later_similar_change_consumes_the_lesson(
    workspace: Path, monkeypatch: Any
) -> None:
    """Петля замыкается здесь: второй заход по тому же адресу читает урок.

    Без этого откат ничему не учит — следующий тик подаёт ту же правку, полоса
    снова гоняет батарею, снова откатывает, и так до конца суток.
    """

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "delta")
    lane = _install(monkeypatch, _Lane("rolled_back", reason="батарея покраснела"))

    _drain(workspace)
    assert len(lane.item_ids) == 1

    # Тот же адрес, новая заявка — ровно та ситуация, которую урок описывает.
    _pending_document(inbox, workspace, "delta")
    out = _drain(workspace)

    assert out["applied"] == 0, "урок записан и не прочитан — это не память, а архив"
    assert out["refused"] == 1
    assert len(lane.item_ids) == 1, "полоса запущена повторно по уже отвергнутому адресу"


def test_an_import_rollback_still_yields_its_hard_rule(
    workspace: Path, monkeypatch: Any
) -> None:
    """Прежнее узкое правило не потеряно: урок добавился рядом, а не вместо.

    `keep_importable` — единственное правило, которое Критик умеет применять
    детерминированно; заменить его прозой значило бы разменять проверяемое
    на общее.
    """
    from core.self_build_rules import RuleStore, default_rules_path

    inbox = _inbox(workspace)
    _grant(inbox)
    _pending_document(inbox, workspace, "theta")
    _install(monkeypatch, _Lane(
        "rolled_back",
        reason="ImportError: cannot import name 'observe' from 'core.success_check'",
    ))

    _drain(workspace)

    rules = RuleStore(default_rules_path(workspace)).load()
    assert [r.symbol for r in rules] == ["observe"]
    assert rules[0].target == "core/success_check.py"


# ── Ревизия PR #333: урок обязан идти ЧЕРЕЗ ворота памяти ─────────────────────
#
# Первый заход замкнул петлю и открыл `procedure`/`knowledge` профилю
# песочницы. Но сам урок писался `LessonStore.add()` напрямую в
# `data/self_build_lessons.jsonl` — мимо `MemoryWritePolicy`, и звался в том
# числе на производственном `rule_approved_apply`. Требование «никаких
# неуправляемых прямых записей» выполнено не было: ворота открыли одну дверь, а
# урок ходил другой.


def test_an_unreadable_lesson_journal_blocks_instead_of_allowing(
    workspace: Path
) -> None:
    """Испорченная строка журнала уроков ЗАПРЕЩАЕТ повтор, а не разрешает его.

    До ревизии PR #333 любое исключение чтения отвечало «препятствий нет».
    У ворот незнание обязано означать отказ, иначе порча одной строки снимает
    защиту, ради которой ворота и стоят.
    """
    from core.self_build_rules import blocking_lesson, default_lessons_path

    path = default_lessons_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{это не json\n", encoding="utf-8")

    verdict = blocking_lesson(workspace, ["core/foo.py"])

    assert verdict is not None, "нечитаемый журнал уроков снял ворота"
    assert "не прочитан" in verdict.failure or "не целиком" in verdict.failure


# ── Ревизия Copilot по PR #333: происхождение урока ─────────────────────────


def test_a_timed_out_battery_is_not_a_failed_change() -> None:
    """Суточный прогон 2026-09-19: полная батарея (~10 000 тестов) упёрлась в
    лимит 900 с; отчёт сказал «full pytest failed», урок запретил повтор
    правки core/step_sanitizer.py навсегда. Не успевшая проверка — не провал."""
    from core.self_apply_lane import _failure_detail
    from core.self_build_rules import lesson_from_apply_result

    detail = _failure_detail({"timed_out": True, "failed_tests": [], "stdout_tail": "", "stderr_tail": ""})
    assert "timed out" in detail
    timed_out = {"status": "rolled_back", "reason": f"full pytest failed: {detail}",
                 "files_changed": ["core/step_sanitizer.py"], "tests_run": ["targeted", "full"]}
    assert lesson_from_apply_result(timed_out, origin="burn_in_sandbox") is None
    red = dict(timed_out, reason="full pytest failed: tests=tests/test_x.py::test_y")
    assert lesson_from_apply_result(red, origin="burn_in_sandbox") is not None, "a real red still teaches"
