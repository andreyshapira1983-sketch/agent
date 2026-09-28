"""Два писателя копий, два формата — уборка узнаёт копии обоих.

Замер, отвергнутые варианты и границы: H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md,
MIR-125 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.backup_cleanup import BACKUP_NAME_RE, cleanup_backups
from core.state_integrity import backup_state_file
from tools.file_write import FileWriteTool

#: Позже любой метки, что писатель поставит сейчас: копия уже старше срока.
_LATER = datetime.now(timezone.utc) + timedelta(days=1)


def test_the_cleanup_matches_its_own_writer(tmp_path) -> None:
    """Копию, которую `FileWriteTool` оставил при перезаписи, уборка узнаёт и метёт.

    Имя строит живой писатель: разойдутся формы — копии копятся без срока.
    """
    tool = FileWriteTool(workspace_root=tmp_path)
    tool.run("notes.md", "первая версия\n")
    backup = tool.run("notes.md", "вторая версия\n")["backup_path"]

    report = cleanup_backups(tmp_path, keep_last=0, max_age_days=0, now=_LATER)

    assert report.deleted == [backup], "уборка не узнала копию FileWriteTool"
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "вторая версия\n"


def test_the_cleanup_matches_the_state_writer(tmp_path) -> None:
    """`backup_state_file` ставит метку ПЕРЕД `.bak` — уборка узнаёт и эту форму."""
    store = tmp_path / "runtime_tasks.jsonl"
    store.write_text("строка\n", encoding="utf-8")
    produced = backup_state_file(store)

    report = cleanup_backups(tmp_path, keep_last=0, max_age_days=0, now=_LATER)

    assert report.deleted == [produced.name], "уборка не узнала копию backup_state_file"
    assert store.exists()


@pytest.mark.parametrize("name", [
    "persistent_memory.jsonl.pre-injection-cleanup.bak",
    "source_registry.jsonl.pre-truncation-debris.bak",
])
def test_a_hand_labelled_backup_has_no_age_in_its_name(name: str) -> None:
    """Живые имена на 2026-08-25: шесть из девяти помечены словом, не временем.

    У такой копии нет возраста в имени, и уборка по сроку к ней неприменима в
    принципе — не потому, что её забыли, а потому, что сметать снятое человеком
    перед правкой было бы потерей.
    """
    assert not BACKUP_NAME_RE.match(name)
