"""Суточный снимок состояния: семь поколений, одно на сутки.

Замер, отвергнутые варианты и границы: H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
from datetime import datetime, timezone

#: Сколько поколений держим. Решение оператора 2026-08-25.
KEEP_GENERATIONS = 7

#: Куда кладём. Внутри `data/`, потому что каталог уже исключён из git и уже
#: несёт состояние; отдельное место означало бы ещё один путь, который надо
#: помнить при восстановлении.
SNAPSHOT_DIRNAME = "snapshots"


def _today_stamp(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y%m%d")


def _stores(data: pathlib.Path) -> list[pathlib.Path]:
    """Живые хранилища: без копий, без снимков, без замков."""
    return sorted(
        p for p in data.glob("*.jsonl")
        if not p.name.endswith(".bak") and not p.name.endswith(".lock")
    )


def take_snapshot(
    workspace: pathlib.Path, *, now: datetime | None = None, keep: int = KEEP_GENERATIONS,
) -> tuple[pathlib.Path | None, int, int]:
    """Снять снимок, если сегодняшнего ещё нет. Вернуть (путь, файлов, удалено).

    `путь is None` означает «сегодняшний снимок уже есть» — это не ошибка, а
    идемпотентность: тик зовёт эту функцию каждый раз, а работа делается раз в
    сутки.
    """
    data = pathlib.Path(workspace) / "data"
    if not data.is_dir():
        return None, 0, 0

    root = data / SNAPSHOT_DIRNAME
    stamp = _today_stamp(now)
    target = root / stamp
    if target.exists():
        return None, 0, 0

    stores = _stores(data)
    if not stores:
        return None, 0, 0

    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for store in stores:
        try:
            shutil.copy2(store, target / store.name)
            copied += 1
        except OSError:  # pragma: no cover — занятый файл не должен ронять снимок
            continue

    # Прополка: держим `keep` самых новых поколений. Удаляются ТОЛЬКО
    # собственные снимки, живое состояние не трогается никогда.
    generations = sorted(
        (d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True
    )
    pruned = 0
    for old in generations[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        pruned += 1

    return target, copied, pruned


def main() -> int:
    ap = argparse.ArgumentParser(description="daily state snapshot, 7 generations")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--keep", type=int, default=KEEP_GENERATIONS)
    args = ap.parse_args()

    target, copied, pruned = take_snapshot(
        pathlib.Path(args.workspace), keep=args.keep
    )
    if target is None:
        print("снимок за эти сутки уже есть — ничего не делаю")
        return 0
    print(f"снимок: {target}  файлов: {copied}  удалено старых поколений: {pruned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
