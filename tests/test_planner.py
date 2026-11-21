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

import core.planner as planner_module
from core.doc_routing import (
    _ensure_memory_governance_docs_first,
    _ensure_self_repair_doctrine_docs_first,
    _ensure_subagent_governance_docs_first,
    _norm_source_path,
)
from core.planner import LLMPlanner
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.list_dir import ListDirTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool


def _registry(workspace: Path) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(ListDirTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace))
    return reg


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


def test_corporate_doctrine_question_forces_docs_first_over_core_defaults(
    workspace: Path,
) -> None:
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "Mistakenly use central mechanics for doctrine.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "generic project overview",
                },
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/planner.py"},
                    "rationale": "planner mechanics",
                },
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/loop.py"},
                    "rationale": "loop mechanics",
                },
                {
                    "tool": "file_read",
                    "arguments": {"path": "knowledge/doctrine/ROADMAP.md"},
                    "rationale": "one relevant doc",
                },
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question=(
            "Explain the corporate model, central agent governance, subagents, "
            "self-build, night observation, and safe autonomy."
        ),
        file_hint=None,
    )

    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert paths == [
        "knowledge/doctrine/future/CORPORATE_MODEL.md",
        "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
        "knowledge/generated/AGENT_ANATOMY.md",
        "knowledge/doctrine/ROADMAP.md",
        "knowledge/maps/COMMANDS_MAP.md",
        "knowledge/doctrine/SUBAGENT_LIFECYCLE.md",
    ]
    assert "README.md" not in paths
    assert "core/planner.py" not in paths
    assert "core/loop.py" not in paths
    assert any("doctrine/corporate docs injected" in w for w in out.warnings)
    assert any("dropped low-signal defaults" in w for w in out.warnings)


def test_corporate_doctrine_implementation_question_keeps_code_after_docs(
    workspace: Path,
) -> None:
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "Check implementation after doctrine docs.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/autonomous_runtime.py"},
                    "rationale": "implementation check",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="Is safe autonomy implemented in code?",
        file_hint=None,
    )

    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert paths[:5] == [
        "knowledge/doctrine/future/CORPORATE_MODEL.md",
        "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
        "knowledge/generated/AGENT_ANATOMY.md",
        "knowledge/doctrine/ROADMAP.md",
        "knowledge/maps/COMMANDS_MAP.md",
    ]
    assert paths[5:] == ["core/autonomous_runtime.py"]
    assert "knowledge/doctrine/SUBAGENT_LIFECYCLE.md" not in paths


def test_confidence_evidence_question_forces_verifier_confidence_sources(
    workspace: Path,
) -> None:
    _with_verifier_sources(workspace)
    canned = json.dumps(
        {
            "reasoning": "Mistakenly use generic project context.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "generic overview",
                },
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/planner.py"},
                    "rationale": "planner internals",
                },
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question=(
            "Why did the low-confidence gate trigger when evidence_score was low "
            "and chunks were verified/unverified?"
        ),
        file_hint=None,
    )

    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert paths[:4] == [
        "core/verifier.py",
        "tests/test_verifier.py",
        "tests/test_evidence_support.py",
        "tests/test_confidence_vector.py",
    ]
    assert "README.md" not in paths
    assert any("confidence/evidence verifier sources injected" in w for w in out.warnings)


def test_mixed_doctrine_confidence_question_selects_docs_and_verifier_sources(
    workspace: Path,
) -> None:
    _with_doctrine_docs(workspace)
    _with_verifier_sources(workspace)
    canned = json.dumps(
        {
            "reasoning": "Only picked one generic source.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "generic overview",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question=(
            "For the corporate model and central agent governance doctrine, why "
            "does correct reading still produce low-confidence evidence citations?"
        ),
        file_hint=None,
    )

    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert paths[:5] == [
        "knowledge/doctrine/future/CORPORATE_MODEL.md",
        "knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md",
        "knowledge/generated/AGENT_ANATOMY.md",
        "knowledge/doctrine/ROADMAP.md",
        "knowledge/maps/COMMANDS_MAP.md",
    ]
    for path in (
        "core/verifier.py",
        "tests/test_verifier.py",
        "tests/test_evidence_support.py",
        "tests/test_confidence_vector.py",
    ):
        assert path in paths
    assert "README.md" not in paths


def test_confidence_diagnostic_drops_readme_and_tools_defaults(
    workspace: Path,
) -> None:
    _with_verifier_sources(workspace)
    canned = json.dumps(
        {
            "reasoning": "Mistakenly inspect self documentation and tool list.",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "README.md"},
                    "rationale": "project overview",
                },
                {
                    "tool": "list_dir",
                    "arguments": {"path": "tools/"},
                    "rationale": "available tools",
                },
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    out = planner.plan(
        question="Debug verifier citations and confidence gate internals.",
        file_hint=None,
    )

    paths_by_tool = [
        (src["tool"], src["arguments"].get("path"))
        for src in out.sources
    ]
    assert ("file_read", "README.md") not in paths_by_tool
    assert ("list_dir", "tools/") not in paths_by_tool
    assert [item[1] for item in paths_by_tool[:4]] == [
        "core/verifier.py",
        "tests/test_verifier.py",
        "tests/test_evidence_support.py",
        "tests/test_confidence_vector.py",
    ]
    assert any("dropped low-signal defaults" in w for w in out.warnings)


# ---------- TD-003: parse-failure diagnostics ----------


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
         "a[b", "a]b", "a\nb", "a\rb", "a\tb", "a\0b"],
    )
    def test_metachar_in_argv_dropped(self, workspace: Path, metachar_arg):
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["touch", metachar_arg]}
        )
        assert sources == []
        assert any("metacharacter" in w for w in warnings), warnings

    def test_braces_pass_the_sanitiser(self, workspace: Path):
        """Exam 2026-09-05, turns 35–36: `findstr /c:{{step:` dropped twice for
        a `{`. Braces compose nothing with shell=False; the agent's own
        reference syntax must be searchable."""
        sources, warnings = self._plan_one_step(
            workspace, {"argv": ["findstr", "/s", "/n", "/c:{{step:", "*.py"]}
        )
        assert warnings == []
        assert sources[0]["arguments"]["argv"][3] == "/c:{{step:"

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

    # --- case insensitivity at planner layer ---


# ---------- thematic memory governance doc routing (conditional) ----------


def test_memory_question_injects_memory_governance_docs(workspace: Path) -> None:
    """A memory-governance question must lead with the memory docs.

    Fail-before: nothing injects them today, so the planner's source list is
    whatever the model asked for (`core/smart_memory.py` here).
    """
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "answer directly",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/smart_memory.py"},
                    "rationale": "mechanics",
                }
            ],
        }
    )
    planner = LLMPlanner(llm=FakeLLM(responses=[canned]), registry=_registry(workspace))
    out = planner.plan(
        question="How does episodic memory get consolidated, and what is forgotten?",
        file_hint=None,
    )
    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md" in paths
    assert "knowledge/doctrine/self-audit-lessons.md" in paths
    # a pure memory question (not a broad doctrine question) → the thematic
    # docs lead the source list.
    assert paths[0] == "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md"


def test_non_memory_doctrine_question_omits_memory_docs(workspace: Path) -> None:
    """A corporate/roadmap question does not pull in the memory governance docs.

    Docs are seeded, so the thematic gate is open and only the routing decides.
    """
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "doctrine",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "knowledge/doctrine/ROADMAP.md"},
                    "rationale": "roadmap",
                }
            ],
        }
    )
    planner = LLMPlanner(llm=FakeLLM(responses=[canned]), registry=_registry(workspace))
    out = planner.plan(
        question="Explain the corporate model and what is on the roadmap.",
        file_hint=None,
    )
    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert "knowledge/doctrine/future/CORPORATE_MODEL.md" in paths, paths
    assert "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md" not in paths, paths
    assert "knowledge/doctrine/self-audit-lessons.md" not in paths, paths


def test_question_touching_three_themes_keeps_stable_order(workspace: Path) -> None:
    """All three thematic groups may fire at once; the order must be deterministic.

    Sub-agent contract, then memory docs, then the repair protocol — the three
    injectors must not compete for the same slot or duplicate each other.
    """
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "answer directly",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/subagent_runner.py"},
                    "rationale": "mechanics",
                }
            ],
        }
    )
    planner = LLMPlanner(llm=FakeLLM(responses=[canned]), registry=_registry(workspace))
    out = planner.plan(
        question=(
            "When a subagent writes episodic memory, what is the root cause "
            "protocol if that write regresses?"
        ),
        file_hint=None,
    )
    paths = [
        src["arguments"]["path"]
        for src in out.sources
        if src["tool"] == "file_read"
    ]
    assert paths.index("knowledge/doctrine/SUBAGENT_LIFECYCLE.md") < paths.index(
        "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md"
    )
    assert paths.index("knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md") < paths.index(
        "knowledge/doctrine/SELF_REPAIR_DOCTRINE.md"
    )
    assert len(paths) == len(set(paths)), paths


@pytest.mark.parametrize(
    ("injector", "doc_path"),
    (
        (_ensure_subagent_governance_docs_first, "knowledge/doctrine/SUBAGENT_LIFECYCLE.md"),
        (_ensure_memory_governance_docs_first, "knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md"),
        (_ensure_self_repair_doctrine_docs_first, "knowledge/doctrine/SELF_REPAIR_DOCTRINE.md"),
    ),
)
def test_thematic_injector_collapses_repeated_doc_request(injector, doc_path) -> None:
    """A doc the model asked for twice must be read once, not twice.

    Fail-before: each thematic injector captured only the *first* occurrence
    and let every later one fall through into the remainder, so the plan
    carried the same `file_read` twice and burned context for nothing. The
    second spelling uses a backslash to prove the collapse is on the
    *normalised* path, not on raw string equality.
    """
    sources = [
        {"tool": "file_read", "arguments": {"path": doc_path}},
        {"tool": "file_read", "arguments": {"path": doc_path.replace("/", "\\")}},
        {"tool": "file_read", "arguments": {"path": "core/loop.py"}},
    ]
    warnings: list[str] = []
    ordered = injector(sources, warnings)
    paths = [
        _norm_source_path(src["arguments"]["path"])
        for src in ordered
        if src["tool"] == "file_read"
    ]
    assert len(paths) == len(set(paths)), paths
    assert paths.count(_norm_source_path(doc_path)) == 1, paths
    assert _norm_source_path("core/loop.py") in paths


@pytest.mark.parametrize(
    ("question", "directive"),
    (
        (
            ("Explain the corporate model, central agent governance, subagents, "
            "self-build, night observation, and safe autonomy."),
            "[DOCTRINE_DOCS=required",
        ),
        (
            "How does delegation to a subagent work, and when is it retired?",
            "[SUBAGENT_DOCS=required",
        ),
        (
            "How does episodic memory get consolidated, and what is forgotten?",
            "[MEMORY_DOCS=required",
        ),
        (
            "How must the root cause be proven before a self-repair is applied?",
            "[SELF_REPAIR_DOCS=required",
        ),
    ),
)
def test_docs_directive_is_suppressed_when_file_read_is_hidden(
    workspace: Path, question: str, directive: str
) -> None:
    """Fail closed: never order a read the model is not allowed to perform.

    Docs are seeded, so only the hidden file_read can keep the directive out.
    """
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "answer directly",
            "steps": [
                {
                    "tool": "list_dir",
                    "arguments": {"path": "."},
                    "rationale": "no file_read available",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))
    planner.hidden_tools = frozenset({"file_read"})

    out = planner.plan(question=question, file_hint=None)

    prompt = llm.calls[0]["user"]
    assert directive not in prompt, prompt
    assert not [src for src in out.sources if src["tool"] == "file_read"], out.sources


@pytest.mark.parametrize(
    ("question", "directive"),
    (
        (
            "How does delegation to a subagent work, and when is it retired?",
            "[SUBAGENT_DOCS=required",
        ),
        (
            "How does episodic memory get consolidated, and what is forgotten?",
            "[MEMORY_DOCS=required",
        ),
        (
            "How must the root cause be proven before a self-repair is applied?",
            "[SELF_REPAIR_DOCS=required",
        ),
    ),
)
def test_docs_directive_is_emitted_when_file_read_is_available(
    workspace: Path, question: str, directive: str
) -> None:
    """Scope guard for the fail-closed gate above: it must not silence the
    directive on the normal path, or the thematic groups stop working at all.
    """
    _with_doctrine_docs(workspace)
    canned = json.dumps(
        {
            "reasoning": "answer directly",
            "steps": [
                {
                    "tool": "file_read",
                    "arguments": {"path": "core/loop.py"},
                    "rationale": "mechanics",
                }
            ],
        }
    )
    llm = FakeLLM(responses=[canned])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))

    planner.plan(question=question, file_hint=None)

    assert directive in llm.calls[0]["user"], llm.calls[0]["user"]

# --- empty model output is a distinct, named failure -------------------------
#
# Regression guard for a production defect: an OpenAI reasoning model consumed
# its entire token budget on internal reasoning and returned zero characters
# with `status=success`. The planner reported `json_decode_error`, which sent
# every reader looking for malformed JSON that was never emitted.


@pytest.mark.parametrize("raw", ["", "   ", "\n\n\t  \n"])
def test_empty_model_output_is_named_not_a_json_error(
    workspace: Path, raw: str
) -> None:
    planner = LLMPlanner(llm=FakeLLM(responses=[raw]), registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)

    assert "empty_model_output" in out.warnings
    assert "json_decode_error" not in out.warnings
    assert out.diagnostics["stage"] == "empty_output"
    assert out.diagnostics["fallback"] == "empty_plan"
    assert out.diagnostics["json_block_found"] is False
    # The reason must point at the budget, not at JSON syntax.
    assert "budget" in out.diagnostics["reason"].lower()


def test_empty_output_still_falls_back_to_an_empty_plan(workspace: Path) -> None:
    planner = LLMPlanner(llm=FakeLLM(responses=[""]), registry=_registry(workspace))

    out = planner.plan(question="something", file_hint=None)

    assert out.sources == []
    assert "plan_parse_failed" in out.warnings


# --- planner token budget ----------------------------------------------------


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "", "  "])
def test_planner_budget_rejects_unusable_values(monkeypatch, bad: str) -> None:
    # A malformed environment must never yield a zero budget, which would
    # guarantee an empty plan on every single run.
    monkeypatch.setenv("AGENT_PLAN_MAX_TOKENS", bad)
    assert planner_module._plan_max_tokens() == planner_module._PLAN_MAX_TOKENS_DEFAULT


def test_planner_passes_the_configured_budget_to_the_llm(
    workspace: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_PLAN_MAX_TOKENS", "3333")

    # FakeLLM already records every argument of every `complete(...)` call,
    # so a spy subclass would only add a second `complete` signature that can
    # drift away from the base one. Read the recorded call instead: it stays
    # correct whether the planner passes the budget positionally or by name.
    llm = FakeLLM(responses=[json.dumps({"reasoning": "ok", "steps": []})])
    planner = LLMPlanner(llm=llm, registry=_registry(workspace))
    planner.plan(question="something", file_hint=None)

    assert len(llm.calls) == 1
    assert llm.calls[0]["max_tokens"] == 3333


def test_low_signal_defaults_are_read_from_the_constant(monkeypatch) -> None:
    """Audit finding Д-2: _is_low_signal_confidence_source used to re-encode
    "readme.md"/"tools" by hand while _CONFIDENCE_LOW_SIGNAL_DEFAULT_PATHS had
    zero readers — editing the constant changed nothing. The function must
    follow the constant. Proven failing on the old code: with the constant
    swapped out, the hardcoded version still answered by its own literals."""
    from core import doc_routing

    src_readme = {"tool": "file_read", "arguments": {"path": "README.md"}}
    src_tools = {"tool": "list_dir", "arguments": {"path": "tools/"}}
    src_notes = {"tool": "file_read", "arguments": {"path": "NOTES.md"}}

    assert doc_routing._is_low_signal_confidence_source(src_readme) is True
    assert doc_routing._is_low_signal_confidence_source(src_tools) is True
    assert doc_routing._is_low_signal_confidence_source(src_notes) is False

    monkeypatch.setattr(
        doc_routing, "_CONFIDENCE_LOW_SIGNAL_DEFAULT_PATHS", ("NOTES.md",)
    )
    assert doc_routing._is_low_signal_confidence_source(src_notes) is True
    assert doc_routing._is_low_signal_confidence_source(src_readme) is False


def _with_verifier_sources(workspace: Path) -> None:
    """Папка изображает собственный репозиторий агента: файлы, которые маршрут
    верификатора велит читать, в ней есть (`LLMPlanner._confidence_sources_here`)."""
    from core.doc_routing import CONFIDENCE_EVIDENCE_SOURCE_PATHS

    for rel in CONFIDENCE_EVIDENCE_SOURCE_PATHS:
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text("# stub", encoding="utf-8")


def _with_doctrine_docs(workspace: Path) -> None:
    """Папка изображает собственный репозиторий агента: документы, которые велят
    читать маршруты доктрины, в ней есть (`doc_routing.doctrine_docs_present`)."""
    from core import doc_routing as d

    for rel in (d.DOCTRINE_CORPORATE_DOC_PATHS + d._SUBAGENT_GOVERNANCE_DOC_PATHS
                + d._MEMORY_GOVERNANCE_DOC_PATHS + d._SELF_REPAIR_DOCTRINE_DOC_PATHS):
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text("# stub", encoding="utf-8")
