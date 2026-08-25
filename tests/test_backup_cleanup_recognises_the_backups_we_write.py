"""Два писателя копий, два формата, и уборка покрывает один из них.

Замер, отвергнутые варианты и границы: H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pytest

from core.backup_cleanup import BACKUP_NAME_RE
from core.state_integrity import backup_state_file


def test_the_cleanup_matches_its_own_writer(tmp_path) -> None:
    """`FileWriteTool` пишет `<файл>.bak.<метка>` — уборка это узнаёт."""
    name = "notes.md.bak.20260825T120000Z"

    match = BACKUP_NAME_RE.match(name)

    assert match is not None, (
        "уборка перестала узнавать формат FileWriteTool — писатель и читатель "
        "разошлись, и механизм снова не сможет сработать"
    )
    assert match.group("target") == "notes.md"
    assert match.group("ts") == "20260825T120000Z"


def test_the_state_writer_uses_a_different_shape(tmp_path) -> None:
    """Второй писатель кладёт метку ПЕРЕД `.bak`, и это осознанно разные формы.

    Замер, а не догадка: имя строится здесь же, живым вызовом.
    """
    store = tmp_path / "runtime_tasks.jsonl"
    store.write_text("строка\n", encoding="utf-8")

    produced = backup_state_file(store).name

    assert produced.endswith(".bak")
    assert produced.startswith("runtime_tasks.jsonl.")
    assert not BACKUP_NAME_RE.match(produced), (
        "форматы двух писателей сошлись — тогда уборка начала бы сметать и "
        "копии состояния, включая снятые человеком перед опасной правкой; "
        "это отдельное решение, а не побочный эффект"
    )


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
