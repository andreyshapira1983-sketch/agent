"""Tests for Stage B of the coding-skill ladder (:mod:`core.self_task_builder`).

Stage B consumes one APPROVED ``self_build_task.approve`` item, asks the Builder
to implement the target file so the frozen test passes, and publishes exactly one
``self_apply_lane.run`` proposal (impl + frozen test) for the existing lane.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION, rehydrate_proposal
from core.self_task_builder import TASK_BUILD_ORIGIN, build_coding_task
from core.self_task_producer import SELF_TASK_OPERATION, decode_frozen_test


# ── fakes ────────────────────────────────────────────────────────────────────


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
        self.mutations: list[str] = []

    def is_clean(self) -> bool:
        return self._clean

    def commit(self, message: str) -> str:  # pragma: no cover - guard
        self.mutations.append("commit")
        return "deadbeef"


class FakeKillSwitch:
    def __init__(self, active: bool, reason: str = "") -> None:
        self.active = active
        self.reason = reason


_IMPL = "core/redaction.py"
_TEST_PATH = "tests/test_mask_email.py"
_CURRENT = "SECRET = 1\n"

_FROZEN_TEST = (
    "import core.redaction as redaction\n\n\n"
    "def test_mask_email():\n"
    "    assert redaction.mask_email('alice@mail.ru') == 'a***@mail.ru'\n"
)

_GOOD_IMPL = (
    "SECRET = 1\n\n\n"
    "def mask_email(addr):\n"
    "    if '@' not in addr:\n"
    "        return addr\n"
    "    local, _, domain = addr.partition('@')\n"
    "    return f'{local[:1]}***@{domain}'\n"
)


def _builder_json(
    *, content: str = _GOOD_IMPL, confidence: float = 0.9, reason: str = "impl"
) -> str:
    return json.dumps({"content": content, "confidence": confidence, "reason": reason})


def _reader(files: dict[str, str]):
    def read(path: str) -> str | None:
        return files.get(path)
    return read


def _seed_task(
    inbox: ApprovalInbox,
    *,
    impl_path: str = _IMPL,
    test_path: str = _TEST_PATH,
    frozen_test: str = _FROZEN_TEST,
    approve: bool = True,
    with_b64: bool = True,
    raw_test: str | None = None,
):
    payload = {
        "task_title": "Add email masking helper",
        "task_summary": "Implement mask_email in core/redaction.py.",
        "impl_path": impl_path,
        "test_path": test_path,
        "test_content": raw_test if raw_test is not None else frozen_test,
        "evidence_ref": f"{impl_path}:31",
    }
    if with_b64:
        payload["test_content_b64"] = base64.b64encode(
            frozen_test.encode("utf-8")
        ).decode("ascii")
    item = inbox.add(operation=SELF_TASK_OPERATION, summary="task", payload=payload)
    if approve:
        inbox.approve(item.id)
    return item


def _run(workspace: Path, inbox: ApprovalInbox, approval_id: str, **kwargs):
    defaults = dict(
        workspace=workspace,
        inbox=inbox,
        approval_id=approval_id,
        llm=FakeLLM([_builder_json()]),
        vcs=FakeVCS(),
        file_reader=_reader({_IMPL: _CURRENT}),
    )
    defaults.update(kwargs)
    return build_coding_task(**defaults)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ── gates ────────────────────────────────────────────────────────────────────


def test_kill_switch_blocks_before_llm(workspace: Path):
    inbox = ApprovalInbox(path=None)
    item = _seed_task(inbox)
    llm = FakeLLM([_builder_json()])
    report = _run(workspace, inbox, item.id, llm=llm,
                  kill_switch=FakeKillSwitch(True, "stop"))
    assert report.status == "budget_kill_switch"
    assert llm.calls == []


def test_near_exhaustion_budget_waits(workspace: Path):
    inbox = ApprovalInbox(path=None)
    item = _seed_task(inbox)
    budget = {"windows": [{"name": "hour", "counters": {
        "model_tokens": {"used": 990, "limit": 1000}}}]}
    llm = FakeLLM([_builder_json()])
    report = _run(workspace, inbox, item.id, llm=llm, budget_snapshot=budget)
    assert report.status == "budget_wait"
    assert llm.calls == []


def test_dirty_tree_waits(workspace: Path):
    inbox = ApprovalInbox(path=None)
    item = _seed_task(inbox)
    report = _run(workspace, inbox, item.id, vcs=FakeVCS(clean=False))
    assert report.status == "dirty_tree_wait"


def test_unknown_id_not_found(workspace: Path):
    inbox = ApprovalInbox(path=None)
    llm = FakeLLM([_builder_json()])
    report = _run(workspace, inbox, "ain_does_not_exist", llm=llm)
    assert report.status == "not_found"
    assert llm.calls == []


def test_wrong_operation_rejected(workspace: Path):
    inbox = ApprovalInbox(path=None)
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="not a task", payload={})
    inbox.approve(item.id)
    llm = FakeLLM([_builder_json()])
    report = _run(workspace, inbox, item.id, llm=llm)
    assert report.status == "wrong_operation"
    assert llm.calls == []


def test_unapproved_task_rejected(workspace: Path):
    inbox = ApprovalInbox(path=None)
    item = _seed_task(inbox, approve=False)  # still pending
    llm = FakeLLM([_builder_json()])
    report = _run(workspace, inbox, item.id, llm=llm)
    assert report.status == "not_approved"
    assert llm.calls == []


def test_redaction_corrupted_frozen_test_rejected(workspace: Path):
    inbox = ApprovalInbox(path=None)
    # No base64 blob and a scrubbed raw preview -> unusable frozen test.
    item = _seed_task(
        inbox,
        with_b64=False,
        raw_test="def test_x():\n    assert f('[REDACTED:pii-email]') == 'x'\n",
    )
    llm = FakeLLM([_builder_json()])
    report = _run(workspace, inbox, item.id, llm=llm)
    assert report.status == "invalid_task"
    assert any("redaction-corrupted" in r for r in report.veto_reasons)
    assert llm.calls == []


# ── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_publishes_one_self_apply_item(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id)
    assert report.status == "proposed"
    assert report.target_path == _IMPL

    apply_items = [i for i in inbox.list() if i.operation == SELF_APPLY_OPERATION]
    assert len(apply_items) == 1
    item = apply_items[0]
    assert report.approval_id == item.id
    paths = [f["path"] for f in item.payload["files"]]
    assert paths == [_IMPL, _TEST_PATH]
    assert item.payload["test_paths"] == [_TEST_PATH]
    assert item.payload["origin"] == TASK_BUILD_ORIGIN


def test_frozen_test_is_passed_through_exactly(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id)
    assert report.status == "proposed"
    item = inbox.get(report.approval_id)
    # The lane reads file content via rehydrate_proposal, which restores the
    # byte-exact copy that the durable redactor would otherwise have scrubbed.
    prop = rehydrate_proposal(item.payload)
    test_file = next(c for c in prop.files if c.path == _TEST_PATH)
    assert test_file.content == _FROZEN_TEST


def test_impl_content_reaches_lane(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id)
    item = inbox.get(report.approval_id)
    prop = rehydrate_proposal(item.payload)
    impl_file = next(c for c in prop.files if c.path == _IMPL)
    assert "def mask_email" in impl_file.content


def test_happy_path_never_touches_git(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    vcs = FakeVCS()
    _run(workspace, inbox, task.id, vcs=vcs)
    assert vcs.mutations == []


# ── builder / critic vetoes ──────────────────────────────────────────────────


def test_empty_content_vetoes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id,
                  llm=FakeLLM([_builder_json(content="")]))
    assert report.status == "build_veto"
    assert [i for i in inbox.list() if i.operation == SELF_APPLY_OPERATION] == []


def test_no_json_vetoes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id, llm=FakeLLM(["not json at all"]))
    assert report.status == "build_veto"


def test_low_confidence_vetoes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id,
                  llm=FakeLLM([_builder_json(confidence=0.1)]))
    assert report.status == "build_veto"
    assert any("confidence" in r for r in report.veto_reasons)


def test_unparseable_impl_vetoes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    bad = "def mask_email(:\n    return None\n"
    report = _run(workspace, inbox, task.id,
                  llm=FakeLLM([_builder_json(content=bad)]))
    assert report.status == "build_veto"
    assert any("does not parse" in r for r in report.veto_reasons)


def test_noop_identical_impl_vetoes(workspace: Path):
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    report = _run(workspace, inbox, task.id,
                  llm=FakeLLM([_builder_json(content=_CURRENT)]))
    assert report.status == "build_veto"
    assert any("no-op" in r for r in report.veto_reasons)


def test_decode_frozen_test_used_for_lane(workspace: Path):
    # Sanity: the seeded item exposes the exact test via decode_frozen_test.
    inbox = ApprovalInbox(path=None)
    task = _seed_task(inbox)
    assert decode_frozen_test(inbox.get(task.id).payload) == _FROZEN_TEST
