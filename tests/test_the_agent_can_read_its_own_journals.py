"""The agent can read and compute over its OWN journals.

Live dialogue 2026-09-20, the operator asked why it had filed no proposal to
change its own code. It answered with invented gates and said honestly what it
could not check: `data/approval_inbox.jsonl` is 2 435 380 bytes — over the
whole-file read cap, and `python_probe` refused it as input at 2 MB. Its own
evidence was out of reach, so it guessed. Three things had to be true and one
was not: a workspace-wide search still skips logs/ and data/ (an answer must not
be built from the echo of its own question), but pointing `path` at them works
and is now SAID in the tool; a window read of a big file works and its refusal
already teaches that; the lab's input cap now fits a journal, and its refusal
names the numbers and the narrower path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.find_in_files import FindInFilesTool
from tools.python_probe import PythonProbeTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("Текущий выпуск: 5.7.8\n", encoding="utf-8")
    rows = "".join(f'{{"cycle": {i}, "result": "completed"}}\n' for i in range(1, 2001))
    (tmp_path / "data" / "campaign_ledger.jsonl").write_text(rows, encoding="utf-8")
    return tmp_path


def test_a_workspace_search_still_skips_its_own_journals(workspace: Path) -> None:
    out = FindInFilesTool(workspace_root=workspace).run(query="completed", path=".")
    assert "campaign_ledger" not in out, "общий поиск не должен утыкаться в собственные журналы"


def test_pointing_the_path_at_them_searches_them(workspace: Path) -> None:
    tool = FindInFilesTool(workspace_root=workspace)
    out = tool.run(query='"cycle": 1999', path="data")
    assert "campaign_ledger.jsonl:1999" in out, out[:200]
    assert "path='data'" in tool.description, "инструмент обязан сказать, как искать у себя"


def test_the_lab_computes_over_a_journal_bigger_than_two_megabytes(workspace: Path) -> None:
    big = workspace / "data" / "approval_inbox.jsonl"
    big.write_text('{"x": "' + "y" * 2_500_000 + '"}\n', encoding="utf-8")
    assert big.stat().st_size > 2 * 1024 * 1024
    out = PythonProbeTool(workspace_root=workspace).run(
        code="print(sum(1 for _ in open('data/approval_inbox.jsonl', encoding='utf-8')))",
        inputs=["data/approval_inbox.jsonl"],
    )
    assert out["exit_code"] == 0, out
    assert out["stdout"].strip() == "1"
    assert not out["missing_inputs"]


def test_a_refused_input_names_the_numbers_and_the_way_round(workspace: Path) -> None:
    huge = workspace / "data" / "huge.jsonl"
    huge.write_text("z" * (17 * 1024 * 1024), encoding="utf-8")
    with pytest.raises(ValueError) as refusal:
        PythonProbeTool(workspace_root=workspace).run(code="print(1)", inputs=["data/huge.jsonl"])
    said = str(refusal.value)
    assert "bytes" in said and "find_in_files" in said and "start_line" in said
