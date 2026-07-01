"""Tests for the subagent-backed self-apply proposal producer (TD-025).

Every dependency is faked: FakeLLM (no real provider/network), an in-memory
ApprovalInbox, a FakeVCS, and an in-memory file reader. The producer must:

* honour the four safety gates before any LLM-heavy work runs;
* run the Manager/Researcher/Builder/Critic/Reporter roles in order;
* reject diff-only / denylisted / low-confidence candidates via a Critic veto;
* create at most one ``self_apply_lane.run`` approval item whose payload
  round-trips through the TD-024 bridge;
* never apply the patch, commit, push, fetch, pull, merge, or touch git.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION, rehydrate_proposal
from core.self_build_producer import (
    PRODUCER_ORIGIN,
    ProducerReport,
    produce_self_apply_proposal,
)


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    """Returns canned responses in order; records every call so a gate test can
    assert that no LLM-heavy work happened."""

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
    """Minimal SafeVCS stand-in. Records any mutating call so tests can prove the
    producer never touches git."""

    def __init__(self, clean: bool = True) -> None:
        self._clean = clean
        self.mutations: list[str] = []

    def is_clean(self) -> bool:
        return self._clean

    def create_temp_branch(self, name: str) -> None:  # pragma: no cover - guard
        self.mutations.append(f"create_temp_branch:{name}")

    def commit(self, message: str) -> str:  # pragma: no cover - guard
        self.mutations.append("commit")
        return "deadbeef"


class FakeKillSwitch:
    def __init__(self, active: bool, reason: str = "") -> None:
        self.active = active
        self.reason = reason


def _reader(files: dict[str, str]):
    def read(path: str) -> str | None:
        return files.get(path)
    return read


# ── canned role responses ────────────────────────────────────────────────────

_TARGET = "core/redaction.py"


def _manager_ok(target: str = _TARGET) -> str:
    return json.dumps({"target": target, "diagnosis": "tidy a helper"})


def _manager_none() -> str:
    return json.dumps({"target": None, "diagnosis": "nothing worth it"})


def _builder_ok(content: str = "VALUE = 1\n", confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "content": content,
            "test_paths": ["tests/test_redaction.py"],
            "test_pattern": "redaction",
            "reason": "small tidy",
            "confidence": confidence,
        }
    )


def _near_exhaustion_budget() -> dict:
    return {
        "windows": [
            {
                "name": "hour",
                "counters": {
                    "llm_calls": {"used": 9, "limit": 10},
                    "model_tokens": {"used": 900, "limit": 1000},
                },
            }
        ]
    }


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


def _produce(workspace: Path, **kwargs) -> ProducerReport:
    defaults = dict(
        workspace=workspace,
        inbox=ApprovalInbox(path=None),
        vcs=FakeVCS(clean=True),
        budget_snapshot=_headroom_budget(),
        kill_switch=FakeKillSwitch(active=False),
        file_reader=_reader({_TARGET: "OLD = 0\n"}),
    )
    defaults.update(kwargs)
    return produce_self_apply_proposal(**defaults)


# ── gate tests (no LLM-heavy work) ───────────────────────────────────────────


def test_kill_switch_active_refuses_before_any_subagent(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, kill_switch=FakeKillSwitch(True, "day budget"))
    assert report.status == "budget_kill_switch"
    assert llm.calls == []  # no subagent ran
    assert report.role_outputs == []


def test_low_budget_returns_budget_wait(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, budget_snapshot=_near_exhaustion_budget())
    assert report.status == "budget_wait"
    assert llm.calls == []


def test_pending_self_apply_returns_approval_wait(workspace: Path):
    inbox = ApprovalInbox(path=None)
    inbox.add(operation=SELF_APPLY_OPERATION, summary="existing")
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "approval_wait"
    assert llm.calls == []


def test_dirty_tree_refuses_before_proposal(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, vcs=FakeVCS(clean=False))
    assert report.status == "dirty_tree_wait"
    assert llm.calls == []


# ── role pipeline ─────────────────────────────────────────────────────────────


def test_no_candidate_returns_no_patch(workspace: Path):
    llm = FakeLLM([_manager_none()])
    report = _produce(workspace, llm=llm)
    assert report.status == "no_patch"
    # Only the Manager ran; no builder work.
    assert [r.role for r in report.role_outputs] == ["manager"]


def test_selected_candidate_runs_roles_in_order_and_proposes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "proposed"
    assert [r.role for r in report.role_outputs] == [
        "manager",
        "researcher",
        "builder",
        "critic",
        "reporter",
    ]
    assert report.target_path == _TARGET
    assert report.approval_id


def test_builder_diff_only_is_vetoed(workspace: Path):
    diff = "--- a/core/redaction.py\n+++ b/core/redaction.py\n@@ -1 +1 @@\n-OLD\n+NEW\n"
    llm = FakeLLM([_manager_ok(), _builder_ok(content=diff)])
    report = _produce(workspace, llm=llm)
    assert report.status == "critic_veto"
    assert any("diff" in r for r in report.veto_reasons)


def test_critical_target_is_denied_even_if_listed(workspace: Path):
    # Denylist wins before the allowlist: a critical organ can never be picked.
    llm = FakeLLM([_manager_ok(target="core/loop.py")])
    report = _produce(
        workspace,
        llm=llm,
        candidate_targets=("core/loop.py",),
        file_reader=_reader({"core/loop.py": "x=1\n"}),
    )
    assert report.status == "no_patch"


def test_off_allowlist_target_is_rejected(workspace: Path):
    llm = FakeLLM([_manager_ok(target="core/secret_stuff.py")])
    report = _produce(workspace, llm=llm, candidate_targets=(_TARGET,))
    assert report.status == "no_patch"


def test_low_confidence_is_vetoed_and_no_item_created(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok(confidence=0.1)])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "critic_veto"
    assert inbox.list() == []  # veto blocks approval-item creation


def test_unparseable_builder_output_is_vetoed(workspace: Path):
    llm = FakeLLM([_manager_ok(), "totally not json"])
    report = _produce(workspace, llm=llm)
    assert report.status == "critic_veto"


def test_creates_exactly_one_self_apply_item(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "proposed"
    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.operation == SELF_APPLY_OPERATION
    assert item.payload["origin"] == PRODUCER_ORIGIN


def test_payload_round_trips_through_bridge(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_manager_ok(), _builder_ok(content="NEW = 2\n")])
    report = _produce(workspace, llm=llm, inbox=inbox)
    assert report.status == "proposed"
    payload = inbox.get(report.approval_id).payload
    proposal = rehydrate_proposal(payload)
    assert proposal.files[0].path == _TARGET
    assert proposal.files[0].content == "NEW = 2\n"


def test_producer_never_touches_git(workspace: Path):
    vcs = FakeVCS(clean=True)
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm, vcs=vcs)
    assert report.status == "proposed"
    assert vcs.mutations == []  # no branch/commit — producer only proposes


def test_no_push_or_network_methods_in_producer_and_vcs():
    import core.self_build_producer as producer
    from core.safe_vcs import SafeVCS

    src = Path(producer.__file__).read_text(encoding="utf-8")
    import_lines = [
        ln for ln in src.splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    for banned in ("requests", "urllib", "httpx", "socket", "subprocess"):
        assert not any(banned in ln for ln in import_lines), banned
    for banned in ("push", "fetch", "pull", "remote", "merge"):
        assert not hasattr(SafeVCS, banned), banned


def test_config_budget_limits_never_written(workspace: Path):
    llm = FakeLLM([_manager_ok(), _builder_ok()])
    report = _produce(workspace, llm=llm)
    assert report.status == "proposed"
    assert not (workspace / "config" / "budget_limits.json").exists()
