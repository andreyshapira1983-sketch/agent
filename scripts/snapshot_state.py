"""Daily snapshot of the data/*.jsonl stores: one generation per day, seven kept."""
from __future__ import annotations

import argparse
import pathlib
import shutil
from datetime import datetime, timezone

KEEP_GENERATIONS = 7

# Inside data/: already ignored by git and already holds state, so no extra path to remember.
SNAPSHOT_DIRNAME = "snapshots"


def _today_stamp(now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y%m%d")


def _stores(data: pathlib.Path) -> list[pathlib.Path]:
    """Live stores only: no backups, no snapshots, no locks."""
    return sorted(
        p for p in data.glob("*.jsonl")
        if not p.name.endswith(".bak") and not p.name.endswith(".lock")
    )


def take_snapshot(
    workspace: pathlib.Path, *, now: datetime | None = None, keep: int = KEEP_GENERATIONS,
) -> tuple[pathlib.Path | None, int, int]:
    """Take today's snapshot unless it exists; return (path, files copied, generations pruned).

    A None path means nothing was taken (already taken today, or no data folder or stores):
    the tick calls this every time and the work happens once a day.
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
        except OSError:  # pragma: no cover — a busy file must not abort the snapshot
            continue

    # Keep the newest `keep` generations; only snapshot folders are removed, never live state.
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
