"""Tests for the multi-file (module-split) self-build path.

The oversized-module organ emits abstract ``split:<rel>`` targets. This suite
proves the head can now:

* map a ``split:<rel>`` signal to its concrete, low-risk, existing .py module
  (and refuse missing / non-Python / critical-by-classifier targets);
* have the Builder emit MULTIPLE files (the shrunk target plus new sibling
  modules) while keeping the single-file path byte-identical;
* have the Critic validate EVERY file (a critical extra file is vetoed);
* publish one approval item whose payload carries all files.

Every dependency is faked — no real provider, network, or git.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.backlog_target_mapper import map_backlog_candidate
from core.self_build_producer import (
    _builder_generate,
    _critic_review,
    _normalize_builder_files,
    produce_self_apply_proposal,
)


class FakeLLM:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, max_tokens: int = 2000,
                 temperature: float = 0.0) -> str:
        self.calls.append({"system": system, "user": user})
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class FakeVCS:
    def __init__(self, clean: bool = True) -> None:
        self._clean = clean

    def is_clean(self) -> bool:
        return self._clean


class FakeKillSwitch:
    def __init__(self, active: bool = False, reason: str = "") -> None:
        self.active = active
        self.reason = reason


def _headroom_budget() -> dict:
    return {
        "windows": [
            {
                "name": "hour",
                "counters": {
                    "llm_calls": {"used": 1, "limit": 100},
                    "model_tokens": {"used": 10, "limit": 1000},
                },
            }
        ]
    }


def _candidate(target_path: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        target_path=target_path,
        signal_source="oversized_module",
        evidence_ref=f"oversized_module:{target_path}",
        problem_quote="module is oversized (900 lines)",
        proposed_change="split into cohesive smaller modules",
        proof_of_value="",
        expected_effect="",
        confidence=0.4,
    )


# ── mapper ───────────────────────────────────────────────────────────────────


def test_mapper_resolves_split_to_concrete_low_risk_module(workspace: Path):
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    (workspace / "core" / "sample_mod.py").write_text("x = 1\n", encoding="utf-8")
    result = map_backlog_candidate(
        _candidate("split:core/sample_mod.py"), workspace=workspace
    )
    assert result.decision == "mapped"
    assert result.ok
    assert result.candidate.target_path == "core/sample_mod.py"
    assert result.mapping_rule == "split_module"


def test_mapper_refuses_missing_split_target(workspace: Path):
    result = map_backlog_candidate(
        _candidate("split:core/does_not_exist.py"), workspace=workspace
    )
    assert result.decision == "no_target"
    assert not result.ok


def test_mapper_refuses_non_python_split_target(workspace: Path):
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    (workspace / "core" / "notes.md").write_text("hi\n", encoding="utf-8")
    result = map_backlog_candidate(
        _candidate("split:core/notes.md"), workspace=workspace
    )
    assert result.decision == "no_target"


# ── normaliser ───────────────────────────────────────────────────────────────


def test_normalize_single_file_reply_is_backward_compatible():
    files, primary = _normalize_builder_files({"content": "A = 1\n"}, "core/x.py")
    assert files == [{"path": "core/x.py", "content": "A = 1\n"}]
    assert primary == "A = 1\n"


def test_normalize_multi_file_reply_surfaces_target_primary():
    parsed = {
        "files": [
            {"path": "core/x.py", "content": "from core.x_util import helper\n"},
            {"path": "core/x_util.py", "content": "def helper():\n    return 1\n"},
        ]
    }
    files, primary = _normalize_builder_files(parsed, "core/x.py")
    assert len(files) == 2
    assert primary == "from core.x_util import helper\n"


# ── builder (split mode) ─────────────────────────────────────────────────────


def test_builder_split_mode_emits_multiple_files():
    reply = json.dumps(
        {
            "files": [
                {"path": "core/x.py", "content": "from core.x_util import a\n"},
                {"path": "core/x_util.py", "content": "def a():\n    return 1\n"},
            ],
            "test_paths": ["tests"],
            "reason": "split module",
            "confidence": 0.9,
        }
    )
    out = _builder_generate(
        FakeLLM([reply]), "core/x.py", "big old content\n", "oversized", split_mode=True
    )
    assert out.decision == "built"
    assert len(out.data["files"]) == 2
    assert out.data["content"] == "from core.x_util import a\n"


def test_builder_split_prompt_requests_multiple_files():
    llm = FakeLLM(["{}"])
    _builder_generate(llm, "core/x.py", "content\n", "oversized", split_mode=True)
    assert '"files"' in llm.calls[0]["system"]
    assert "Split the oversized" in llm.calls[0]["system"]


# ── critic (per-file guard) ──────────────────────────────────────────────────


def test_critic_vetoes_when_extra_file_is_critical():
    build = {
        "content": "from core.loop import x\n",
        "files": [
            {"path": "core/x.py", "content": "from core.loop import x\n"},
            {"path": "core/loop.py", "content": "y = 2\n"},  # critical file
        ],
        "test_paths": ["tests"],
        "confidence": 0.9,
    }
    out = _critic_review("core/x.py", "old\n", build, confidence_threshold=0.5)
    assert out.decision == "veto"
    assert any("core/loop.py" in r and "critical" in r for r in out.data["veto_reasons"])


def test_critic_vetoes_unparseable_extra_file():
    build = {
        "content": "import core.x_util\n",
        "files": [
            {"path": "core/x.py", "content": "import core.x_util\n"},
            {"path": "core/x_util.py", "content": "def broken(:\n"},
        ],
        "test_paths": ["tests"],
        "confidence": 0.9,
    }
    out = _critic_review("core/x.py", "old\n", build, confidence_threshold=0.5)
    assert out.decision == "veto"
    assert any("does not parse" in r for r in out.data["veto_reasons"])


# ── end to end ───────────────────────────────────────────────────────────────


def test_split_produces_multifile_proposal(workspace: Path):
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    target_rel = "core/sample_big.py"
    current = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    (workspace / target_rel).write_text(current, encoding="utf-8")

    inbox = ApprovalInbox(path=None)
    reply = json.dumps(
        {
            "files": [
                {"path": target_rel, "content": "from core.sample_big_helpers import a, b\n"},
                {
                    "path": "core/sample_big_helpers.py",
                    "content": "def a():\n    return 1\n\n\ndef b():\n    return 2\n",
                },
            ],
            "test_paths": ["tests"],
            "reason": "split oversized module into helpers",
            "confidence": 0.9,
        }
    )
    llm = FakeLLM([reply])

    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(),
        file_reader=lambda p: current if p == target_rel else None,
        grounded_selector=lambda: _candidate(f"split:{target_rel}"),
    )

    assert report.status == "proposed", report.reason
    items = inbox.list()
    assert len(items) == 1
    files = items[0].payload["files"]
    paths = sorted(f["path"] for f in files)
    assert paths == ["core/sample_big.py", "core/sample_big_helpers.py"]
    assert "split" in items[0].summary


def test_split_critical_target_is_refused(workspace: Path):
    # core/loop.py is critical: even though the file exists in the real repo, the
    # Manager critical-gate must refuse the split (report-only for the riskiest).
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM(["{}"])
    (workspace / "core").mkdir(parents=True, exist_ok=True)
    (workspace / "core" / "loop.py").write_text("x = 1\n", encoding="utf-8")
    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=llm,
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(),
        file_reader=lambda p: "x = 1\n",
        grounded_selector=lambda: _candidate("split:core/loop.py"),
    )
    assert report.status == "no_patch"
    assert llm.calls == []  # refused before any builder work
    assert inbox.list() == []
