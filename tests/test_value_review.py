"""TD-032 — human value-review capture layer.

Hermetic: no git, subprocess, LLM, provider, or network. The approval inbox and
value-review ledger are backed by files under ``tmp_path`` only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION
from core.self_build_producer import PRODUCER_ORIGIN
from core.value_review import (
    MAX_NOTE_CHARS,
    VALID_VERDICTS,
    InvalidVerdictError,
    ValueReview,
    ValueReviewLog,
)
from cli.commands_value_review import _handle_value_review, _handle_value_review_list


_ALL_VERDICTS = [
    "accepted",
    "rejected_low_value",
    "rejected_misleading_summary",
    "rejected_risky",
    "rejected_wrong_target",
]


# ── fakes ──────────────────────────────────────────────────────────────────────


class _FakeLog:
    def log(self, *args, **kwargs):  # pragma: no cover - trivial
        pass


class _FakeAgent:
    def __init__(self, inbox: ApprovalInbox | None = None):
        self.approval_inbox = inbox
        self.log = _FakeLog()


def _inbox(tmp_path: Path) -> ApprovalInbox:
    return ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")


def _executed_producer_item(inbox: ApprovalInbox):
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary="applied proposal",
        payload={"origin": PRODUCER_ORIGIN},
    )
    inbox.approve(item.id)
    inbox.mark_executed(item.id)
    return inbox.get(item.id)


def _ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "value_reviews.jsonl"


# ── ledger unit ────────────────────────────────────────────────────────────────


def test_all_verdicts_persist_and_reload(tmp_path):
    log = ValueReviewLog.for_workspace(tmp_path)
    for i, verdict in enumerate(_ALL_VERDICTS):
        log.append(f"ain_{i}", verdict)
    reloaded = ValueReviewLog.for_workspace(tmp_path)
    got = {r.item_id: r.verdict for r in reloaded.list()}
    assert got == {f"ain_{i}": v for i, v in enumerate(_ALL_VERDICTS)}
    assert set(_ALL_VERDICTS) == VALID_VERDICTS


def test_latest_verdict_wins_for_same_item(tmp_path):
    log = ValueReviewLog.for_workspace(tmp_path)
    log.append("ain_1", "accepted")
    log.append("ain_1", "rejected_low_value")
    effective = ValueReviewLog.for_workspace(tmp_path).effective_by_item_id()
    assert effective["ain_1"].verdict == "rejected_low_value"
    # both rows are retained (append-only history)
    assert len(ValueReviewLog.for_workspace(tmp_path).list()) == 2


def test_invalid_verdict_writes_nothing(tmp_path):
    log = ValueReviewLog.for_workspace(tmp_path)
    with pytest.raises(InvalidVerdictError):
        log.append("ain_1", "totally_bogus")
    assert not _ledger_path(tmp_path).exists()


def test_missing_and_corrupt_ledger_loads_empty(tmp_path):
    # missing file
    assert ValueReviewLog.for_workspace(tmp_path).list() == []
    # corrupt file degrades to [] (state layer quarantines bad rows)
    p = _ledger_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json at all\n{broken\n", encoding="utf-8")
    assert ValueReviewLog.for_workspace(tmp_path).list() == []


def test_note_is_redacted_and_truncated(tmp_path):
    log = ValueReviewLog.for_workspace(tmp_path)
    secret = "AKIAABCDEFGHIJKLMNOP"  # matches aws-access-key
    long_tail = "x" * (MAX_NOTE_CHARS + 200)
    log.append("ain_1", "accepted", note=f"leak {secret} {long_tail}")
    review = ValueReviewLog.for_workspace(tmp_path).list()[0]
    assert secret not in review.note  # redacted
    assert "[REDACTED:aws-access-key]" in review.note
    assert len(review.note) <= MAX_NOTE_CHARS  # truncated


def test_from_dict_rejects_malformed_rows():
    with pytest.raises(ValueError):
        ValueReview.from_dict({"item_id": "", "verdict": "accepted"})
    with pytest.raises(ValueError):
        ValueReview.from_dict({"item_id": "ain_1", "verdict": "bogus"})


# ── CLI eligibility (write nothing on any failure) ──────────────────────────────


def test_cli_records_verdict_for_eligible_item(tmp_path, capsys):
    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    assert _handle_value_review(f"{item.id} accepted looks good", agent, tmp_path) is True
    effective = ValueReviewLog.for_workspace(tmp_path).effective_by_item_id()
    assert effective[item.id].verdict == "accepted"
    assert effective[item.id].note == "looks good"
    assert "recorded" in capsys.readouterr().err


def test_cli_invalid_verdict_writes_nothing(tmp_path, capsys):
    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} nope", agent, tmp_path)
    assert not _ledger_path(tmp_path).exists()
    assert "invalid verdict" in capsys.readouterr().err


def test_cli_unknown_item_writes_nothing(tmp_path, capsys):
    agent = _FakeAgent(_inbox(tmp_path))
    _handle_value_review("ain_does_not_exist accepted", agent, tmp_path)
    assert not _ledger_path(tmp_path).exists()
    assert "unknown approval id" in capsys.readouterr().err


def test_cli_non_executed_item_writes_nothing(tmp_path, capsys):
    inbox = _inbox(tmp_path)
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s",
                     payload={"origin": PRODUCER_ORIGIN})  # pending, not executed
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} accepted", agent, tmp_path)
    assert not _ledger_path(tmp_path).exists()
    assert "not executed" in capsys.readouterr().err


def test_cli_wrong_operation_writes_nothing(tmp_path, capsys):
    inbox = _inbox(tmp_path)
    item = inbox.add(operation="something_else", summary="s",
                     payload={"origin": PRODUCER_ORIGIN})
    inbox.approve(item.id)
    inbox.mark_executed(item.id)
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} accepted", agent, tmp_path)
    assert not _ledger_path(tmp_path).exists()
    assert "operation" in capsys.readouterr().err


def test_cli_wrong_origin_writes_nothing(tmp_path, capsys):
    inbox = _inbox(tmp_path)
    item = inbox.add(operation=SELF_APPLY_OPERATION, summary="s",
                     payload={"origin": "repair"})
    inbox.approve(item.id)
    inbox.mark_executed(item.id)
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} accepted", agent, tmp_path)
    assert not _ledger_path(tmp_path).exists()
    assert "origin" in capsys.readouterr().err


# ── read-only / no-side-effect guarantees ───────────────────────────────────────


def test_cli_does_not_mutate_inbox_item_status(tmp_path):
    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} rejected_low_value", agent, tmp_path)
    # re-read from a fresh inbox bound to the same file
    reread = _inbox(tmp_path).get(item.id)
    assert reread.status == "executed"  # unchanged


def test_value_review_list_is_read_only(tmp_path, capsys):
    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    assert _handle_value_review_list("", agent, tmp_path) is True
    # listing must not create the ledger
    assert not _ledger_path(tmp_path).exists()
    out = capsys.readouterr().err
    assert item.id in out and "no verdict yet" in out


def test_only_expected_files_written_no_stray_side_effects(tmp_path):
    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} accepted", agent, tmp_path)
    written = {p.name for p in (tmp_path / "data").glob("*.jsonl")}
    assert "value_reviews.jsonl" in written
    # no budget/config file was created or touched anywhere under the workspace
    assert not (tmp_path / "config" / "budget_limits.json").exists()
    assert list(tmp_path.rglob("budget_limits.json")) == []


# ── TD-033: value review feeds the subagent registry (guarded) ──────────────────


def test_cli_reconciles_registry_after_recording(tmp_path):
    from core.subagent_registry import SubagentRegistry

    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    _handle_value_review(f"{item.id} accepted", agent, tmp_path)
    reg = SubagentRegistry.load(tmp_path)
    assert reg.roles["builder"].confirmed_value == 1


def test_cli_persists_even_if_registry_reconcile_fails(tmp_path, monkeypatch):
    import core.subagent_registry as sr

    def _boom(*args, **kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(sr.SubagentRegistry, "load", classmethod(_boom))

    inbox = _inbox(tmp_path)
    item = _executed_producer_item(inbox)
    agent = _FakeAgent(inbox)
    # Must not raise; the value review must still be persisted.
    assert _handle_value_review(f"{item.id} accepted", agent, tmp_path) is True
    effective = ValueReviewLog.for_workspace(tmp_path).effective_by_item_id()
    assert effective[item.id].verdict == "accepted"
