import pytest

from tools.journal_append import JournalAppendTool


def _tool(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return JournalAppendTool(workspace_root=tmp_path)


def test_boundary_rejects_core_loop(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tool.run(path="core/loop.py", record={"probe": "x"})
    assert not (tmp_path / "core" / "loop.py").exists()


def test_extra_argument_raises_permission_error(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        tool.run(path="data/journal.jsonl", record={"probe": "x"}, extra="unexpected")
