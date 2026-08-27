"""Уборщик копий обязан узнавать все живые семьи имён — и ходить без человека.

Замер и границы: MIR-125 (половина «неограниченный рост») в
docs/audit/MASTER_ISSUE_REGISTRY.md.

Перемер 2026-08-27: data/ удвоился за пять дней (17 → 34 МБ), и рост — не
живые журналы, а .bak-мусор наших же разовых миграций. Орган уборки существовал
и не узнал НИ ОДИН из четырёх реальных файлов: его маска знала только
`<target>.bak.<ts>`, а миграции пишут `<target>.<ts>.bak`,
`<target>.pre-<slug>-<ts>.bak` и `<target>.pre-<slug>.bak` без метки времени.
Болезнь двух словарей — в именах файлов; та же гориллья форма, что MIR-181:
временный артефакт без срока жизни.

Возраст: из метки в имени, а без метки — из mtime файла. Неузнанное не
трогается, как и раньше. Проводка — в безлюдный проход гигиены (shadow по
умолчанию: считает и докладывает, не удаляет).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from core.backup_cleanup import cleanup_backups

_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

#: Ровно те имена, что лежали в живом data/ на момент перемера.
_LIVE_FAMILIES = [
    "source_registry.jsonl.bak.20260810T120000Z",
    "runtime_tasks.jsonl.20260810T120000Z.bak",
    "source_registry.jsonl.pre-marker-cleanup-20260810T120000Z.bak",
    "source_registry.jsonl.pre-truncation-debris.bak",
]


def _make_old(path: Path) -> None:
    path.write_text("x", encoding="utf-8")
    old = time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0))
    os.utime(path, (old, old))


def test_every_live_family_is_recognized_and_aged_out(tmp_path: Path) -> None:
    """Красный свидетель: 4 реальных имени, узнаны 4, а не 1."""
    for name in _LIVE_FAMILIES:
        _make_old(tmp_path / name)

    report = cleanup_backups(tmp_path, keep_last=0, max_age_days=14, now=_NOW)

    assert report.scanned == 4, "узнаны обязаны быть ВСЕ живые семьи имён"
    assert len(report.deleted) == 4
    for name in _LIVE_FAMILIES:
        assert not (tmp_path / name).exists()


def test_an_undated_backup_ages_by_mtime_not_by_name(tmp_path: Path) -> None:
    """Без метки в имени возраст берётся у файла; свежий mtime — хранится."""
    fresh = tmp_path / "a.jsonl.pre-cleanup.bak"
    fresh.write_text("x", encoding="utf-8")  # mtime = сейчас
    stale = tmp_path / "b.jsonl.pre-cleanup.bak"
    _make_old(stale)

    report = cleanup_backups(
        tmp_path, keep_last=0, max_age_days=14,
        now=datetime.now(timezone.utc),
    )

    assert fresh.exists(), "свежая копия без метки обязана пережить уборку"
    assert not stale.exists()
    assert report.scanned == 2


def test_what_is_not_understood_is_not_touched(tmp_path: Path) -> None:
    """Прежний закон органа держится: незнакомое имя — не кандидат."""
    for name in ("live.jsonl", "weird.bakery", "x.bak-not-really"):
        _make_old(tmp_path / name)

    report = cleanup_backups(tmp_path, keep_last=0, max_age_days=0, now=_NOW)

    assert report.scanned == 0
    for name in ("live.jsonl", "weird.bakery", "x.bak-not-really"):
        assert (tmp_path / name).exists()


def test_keep_last_pools_by_target_across_name_families(tmp_path: Path) -> None:
    """keep_last считает по ЦЕЛИ: три семьи имён одной цели — один пул."""
    names = [
        "t.jsonl.bak.20260801T120000Z",
        "t.jsonl.20260802T120000Z.bak",
        "t.jsonl.pre-fix-20260803T120000Z.bak",
    ]
    for name in names:
        _make_old(tmp_path / name)

    report = cleanup_backups(tmp_path, keep_last=2, max_age_days=1, now=_NOW)

    assert report.scanned == 3
    assert len(report.deleted) == 1
    assert not (tmp_path / names[0]).exists(), "старейшая в пуле цели уходит первой"


def test_the_sweep_walks_with_the_unattended_hygiene_pass(tmp_path: Path) -> None:
    """Проводка: проход гигиены метёт копии; dry_run считает и НЕ удаляет.

    Закон органа стоит как стоял: keep_last=3 — новейшие три копии цели
    неприкосновенны, единственная не удаляется никогда. Поэтому в полигоне
    ЧЕТЫРЕ копии одной цели: удаляемое — ровно старейшая.
    """
    from core.memory_hygiene_commands import run_maintenance_pass

    for day in ("01", "02", "03"):
        _make_old(tmp_path / f"t.jsonl.pre-fix-202608{day}T120000Z.bak")
    debris = tmp_path / "t.jsonl.pre-fix-20260731T120000Z.bak"
    _make_old(debris)

    class _Log:
        def log(self, *_a, **_k): ...

    class _Store:
        def load(self): return []
        def save(self, *_a, **_k): ...

    report = run_maintenance_pass(
        log=_Log(), persistent_store=None, episodic_store=None,
        assumption_store=None, suppressed_reason=None,
        dry_run=True, workspace=tmp_path,
    )

    assert report.get("backups_deleted") == 1, "тень обязана СЧИТАТЬ удаляемое"
    assert debris.exists(), "тень не удаляет"

    report = run_maintenance_pass(
        log=_Log(), persistent_store=None, episodic_store=None,
        assumption_store=None, suppressed_reason=None,
        dry_run=False, workspace=tmp_path,
    )

    assert report.get("backups_deleted") == 1
    assert not debris.exists()
