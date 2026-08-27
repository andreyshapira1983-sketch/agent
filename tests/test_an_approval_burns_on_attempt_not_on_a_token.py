"""Одноразовое «да» сгорает попыткой — не пустым токеном жизненного цикла.

Замер и нормы: MIR-117 (властная половина, слово оператора 2026-08-27):
сгорает ПОПЫТКОЙ (правило полосы, ратифицировано ранее); отказ до старта —
0 трат, 0 продукта — попыткой НЕ считается, грант остаётся.

До починки жгло `status == "completed"` — токен «очередь дочерпана», который
носил и прогон, чью единственную задачу отвергли до старта (MIR-116, живой
случай). Обратная сторона прежнего ключа: остановленный прогон, успевший
СДЕЛАТЬ работу, грант сохранял — теперь попытка есть попытка в обе стороны.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.autonomous_runtime_types import AutonomousRunReport


def _report(status: str, task_statuses: list[str], llm_used: int = 0,
            web_used: int = 0) -> AutonomousRunReport:
    return AutonomousRunReport(
        status=status, dry_run=False, goal="g",
        tasks=[SimpleNamespace(status=s) for s in task_statuses],
        budget={"used": {"llm_calls": llm_used, "web_fetches": web_used}},
        circuit={}, approvals={},
    )


def test_a_preflight_refusal_is_not_an_attempt() -> None:
    """Красный свидетель: всё skipped, ноль трат — «да» обязано выжить."""
    hollow = _report("completed", ["skipped", "skipped"])

    assert hollow.attempted() is False


def test_engagement_or_spend_is_an_attempt() -> None:
    """Обе двери попытки: задача началась ИЛИ деньги потрачены."""
    engaged = _report("completed", ["done"])
    spent_and_failed = _report("completed", ["failed"], llm_used=3)
    fetched = _report("completed", ["failed"], web_used=1)

    assert engaged.attempted() is True
    assert spent_and_failed.attempted() is True
    assert fetched.attempted() is True


def test_a_failure_with_zero_spend_is_not_an_attempt() -> None:
    """failed без единой траты — отказ до старта, не попытка."""
    refused = _report("completed", ["failed"])

    assert refused.attempted() is False


def test_a_stopped_run_that_attempted_burns_too() -> None:
    """Прежний ключ прятал и это: остановленный ПОСЛЕ работы прогон — попытка."""
    stopped = _report("stopped", ["done", "failed"], llm_used=5)

    assert stopped.attempted() is True


def test_the_burn_is_keyed_to_the_attempt() -> None:
    """Проводка: сжигание читает attempted(), а не токен completed."""
    import inspect

    from cli import commands_approval as mod

    src = inspect.getsource(mod._handle_approval_run)
    assert "report.attempted()" in src
    assert 'report.status == "completed"' not in src


def test_a_broken_budget_snapshot_does_not_crash_the_verdict() -> None:
    """Битый снимок бюджета — не падение; задачи решают сами."""
    report = AutonomousRunReport(
        status="completed", dry_run=False, goal="g",
        tasks=[SimpleNamespace(status="done")],
        budget={}, circuit={}, approvals={},
    )

    assert report.attempted() is True
