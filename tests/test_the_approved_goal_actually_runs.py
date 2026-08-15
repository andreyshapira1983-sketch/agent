"""Одобрили «сделай X» — выполниться должно X, а не проверка здоровья.

Background: docs/CODE_NOTES.md, "The approved goal that never ran".
"""
from __future__ import annotations

from core.autonomous_runtime import AutonomousRuntimeConfig, AutonomousRuntime

_GOAL = "найди и почини свои собственные дефекты"


def _queue(config: AutonomousRuntimeConfig) -> list[str]:
    return [t.kind for t in AutonomousRuntime._build_queue(None, config)]


def test_a_goal_run_actually_carries_a_goal_task():
    assert "goal" in _queue(
        AutonomousRuntimeConfig(goal=_GOAL, include_goal=True, dry_run=False)
    )


def test_a_health_pass_stays_a_health_pass():
    """Улов не отдан: без цели меню прежнее — статус, обучение, тесты."""
    kinds = _queue(AutonomousRuntimeConfig(goal=_GOAL, dry_run=False))

    assert "goal" not in kinds
    assert kinds[:2] == ["status", "learn"]


def test_the_gate_payload_carries_the_goal_flag():
    """Измерено 2026-08-15 на первом же прогоне с эффектами.

    Затвор писал в заявку `goal`, `limit`, `include_tests`, `learning_limit` —
    и терял `include_goal`. Человек одобрял «сделай X»; `:approval-run`
    собирал конфиг заново, флаг вставал в умолчание False, и выполнялся
    health-pass. Значение было вычислено и не спрошено там, где решают.
    """
    import inspect

    from core import autonomous_runtime

    source = inspect.getsource(autonomous_runtime.AutonomousRuntime.run)
    assert '"include_goal": config.include_goal' in source


def test_the_approval_runner_reads_the_goal_flag_back():
    """Вторая половина той же дороги: положить в заявку мало, надо прочесть."""
    import inspect

    from cli import commands_approval

    source = inspect.getsource(commands_approval._handle_approval_run)
    assert 'payload.get("include_goal")' in source


def test_an_older_approval_without_the_field_keeps_its_old_behaviour():
    """Заявки, записанные до правки, поля не имеют. Менять им поведение
    задним числом нельзя: человек одобрял то, что тогда показывали.
    """
    from cli.commands_approval import _payload_bool

    assert _payload_bool(None, default=False) is False
