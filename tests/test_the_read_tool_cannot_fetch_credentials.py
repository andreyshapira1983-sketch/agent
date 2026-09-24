"""The agent's own read tool must not hand it the credential file.

Measured 2026-08-22 before the fix: `FileReadTool.run(path=".env")` returned the
whole file — 2918 characters, eight secret-shaped lines — because the tool's only
boundary is the workspace and `.env` lives at the workspace root.

Why this is the one worth closing first. Two independent 2026 incidents name
this exact shape in their own root-cause lists: Hugging Face's containment
review lists "long-lived credentials stored in environment variables", and the
LiteLLM supply-chain payload paid off precisely because a credential harvester
running in-process finds them. Our own OWASP self-assessment put ASI03
(Identity & Privilege Abuse) as the weakest row. See
`INCIDENT_CATALOGUE_2024_2026.md` in git history.

What was ALREADY closed and is asserted here so it stays closed:
  * `shell_exec` passes an env ALLOWLIST (`_safe_env`: PATH, SystemRoot,
    PATHEXT, git identity) — subprocesses never inherit the keys.
  * `file_write` refuses to write secrets (`contains_secret`).

Scope, so the green does not overclaim: this closes the READ TOOL's path to
credential-shaped files. It does not remove credentials from the process
environment — `os.environ` still holds them and in-process Python still reaches
them. That is the wall-class gap (MIR-120) and needs a different class of fix.
What this removes is the path that needs no compromise at all: simply asking.
"""
from __future__ import annotations

import pytest

from tools.file_read import FileReadTool


def _write(ws, rel: str, text: str = "OPENAI_API_KEY=sk-test-not-a-real-key\n"):
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize("rel", [
    ".env",
    ".env.local",
    ".env.production",
    "credentials",
    "credentials.json",
    "id_rsa",
    "config/service.key",
    "certs/server.pem",
    "secrets/token.txt",
])
def test_a_credential_shaped_file_is_refused(workspace, rel: str) -> None:
    """Every shape the self-apply lane already refuses to WRITE must also be
    refused to READ. The lane's denylist existed; the read tool never consulted
    one."""
    _write(workspace, rel)
    tool = FileReadTool(workspace_root=workspace)
    with pytest.raises(PermissionError) as caught:
        tool.run(path=rel)
    assert "credential" in str(caught.value).lower(), (
        f"{rel} was refused, but not for the credential reason: {caught.value}"
    )


def test_the_refusal_does_not_leak_what_it_refused(workspace) -> None:
    """A refusal message must not carry the secret it just protected."""
    _write(workspace, ".env", "OPENAI_API_KEY=sk-canary-9Q7Z\n")
    tool = FileReadTool(workspace_root=workspace)
    with pytest.raises(PermissionError) as caught:
        tool.run(path=".env")
    assert "sk-canary-9Q7Z" not in str(caught.value)


@pytest.mark.parametrize("rel", [
    "core/loop.py",
    "docs/notes.md",
    "README.md",
    "data/episodic_memory.jsonl",
    "environment.md",          # contains "env" but is not a credential file
    "tests/test_env_probe.py",
])
def test_ordinary_files_still_read(workspace, rel: str) -> None:
    """The control that must stay green: this is a narrow denylist, not a new
    wall around the workspace. A tool that refuses everything is not a fix."""
    _write(workspace, rel, "ordinary content\n")
    tool = FileReadTool(workspace_root=workspace)
    out = tool.run(path=rel)
    assert "ordinary content" in str(out)


def test_the_workspace_escape_refusal_is_unchanged(workspace) -> None:
    """The pre-existing boundary must survive the new one."""
    tool = FileReadTool(workspace_root=workspace)
    with pytest.raises(PermissionError):
        tool.run(path="../../../etc/passwd")


# --- what was already closed, asserted so a later change cannot silently open it

def test_shell_exec_hands_subprocesses_an_env_allowlist_not_the_keys(workspace) -> None:
    """`_safe_env` is an ALLOWLIST (PATH, SystemRoot, PATHEXT, git identity), so
    a new credential variable is excluded by construction rather than by being
    remembered in a blocklist."""
    import os

    from tools.shell_exec import ShellExecTool

    tool = ShellExecTool(workspace_root=workspace)
    os.environ["PROBE_FAKE_SECRET_KEY"] = "sk-must-not-propagate"
    try:
        env = tool._safe_env()
    finally:
        os.environ.pop("PROBE_FAKE_SECRET_KEY", None)

    assert "PROBE_FAKE_SECRET_KEY" not in env
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        assert name not in env, f"{name} would reach a subprocess"
    assert "PATH" in env, "the allowlist must still be usable"
