"""ASCII-only identifier policy — defence in depth across the stack.

Programming identifiers in this codebase (write paths, shell argv) MUST be
ASCII. Human content (file body, memory note body, web search query, user
question) may use any unicode. Read-only `file_read` may also target
user-supplied Unicode filenames inside the workspace.

Memory tags left this policy on 2026-08-07. A tag is a LABEL, not an
identifier: it never becomes a path, an argv entry or a URL, and it is stored
in JSONL written with `ensure_ascii=False`. Only the reserved words that switch
the write policy are matched by name; everything else is kept as typed, in any
script. See `TestRememberTagsAreLabelsNotIdentifiers` below and
`TestUserLabelsVersusReservedTags` in tests/test_memory_policy.py.

Tests pin this contract at every layer:
  1. `tools.base.require_ascii_identifier` — the shared utility
  2. `FileWriteTool` / `ShellExecTool` — tool-level guard
     (`FileReadTool` is read-only and allows Unicode workspace filenames)
  3. `LLMPlanner` sanitiser — planner-level guard
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.planner import LLMPlanner
from tests.conftest import FakeLLM
from tools.base import ToolRegistry, require_ascii_identifier
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool


def _registry(workspace: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace))
    return reg


# ============================================================
# Layer 1 — the shared utility
# ============================================================

class TestRequireAsciiIdentifier:
    def test_non_string_raises(self):
        with pytest.raises(PermissionError, match="must be a string"):
            require_ascii_identifier(42, role="x")  # type: ignore[arg-type]


# ============================================================
# Layer 2 — tool-level enforcement
# ============================================================


class TestShellExecAsciiArgv:
    @pytest.mark.parametrize(
        "bad_argv",
        [
            ["mkdir", "новая_папка"],
            ["touch", "файл.txt"],
            ["whoami", "—флаг"],  # em-dash flag
            ["whoami", "naïve"],
        ],
    )
    def test_non_ascii_argv_element_rejected(self, workspace: Path, bad_argv):
        tool = ShellExecTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            tool.run(argv=bad_argv)


# ============================================================
# Layer 3 — planner sanitiser drops non-ASCII identifiers
# ============================================================

class TestPlannerSanitizerAscii:
    def _plan(self, workspace: Path, tool: str, args: dict, file_hint=None):
        canned = json.dumps({
            "reasoning": "test",
            "steps": [{"tool": tool, "arguments": args, "rationale": "test"}],
        })
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))
        out = planner.plan(question="test", file_hint=file_hint)
        return out.sources, out.warnings

    def test_file_write_with_cyrillic_path_dropped(self, workspace: Path):
        sources, warnings = self._plan(
            workspace, "file_write", {"path": "привет.txt", "content": "x"}
        )
        assert sources == []
        assert any("not ASCII" in w or "ASCII" in w for w in warnings)


    def test_shell_exec_with_cyrillic_path_dropped(self, workspace: Path):
        sources, warnings = self._plan(
            workspace, "shell_exec", {"argv": ["mkdir", "новая_папка"]}
        )
        assert sources == []
        assert any("ASCII" in w for w in warnings)

    def test_web_search_with_cyrillic_query_allowed(self, workspace: Path):
        """Query is human content; cyrillic must NOT be filtered here."""
        sources, _ = self._plan(
            workspace, "web_search", {"query": "новости погоды", "max_results": 5}
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["query"] == "новости погоды"
