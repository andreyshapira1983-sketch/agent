"""shell_exec, second round: searches skip credentials and links, git starts no configured program."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.shell_exec import ShellExecTool

SECRET = "needle-private-line"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "sub" / "a.txt").write_text("needle-inside\n", encoding="utf-8")
    (ws / "inside.txt").write_text("needle-inside\n", encoding="utf-8")
    return ws


@pytest.fixture()
def tool(workspace: Path) -> ShellExecTool:
    return ShellExecTool(workspace_root=workspace, timeout_seconds=30)


def _with_env_file(workspace: Path) -> None:
    (workspace / ".env").write_text(f"{SECRET}\n", encoding="utf-8")


def _link_dir(link: Path, target: Path) -> None:
    """A directory link: a junction on Windows, a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("links cannot be created here")


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],  # noqa: S607
        cwd=workspace, check=True, capture_output=True,
    )


# ── 1. a search does not read credential files ───────────────────────────────

@pytest.mark.parametrize("argv", [
    ["grep", "needle", ".env"],
    ["findstr", "needle", ".env"],
    ["grep", "-e", "needle", ".env.local"],
    ["grep", "needle", "secrets/notes.txt"],
    ["findstr", "needle", "keys/server.pem"],
    ["findstr", "/G:.env", "inside.txt"],
    ["grep", "-f", ".env", "inside.txt"],
    ["grep", "-f.env", "inside.txt"],
    ["grep", "--file=.env", "inside.txt"],
])
def test_a_search_refuses_a_named_credential_file(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError):
        tool._validate_argv(argv)


@pytest.mark.parametrize("argv", [
    ["findstr", "needle", "*"],
    ["findstr", "needle", ".e*"],
    ["findstr", "needle", ".en?"],
    ["grep", "-r", "needle", "."],
    ["grep", "-rn", "needle"],
    ["grep", "--recursive", "needle", "."],
    ["grep", "-d", "recurse", "needle", "."],
    ["findstr", "/s", "needle", "*"],
    ["findstr", "/S", "/N", "needle", "*.*"],
    ["findstr", "/spin", "needle", "*.*"],
])
def test_a_mask_or_a_recursive_search_does_not_sweep_up_a_credential_file(
    tool: ShellExecTool, workspace: Path, argv: list[str]
):
    assert tool._validate_argv(argv)[1] == argv
    _with_env_file(workspace)

    with pytest.raises(PermissionError, match="credential"):
        tool._validate_argv(argv)


def test_a_search_with_a_credential_in_reach_returns_nothing(tool: ShellExecTool, workspace: Path):
    _with_env_file(workspace)

    for argv in (["findstr", "needle", "*"], ["grep", "-r", "needle", "."]):
        with pytest.raises(PermissionError):
            tool.run(argv)


@pytest.mark.parametrize("argv", [
    ["findstr", "/s", "/n", "/c:needle", "*.py"],
    ["findstr", "/s", "needle", "*.txt"],
    ["grep", "-rn", "needle", "sub"],
    ["grep", "-n", "needle", "inside.txt"],
    ["findstr", "needle", "sub/*.txt"],
])
def test_searches_that_cannot_reach_the_credential_still_pass(
    tool: ShellExecTool, workspace: Path, argv: list[str]
):
    _with_env_file(workspace)

    assert tool._validate_argv(argv)[1] == argv


def test_a_mask_reaches_into_a_credential_folder(tool: ShellExecTool, workspace: Path):
    (workspace / "secrets").mkdir()
    (workspace / "secrets" / "tokens.py").write_text(f"{SECRET}\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="credential"):
        tool._validate_argv(["findstr", "/s", "needle", "*.py"])


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 aliases are a Windows thing")
def test_a_mask_that_matches_the_short_alias_is_refused(tool: ShellExecTool, workspace: Path):
    import ctypes

    target = workspace / ".env.production"
    target.write_text(f"{SECRET}\n", encoding="utf-8")
    buf = ctypes.create_unicode_buffer(520)
    ctypes.windll.kernel32.GetShortPathNameW(str(target), buf, 520)
    alias = Path(buf.value).name
    if not alias or alias.lower() == target.name or "." not in alias:
        pytest.skip("this volume keeps no 8.3 aliases")

    with pytest.raises(PermissionError, match="credential"):
        tool._validate_argv(["findstr", "needle", "*." + alias.rsplit(".", 1)[1]])


def test_a_glued_pattern_does_not_hide_the_file_argument(tool: ShellExecTool):
    with pytest.raises(PermissionError):
        tool._validate_argv(["grep", "-eneedle", "../outside.txt"])
    with pytest.raises(PermissionError):
        tool._validate_argv(["grep", "-f../patterns.txt", "inside.txt"])
    argv = ["grep", "-eROOT", "inside.txt"]
    assert tool._validate_argv(argv)[1] == argv


@pytest.mark.skipif(sys.platform != "win32", reason="argument expansion by the MSYS runtime")
@pytest.mark.parametrize("argv", [
    ["grep", "-s", "{needle,.env}"],
    ["grep", "-s", "needle", "sub/{a.txt,../.env}"],
])
def test_grep_arguments_are_not_expanded_into_file_names(
    tool: ShellExecTool, workspace: Path, argv: list[str]
):
    if tool._which("grep") is None:
        pytest.skip("no grep on PATH")
    _with_env_file(workspace)

    assert SECRET not in tool.run(argv)["stdout"]


# ── 2. links met while descending ────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["grep", "-R", "needle", "."],
    ["grep", "-Rn", "needle", "sub"],
    ["grep", "-nR", "-e", "needle", "sub"],
    ["grep", "--dereference-recursive", "needle", "sub"],
    ["grep", "--deref", "needle", "sub"],
])
def test_grep_does_not_follow_links_while_descending(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError, match="link"):
        tool._validate_argv(argv)


@pytest.mark.skipif(sys.platform != "win32", reason="findstr /S walks through junctions")
def test_a_recursive_findstr_does_not_leave_through_a_junction(
    tool: ShellExecTool, workspace: Path, tmp_path: Path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "o.txt").write_text(f"{SECRET}\n", encoding="utf-8")
    _link_dir(workspace / "sub" / "jump", outside)

    with pytest.raises(PermissionError, match="link"):
        tool.run(["findstr", "/s", "needle", "*.txt"])
    assert "needle-inside" in tool.run(["findstr", "needle", "inside.txt"])["stdout"]


# ── 3. git starts no program named in repository config ──────────────────────

def test_a_git_read_does_not_run_a_configured_converter(
    tool: ShellExecTool, workspace: Path, tmp_path: Path
):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    marker = tmp_path / "ran.txt"
    script = tmp_path / "conv.py"
    script.write_text(
        f"open({marker.as_posix()!r}, 'w').close()\n"
        "print('converted')\n",
        encoding="utf-8",
    )
    _git(workspace, "init", "-q")
    (workspace / ".gitattributes").write_text("*.txt diff=conv\n", encoding="utf-8")
    _git(workspace, "add", "inside.txt", ".gitattributes")
    _git(workspace, "commit", "-q", "-m", "base")
    _git(workspace, "config", "diff.conv.textconv",
         f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')
    (workspace / "inside.txt").write_text("needle-changed\n", encoding="utf-8")

    for argv in (["git", "diff"], ["git", "log", "-p", "-n", "1"], ["git", "show", "HEAD"]):
        result = tool.run(argv)
        assert result["exit_code"] == 0, result["stderr"]
        assert result["argv"] == argv

    assert not marker.exists()


@pytest.mark.parametrize(("sub", "expected"), [
    ("diff", ["--no-ext-diff", "--no-textconv"]),
    ("log", ["--no-ext-diff", "--no-textconv"]),
    ("show", ["--no-ext-diff", "--no-textconv"]),
    ("blame", ["--no-textconv"]),
    ("status", []),
])
def test_read_only_git_carries_the_neutralising_options(sub: str, expected: list[str]):
    hardened = ShellExecTool._git_hardened(["git", sub, "HEAD"])

    assert hardened[0] == "git"
    assert hardened[-1] == "HEAD"
    at = hardened.index(sub)
    assert "core.fsmonitor=false" in hardened[:at]
    assert hardened[at + 1:-1] == expected


def test_touch_under_the_git_folder_is_refused(tool: ShellExecTool, workspace: Path):
    (workspace / ".git" / "hooks").mkdir(parents=True)

    with pytest.raises(PermissionError, match=r"\.git"):
        tool.run(["touch", ".git/hooks/pre-commit"])
    assert not (workspace / ".git" / "hooks" / "pre-commit").exists()


# ── 4. short git options with a glued path ───────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["git", "diff", "-O../order.txt"],
    ["git", "log", "-p", "-OC:/order.txt"],
    ["git", "show", "-O/etc/order", "HEAD"],
    ["git", "blame", "-S../revs.txt", "inside.txt"],
    ["git", "ls-files", "-X../exclude.txt"],
])
def test_a_glued_git_path_option_is_confined(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError):
        tool._validate_argv(argv)


@pytest.mark.parametrize("argv", [
    ["git", "diff", "-Oorder.txt"],
    ["git", "log", "-S/api/v1"],
    ["git", "diff", "-U5"],
])
def test_ordinary_glued_git_options_still_pass(tool: ShellExecTool, argv: list[str]):
    assert tool._validate_argv(argv)[1] == argv


# ── 5. hostname only reads ───────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["hostname", "newname"],
    ["hostname", "-F", "inside.txt"],
    ["hostname", "-b", "newname"],
])
def test_hostname_takes_no_arguments(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError, match="hostname"):
        tool._validate_argv(argv)
    assert tool._validate_argv(["hostname"])[0] == "hostname"


# ── 6. the risk class agrees with validation ─────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["git"],
    ["git", "push"],
    ["git", "branch", "-D", "agent/x"],
    ["git", "diff", "--output=out.txt"],
    ["grep", "needle", "../outside.txt"],
    ["findstr", "/F:list.txt", "needle"],
    ["hostname", "newname"],
    ["uv", "pip", "list", "--python=elsewhere"],
    ["whoami", "a;b"],
])
def test_a_refused_argv_is_not_classed_read_only(tool: ShellExecTool, argv: list[str]):
    with pytest.raises(PermissionError):
        tool._validate_argv(argv)

    assert tool.risk_for({"argv": argv}) == "external"


@pytest.mark.parametrize(("argv", "risk"), [
    (["git", "log", "--oneline"], "read_only"),
    (["grep", "-n", "needle", "inside.txt"], "read_only"),
    (["hostname"], "read_only"),
    (["uv", "pip", "list"], "read_only"),
    (["git", "commit", "-m", "x"], "irreversible"),
    (["uv", "pip", "install", "cowsay"], "reversible"),
    (["mkdir", "new"], "irreversible"),
    (["rm", "-rf", "x"], "external"),
])
def test_accepted_forms_keep_their_class(tool: ShellExecTool, argv: list[str], risk: str):
    assert tool.risk_for({"argv": argv}) == risk


# ── 7. the child's PATH has no workspace entry ───────────────────────────────

def test_the_child_path_drops_workspace_entries(
    tool: ShellExecTool, workspace: Path, tmp_path: Path, monkeypatch
):
    inside = workspace / "bin"
    inside.mkdir()
    outside = tmp_path / "tools"
    outside.mkdir()
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(inside), "bin", "", str(workspace), str(outside)])
    )
    monkeypatch.chdir(workspace)

    assert tool._safe_env()["PATH"].split(os.pathsep) == [str(outside)]


# ── 8. uv reads the agent's own environment ──────────────────────────────────

@pytest.mark.parametrize("argv", [
    ["uv", "pip", "list"],
    ["uv", "pip", "show", "cowsay"],
    ["uv", "pip", "freeze"],
    ["uv", "pip", "tree", "--depth=1"],
    ["uv", "pip", "install", "cowsay"],
])
def test_uv_pip_is_pinned_to_the_running_interpreter(
    tool: ShellExecTool, monkeypatch, argv: list[str]
):
    seen: list[list[str]] = []
    monkeypatch.setattr(
        tool, "_run_subprocess", lambda cmd, run_argv, plan: seen.append(run_argv) or {}
    )

    tool.run(argv)

    assert seen == [[*argv[:3], "--python", sys.executable, *argv[3:]]]
