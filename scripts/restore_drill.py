"""Read-only restore drill: name stores with no backup, tell repair from replacement backups."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.state_integrity import read_state_jsonl

#: Identity field candidates; order matters, the first one present wins.
_IDENTITY_FIELDS = ("id", "fingerprint", "record_id", "episode_id")

#: Below this share of records shared with the live file, restoring a backup would
#: replace the current state rather than repair it.
_REPAIR_OVERLAP = 0.5


def _identity(rows: list[dict]) -> str | None:
    if not rows:
        return None
    keys = set(rows[0])
    return next((field for field in _IDENTITY_FIELDS if field in keys), None)


def _backups_for(data: pathlib.Path, store: pathlib.Path) -> list[pathlib.Path]:
    """Backups of this store, newest first: one-off ``*.bak`` files and daily snapshots.

    Daily snapshots live in ``data/snapshots/<date>/``.
    """
    prefix = store.name.split(".jsonl")[0]
    found = [b for b in data.glob("*.bak") if b.name.split(".jsonl")[0] == prefix]
    snapshots = data / "snapshots"
    if snapshots.is_dir():
        found += [
            copy for generation in snapshots.iterdir() if generation.is_dir()
            for copy in [generation / store.name] if copy.exists()
        ]
    return sorted(found, key=lambda b: b.stat().st_mtime, reverse=True)


def main() -> int:
    """Print backup coverage and a verdict per backup; exit 1 if any backup is unreadable."""
    data = pathlib.Path("data")
    backups = sorted(data.glob("*.bak"))
    stores = sorted(p for p in data.glob("*.jsonl") if not p.name.endswith(".bak"))

    # Walk stores, not backups, so a store with no backup at all is named; data/ is not
    # in git, so version control is no fallback here.
    uncovered = [s for s in stores if not _backups_for(data, s)]
    covered = len(stores) - len(uncovered)
    print(f"живых хранилищ: {len(stores)}, из них с копией: {covered}")
    if uncovered:
        print(f"БЕЗ ЕДИНОЙ КОПИИ: {len(uncovered)} — восстанавливать не из чего:")
        for store in uncovered:
            print(f"    {store.name}")
    print()

    if not backups:
        print("резервных копий не найдено")
        return 0

    unreadable = 0
    print(f"копий: {len(backups)}\n")
    for backup in backups:
        live = data / (backup.name.split(".jsonl")[0] + ".jsonl")
        try:
            rows = read_state_jsonl(backup)
        except Exception as exc:  # noqa: BLE001 — an unreadable backup is itself the finding
            print(f"  НЕ ЧИТАЕТСЯ  {backup.name}: {type(exc).__name__}: {exc}")
            unreadable += 1
            continue

        try:
            current = read_state_jsonl(live) if live.exists() else []
        except Exception:  # noqa: BLE001 — an unreadable live file compares as empty
            current = []

        field = _identity(rows)
        if field is None:
            verdict = "неизвестно (нет поля тождества)"
        else:
            ids_backup = {r.get(field) for r in rows}
            ids_live = {r.get(field) for r in current}
            shared = len(ids_backup & ids_live)
            share = shared / max(1, len(ids_backup))
            verdict = (
                f"ремонт (общих {shared} из {len(ids_backup)})"
                if share >= _REPAIR_OVERLAP
                else f"ЗАМЕНА (общих {shared} из {len(ids_backup)})"
            )

        print(f"  {backup.name[:58]:<60} {len(rows):>5} рядов  {verdict}")

    print()
    print(f"нечитаемых копий: {unreadable}")
    return 1 if unreadable else 0


if __name__ == "__main__":
    raise SystemExit(main())
