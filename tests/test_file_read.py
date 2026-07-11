"""Tool-level safety tests for `FileReadTool` (§5 Tools, §5 Tool Result Validation).

Six minimal cases that must pass before the agent can claim "safe file reads":
  1. file exists -> ok, validate_output ok
  2. file does not exist -> FileNotFoundError
  3. path escapes workspace -> PermissionError
  4. empty file -> reads ok but validate_output flags it
  5. file larger than MAX_BYTES -> ValueError
  6. non-UTF-8 file -> UnicodeDecodeError
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.file_read import FileReadTool, MAX_BYTES


def _tool(workspace: Path) -> FileReadTool:
    return FileReadTool(workspace_root=workspace)


# ---------- 1. existing file ----------

def test_existing_file_is_read(workspace: Path) -> None:
    target = workspace / "note.txt"
    target.write_text("hello world\nsecond line\n", encoding="utf-8")

    tool = _tool(workspace)
    output = tool.run(path="note.txt")

    assert "hello world" in output
    assert "second line" in output

    is_ok, issues = tool.validate_output(output)
    assert is_ok is True
    assert issues == []


def test_unicode_filename_is_read_when_inside_workspace(workspace: Path) -> None:
    target = workspace / "архитектура автономного Агента.txt"
    target.write_text("Проверенный текст архитектуры.\n", encoding="utf-8")

    tool = _tool(workspace)
    output = tool.run(path="архитектура автономного Агента.txt")

    assert output == "Проверенный текст архитектуры.\n"


# ---------- 2. missing file ----------

def test_missing_file_raises(workspace: Path) -> None:
    tool = _tool(workspace)
    with pytest.raises(FileNotFoundError):
        tool.run(path="does_not_exist.txt")


def test_missing_file_hint_lists_real_neighbors(workspace: Path) -> None:
    """A wrong guess should surface the real files in the nearest existing
    directory, so the planner can self-correct on replan instead of guessing
    another non-existent path (the 'scan itself' behaviour)."""
    (workspace / "README.md").write_text("hi", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "AGENT_ANATOMY.md").write_text("x", encoding="utf-8")
    (workspace / "docs" / "daemon-progress.md").write_text("y", encoding="utf-8")
    tool = _tool(workspace)

    with pytest.raises(FileNotFoundError) as exc:
        tool.run(path="docs/CORPORATE_MODEL.md")
    msg = str(exc.value)
    # Names the nearest real directory and lists its actual contents.
    assert "docs" in msg
    assert "AGENT_ANATOMY.md" in msg
    assert "daemon-progress.md" in msg
    assert "Use one of these real paths" in msg


def test_missing_file_hint_walks_up_to_existing_ancestor(workspace: Path) -> None:
    """If several path segments are hallucinated, the hint comes from the
    nearest ancestor that really exists."""
    (workspace / "AGENT_DOCTRINE.md").write_text("d", encoding="utf-8")
    tool = _tool(workspace)

    with pytest.raises(FileNotFoundError) as exc:
        tool.run(path="docs/future/deep/CORPORATE_MODEL.md")
    msg = str(exc.value)
    assert "AGENT_DOCTRINE.md" in msg
    assert "workspace root" in msg


# ---------- 3. workspace escape ----------

def test_path_traversal_is_rejected(workspace: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    # Sibling directory OUTSIDE the workspace
    outside_dir = tmp_path_factory.mktemp("outside")
    secret = outside_dir / "secret.txt"
    secret.write_text("classified", encoding="utf-8")

    tool = _tool(workspace)

    # Absolute path outside workspace
    with pytest.raises(PermissionError):
        tool.run(path=str(secret))

    # Relative traversal
    traversal = f"..{Path('/').name or '/'}..{Path('/').name or '/'}etc{Path('/').name or '/'}passwd"
    # Use a plain '../../' regardless of OS sep — pathlib normalises
    with pytest.raises(PermissionError):
        tool.run(path="../../etc/passwd")


# ---------- 4. empty file ----------

def test_empty_file_validation_fails(workspace: Path) -> None:
    target = workspace / "empty.txt"
    target.write_text("", encoding="utf-8")

    tool = _tool(workspace)
    output = tool.run(path="empty.txt")

    # The read itself succeeds (the file is reachable, in-budget, UTF-8).
    assert output == ""

    # validate_output is the layer that catches semantic emptiness.
    is_ok, issues = tool.validate_output(output)
    assert is_ok is False
    assert issues, "empty file must produce at least one issue"
    assert any("empty" in i.lower() for i in issues)


def test_whitespace_only_file_validation_fails(workspace: Path) -> None:
    target = workspace / "ws.txt"
    target.write_text("   \n\n  \t\n", encoding="utf-8")

    tool = _tool(workspace)
    output = tool.run(path="ws.txt")
    is_ok, issues = tool.validate_output(output)
    assert is_ok is False
    assert any("whitespace" in i.lower() or "empty" in i.lower() for i in issues)


# ---------- 5. oversized file ----------

def test_oversized_file_is_rejected(workspace: Path) -> None:
    target = workspace / "big.txt"
    # Write 1 byte over the cap. Buffered write keeps the test fast.
    with open(target, "wb") as fh:
        fh.write(b"x" * (MAX_BYTES + 1))

    tool = _tool(workspace)
    with pytest.raises(ValueError) as exc_info:
        tool.run(path="big.txt")
    assert "too large" in str(exc_info.value).lower()


# ---------- 6. non-UTF-8 file ----------

def test_binary_file_raises_unicode_error(workspace: Path) -> None:
    target = workspace / "binary.bin"
    # Bytes that are NOT valid UTF-8 anywhere — ff/fe as first bytes of a
    # would-be code point are explicitly invalid in UTF-8.
    target.write_bytes(b"\xff\xfe\x00\x01\x80\x81")

    tool = _tool(workspace)
    with pytest.raises(UnicodeDecodeError):
        tool.run(path="binary.bin")
