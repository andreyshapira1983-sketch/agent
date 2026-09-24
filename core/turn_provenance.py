"""Порождено этим ходом или только использовано — по диску, а не по словам.

24.09, 18:51: оператор попросил «создай 3D-сцену… не считай задачу
выполненной, пока видео нельзя открыть». Рендер нового скрипта упал, а в
ответе результатом значилось converted/build_character_v4…/character.mp4 —
видео, сделанное в 17:46 по прошлой просьбе. Все проверки («файл есть,
200 КБ, 10 с») старый файл прошёл: ни одна не спросила, КОГДА он появился.

W3C PROV различает сущность, порождённую действием (wasGeneratedBy, со
временем порождения), и использованную им (used). Здесь то же: если ход
действовал (писал, собирал, конвертировал), а файл из вывода ответа старше
начала хода — к ответу приписывается показание журнала. Слова ответа не
разбираются: решают путь, существующий на диске, и его время.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.workspace_reference import workspace_paths_named

#: Где лежат ПРОДУКТЫ работы агента: выходы convert_file (tools/convert_file.py,
#: OUTPUT_DIR) и его опыты. Код и документы, которые он читает (core/, tools/,
#: docs/…), ход только ИСПОЛЬЗУЕТ — ночь 24→25.09: строка ложно называла
#: «не созданными» прочитанные tools/base.py и core/model_router.py, и этот
#: сигнал кормил его цели «объяснить наблюдение о себе».
PRODUCT_DIRS = ("converted/", "experiments/")

#: Инструменты, которыми ход что-то порождает. Ход без них только читал — там
#: старые файлы в ответе законны («что лежит в X»).
PRODUCING_TOOLS = frozenset({
    "file_write", "journal_append", "memory_bank", "convert_file", "render3d", "shell_exec",
})


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m %H:%M UTC")


def older_than_turn(head: str, root: Path | None, started_at: float,
                    executed_tools: list[str]) -> str | None:
    """Показание журнала: файлы вывода, не порождённые этим ходом; None — нечего сказать."""
    if root is None or started_at <= 0 or not PRODUCING_TOOLS & set(executed_tools):
        return None
    old: list[str] = []
    for rel in workspace_paths_named(head, root=root):
        if not rel.replace("\\", "/").startswith(PRODUCT_DIRS):
            continue
        path = root / rel
        if path.is_file() and path.stat().st_mtime < started_at:
            old.append(f"{rel} (изменён {_clock(path.stat().st_mtime)})")
    if not old:
        return None
    return ("⚠️ По журналу хода: названное в выводе этим ходом НЕ создано — оно старше хода "
            f"(ход начат {_clock(started_at)}): " + "; ".join(old[:5]) + ".")
