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
    sources, warnings, _dropped = planner._validate_steps(steps, file_hint=hint)
    return sources, warnings


# ============================================================
# Custom allowlist via constructor
# ============================================================

class TestCustomAllowlist:
    def test_custom_paths_accepted(self, workspace: Path):
        p = _planner(workspace, self_documentation_paths=("README.md", "AGENTS.md"))
        assert p.self_documentation_paths == ("README.md", "AGENTS.md")


    def test_readme_allowed_even_when_not_in_custom_allowlist(self, workspace: Path):
        """Regression Bug 6: file_read is allowed without hint regardless
        of the self-documentation allowlist contents."""
        p = _planner(workspace, self_documentation_paths=("AGENTS.md",))
        sources, warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "README.md"}}],
            hint=None,
        )
        assert len(sources) == 1
        assert not any("allowlist" in w for w in warnings)


# ============================================================
# Self-doc reads with hostile paths still rejected by other checks
# ============================================================

class TestSelfDocStillCheckedByOtherRules:
    def test_non_ascii_path_in_allowlisted_position_still_dropped(self, workspace: Path):
        """Even if a caller somehow allowlists a non-ASCII path (they
        can't — the validator filters), a step asking for a non-ASCII
        path stays dropped by the existing ASCII-only check."""
        p = _planner(workspace)
        sources, _warnings = _run(
            p,
            [{"tool": "file_read", "arguments": {"path": "привет.md"}}],
            hint=None,
        )
        # Either the allowlist check or the ASCII check rejects it —
        # both are acceptable. The point: it doesn't sneak through.
        assert sources == []
