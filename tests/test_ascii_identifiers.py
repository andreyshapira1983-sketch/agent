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

from cli.parsers import _parse_remember
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

class TestRememberTagsAreLabelsNotIdentifiers:
    """A memory tag is a LABEL the operator writes, not a programming
    identifier — so the ASCII rule that governs paths, argv and URLs does not
    reach it. Tags are never used as a filename, an argument or a URL; they are
    stored in JSONL written with `ensure_ascii=False` and compared as strings.

    What stays reserved is a short set of WORDS that switch the write policy
    (`preference`, `fact`, … and the blocking `transient`, `temporary`, …).
    Those are rule names, matched exactly (case-insensitively, as the policy
    lowercases before comparing). Everything else the operator types is kept
    verbatim and simply matches no rule.

    Until 2026-08-07 a non-ASCII tag was dropped in silence and replaced with
    `user-approved`, so a Russian label became a consent tag the operator never
    typed.
    """

    def test_a_cyrillic_tag_is_kept_verbatim(self):
        tags, content = _parse_remember("важное,факт текст заметки")
        assert tags == ["важное", "факт"]
        assert content == "текст заметки"

    def test_mixed_unicode_labels_are_kept(self):
        tags, content = _parse_remember("проект-α,naïve,数据 тело")
        assert tags == ["проект-α", "naïve", "数据"]
        assert content == "тело"

    def test_a_reserved_word_is_still_recognised_beside_a_label(self):
        tags, content = _parse_remember("важное,fact текст")
        assert tags == ["важное", "fact"]
        assert content == "текст"

    def test_a_single_cyrillic_first_token_is_still_content(self):
        """No comma and no reserved word: nothing here says "these are tags"."""
        tags, content = _parse_remember("предпочтение я люблю краткие ответы")
        assert tags == ["user-approved"]
        assert content == "предпочтение я люблю краткие ответы"

    def test_the_silent_fallback_to_user_approved_is_gone(self):
        """The regression this class exists for: labels are not consent."""
        tags, _ = _parse_remember("один,два текст")
        assert tags == ["один", "два"]
        assert "user-approved" not in tags

    def test_cyrillic_content_with_reserved_tags_preserved(self):
        tags, content = _parse_remember("preference,fact Я предпочитаю краткие ответы")
        assert tags == ["preference", "fact"]
        assert content == "Я предпочитаю краткие ответы"
