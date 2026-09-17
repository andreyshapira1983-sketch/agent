"""В песочнице проверенный урок вправе стать процедурой; в производстве — нет.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находка 10, вторая половина:
безнадзорный профиль памяти (`agent_tick.UNATTENDED_MEMORY_PROFILE`) разрешает
ровно два стока — `episode` и `hygiene`. Всё, что похоже на ОБОБЩЕНИЕ опыта —
`procedure` и `knowledge`, — закрыто умолчанием-запретом. Это верная
производственная политика: обобщение без присмотра меняет поведение будущих
прогонов, и решение об этом принадлежит оператору.

Но у неё есть следствие, которое видно только в эксперименте: агент, который
не вправе записать процедуру, в десятичасовом прогоне не может ничему
научиться в смысле «позже применить». Он копит эпизоды и повторяет ошибки.

Поэтому здесь проверяется ровно граница, а не её снятие:

* производственный профиль остаётся БУКВА В БУКВУ прежним;
* расширение действует только при явном полномочии песочницы — том же самом,
  что и в `core/burn_in_sandbox.py`: две независимые подписи, срок, потолок;
* расширение узкое: добавляются `procedure` и `knowledge` и НИ ОДИН из
  разрушающих или властных стоков;
* правило «сухой прогон не пишет» и «аудит не пишет» старше полномочия и
  остаётся выше него;
* неизвестный сток по-прежнему отвергается — песочница не отменяет
  умолчание-запрет, она двигает ровно две строки списка.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_tick import UNATTENDED_MEMORY_PROFILE, unattended_memory_profile
from core.burn_in_sandbox import SANDBOX_ENV_FLAG, SANDBOX_MARKER


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    return tmp_path


def _marker(workspace: Path) -> None:
    (workspace / SANDBOX_MARKER).write_text(
        json.dumps({
            "sandbox": True,
            "workspace": str(workspace),
            "max_applies_per_day": 20,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "reason": "10-часовой burn-in, слово оператора",
        }),
        encoding="utf-8",
    )


def test_the_production_profile_is_unchanged(workspace: Path) -> None:
    """Без полномочия — ровно те же два стока, что и до 2026-09-17."""
    profile = unattended_memory_profile(workspace, env={})

    assert profile["durable_writes"] == frozenset({"episode", "hygiene"})
    assert profile["with_memory"] is False
    assert profile["episodic_replay"] is False
    assert profile == dict(UNATTENDED_MEMORY_PROFILE)


def test_the_marker_alone_does_not_open_memory(workspace: Path) -> None:
    """Забытый файл метки не вправе открыть постоянную запись."""
    _marker(workspace)

    profile = unattended_memory_profile(workspace, env={})

    assert profile["durable_writes"] == frozenset({"episode", "hygiene"})


def test_the_sandbox_profile_admits_a_verified_lesson(workspace: Path) -> None:
    """Смысл расширения: урок, добытый в песочнице, вправе пережить прогон."""
    _marker(workspace)

    profile = unattended_memory_profile(workspace, env={SANDBOX_ENV_FLAG: "on"})

    assert "procedure" in profile["durable_writes"]
    assert "knowledge" in profile["durable_writes"]
    assert {"episode", "hygiene"} <= profile["durable_writes"]


def test_the_sandbox_profile_opens_nothing_else(workspace: Path) -> None:
    """Расширение узкое и перечислимое: ни профиля, ни реестра, ни допущений.

    Это и отличает названное полномочие от снятия политики.
    """
    _marker(workspace)

    profile = unattended_memory_profile(workspace, env={SANDBOX_ENV_FLAG: "on"})

    assert profile["durable_writes"] == frozenset(
        {"episode", "hygiene", "procedure", "knowledge"}
    )
    assert profile["with_memory"] is False, "межпрогонная память — отдельное решение"


def test_the_sandbox_sinks_are_known_to_the_gate() -> None:
    """Открывать можно только то, что сторож умеет назвать.

    Сток вне `KNOWN_DURABLE_SINKS` был бы отвергнут в любом случае, и
    «разрешение» на него оказалось бы декорацией.
    """
    from core.burn_in_sandbox import SANDBOX_DURABLE_SINKS
    from core.loop_memory_write import KNOWN_DURABLE_SINKS

    assert SANDBOX_DURABLE_SINKS <= KNOWN_DURABLE_SINKS


def test_a_dry_run_still_writes_nothing_in_the_sandbox() -> None:
    """Сухость старше полномочия: порядок правил сторожа это гарантирует."""

    class _Log:
        def log(self, *_a, **_kw) -> None:
            return None

    from core.loop_memory_write import AgentLoopMemoryWrite

    writer = AgentLoopMemoryWrite()
    writer.log = _Log()
    writer.durable_writes = frozenset({"episode", "hygiene", "procedure", "knowledge"})
    writer.suppress_durable_learning_writes = True

    assert writer._durable_learning_suppressed("procedure") is True

    writer.suppress_durable_learning_writes = False
    assert writer._durable_learning_suppressed("procedure") is False
    assert writer._durable_learning_suppressed("profile") is True
    assert writer._durable_learning_suppressed("не-сток") is True
