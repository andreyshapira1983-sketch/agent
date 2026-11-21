"""Одобрили «сделай X» — выполниться должно X, а не проверка здоровья.

Background: docs/CODE_NOTES.md, "The approved goal that never ran".
"""
from __future__ import annotations


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
