"""Учение по восстановлению: каждая резервная копия читается и знает, чем она является.

ИСТОРИЧЕСКИЙ КЛАСС (H-12, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
GitLab.com, 31 января 2017: во время восстановления после инцидента был удалён
рабочий каталог основной базы, и из ПЯТИ заведённых способов резервирования не
сработал ни один — часть была настроена неверно, часть не проверялась годами.
Отчёт называет причину прямо: резервная копия, восстановление из которой никто
не пробовал, есть не копия, а предположение.

ЧТО ЭТОТ СКРИПТ ДЕЛАЕТ. Прогоняет каждую копию через НАСТОЯЩИЙ загрузчик
состояния (тот же, что читает живые файлы, со всеми контрольными суммами и
карантином) и сообщает, чем копия является ПО ОТНОШЕНИЮ к текущему файлу:

  «ремонт»   — записи копии в основном лежат и в живом файле: восстановление
               вернёт потерянное, не тронув остального;
  «замена»   — пересечение мало или пусто: восстановление СОТРЁТ нынешнее
               состояние и поставит другое. Это не запрет, это предупреждение;
  «неизвестно» — у хранилища нет поля тождества, сравнивать нечем.

ОХВАТ, добавлен 2026-08-25 (H-51, класс Atlassian). Скрипт перебирал КОПИИ и
потому не мог задать вопрос «у чего копий нет». Замер: 19 живых хранилищ из 24
не имели ни одной копии — среди них журнал бюджета, ящик одобрений и расход
моделей, — а учение при этом показывало девять зелёных строк. Каталог `data/`
исключён из git, поэтому версионный контроль запасным путём здесь не является.
Теперь перебор идёт по ХРАНИЛИЩАМ, и непокрытые называются первыми.

Замер 2026-08-24 на девяти копиях: читаются все девять. Копия постоянной
памяти от 31 июля пересекается с живым файлом на НОЛЬ записей — то есть она
замена, а выглядит как обычная копия рядом с остальными.

Ничего не изменяет. Запуск: `python scripts/restore_drill.py`.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.state_integrity import read_state_jsonl

#: Поле тождества по хранилищам. Порядок важен: берётся первое найденное.
_IDENTITY_FIELDS = ("id", "fingerprint", "record_id", "episode_id")

#: Ниже этой доли общих записей копия считается заменой, а не ремонтом.
_REPAIR_OVERLAP = 0.5


def _identity(rows: list[dict]) -> str | None:
    if not rows:
        return None
    keys = set(rows[0])
    return next((field for field in _IDENTITY_FIELDS if field in keys), None)


def _backups_for(data: pathlib.Path, store: pathlib.Path) -> list[pathlib.Path]:
    """Копии, относящиеся к этому хранилищу, от новых к старым.

    Считаются ОБА источника восстановления: разовые `.bak`, снимаемые перед
    рискованной правкой, и суточные снимки `data/snapshots/<дата>/`, заведённые
    решением оператора 2026-08-25. Учитывать только первый источник значило бы
    оставить прибор врущим ровно в тот день, когда второй появился, — а именно
    ложь прибора и была находкой H-51.
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
    data = pathlib.Path("data")
    backups = sorted(data.glob("*.bak"))
    stores = sorted(p for p in data.glob("*.jsonl") if not p.name.endswith(".bak"))

    # H-51: раньше перебирались КОПИИ, и потому вопрос «у чего копий нет» не
    # задавался вовсе. Замер 2026-08-25: 19 живых хранилищ из 24 — включая
    # журнал бюджета, ящик одобрений и расход моделей — не имели ни одной
    # копии, а учение показывало девять зелёных строк. Каталог `data/`
    # исключён из git, так что версионный контроль здесь не запасной путь.
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
        except Exception as exc:  # noqa: BLE001 — нечитаемая копия и есть находка
            print(f"  НЕ ЧИТАЕТСЯ  {backup.name}: {type(exc).__name__}: {exc}")
            unreadable += 1
            continue

        try:
            current = read_state_jsonl(live) if live.exists() else []
        except Exception:  # noqa: BLE001
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
