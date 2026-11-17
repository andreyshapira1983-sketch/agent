"""find_in_files does not leave through links; file_write stays out of the git folder."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tools.file_write import FileWriteTool
from tools.find_in_files import FindInFilesTool

SECRET = "needle-private-line"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "a.txt").write_text("needle-inside\n", encoding="utf-8")
    return ws


@pytest.fixture()
def outside(tmp_path: Path) -> Path:
    out = tmp_path / "outside"
    out.mkdir()
    (out / "o.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    return out


def _link_file(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("file links cannot be created here")


def _link_dir(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("links cannot be created here")


def test_a_file_link_leading_outside_is_not_searched(workspace: Path, outside: Path):
    _link_file(workspace / "docs" / "link.txt", outside / "o.txt")
    tool = FindInFilesTool(workspace)

    assert SECRET not in tool.run(query="needle")
    assert "link.txt" not in tool.run(name="*.txt")
    assert "docs/a.txt" in tool.run(query="needle")


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is descended into on Windows")
def test_a_folder_link_leading_outside_is_not_descended(workspace: Path, outside: Path):
    _link_dir(workspace / "docs" / "jump", outside)
    tool = FindInFilesTool(workspace)

    assert SECRET not in tool.run(query="needle")
    assert "o.txt" not in tool.run(name="*.txt")
    assert "docs/a.txt" in tool.run(query="needle")


def test_a_link_to_a_credential_file_is_not_searched(workspace: Path):
    (workspace / ".env").write_text(f"{SECRET}\n", encoding="utf-8")
    _link_file(workspace / "docs" / "notes.txt", workspace / ".env")
    tool = FindInFilesTool(workspace)

    assert SECRET not in tool.run(query="needle")
    with pytest.raises(PermissionError):
        tool.run(query="needle", path="docs/notes.txt")


def test_a_link_that_stays_inside_is_still_searched(workspace: Path):
    _link_file(workspace / "docs" / "same.txt", workspace / "docs" / "a.txt")

    assert "docs/same.txt" in FindInFilesTool(workspace).run(query="needle")


@pytest.mark.parametrize("rel", [
    ".git/config",
    ".git/hooks/pre-commit",
    "vendor/.git/config",
    ".GIT/hooks/post-checkout",
])
def test_file_write_refuses_the_git_folder(workspace: Path, rel: str):
    tool = FileWriteTool(workspace)

    assert tool.risk_for({"path": rel, "content": "x\n"}) == "irreversible"
    with pytest.raises(PermissionError, match=r"\.git"):
        tool.run(path=rel, content="x\n")
    assert not (workspace / rel).exists()


@pytest.mark.parametrize("rel", [".gitignore", ".github/notes.md", "docs/git.txt"])
def test_file_write_still_writes_beside_the_git_folder(workspace: Path, rel: str):
    assert FileWriteTool(workspace).run(path=rel, content="x\n")["mode"] == "create"
