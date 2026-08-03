"""Tests for the Stage-A coding-task producer (roadmap Ступень 1).

Every dependency is faked: FakeLLM (no provider/network), an in-memory
ApprovalInbox, a FakeVCS, an in-memory reader, and a plain candidate object for
the grounded ``code_todo`` selector. The producer must:

* honour the deterministic gates (kill-switch, budget, one-in-flight, dirty tree)
  before any LLM-heavy work runs;
* refuse when there is no grounded code TODO/FIXME candidate;
* VETO garbage tasks (trivial assert, no module reference, pre-existing test
  file, mismatched impl path, low confidence) so a human never sees them;
* on the happy path create exactly ONE ``self_build_task.approve`` inbox item
  carrying the frozen test content — and never write code or touch git.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.approval_inbox import ApprovalInbox
from core.self_task_producer import (
    SELF_TASK_OPERATION,
    TASK_PRODUCER_ORIGIN,
    decode_frozen_test,
    produce_coding_task,
)


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


def _candidate(target: str = _IMPL) -> SimpleNamespace:
    return SimpleNamespace(
        signal_source="code_todo",
        target_path=target,
        evidence_ref=f"{target}:12",
        problem_quote="TODO: add a redact() helper for secrets",
        proposed_change="",
        proof_of_value="",
        expected_effect="",
        confidence=0.6,
    )


_GOOD_TEST = (
    "from core.redaction import redact\n\n\n"
    "def test_redacts_token():\n"
    "    assert redact('token=abc') == 'token=***'\n"
)


def _builder(
    *,
    impl_path: str = _IMPL,
    test_path: str = "tests/test_redaction_stagea.py",
    test_content: str = _GOOD_TEST,
    confidence: float = 0.9,
    title: str = "Add redact() helper",
) -> str:
    return json.dumps(
        {
            "task_title": title,
            "task_summary": "Implement redact() to mask secret values.",
            "impl_path": impl_path,
            "test_path": test_path,
            "test_content": test_content,
            "confidence": confidence,
        }
    )


def _reader(files: dict[str, str]):
    def read(path: str) -> str | None:
        return files.get(path)
    return read


def _run(workspace: Path, **kwargs):
    defaults = dict(
        workspace=workspace,
        inbox=ApprovalInbox(path=None),
        llm=FakeLLM([_builder()]),
        vcs=FakeVCS(),
        task_selector=lambda: _candidate(),
        file_reader=_reader({_IMPL: "SECRET = 1\n"}),
    )
    defaults.update(kwargs)
    return defaults["inbox"], produce_coding_task(**defaults)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ── gates ────────────────────────────────────────────────────────────────────


def test_kill_switch_blocks_before_llm(workspace: Path):
    llm = FakeLLM([_builder()])
    _, report = _run(workspace, llm=llm, kill_switch=FakeKillSwitch(True, "stop"))
    assert report.status == "budget_kill_switch"
    assert llm.calls == []


def test_near_exhaustion_budget_waits(workspace: Path):
    budget = {"windows": [{"name": "hour", "counters": {
        "model_tokens": {"used": 990, "limit": 1000}}}]}
    llm = FakeLLM([_builder()])
    _, report = _run(workspace, llm=llm, budget_snapshot=budget)
    assert report.status == "budget_wait"
    assert llm.calls == []


def test_dirty_tree_waits(workspace: Path):
    _, report = _run(workspace, vcs=FakeVCS(clean=False))
    assert report.status == "dirty_tree_wait"


def test_pending_task_waits(workspace: Path):
    inbox = ApprovalInbox(path=None)
    inbox.add(operation=SELF_TASK_OPERATION, summary="existing", payload={})
    llm = FakeLLM([_builder()])
    _, report = _run(workspace, inbox=inbox, llm=llm)
    assert report.status == "task_wait"
    assert llm.calls == []


def test_no_candidate_refuses(workspace: Path):
    llm = FakeLLM([_builder()])
    _, report = _run(workspace, llm=llm, task_selector=lambda: None)
    assert report.status == "no_task"
    assert llm.calls == []


def test_broken_selector_refuses(workspace: Path):
    def _boom():
        raise RuntimeError("selector down")

    _, report = _run(workspace, task_selector=_boom)
    assert report.status == "no_task"


# ── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_publishes_one_task_item(workspace: Path):
    inbox, report = _run(workspace)
    assert report.status == "proposed"
    assert report.target_path == _IMPL
    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.operation == SELF_TASK_OPERATION
    assert item.payload["impl_path"] == _IMPL
    assert item.payload["test_content"] == _GOOD_TEST
    assert item.payload["origin"] == TASK_PRODUCER_ORIGIN
    assert item.payload["test_path"] == "tests/test_redaction_stagea.py"


def test_happy_path_never_touches_git(workspace: Path):
    vcs = FakeVCS()
    _run(workspace, vcs=vcs)
    assert vcs.mutations == []


def test_payload_carries_exact_base64_frozen_test(workspace: Path):
    inbox, report = _run(workspace)
    assert report.status == "proposed"
    item = inbox.list()[0]
    # The exact test is preserved byte-for-byte in the redaction-inert blob.
    import base64

    assert base64.b64decode(item.payload["test_content_b64"]).decode() == _GOOD_TEST
    assert decode_frozen_test(item.payload) == _GOOD_TEST


def test_frozen_test_survives_durable_redaction(workspace: Path):
    # A test that legitimately embeds an example email would be scrubbed by the
    # DLP redactor in the raw field, but must survive exactly via base64.
    pii_test = (
        "from core.redaction import mask_email\n\n\n"
        "def test_mask():\n"
        '    assert mask_email("alice@mail.ru") == "a***@mail.ru"\n'
    )
    inbox, report = _run(workspace, llm=FakeLLM([_builder(test_content=pii_test)]))
    assert report.status == "proposed"
    # ApprovalInbox.add() redacts the payload in-memory before persisting.
    item = inbox.list()[0]
    assert "[REDACTED:" in item.payload["test_content"]  # preview is scrubbed
    assert decode_frozen_test(item.payload) == pii_test  # exact test intact


def test_decode_frozen_test_falls_back_to_plaintext():
    assert decode_frozen_test({"test_content": "def test_x():\n    assert 1\n"}) == (
        "def test_x():\n    assert 1\n"
    )


def test_veto_test_with_redaction_markers(workspace: Path):
    bad = (
        "from core.redaction import redact\n\n\n"
        "def test_x():\n    assert redact() == \"[REDACTED:pii-email]\"\n"
    )
    inbox, report = _run(workspace, llm=FakeLLM([_builder(test_content=bad)]))
    assert report.status == "task_veto"
    assert any("redaction markers" in r for r in report.veto_reasons)
    assert inbox.list() == []


# ── critic vetoes (anti-garbage) ─────────────────────────────────────────────


def test_veto_trivial_assert(workspace: Path):
    bad = ("from core.redaction import redact\n\n\n"
           "def test_x():\n    assert True\n")
    inbox, report = _run(workspace, llm=FakeLLM([_builder(test_content=bad)]))
    assert report.status == "task_veto"
    assert any("meaningful assertion" in r for r in report.veto_reasons)
    assert inbox.list() == []


def test_veto_test_missing_module_reference(workspace: Path):
    bad = "def test_x():\n    assert 2 + 2 == 4\n"
    _, report = _run(workspace, llm=FakeLLM([_builder(test_content=bad)]))
    assert report.status == "task_veto"
    assert any("does not reference the implementation module"
               in r for r in report.veto_reasons)


def test_veto_test_file_already_exists(workspace: Path):
    existing = "tests/test_redaction_stagea.py"
    _, report = _run(
        workspace,
        file_reader=_reader({_IMPL: "SECRET = 1\n", existing: "old test\n"}),
    )
    assert report.status == "task_veto"
    assert any("already exists" in r for r in report.veto_reasons)


def test_veto_impl_path_mismatch(workspace: Path):
    _, report = _run(
        workspace,
        llm=FakeLLM([_builder(impl_path="core/other_module.py")]),
    )
    assert report.status == "task_veto"
    assert any("does not match grounded target" in r for r in report.veto_reasons)


def test_veto_low_confidence(workspace: Path):
    _, report = _run(workspace, llm=FakeLLM([_builder(confidence=0.1)]))
    assert report.status == "task_veto"
    assert any("confidence" in r for r in report.veto_reasons)


def test_veto_test_does_not_parse(workspace: Path):
    bad = "from core.redaction import redact\n\ndef test_x(:\n    assert redact()\n"
    _, report = _run(workspace, llm=FakeLLM([_builder(test_content=bad)]))
    assert report.status == "task_veto"
    assert any("does not parse" in r for r in report.veto_reasons)


def test_builder_no_json_vetoes(workspace: Path):
    _, report = _run(workspace, llm=FakeLLM(["not json at all"]))
    assert report.status == "task_veto"


def test_builder_accepts_test_lines_array(workspace: Path):
    """Multiline test bodies as JSON arrays stay parseable (Stage A JSON fix)."""
    payload = {
        "task_title": "Add redact() helper",
        "task_summary": "Implement redact() to mask secret values.",
        "impl_path": _IMPL,
        "test_path": "tests/test_redaction_stagea.py",
        "test_lines": _GOOD_TEST.splitlines(),
        "confidence": 0.9,
    }
    inbox, report = _run(workspace, llm=FakeLLM([json.dumps(payload)]))
    assert report.status == "proposed"
    item = next(i for i in inbox.list() if i.id == report.approval_id)
    assert "def test_" in item.payload["test_content"]
    assert "redact" in item.payload["test_content"]
