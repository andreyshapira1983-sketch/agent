"""MVP-14.4.x — planner's self-documentation allowlist.

`file_read` USED to require a `--file hint` from the user. That made
introspective questions like "what do you understand about yourself?"
impossible to answer with verified evidence: planner had no way to
reach for README.md, so it called weak tools (read_logs, shell_exec)
or fell back to LLM-prior knowledge.

This module pins the narrow exception we added:

  * `file_read README.md` is allowed even without `--file hint`;
  * any OTHER path without a hint still gets dropped;
  * the allowlist is overridable via the constructor with strict
    validation (no absolute paths, no traversal, ASCII only).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.planner import LLMPlanner
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool


def _planner(
    workspace: Path,
    self_documentation_paths: tuple[str, ...] | None = None,
) -> LLMPlanner:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace))
    reg.register(RunTestsTool(workspace_root=workspace))
    reg.register(ReadLogsTool(workspace_root=workspace))
    reg.register(DiffFileTool(workspace_root=workspace))
    reg.register(WebFetchTool())

    class _StubLLM:
        def complete(self, **_kw):
            raise AssertionError("LLM must not be called")

    if self_documentation_paths is None:
        return LLMPlanner(llm=_StubLLM(), registry=reg)
    return LLMPlanner(
        llm=_StubLLM(),
        registry=reg,
        self_documentation_paths=self_documentation_paths,
    )


def _run(planner: LLMPlanner, steps: list[dict[str, Any]], hint: str | None):
    return planner._validate_steps(steps, file_hint=hint)


# ============================================================
# Default allowlist
# ============================================================

class TestDefaultAllowlist:
    def test_default_contains_readme(self, workspace: Path):
        p = _planner(workspace)
        assert "README.md" in p.self_documentation_paths

    def test_readme_passes_without_hint(self, workspace: Path):
        p = _planner(workspace)
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "README.md"}}],
            hint=None,
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["path"] == "README.md"
        # No "no --file hint" warning was emitted.
        assert not any("no --file hint" in w for w in warnings)

    def test_non_allowlisted_path_dropped_without_hint(self, workspace: Path):
        p = _planner(workspace)
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "core/loop.py"}}],
            hint=None,
        )
        assert sources == []
        assert any("self-documentation allowlist" in w for w in warnings)
        # Helpful: warning lists the actual allowlist so the planner
        # learns what it can read.
        assert any("README.md" in w for w in warnings)

    def test_readme_still_passes_with_matching_hint(self, workspace: Path):
        p = _planner(workspace)
        sources, _ = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "README.md"}}],
            hint="README.md",
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["path"] == "README.md"

    def test_hint_mismatch_remaps_to_hint(self, workspace: Path):
        """The pre-MVP-14 behaviour is preserved: when a hint IS
        provided, only that exact path is allowed. The allowlist
        does NOT override an explicit hint."""
        p = _planner(workspace)
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "README.md"}}],
            hint="doc.txt",
        )
        # Remapped to hinted path (not to README.md).
        assert len(sources) == 1
        assert sources[0]["arguments"]["path"] == "doc.txt"
        assert any("does not match hint" in w for w in warnings)


# ============================================================
# Custom allowlist via constructor
# ============================================================

class TestCustomAllowlist:
    def test_custom_paths_accepted(self, workspace: Path):
        p = _planner(workspace, self_documentation_paths=("README.md", "AGENTS.md"))
        assert p.self_documentation_paths == ("README.md", "AGENTS.md")

    def test_agents_md_now_passes_without_hint(self, workspace: Path):
        p = _planner(workspace, self_documentation_paths=("AGENTS.md",))
        sources, _ = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "AGENTS.md"}}],
            hint=None,
        )
        assert len(sources) == 1

    def test_readme_dropped_when_not_in_custom_allowlist(self, workspace: Path):
        p = _planner(workspace, self_documentation_paths=("AGENTS.md",))
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "README.md"}}],
            hint=None,
        )
        assert sources == []
        assert any("allowlist" in w for w in warnings)


# ============================================================
# Constructor validation: hostile inputs are silently filtered
# ============================================================

class TestAllowlistValidation:
    def test_traversal_filtered(self, workspace: Path):
        p = _planner(
            workspace,
            self_documentation_paths=("README.md", "../etc/passwd"),
        )
        assert "README.md" in p.self_documentation_paths
        assert "../etc/passwd" not in p.self_documentation_paths

    def test_absolute_path_filtered(self, workspace: Path):
        p = _planner(
            workspace,
            self_documentation_paths=("README.md", "/etc/passwd", "\\Windows\\System32"),
        )
        assert "README.md" in p.self_documentation_paths
        assert "/etc/passwd" not in p.self_documentation_paths
        assert "\\Windows\\System32" not in p.self_documentation_paths

    def test_drive_letter_filtered(self, workspace: Path):
        p = _planner(
            workspace,
            self_documentation_paths=("README.md", "C:\\Windows\\notepad.exe"),
        )
        assert "C:\\Windows\\notepad.exe" not in p.self_documentation_paths

    def test_non_ascii_filtered(self, workspace: Path):
        p = _planner(
            workspace,
            self_documentation_paths=("README.md", "архитектура.txt"),
        )
        assert "README.md" in p.self_documentation_paths
        assert "архитектура.txt" not in p.self_documentation_paths

    def test_empty_and_non_string_filtered(self, workspace: Path):
        p = _planner(
            workspace,
            self_documentation_paths=(
                "README.md", "", "   ", None, 42, "valid.md"
            ),  # type: ignore[arg-type]
        )
        assert "README.md" in p.self_documentation_paths
        assert "valid.md" in p.self_documentation_paths
        assert "" not in p.self_documentation_paths
        assert "   " not in p.self_documentation_paths

    def test_all_invalid_yields_empty_allowlist(self, workspace: Path):
        """If the caller passes ONLY garbage, the allowlist is empty —
        the safe path. The default tuple is NOT silently restored."""
        p = _planner(
            workspace,
            self_documentation_paths=("../bad", "/abs", "non\u00e1scii"),
        )
        assert p.self_documentation_paths == ()
        # And now even README.md gets dropped without a hint, because
        # the allowlist is empty:
        sources, _ = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "README.md"}}],
            hint=None,
        )
        assert sources == []


# ============================================================
# Self-doc reads with hostile paths still rejected by other checks
# ============================================================

class TestSelfDocStillCheckedByOtherRules:
    def test_non_ascii_path_in_allowlisted_position_still_dropped(self, workspace: Path):
        """Even if a caller somehow allowlists a non-ASCII path (they
        can't — the validator filters), a step asking for a non-ASCII
        path stays dropped by the existing ASCII-only check."""
        p = _planner(workspace)
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "привет.md"}}],
            hint=None,
        )
        # Either the allowlist check or the ASCII check rejects it —
        # both are acceptable. The point: it doesn't sneak through.
        assert sources == []

    def test_empty_string_path_dropped(self, workspace: Path):
        p = _planner(workspace)
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": ""}}],
            hint=None,
        )
        assert sources == []
        assert any("without path" in w for w in warnings)
