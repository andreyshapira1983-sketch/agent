"""Чужой файл состояния закрыт для journal_append в любом написании пути."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.journal_append import _KNOWN_JOURNALS, JournalAppendTool


@pytest.mark.parametrize("path", [
    "data/approval_inbox.jsonl",
    "data/./approval_inbox.jsonl",
    "data/x/../approval_inbox.jsonl",
    "data//approval_inbox.jsonl",
    "data/Approval_Inbox.jsonl",
    "data/./judge_rulings.jsonl",
    "data/x/../budget_ledger.jsonl",
])
def test_an_owned_state_file_is_refused_in_every_spelling(tmp_path: Path, path: str) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    with pytest.raises(PermissionError):
        tool.run(path=path, record={"id": "x", "status": "approved"})
    assert not list((tmp_path / "data").glob("*.jsonl")) if (tmp_path / "data").exists() else True


def test_an_alias_of_a_known_journal_keeps_its_contract(tmp_path: Path) -> None:
    """Обходное написание не снимает с журнала проверку его формы."""
    known = min(_KNOWN_JOURNALS)
    alias = known.replace("data/", "data/./", 1)
    tool = JournalAppendTool(workspace_root=tmp_path)
    try:
        direct = tool.run(path=known, record={})
    except Exception as exc:  # noqa: BLE001 — сравнивается сам вид отказа
        direct = type(exc)
    try:
        via_alias = tool.run(path=alias, record={})
    except Exception as exc:  # noqa: BLE001
        via_alias = type(exc)
    assert isinstance(direct, type), "пустая запись в известный журнал должна быть отказом"
    assert via_alias is direct


def test_a_free_journal_is_still_written(tmp_path: Path) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    out = tool.run(path="data/./notes_fix1110.jsonl", record={"note": "x"})
    assert out["appended"] is True
    assert (tmp_path / "data" / "notes_fix1110.jsonl").is_file()
