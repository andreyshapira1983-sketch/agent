"""Журнал отказов раскольщика — пауза для файла, который нельзя разрезать.

Замысел — агента (разговор через мостик 2026-09-21, ~17:00–17:14): раскольщик
честно отказывает («доказанная группа не переезжает целиком»), а производитель
берёт тот же файл КАЖДЫЙ цикл — отказ `no_patch` не ставит паузы, в отличие от
отказа человека. Агент сам решил: машинный отказ — не вердикт человека, в ящик
одобрений ему нельзя (там `decided_by` — человек); нужен свой журнал; и выход из
паузы — не по часам, а по отпечатку содержимого: файл снова берётся, когда его
текст изменился. Подвох он же и назвал: вернули прежний текст — пауза вернулась.

Его черновик (proposals/2026-09-21_no_patch_cooldown_v2.md) держал журнал по
пути, зависящему от того, откуда запущен процесс, и «было» выдумал; здесь путь —
от рабочей папки, как у соседних журналов, а читатели — настоящие.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from core.state_integrity import append_state_jsonl, read_state_jsonl

JOURNAL = Path("data") / "splitter_refusals.jsonl"


def _fingerprint(workspace: str | Path, target: str) -> str | None:
    try:
        return hashlib.sha256((Path(workspace) / target).read_bytes()).hexdigest()
    except OSError:
        return None


def record_refusal(workspace: str | Path, target: str, reason: str) -> None:
    """Запомнить отказ вместе с отпечатком того текста, который видел раскольщик."""
    fingerprint = _fingerprint(workspace, target)
    if fingerprint is None:
        return
    append_state_jsonl(Path(workspace) / JOURNAL, [{
        "target": target.replace("\\", "/"),
        "content_sha256": fingerprint,
        "reason": reason[:300],
        "decided_by": "splitter",
        "ts": datetime.now(timezone.utc).isoformat(),
    }])


def refused_unchanged(workspace: str | Path) -> frozenset[str]:
    """Файлы, которые раскольщик отказался резать и которые с тех пор не менялись."""
    path = Path(workspace) / JOURNAL
    if not path.exists():
        return frozenset()
    latest: dict[str, str] = {}
    for row in read_state_jsonl(path):
        target, fingerprint = row.get("target"), row.get("content_sha256")
        if target and fingerprint:
            latest[str(target)] = str(fingerprint)
    return frozenset(t for t, fp in latest.items() if _fingerprint(workspace, t) == fp)
