import pytest

from core.state_integrity import read_state_jsonl_unlocked
from tools.journal_append import JournalAppendTool


def _tool(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return JournalAppendTool(workspace_root=tmp_path)


def test_append_does_not_overwrite(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch)
    path = "data/journal.jsonl"
    first = {"text": "Суд выносит вердикт по журналам а модель лишь предлагает объяснения", "kind": "[ВЫВОД, проверен боем]", "provenance": "test"}
    second = {"text": "Свидетель держит контракт инструмента а не только его счастливый путь", "kind": "[ВЫВОД, проверен боем]", "provenance": "test"}
    tool.run(path=path, record=first)
    tool.run(path=path, record=second)
    records = read_state_jsonl_unlocked(path)
    assert len(records) == 2
    assert records[0]["text"] == first["text"]
    assert records[0]["kind"] == first["kind"]
    assert records[0]["provenance"] == first["provenance"]


def test_envelope_is_removed(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch)
    path = "data/journal.jsonl"
    payload = {"text": "Свидетель держит контракт инструмента а не только его счастливый путь", "kind": "[ВЫВОД, проверен боем]", "provenance": "test"}
    tool.run(path=path, record=payload)
    records = read_state_jsonl_unlocked(path)
    assert len(records) == 1
    assert records[0] == payload


def test_boundary_rejects_core_loop(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tool.run(path="core/loop.py", record={"probe": "x"})
    assert not (tmp_path / "core" / "loop.py").exists()


def test_extra_argument_raises_permission_error(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        tool.run(path="data/journal.jsonl", record={"probe": "x"}, extra="unexpected")
