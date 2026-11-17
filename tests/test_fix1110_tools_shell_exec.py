"""shell_exec guards: a read-only verdict must mean nothing is written and nothing outside is read."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.shell_exec import ShellExecTool


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture()
def tool(workspace: Path) -> ShellExecTool:
    return ShellExecTool(workspace_root=workspace)


def _git_repo(workspace: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    for args in (
        ["init", "-q"],
        ["-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "base"],
    ):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)  # noqa: S603, S607


# ── 1. git options that write ────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["git", "diff", "--output=out.txt"],
    ["git", "diff", "--output", "out.txt"],
    ["git", "log", "--output=out.txt"],
    ["git", "show", "--outp=out.txt"],
    ["git", "diff", "--no-index", "a.txt", "b.txt"],
])
def test_a_read_only_git_verb_takes_no_writing_option(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError):
        tool._validate_argv(argv)


def test_git_diff_output_writes_nothing(tool: ShellExecTool, workspace: Path, tmp_path: Path):
    _git_repo(workspace)
    (workspace / "a.txt").write_text("changed\n", encoding="utf-8")

    for target in ("leak.txt", "../victim.txt"):
        with pytest.raises(PermissionError):
            tool.run(["git", "diff", f"--output={target}"])

    assert not (workspace / "leak.txt").exists()
    assert not (tmp_path / "victim.txt").exists()


@pytest.mark.parametrize("argv", [
    ["git", "ls-files", "-o"],
    ["git", "log", "--oneline", "-n", "5"],
    ["git", "diff", "--stat", "HEAD~1..HEAD"],
    ["git", "log", "--grep=/api/v1"],
    ["git", "show", "HEAD", "--", "core/loop.py"],
])
def test_ordinary_git_reads_still_pass(tool: ShellExecTool, argv: list[str]):
    assert tool._validate_argv(argv)[0] == "git"


# ── 2. git reflog ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["git", "reflog", "expire", "--expire=now", "--all"],
    ["git", "reflog", "delete", "HEAD@{0}"],
    ["git", "reflog", "drop", "--all"],
    ["git", "reflog", "-n", "1", "expire"],
])
def test_reflog_subcommands_that_destroy_history_are_refused(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError, match="reflog"):
        tool._validate_argv(argv)


@pytest.mark.parametrize("argv", [
    ["git", "reflog"],
    ["git", "reflog", "-n", "5"],
    ["git", "reflog", "show"],
    ["git", "reflog", "show", "HEAD"],
    ["git", "reflog", "exists", "HEAD"],
])
def test_reflog_reading_still_passes(tool: ShellExecTool, argv: list[str]):
    assert tool._validate_argv(argv)[0] == "git"


# ── 3. read-only commands stay inside the workspace ──────────────────────────

def test_search_does_not_read_a_file_outside_the_workspace(
    tool: ShellExecTool, workspace: Path, tmp_path: Path
):
    outside = tmp_path / "outside.txt"
    outside.write_text("needle-outside\n", encoding="utf-8")
    (workspace / "inside.txt").write_text("needle-inside\n", encoding="utf-8")

    for cmd in ("grep", "findstr"):
        for path in (str(outside), outside.as_posix(), "../outside.txt"):
            with pytest.raises(PermissionError):
                tool.run([cmd, "needle", path])


@pytest.mark.parametrize("argv", [
    ["findstr", "/F:list.txt", "needle"],
    ["findstr", "/G:../patterns.txt", "inside.txt"],
    ["findstr", "/S", "/D:..", "needle", "*.txt"],
    ["grep", "-f", "../patterns.txt", "inside.txt"],
    ["grep", "-e", "needle", "/etc/hostname"],
    ["grep", "--file=../patterns.txt", "inside.txt"],
    ["git", "diff", "../a.txt", "../b.txt"],
    ["git", "blame", "--contents", "/etc/hostname", "a.txt"],
    ["where", "/R", "C:/", "python"],
    ["which", "/usr/bin/python"],
])
def test_outside_references_are_refused(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError):
        tool._validate_argv(argv)


@pytest.mark.parametrize("argv", [
    ["grep", "-n", "/api/v1", "inside.txt"],
    ["grep", "-rn", "-e", "/api/v1", "core"],
    ["grep", "-c", "^", "core/loop.py"],
    ["grep", "-A", "3", "needle", "inside.txt"],
    ["findstr", "/s", "/n", "/c:{{step:", "*.py"],
    ["findstr", "/S", "/N", "/C:core/x", "pattern", "*.py"],
    ["findstr", "2026-08-28T08:31", "logs/daemon_tick.jsonl"],
    ["findstr", "TODO"],
    ["where", "python"],
    ["which", "python"],
])
def test_ordinary_searches_still_pass(tool: ShellExecTool, argv: list[str]):
    assert tool._validate_argv(argv)[1] == argv


def test_an_inside_search_still_runs(tool: ShellExecTool, workspace: Path):
    if shutil.which("grep") is None and shutil.which("findstr") is None:
        pytest.skip("no search binary on PATH")
    (workspace / "inside.txt").write_text("needle-inside\n", encoding="utf-8")

    result = tool.run(["findstr", "needle", "inside.txt"])

    assert result["exit_code"] == 0
    assert "needle-inside" in result["stdout"]


# ── 4. programs come from PATH, never from the workspace ─────────────────────

def _plant(directory: Path, name: str) -> Path:
    if sys.platform == "win32":
        planted = directory / f"{name}.cmd"
        planted.write_text("@echo planted\r\n", encoding="ascii")
    else:
        planted = directory / name
        planted.write_text("#!/bin/sh\necho planted\n", encoding="ascii")
        planted.chmod(0o755)
    return planted


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def test_a_program_in_the_working_folder_is_not_picked_up(
    tool: ShellExecTool, workspace: Path, monkeypatch
):
    if shutil.which("whoami") is None:
        pytest.skip("no whoami on PATH")
    _plant(workspace, "whoami")
    monkeypatch.chdir(workspace)

    found = tool._which("whoami")

    assert found is not None
    assert not _inside(found, workspace)
    assert "planted" not in tool.run(["whoami"])["stdout"]


def test_a_path_entry_inside_the_workspace_is_skipped(
    tool: ShellExecTool, workspace: Path, monkeypatch
):
    bin_dir = workspace / "bin"
    bin_dir.mkdir()
    _plant(bin_dir, "whoami")
    _plant(bin_dir, "onlyplanted")
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(bin_dir), "bin", os.environ.get("PATH", "")])
    )
    monkeypatch.chdir(workspace)

    found = tool._which("whoami")

    assert found is None or not _inside(found, workspace)
    assert tool._which("onlyplanted") is None


def test_the_branch_read_uses_the_resolved_git(tool: ShellExecTool, workspace: Path, monkeypatch):
    _git_repo(workspace)
    expected = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
        cwd=workspace, check=True, capture_output=True, text=True,
    ).stdout.strip()
    _plant(workspace, "git")
    monkeypatch.chdir(workspace)

    assert tool._current_branch() == expected


# ── 5. uv pip read subcommands ───────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["uv", "pip", "list", "--python", "elsewhere"],
    ["uv", "pip", "list", "--python=elsewhere"],
    ["uv", "pip", "show", "--config-file=uv.toml", "cowsay"],
    ["uv", "pip", "freeze", "--target", "dir"],
    ["uv", "pip", "freeze", "--prefix=dir"],
    ["uv", "pip", "tree", "--index-url=http://elsewhere/"],
    ["uv", "pip", "list", "--directory=.."],
    ["uv", "pip", "show", "../pkg"],
    ["uv", "pip", "show", "C:/pkg"],
    ["uv", "pip", "list", "--format=../x"],
    ["uv", "pip", "list", "--outdated"],
])
def test_uv_pip_read_flags_are_allow_listed(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError, match="uv pip"):
        tool._validate_argv(argv)


@pytest.mark.parametrize("argv", [
    ["uv", "pip", "list"],
    ["uv", "pip", "list", "--format=json"],
    ["uv", "pip", "list", "--format", "json"],
    ["uv", "pip", "show", "cowsay"],
    ["uv", "pip", "show", "--files", "cowsay"],
    ["uv", "pip", "freeze", "--exclude-editable"],
    ["uv", "pip", "tree", "--depth=2"],
])
def test_uv_pip_plain_reads_still_pass(tool: ShellExecTool, argv: list[str]):
    assert tool._validate_argv(argv)[0] == "uv"
    assert tool.risk_for({"argv": argv}) == "read_only"
