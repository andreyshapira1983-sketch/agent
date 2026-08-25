"""Суточный снимок покрывает КАЖДОЕ хранилище и держит семь поколений.

Замер, отвергнутые варианты и границы: H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

from scripts.snapshot_state import take_snapshot


def _workspace(tmp_path: pathlib.Path, *, stores: int = 3) -> pathlib.Path:
    data = tmp_path / "data"
    data.mkdir()
    for i in range(stores):
        (data / f"store_{i}.jsonl").write_text(f"строка {i}\n", encoding="utf-8")
    (data / "store_0.jsonl.old.bak").write_text("копия\n", encoding="utf-8")
    (data / "store_0.jsonl.lock").write_text("", encoding="utf-8")
    return tmp_path


def test_every_store_is_copied_and_nothing_else_is(tmp_path) -> None:
    ws = _workspace(tmp_path)

    target, copied, pruned = take_snapshot(ws)

    assert target is not None and copied == 3, (
        "снимок покрыл не все хранилища — повторяется ошибка прибора, "
        "перебиравшего не то множество"
    )
    names = sorted(p.name for p in target.iterdir())
    assert names == ["store_0.jsonl", "store_1.jsonl", "store_2.jsonl"], names
    assert pruned == 0


def test_a_second_run_the_same_day_does_nothing(tmp_path) -> None:
    """Идемпотентность: тик зовёт каждый раз, работа делается раз в сутки."""
    ws = _workspace(tmp_path)
    take_snapshot(ws)

    target, copied, pruned = take_snapshot(ws)

    assert target is None and copied == 0 and pruned == 0


def test_seven_generations_are_kept_and_the_eighth_falls_off(tmp_path) -> None:
    ws = _workspace(tmp_path)
    base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    for day in range(9):
        take_snapshot(ws, now=base + timedelta(days=day))

    generations = sorted(
        p.name for p in (ws / "data" / "snapshots").iterdir() if p.is_dir()
    )
    assert len(generations) == 7, generations
    assert generations[0] == "20260803", "выпало не самое старое поколение"
    assert generations[-1] == "20260809"


def test_the_live_state_is_never_touched(tmp_path) -> None:
    """Граница: снимок читает живое и не трогает его — ни файла, ни строки."""
    ws = _workspace(tmp_path)
    before = {
        p.name: p.read_text(encoding="utf-8")
        for p in (ws / "data").glob("*.jsonl")
    }

    base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    for day in range(9):
        take_snapshot(ws, now=base + timedelta(days=day))

    after = {
        p.name: p.read_text(encoding="utf-8")
        for p in (ws / "data").glob("*.jsonl")
    }
    assert after == before


def test_a_snapshot_reads_back_through_the_real_loader(tmp_path) -> None:
    """Копия, которую не прочесть настоящим загрузчиком, — предположение.

    Тот же довод, что в H-12: конверт целостности обязан пережить копирование,
    иначе учение по восстановлению отвергнет снимок как порченый.
    """
    from core.state_integrity import append_state_jsonl, read_state_jsonl

    data = tmp_path / "data"
    data.mkdir()
    append_state_jsonl(data / "real.jsonl", [{"kind": "важное", "n": 1}])

    target, copied, _ = take_snapshot(tmp_path)

    assert target is not None and copied == 1
    rows = read_state_jsonl(target / "real.jsonl")
    assert [r["kind"] for r in rows] == ["важное"]

def test_the_tick_actually_takes_the_snapshot(tmp_path, monkeypatch) -> None:
    """Проводка, а не наличие: снимок обязан СТОЯТЬ на пути тика.

    Тест на функцию зелен и тогда, когда её никто не зовёт — за этот день урок
    повторился шесть раз, поэтому проверяется сам вызов из `run_tick`.
    """
    import agent_tick

    called: list[pathlib.Path] = []
    monkeypatch.setattr(agent_tick, "_take_daily_snapshot", called.append)
    monkeypatch.setattr(agent_tick, "_ensure_env_loaded", lambda ws: None)

    class _Stop(RuntimeError):
        pass

    def _boom(*_a, **_k):
        raise _Stop

    # Дальше тика идти незачем: предмет проверки — что снимок сделан РАНЬШЕ
    # любой работы, а не что тик доработал до конца.
    monkeypatch.setattr(agent_tick.os.environ, "setdefault", _boom)

    try:
        agent_tick.run_tick(tmp_path, dry_run=True)
    except _Stop:
        pass

    assert called == [tmp_path], (
        "тик не снимает суточный снимок — копии есть в скрипте и нет в жизни"
    )


def test_a_failing_snapshot_never_stops_the_tick(tmp_path, monkeypatch) -> None:
    """Граница: страховка, способная остановить работу, хуже её отсутствия."""
    import agent_tick

    def _explode(_ws):
        raise OSError("диск отказал")

    monkeypatch.setattr(
        "scripts.snapshot_state.take_snapshot", lambda *a, **k: _explode(None)
    )

    agent_tick._take_daily_snapshot(tmp_path)  # не должно бросить
