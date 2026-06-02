"""ASCII-only identifier policy — defence in depth across the stack.

Programming identifiers in this codebase (write paths, shell argv, memory
tags) MUST be ASCII. Human content (file body, memory note body, web
search query, user question) may use any unicode. Read-only `file_read`
may also target user-supplied Unicode filenames inside the workspace.

Tests pin this contract at every layer:
  1. `tools.base.require_ascii_identifier` — the shared utility
  2. `FileWriteTool` / `ShellExecTool` — tool-level guard
     (`FileReadTool` is read-only and allows Unicode workspace filenames)
  3. `LLMPlanner` sanitiser — planner-level guard
  4. `main._parse_remember` — REPL-level guard for memory tags
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.planner import LLMPlanner
from main import _parse_remember
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
    def test_ascii_string_passes_through(self):
        assert require_ascii_identifier("hello.txt", role="path") == "hello.txt"

    def test_ascii_with_punctuation_passes(self):
        v = "sub/dir-1_file.txt"
        assert require_ascii_identifier(v, role="path") == v

    @pytest.mark.parametrize(
        "value",
        ["привет.txt", "café.md", "файл", "naïve.json", "中文.txt", "🎉.txt"],
    )
    def test_non_ascii_raises_permission_error(self, value):
        with pytest.raises(PermissionError, match="ASCII"):
            require_ascii_identifier(value, role="role")

    def test_non_string_raises(self):
        with pytest.raises(PermissionError, match="must be a string"):
            require_ascii_identifier(42, role="x")  # type: ignore[arg-type]

    def test_message_mentions_codepoint(self):
        with pytest.raises(PermissionError) as exc:
            require_ascii_identifier("файл", role="path")
        # The error must tell the user WHICH char tripped it.
        assert "U+" in str(exc.value)
        assert "ASCII" in str(exc.value)


# ============================================================
# Layer 2 — tool-level enforcement
# ============================================================

class TestFileWriteAsciiPath:
    def test_cyrillic_path_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            tool.run(path="привет.txt", content="hello")
        # No file created.
        assert not any(workspace.glob("*.txt"))

    def test_emoji_path_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            tool.run(path="party-🎉.txt", content="hello")

    def test_ascii_path_with_cyrillic_content_allowed(self, workspace: Path):
        """Russian in the body is fine — it's human content, not an identifier."""
        tool = FileWriteTool(workspace_root=workspace)
        out = tool.run(path="hello.txt", content="Привет, мир!")
        assert out["mode"] == "create"
        # File on disk is correct UTF-8.
        target = workspace / "hello.txt"
        assert target.read_text(encoding="utf-8") == "Привет, мир!"

    def test_risk_for_treats_non_ascii_as_irreversible(self, workspace: Path):
        """The path can't be resolved -> conservative fallback risk."""
        tool = FileWriteTool(workspace_root=workspace)
        risk = tool.risk_for({"path": "привет.txt"})
        assert risk == "irreversible"


class TestFileReadAsciiPath:
    def test_cyrillic_path_allowed_for_read_only_workspace_file(self, workspace: Path):
        (workspace / "привет.txt").write_text("x", encoding="utf-8")
        tool = FileReadTool(workspace_root=workspace)
        assert tool.run(path="привет.txt") == "x"

    def test_ascii_path_works(self, workspace: Path):
        (workspace / "hello.txt").write_text("Привет, мир!", encoding="utf-8")
        tool = FileReadTool(workspace_root=workspace)
        assert tool.run(path="hello.txt") == "Привет, мир!"


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

    def test_ascii_argv_passes_validation(self, workspace: Path):
        """`touch sub_dir` is in the whitelist and ASCII — must pass argv
        validation. Whether it succeeds on disk is incidental to this test."""
        tool = ShellExecTool(workspace_root=workspace)
        # Run touch in-process — this just validates argv and returns
        # the compensation plan + exec result.
        out = tool.run(argv=["touch", "ok_name.txt"])
        assert out["exit_code"] == 0
        assert (workspace / "ok_name.txt").exists()


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

    def test_file_read_with_user_supplied_cyrillic_hint_allowed(self, workspace: Path):
        # User-provided --file hints are explicit human input. The planner
        # still cannot invent Unicode paths, but the hinted path may be a
        # real local filename.
        sources, warnings = self._plan(
            workspace, "file_read", {"path": "привет.txt"}, file_hint="привет.txt",
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["path"] == "привет.txt"
        assert not any("ASCII" in w for w in warnings)

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

    def test_file_write_with_cyrillic_content_allowed(self, workspace: Path):
        """Content is human text; cyrillic must NOT be filtered here."""
        sources, _ = self._plan(
            workspace, "file_write", {"path": "hello.txt", "content": "Привет!"}
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["content"] == "Привет!"


# ============================================================
# Layer 4 — REPL `:remember` tag policy
# ============================================================

class TestRememberTagAscii:
    def test_cyrillic_tag_token_is_not_a_tag(self):
        """A first-token like `предпочтение` doesn't match the whitelist
        AND isn't a comma-list, so it stays as content with default tag."""
        tags, content = _parse_remember("предпочтение я люблю краткие ответы")
        assert tags == ["user-approved"]
        assert "предпочтение" in content

    def test_cyrillic_tag_in_comma_list_dropped(self):
        """Commas trigger the tag path. A non-ASCII entry inside the list
        is dropped silently, ASCII entries remain. If nothing survives,
        fallback to default."""
        tags, content = _parse_remember("предпочтение,fact текст заметки")
        assert tags == ["fact"]
        assert content == "текст заметки"

    def test_all_cyrillic_comma_tags_fallback_to_default(self):
        tags, content = _parse_remember("один,два текст")
        assert tags == ["user-approved"]
        assert content == "текст"

    def test_cyrillic_content_with_ascii_tags_preserved(self):
        tags, content = _parse_remember("preference,fact Я предпочитаю краткие ответы")
        assert tags == ["preference", "fact"]
        assert content == "Я предпочитаю краткие ответы"
