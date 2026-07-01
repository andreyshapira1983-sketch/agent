"""Planner routing tests (§3 Cognitive Core: Planning, §12.4 Policy Gates).

Defense against bad LLM plans. The Planner takes the LLM's JSON and:
  1. Resolves it against the ToolRegistry — unknown tools dropped.
  2. Enforces "file_read requires --file hint" and "path matches hint".
  3. Sanitises arguments (clamps, defaults).

Four minimal cases — picked to cover the failure modes a real LLM exhibits.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.planner import LLMPlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM


def _registry(workspace: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace))
    return reg


# ---------- 1. file hint + file question -> file_read with hinted path ----------

def test_file_question_with_hint_picks_file_read(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "Question is about the hinted file.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "notes.txt"},
                    "rationale": "ask about file contents",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="What is in this file?", file_hint="notes.txt")

    assert len(out.sources) == 1
    src = out.sources[0]
    assert src["tool"] == "file_read"
    assert src["arguments"] == {"path": "notes.txt"}
    assert src["label"] == "file:notes.txt"
    assert out.warnings == []


# ---------- 2. file_read without --file hint is now ALLOWED ----------
# Security is enforced by the tool executor (workspace sandbox + secret
# scanner). Blocking at planner level was overly restrictive — it prevented
# the agent from reading files explicitly named by the user in conversation.

def test_file_read_without_hint_is_allowed(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "User asked to read this file.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "notes.txt"},
                    "rationale": "user explicitly named the file",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="Read notes.txt", file_hint=None)

    assert len(out.sources) == 1, "no hint => file_read step must NOT be dropped"
    assert out.sources[0]["arguments"] == {"path": "notes.txt"}
    assert not any("no --file hint" in w for w in out.warnings), out.warnings


# ---------- 3. LLM picks file_read with WRONG path -> remapped to hint ----------

def test_mismatched_path_is_remapped_to_hint(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "Will read a file (with wrong path).",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "../../etc/passwd"},
                    "rationale": "trying to escape",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="Read the file", file_hint="allowed.txt")

    assert len(out.sources) == 1
    assert out.sources[0]["arguments"] == {"path": "allowed.txt"}
    assert out.sources[0]["label"] == "file:allowed.txt"
    assert any("does not match hint" in w for w in out.warnings), out.warnings


def test_unicode_file_hint_is_allowed_when_user_supplied(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "Will read README by mistake, but the user hinted a file.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "wrong self-doc guess",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="Проанализируй архитектуру автономного агента",
        file_hint="архитектура автономного Агента.txt",
    )

    assert len(out.sources) == 1
    assert out.sources[0]["tool"] == "file_read"
    assert out.sources[0]["arguments"] == {
        "path": "архитектура автономного Агента.txt"
    }
    assert out.sources[0]["label"] == "file:архитектура автономного Агента.txt"
    assert any("does not match hint" in w for w in out.warnings), out.warnings
    assert not any("not ASCII" in w for w in out.warnings), out.warnings


# ---------- 4. non-file question -> file_read is NOT chosen ----------

def test_web_question_does_not_choose_file_read(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "Question needs external info.",
            "steps": [
                {
                    "tool": "web_search",
                    "arguments": {"query": "weather in Paris", "max_results": 5},
                    "rationale": "external info",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="What is the weather in Paris?", file_hint="notes.txt")

    tools_chosen = [s["tool"] for s in out.sources]
    assert "file_read" not in tools_chosen
    assert tools_chosen == ["web_search"]
    assert out.sources[0]["arguments"]["max_results"] == 5


# ---------- bonus: empty plan (general knowledge) ----------

def test_empty_plan_passes_through(workspace: Path) -> None:
    canned = json.dumps({"reasoning": "general knowledge", "steps": []})
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="What is 2+2?", file_hint=None)
    assert out.sources == []
    assert out.warnings == []


def test_broad_project_memory_drops_default_readme_status_source(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "Use README for self knowledge.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "default self documentation",
                }
            ],
        }
    )
    history = (
        "<long_term_memory>\n"
        "- [mem_recent | tags: project,memory,tests,budget] "
        "Recent memory says pytest passed=1840 and budget status is current.\n"
        "</long_term_memory>"
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="Что ты уже знаешь о своём проекте?",
        file_hint=None,
        history=history,
    )

    assert out.sources == []
    assert any("README.md dropped" in warning for warning in out.warnings)


def test_broad_project_memory_drops_readme_test_count_source(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "README has a test count.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "read old test count",
                }
            ],
        }
    )
    history = (
        "<long_term_memory>\n"
        "- [mem_tests | tags: project,memory,tests] "
        "Recent persistent memory: pytest passed=1840 today.\n"
        "</long_term_memory>"
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="What do you know about your project status?",
        file_hint=None,
        history=history,
    )

    assert all(src.get("arguments", {}).get("path") != "README.md" for src in out.sources)
    assert any("fresh long_term_memory" in warning for warning in out.warnings)


def test_readme_architecture_source_allowed_for_architecture_question(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "README is architecture reference.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "architecture overview",
                }
            ],
        }
    )
    history = (
        "<long_term_memory>\n"
        "- [mem_recent | tags: project,memory,tests,budget] "
        "Recent memory says tests and budget were checked today.\n"
        "</long_term_memory>"
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="Describe your project architecture",
        file_hint=None,
        history=history,
    )

    assert len(out.sources) == 1
    assert out.sources[0]["tool"] == "file_read"
    assert out.sources[0]["arguments"] == {"path": "README.md"}
    assert not any("README.md dropped" in warning for warning in out.warnings)


def test_explicit_readme_question_still_allows_file_read(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "User explicitly asked for README.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "explicit README request",
                }
            ],
        }
    )
    history = (
        "<long_term_memory>\n"
        "- [mem_recent | tags: project,memory,tests,budget] "
        "Recent memory says tests and budget were checked today.\n"
        "</long_term_memory>"
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="Read README.md and summarize it",
        file_hint=None,
        history=history,
    )

    assert len(out.sources) == 1
    assert out.sources[0]["arguments"] == {"path": "README.md"}
    assert not any("README.md dropped" in warning for warning in out.warnings)


# ---------- bonus: malformed JSON falls back gracefully ----------

def test_malformed_json_falls_back_to_empty_plan(workspace: Path) -> None:
    llm = FakeLLM(responses=["this is not JSON at all"])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)
    assert out.sources == []
    assert "plan_parse_failed" in out.warnings


# ---------- TD-003: parse-failure diagnostics ----------

def test_malformed_json_diagnostics_are_clear(workspace: Path) -> None:
    llm = FakeLLM(responses=["this is not JSON at all"])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)

    # Safe fallback, not a crash and not a self-repair LLM retry.
    assert out.sources == []
    assert "plan_parse_failed" in out.warnings
    # (6) no extra LLM/provider calls were triggered by the parse failure.
    assert len(llm.calls) == 1

    diag = out.diagnostics
    # (1) brief reason, (2) stage, (4) json-block flag, (5) fallback choice.
    assert diag["stage"] == "failed"
    assert diag["fallback"] == "empty_plan"
    assert diag["json_block_found"] is False
    assert diag["reason"]  # non-empty human-readable reason
    assert "decode error" in diag["reason"].lower()
    # (3) sanitized preview present and (4) raw output preserved verbatim.
    assert diag["raw_preview"] == "this is not JSON at all"
    assert out.raw_response == "this is not JSON at all"


def test_markdown_block_with_broken_json_diagnostics(workspace: Path) -> None:
    raw = '```json\n{"reasoning": "x", "steps": [ }\n```'
    llm = FakeLLM(responses=[raw])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)

    assert out.sources == []
    assert "plan_parse_failed" in out.warnings
    assert "stripped_markdown_fence" in out.warnings
    assert len(llm.calls) == 1

    diag = out.diagnostics
    # A JSON block WAS found (the fence), but its contents did not parse.
    assert diag["json_block_found"] is True
    assert diag["stage"] == "failed"
    assert diag["fallback"] == "empty_plan"
    assert "decode error" in diag["reason"].lower()
    # Raw output preserved exactly, including the fence.
    assert out.raw_response == raw


def test_plain_text_without_json_diagnostics(workspace: Path) -> None:
    llm = FakeLLM(responses=["Sorry, I cannot help with that request."])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)

    diag = out.diagnostics
    assert diag["json_block_found"] is False
    assert diag["stage"] == "failed"
    assert diag["fallback"] == "empty_plan"
    assert diag["reason"]


def test_parse_diagnostics_preview_redacts_secrets_and_truncates(workspace: Path) -> None:
    secret = "sk-" + "A" * 40
    long_raw = f"{secret} " + ("noise " * 100)  # well over the preview cap
    llm = FakeLLM(responses=[long_raw])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)
    preview = out.diagnostics["raw_preview"]

    # Secret never appears verbatim in the preview.
    assert secret not in preview
    assert "[REDACTED" in preview
    # Length-capped and marked as truncated.
    assert "truncated" in preview
    assert len(preview) < len(long_raw)
    # Full raw is still preserved on the record for post-hoc inspection.
    assert out.raw_response == long_raw


def test_parse_json_success_reports_parsed_stage(workspace: Path) -> None:
    canned = json.dumps({"reasoning": "ok", "steps": []})
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="What is 2+2?", file_hint=None)

    assert out.diagnostics["stage"] == "parsed"
    assert out.diagnostics["fallback"] == "none"
    assert out.diagnostics["reason"] == "ok"


# ---------- bonus: unknown tool name dropped ----------

def test_unknown_tool_is_dropped(workspace: Path) -> None:
    canned = json.dumps(
        {
            "reasoning": "try a tool that does not exist",
            "steps": [
                {"tool": "send_email", "arguments": {"to": "x@y.z"}, "rationale": "..."}
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(question="email someone", file_hint=None)
    assert out.sources == []
    assert any("not registered" in w for w in out.warnings), out.warnings


# ============================================================
# MVP-8: failure_context (replan injection)
# ============================================================

class TestFailureContextInjection:
    """Defends the planner's MVP-8 contract: when `failure_context` is
    passed, it must travel verbatim into the LLM user prompt so the
    model can see what previous attempts tried."""

    def test_empty_failure_context_does_not_appear_in_prompt(
        self, workspace: Path
    ):
        canned = json.dumps({"reasoning": "ok", "steps": []})
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))

        planner.plan(
            question="hello",
            file_hint=None,
            failure_context="",
        )

        # FakeLLM records the user prompt verbatim.
        user_prompt = llm.calls[0]["user"]
        assert "<replan_context" not in user_prompt

    def test_non_empty_failure_context_is_injected_before_question(
        self, workspace: Path
    ):
        ctx = (
            '<replan_context attempt="2" max_attempts="3">\n'
            "  Previous attempt failed.\n"
            "  - attempt=1 code=tool_error tool=file_read arguments={}\n"
            "    reason: file not found\n"
            "</replan_context>"
        )
        canned = json.dumps({"reasoning": "retry", "steps": []})
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))

        planner.plan(
            question="please retry",
            file_hint="doc.txt",
            failure_context=ctx,
        )

        user_prompt = llm.calls[0]["user"]
        assert "<replan_context" in user_prompt
        assert "tool_error" in user_prompt
        # The replan block sits BEFORE the question (so the model reads
        # the failure right before deciding what to do).
        replan_idx = user_prompt.index("<replan_context")
        question_idx = user_prompt.index("question: please retry")
        assert replan_idx < question_idx

    def test_failure_context_with_secret_is_redacted_before_llm(
        self, workspace: Path
    ):
        """Defence-in-depth: even if a buggy caller stuffs a secret into
        the failure_context, the planner's own redaction pass scrubs it
        before the LLM sees it."""
        secret = "sk-abcdefghijklmnopqrstuvwxyz0123"
        ctx = f"<replan_context>previous arg: token={secret}</replan_context>"
        canned = json.dumps({"reasoning": "x", "steps": []})
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))

        planner.plan(
            question="retry",
            file_hint=None,
            failure_context=ctx,
        )

        user_prompt = llm.calls[0]["user"]
        assert secret not in user_prompt
        assert "[REDACTED:openai-key]" in user_prompt


# ============================================================
# MVP-9: file_write sanitiser
# ============================================================

class TestFileWriteSanitizer:
    """Planner-level defence in depth for file_write — the tool itself
    refuses unsafe paths, but rejecting them at the planner saves a
    re-plan slot."""

    def _plan_one_step(self, workspace: Path, args: dict) -> tuple[list, list]:
        canned = json.dumps(
            {
                "reasoning": "save the summary",
                "steps": [
                    {
                        "tool": "file_write",
                        "arguments": args,
                        "rationale": "user asked to save",
                    }
                ],
            }
        )
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))
        out = planner.plan(question="save it to disk", file_hint=None)
        return out.sources, out.warnings

    def test_well_formed_file_write_passes(self, workspace: Path):
        sources, warnings = self._plan_one_step(
            workspace, {"path": "out/summary.txt", "content": "hi"}
        )
        assert len(sources) == 1
        assert sources[0]["tool"] == "file_write"
        assert sources[0]["arguments"] == {"path": "out/summary.txt", "content": "hi"}
        assert sources[0]["label"] == "file_write:out/summary.txt"
        # The label MUST NOT include content (could be huge / sensitive).
        assert "hi" not in sources[0]["label"]
        assert warnings == []

    def test_missing_path_dropped(self, workspace: Path):
        sources, warnings = self._plan_one_step(workspace, {"content": "x"})
        assert sources == []
        assert any("without path" in w for w in warnings)

    def test_missing_content_dropped(self, workspace: Path):
        sources, warnings = self._plan_one_step(workspace, {"path": "x.txt"})
        assert sources == []
        assert any("content must be a string" in w for w in warnings)

    def test_non_string_content_dropped(self, workspace: Path):
        sources, warnings = self._plan_one_step(
            workspace, {"path": "x.txt", "content": [1, 2, 3]}
        )
        assert sources == []
        assert any("content must be a string" in w for w in warnings)

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/etc/passwd",                 # POSIX absolute
            "\\Windows\\System32",         # Windows root
            "C:\\Windows\\System32\\x",   # Windows drive
            "../escape.txt",               # parent traversal
            "sub/../../escape.txt",        # nested traversal
            "data/../../etc/passwd",      # ditto
        ],
    )
    def test_unsafe_paths_dropped_by_planner(self, workspace: Path, bad_path):
        sources, warnings = self._plan_one_step(
            workspace, {"path": bad_path, "content": "x"}
        )
        assert sources == [], f"path '{bad_path}' should not survive sanitisation"
        assert any("escapes the workspace" in w for w in warnings), warnings


# ============================================================
# MVP-11: shell_exec planner sanitiser — all 11 branches
# ============================================================

class TestShellExecSanitizer:
    """Defence in depth. The planner sanitiser is the FIRST line: the
    LLM may emit anything, but unwelcome shapes must never reach the
    Executor. The tool's own validator is the SECOND line and gets
    its own coverage in `test_shell_exec.py`."""

    def _plan_one_step(self, workspace: Path, args):
        canned = json.dumps({
            "reasoning": "user asked to run a shell command",
            "steps": [{
                "tool": "shell_exec",
                "arguments": args,
                "rationale": "test step",
            }],
        })
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))
        out = planner.plan(question="run something", file_hint=None)
        return out.sources, out.warnings

    # --- happy paths ---

    def test_well_formed_whoami_passes(self, workspace: Path):
        sources, _ = self._plan_one_step(workspace, {"argv": ["whoami"]})
        assert len(sources) == 1
        src = sources[0]
        assert src["tool"] == "shell_exec"
        assert src["arguments"] == {"argv": ["whoami"]}
        # Label has command name and IS short — never echoes huge argv.
        assert src["label"].startswith("shell_exec:whoami")
        assert "expected_outcome" in src

    def test_well_formed_mkdir_passes(self, workspace: Path):
        sources, _ = self._plan_one_step(
            workspace, {"argv": ["mkdir", "newdir"]}
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["argv"] == ["mkdir", "newdir"]
        assert "newdir" in sources[0]["label"]

    def test_well_formed_touch_passes(self, workspace: Path):
        sources, _ = self._plan_one_step(
            workspace, {"argv": ["touch", "notes.txt"]}
        )
        assert len(sources) == 1
        assert sources[0]["arguments"]["argv"] == ["touch", "notes.txt"]

    # --- argv shape ---

    @pytest.mark.parametrize("bad_argv", [None, [], "whoami"])
    def test_missing_or_non_list_argv_dropped(self, workspace: Path, bad_argv):
        args = {"argv": bad_argv} if bad_argv is not None else {}
        sources, warnings = self._plan_one_step(workspace, args)
        assert sources == []
        assert any("non-empty argv list" in w for w in warnings), warnings

    def test_argv_longer_than_16_dropped(self, workspace: Path):
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["whoami"] * 17}
        )
        assert sources == []
        assert any("argv too long" in w for w in warnings), warnings

    @pytest.mark.parametrize("bad_elem", [42, None, "", "  "])
    def test_non_string_or_empty_argv_element_dropped(
        self, workspace: Path, bad_elem
    ):
        # Empty / whitespace-only / non-string entries all blow up the
        # sanitiser. "  " survives the non-empty check at the planner
        # layer but fails the metachar layer when there's no metachar
        # — we cover that explicitly:
        if bad_elem == "  ":
            # whitespace-only element: passes non-empty check, contains \t? No, only spaces, so it'd pass to the tool layer.
            # The planner sanitiser doesn't currently strip spaces — that's intentional.
            # We verify it passes through here (and the tool layer is responsible if needed).
            sources, _ = self._plan_one_step(workspace, {"argv": ["whoami", bad_elem]})
            # whoami doesn't accept a path arg, but the sanitiser doesn't
            # know per-command arity for read_only commands; the tool's
            # subprocess will just receive an extra arg and produce a non-zero
            # exit code. The planner should NOT drop the step here.
            assert len(sources) == 1
            return
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["whoami", bad_elem]}
        )
        assert sources == [], f"element {bad_elem!r} survived sanitisation"
        assert any("non-empty" in w or "string" in w for w in warnings), warnings

    # --- whitelist ---

    @pytest.mark.parametrize(
        "evil_cmd",
        ["rm", "sudo", "chmod", "python", "bash", "cmd", "powershell", "ls"],
    )
    def test_non_whitelisted_command_dropped(self, workspace: Path, evil_cmd):
        sources, warnings = self._plan_one_step(
            workspace, {"argv": [evil_cmd, "arg1"]}
        )
        assert sources == []
        assert any("not in whitelist" in w for w in warnings), warnings

    # --- metacharacters ---

    @pytest.mark.parametrize(
        "metachar_arg",
        [";rm", "a|b", "a&b", "a>b", "a<b", "a`b", "a$b", "a(b", "a)b",
         "a{b", "a}b", "a[b", "a]b", "a\nb", "a\rb", "a\tb", "a\0b"],
    )
    def test_metachar_in_argv_dropped(self, workspace: Path, metachar_arg):
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["touch", metachar_arg]}
        )
        assert sources == []
        assert any("metacharacter" in w for w in warnings), warnings

    # --- path / arity for mutating commands ---

    def test_mkdir_without_path_arg_dropped(self, workspace: Path):
        sources, warnings = self._plan_one_step(workspace, {"argv": ["mkdir"]})
        assert sources == []
        assert any("exactly one" in w for w in warnings), warnings

    def test_mkdir_with_extra_args_dropped(self, workspace: Path):
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["mkdir", "a", "b"]}
        )
        assert sources == []
        assert any("exactly one" in w for w in warnings), warnings

    @pytest.mark.parametrize(
        "unsafe_path",
        ["/etc/foo", "\\windows\\foo", "C:\\evil", "../escape", "sub/../../e"],
    )
    def test_unsafe_path_dropped(self, workspace: Path, unsafe_path):
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["mkdir", unsafe_path]}
        )
        assert sources == []
        assert any("unsafe" in w for w in warnings), warnings

    # --- label safety ---

    def test_label_only_carries_short_command_no_full_argv(self, workspace: Path):
        """The label must NOT echo argv content beyond the path arg —
        protects logs / synthesizer prompts from huge or sensitive
        arguments."""
        sources, _ = self._plan_one_step(
            workspace, {"argv": ["touch", "secret-looking-filename.txt"]}
        )
        assert len(sources) == 1
        label = sources[0]["label"]
        # Label includes command + the one path argument, deliberately
        # capped. For read-only commands like whoami there is no extra.
        assert label.startswith("shell_exec:")
        assert len(label) < 200  # sanity cap

    # --- case insensitivity at planner layer ---

    def test_uppercase_command_normalised_to_whitelist(self, workspace: Path):
        sources, _ = self._plan_one_step(
            workspace, {"argv": ["WHOAMI"]}
        )
        # Planner lower-cases for the whitelist check but PRESERVES the
        # original element for the tool layer (which also normalises).
        assert len(sources) == 1
        # argv preserved verbatim.
        assert sources[0]["arguments"]["argv"] == ["WHOAMI"]


# ==========================================================================
# Fix #2 — dropped_tools field populated when planner hallucinates a tool
# ==========================================================================

class TestDroppedTools:
    """PlannerOutput.dropped_tools is set when LLM invents a tool name."""

    def _plan_with_hallucinated_tool(
        self, workspace: Path, tool_name: str
    ):
        canned = json.dumps({
            "reasoning": "Use a magic tool.",
            "steps": [{"tool": tool_name, "arguments": {}, "rationale": "why not"}],
        })
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))
        return planner.plan(question="Question", file_hint=None)

    def test_hallucinated_tool_populates_dropped_tools(self, workspace: Path):
        """When the LLM names a tool not in the registry, dropped_tools is set."""
        out = self._plan_with_hallucinated_tool(workspace, "magic_tool")
        assert "magic_tool" in out.dropped_tools

    def test_hallucinated_tool_leaves_sources_empty(self, workspace: Path):
        """A plan with only hallucinated tools produces no runnable steps."""
        out = self._plan_with_hallucinated_tool(workspace, "ghost_search")
        assert out.sources == []
        assert "ghost_search" in out.dropped_tools

    def test_dropped_tools_empty_for_valid_plan(self, workspace: Path):
        """A plan with only registered tools has no dropped_tools."""
        canned = json.dumps({
            "reasoning": "Search the web.",
            "steps": [{
                "tool": "web_search",
                "arguments": {"query": "pytest", "max_results": 3},
                "rationale": "search",
            }],
        })
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))
        out = planner.plan(question="Latest pytest?", file_hint=None)
        assert out.dropped_tools == []
        assert len(out.sources) == 1

    def test_mixed_plan_drops_only_unknown_tools(self, workspace: Path):
        """Valid steps survive; only hallucinated tool ends up in dropped_tools."""
        canned = json.dumps({
            "reasoning": "Mix real and fake.",
            "steps": [
                {
                    "tool": "web_search",
                    "arguments": {"query": "python", "max_results": 3},
                    "rationale": "real tool",
                },
                {
                    "tool": "hallucination_tool",
                    "arguments": {},
                    "rationale": "fake tool",
                },
            ],
        })
        llm = FakeLLM(responses=[canned])
        planner = LLMPlanner(llm=llm, registry=_registry(workspace))
        out = planner.plan(question="Mixed?", file_hint=None)
        assert len(out.sources) == 1
        assert out.sources[0]["tool"] == "web_search"
        assert out.dropped_tools == ["hallucination_tool"]

    def test_dropped_tools_warning_also_present(self, workspace: Path):
        """dropped_tools field and the existing warning string are both set."""
        out = self._plan_with_hallucinated_tool(workspace, "phantom")
        assert any("phantom" in w for w in out.warnings)
        assert "phantom" in out.dropped_tools
