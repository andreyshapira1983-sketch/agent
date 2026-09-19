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

import json
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


# ── Вторая течь того же обещания, найденная ревизией PR #333 ──────────────────
#
# Первый заход чинил ДВА НАЗВАННЫХ места и поэтому проверял два названных
# места. Ревизия указала на третье — `_free_stranded_rows`, стоящее строкой
# ВЫШЕ починенного подметания, — и это правда: возврат брошенной строки и
# пробуждение припаркованной переписывают очередь задач.
#
# Первый черновик этого свидетеля требовал большего: «ни одного изменённого
# БАЙТА долговременного состояния». Замер показал, что такое требование
# противоречит устройству демона, а не ловит дефект. Демон ставится СУХИМ по
# умолчанию (scripts/install_daemon.ps1: «Default: dry-run only (safe)»), и
# сухой прогон штатно разбирает очередь — `_dry_run_visibility` прямо считает
# содержательным тот сухой проход, где `tasks_processed > 0`. Живой замер
# сухого тика: девять изменённых путей, из них планировщик и очередь меняются
# ЗАМЫСЛОМ. Широкий инвариант превратил бы демон по умолчанию в наблюдателя,
# который не делает ничего.
#
# Поэтому договор сужен решением владельца (2026-09-17): сухой прогон не
# применяет эффектов и не делает НЕОБРАТИМОГО. Разбор очереди, планировщик и
# перепостановка строки в очередь продолжают работать.
#
# Необратимое место здесь ровно одно, и оно найдено чтением: попытку тратит
# `mark_running` (task_queue.py:519), а `_failure_transition` (:252) её НЕ
# тратит — он либо возвращает строку в очередь с отступом, либо, если попытки
# исчерпаны, выносит терминальный `failed`. Первое обратимо, второе нет.
# Значит сухой тик вправе перепоставить брошенную строку и не вправе её
# хоронить.


def _stranded_queue(workspace: Path, *, max_attempts: int = 1) -> tuple[Path, str]:
    """Очередь с одной брошенной строкой; возвращает путь и её id.

    Ровно то состояние покоя, ради которого `_free_stranded_rows` существует
    (MIR-039/MIR-040). `max_attempts=1` даёт строку с ИСЧЕРПАННЫМИ попытками
    (их тратит сам захват), то есть кандидата на терминальный вердикт;
    `max_attempts=3` — строку, которой вердикт ещё не положен.
    """
    from datetime import datetime, timedelta, timezone

    from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH, TaskQueueStore

    store = TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    orphan = store.add(goal="брошенная работа", max_attempts=max_attempts)
    store.mark_running(orphan.id)
    rows = [
        row.to_dict() if hasattr(row, "to_dict") else row
        for row in store.load()
    ]
    for row in rows:
        if row.get("id") == orphan.id:
            row["heartbeat_at"] = long_ago
            row["started_at"] = long_ago
            row["updated_at"] = long_ago
    path = workspace / DEFAULT_RUNTIME_TASKS_PATH
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path, orphan.id


def _row_status(workspace: Path, task_id: str) -> str:
    from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH, TaskQueueStore

    store = TaskQueueStore(workspace / DEFAULT_RUNTIME_TASKS_PATH)
    for task in store.load():
        if task.id == task_id:
            return task.status
    raise AssertionError(f"строка {task_id} исчезла из очереди")


def test_a_dry_tick_does_not_bury_a_stranded_row(
    workspace: Path, monkeypatch: Any
) -> None:
    """Красный свидетель ревизии: сухой тик хоронил брошенную строку.

    `_free_stranded_rows` звался без флага, и брошенная строка с исчерпанными
    попытками получала терминальный `failed` — вердикт, который не отменить, от
    прохода, который обещал ничего не применять. Замер до починки: строка
    выходила из сухого тика со `status="failed"` и `last_error="orphaned: no
    heartbeat…"`.
    """
    from agent_tick import run_tick

    _install_fake_agent(monkeypatch)
    _, task_id = _stranded_queue(workspace, max_attempts=1)

    assert run_tick(workspace, dry_run=True) == 0

    assert _row_status(workspace, task_id) != "failed", (
        "сухой тик вынес терминальный вердикт брошенной строке — "
        "необратимое изменение в проходе, объявившем себя сухим"
    )


def test_a_dry_tick_still_requeues_a_recoverable_orphan(
    workspace: Path, monkeypatch: Any
) -> None:
    """Обратная сторона узкого договора: обратимую половину сухость не трогает.

    Строка с оставшимися попытками возвращается в очередь и на сухом прогоне.
    Иначе починка сухости стала бы отключением уборки — а строка, которую
    никто не освобождает, и есть дефект, ради которого уборка написана.
    """
    from agent_tick import run_tick

    _install_fake_agent(monkeypatch)
    _, task_id = _stranded_queue(workspace, max_attempts=3)

    assert run_tick(workspace, dry_run=True) == 0

    assert _row_status(workspace, task_id) != "running", (
        "сухой тик оставил брошенную строку в работе: перепостановка в очередь "
        "обратима и запрещать её узкий договор не просил"
    )


def test_a_live_tick_still_buries_a_stranded_row(
    workspace: Path, monkeypatch: Any
) -> None:
    """Живой тик по-прежнему доводит исчерпанную строку до терминального исхода.

    Без этого MIR-040 вернулся бы: строка, исчерпавшая попытки, воскресала и
    получала прогон сверх потолка.
    """
    from agent_tick import run_tick

    _install_fake_agent(monkeypatch)
    _, task_id = _stranded_queue(workspace, max_attempts=1)

    assert run_tick(workspace, dry_run=False) == 0

    assert _row_status(workspace, task_id) == "failed", (
        "живой тик перестал хоронить исчерпанную брошенную строку"
    )

