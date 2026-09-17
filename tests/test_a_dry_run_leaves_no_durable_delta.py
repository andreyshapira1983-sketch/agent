"""Сухой прогон не оставляет следа — включая уборку.

WHY THIS EXISTS. Аудит автономности 2026-09-17 нашёл две записи, которые тик
делал НЕЗАВИСИМО от ``dry_run``:

* ``_sweep_episodic_duplicates(workspace)`` (agent_tick.py) звался без флага и
  удалял строки эпизодической памяти даже в сухом прогоне;
* проход гигиены получал ``dry_run=_hygiene_mode == "shadow"``, то есть режим
  решала ОДНА переменная окружения: ``AGENT_AUTO_HYGIENE=on`` заставляла
  удалять, хотя сам тик объявил себя сухим.

Обещание ``dry_run`` в этом проекте уже названо словами в
``core/loop_memory_write.py``: «сухой прогон должен не оставить следа». Здесь
оно проверяется на том пути, где человека нет.

Граница теста — РАЗРУШИТЕЛЬНЫЕ и обучающие записи. Журнал тика, heartbeat и
суточный снимок сухой прогон пишет намеренно: это наблюдение за собой, а не
изменение состояния, и без них сухой прогон был бы невидим.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


class _FakeAgent:
    """Агент ровно той формы, какую трогает хвост тика."""

    def __init__(self) -> None:
        self.maintenance_calls: list[bool] = []

    def run_maintenance_pass(self, *, dry_run: bool = True) -> dict:
        self.maintenance_calls.append(dry_run)
        return {"skipped": None, "dry_run": dry_run}


def _seed_identical_episodes(workspace: Path, count: int = 3) -> EpisodicMemoryStore:
    store = EpisodicMemoryStore(path=workspace / "data" / "episodic_memory.jsonl")
    for _ in range(count):
        store.save(
            EpisodeRecord(
                goal="g",
                question="self-build-produce",
                outcome="partial",
                summary="self-build approval_wait: pending item exists",
                tags=("self-build",),
            )
        )
    return store


def _install_fake_agent(monkeypatch: Any) -> _FakeAgent:
    import app.bootstrap as bootstrap

    agent = _FakeAgent()
    monkeypatch.setattr(bootstrap, "build_agent", lambda *a, **k: agent)
    return agent


def test_dry_run_leaves_no_durable_delta(workspace: Path, monkeypatch: Any) -> None:
    """Красный свидетель: три одинаковых эпизода переживают сухой тик.

    До починки подметание дубликатов стояло в `run_tick` без флага и уносило
    две строки из трёх — необратимо, в прогоне, который обещал ничего не менять.
    """
    from agent_tick import run_tick

    _install_fake_agent(monkeypatch)
    store = _seed_identical_episodes(workspace)
    before = [row.id for row in store.load()]

    assert run_tick(workspace, dry_run=True) == 0

    after = [row.id for row in store.load()]
    assert after == before, (
        "сухой прогон удалил строки эпизодической памяти: "
        f"{len(before)} -> {len(after)}"
    )


def test_a_live_tick_still_collapses_duplicates(workspace: Path, monkeypatch: Any) -> None:
    """Обратная сторона: починка не отменяет саму уборку.

    MIR-131 завёл подметание именно потому, что автономный путь плодит повторы
    и не умел за собой убирать. Живой тик обязан убирать по-прежнему.
    """
    from agent_tick import run_tick

    _install_fake_agent(monkeypatch)
    store = _seed_identical_episodes(workspace)

    assert run_tick(workspace, dry_run=False) == 0

    assert len(store.load()) == 1


def test_hygiene_mode_cannot_outrank_dry_run(workspace: Path, monkeypatch: Any) -> None:
    """Переменная окружения не вправе отменить объявленный сухой режим.

    ``AGENT_AUTO_HYGIENE=on`` — согласие оператора на удаление, а не отмена
    ``dry_run``. Два разрешения должны СОВПАСТЬ, чтобы что-то было удалено.
    """
    from agent_tick import run_tick

    agent = _install_fake_agent(monkeypatch)
    monkeypatch.setenv("AGENT_AUTO_HYGIENE", "on")

    assert run_tick(workspace, dry_run=True) == 0

    assert agent.maintenance_calls, "проход гигиены вообще не дошёл до вызова"
    assert all(agent.maintenance_calls), (
        "гигиена получила dry_run=False внутри сухого тика: "
        f"{agent.maintenance_calls}"
    )


def test_hygiene_still_removes_on_a_live_tick(workspace: Path, monkeypatch: Any) -> None:
    """И снова обратная сторона: живой тик с `on` удаляет, как и раньше."""
    from agent_tick import run_tick

    agent = _install_fake_agent(monkeypatch)
    monkeypatch.setenv("AGENT_AUTO_HYGIENE", "on")

    assert run_tick(workspace, dry_run=False) == 0

    assert agent.maintenance_calls == [False]


def test_shadow_mode_stays_shadow_on_a_live_tick(workspace: Path, monkeypatch: Any) -> None:
    """Значение по умолчанию не меняется: `shadow` только считает."""
    from agent_tick import run_tick

    agent = _install_fake_agent(monkeypatch)
    monkeypatch.delenv("AGENT_AUTO_HYGIENE", raising=False)

    assert run_tick(workspace, dry_run=False) == 0

    assert agent.maintenance_calls == [True]
