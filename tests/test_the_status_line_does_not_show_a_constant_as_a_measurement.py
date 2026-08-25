"""Постоянная величина не выдаётся оператору за измерение.

СВЕРКА С ПОЛЕМ, форма F-5 «сенсор» (docs/audit/FIELD_CHECK_QUEUE.md). Провал,
названный полем: прибор меряет ПРОКСИ, а читается как объект.

ЗАМЕР 2026-08-25. Поле `processed_effects` в строке состояния читается как
«эффектов применено за тик», а передаётся ЖЁСТКИМ НУЛЁМ в обоих местах записи
пульса. Его докстринг честно говорит «на практике 0», но обоснование неполно:
«живой путь упирается в ящик одобрений» — при ВЫДАННОМ разрешении (H-41) тик
применяет эффекты сам, и поле всё равно покажет ноль.

Читателей у поля ровно один — строка состояния. Поэтому чинится не схема пульса
(её парсят кампания и память ради `dry_run_streak`), а то единственное место,
где число попадает человеку на глаза: постоянная не должна стоять в строке,
которую читают как измерение.

ЧТО ОСТАЁТСЯ ВЕРНЫМ и не трогается: `mode` и `effects` — настоящие сенсоры,
`dry_run_streak` считает подряд идущие содержательные сухие проходы и его
читают три места.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import agent_tick


def _heartbeat(workspace, *, mode: str) -> None:
    (workspace / "data").mkdir(parents=True, exist_ok=True)
    (workspace / "data" / "daemon_heartbeat.json").write_text(
        json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "tick_complete",
            "mode": mode,
            "effects": "disabled" if mode == "dry_run" else "enabled",
            "processed_effects": 0,
            "dry_run_streak": 3,
        }),
        encoding="utf-8",
    )


def test_the_constant_is_not_printed_as_a_number(tmp_path, capsys) -> None:
    _heartbeat(tmp_path, mode="dry_run")

    agent_tick._print_status(tmp_path)
    printed = capsys.readouterr().err

    mode_line = next((ln for ln in printed.splitlines() if ln.startswith("Mode:")), "")
    assert mode_line, printed
    assert "processed_effects=0" not in mode_line, (
        "постоянная выдаётся за измерение: поле всегда 0 по построению, а в "
        "строке выглядит счётчиком применённых эффектов — " + mode_line
    )


def test_the_real_sensors_survive(tmp_path, capsys) -> None:
    """Контроль: чинится ОДНО поле, а не строка целиком."""
    _heartbeat(tmp_path, mode="live")

    agent_tick._print_status(tmp_path)
    mode_line = next(
        (ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("Mode:")),
        "",
    )

    assert "live" in mode_line
    assert "effects=enabled" in mode_line
    assert "dry_run_streak=3" in mode_line


def test_the_heartbeat_schema_is_untouched(tmp_path) -> None:
    """Граница: схему пульса не трогаем — её парсят кампания и память.

    Чинится ПОКАЗ, а не запись: поле остаётся в пульсе, потому что его
    отсутствие сломало бы читателей, которых мы не проверяли.
    """
    visibility = agent_tick._dry_run_visibility(
        dry_run=True, previous_streak=2, processed_effects=0, did_work=True,
    )

    assert "processed_effects" in visibility
    assert visibility["dry_run_streak"] == 3
