"""The verdict bridge: a review outcome becomes the author's memory.

Found 2026-08-19 while landing the agent's two doctrine drafts: no organ
ever learns what happened to his proposals — approved/denied died in the
inbox status field, denial reasons were not even capturable, and the goal
selector chose the next work as if past reviews never happened. Same
family as Groundhog Day, on the positive side of the ledger: the fact must
exist as state before any behaviour can grow from it. The bridge: every
approve/deny appends an outcome record (with the human's reason), and the
charter selector is SHOWN the recent verdicts on the agent's own work.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.charter_goal import propose_charter_goal


def _inbox(tmp_path: Path) -> ApprovalInbox:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")


def _outcomes(tmp_path: Path) -> list[dict]:
    p = tmp_path / "data" / "approval_outcomes.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        rows.append(rec.get("payload", rec))
    return rows


def test_a_denial_with_a_reason_becomes_a_record(tmp_path: Path) -> None:
    inbox = _inbox(tmp_path)
    item = inbox.add(operation="self_apply_lane.run",
                     summary="doctrine draft for X.md")
    inbox.deny(item.id, reason="invented attribute claim.state — mock-blind test")
    rows = _outcomes(tmp_path)
    assert rows and rows[-1]["verdict"] == "denied"
    assert "invented attribute" in rows[-1]["reason"]
    assert rows[-1]["summary"] == "doctrine draft for X.md"


def test_an_approval_is_a_record_too(tmp_path: Path) -> None:
    inbox = _inbox(tmp_path)
    item = inbox.add(operation="self_apply_lane.run",
                     summary="organisational roles contract")
    inbox.approve(item.id, reason="0% retell, invariants preserved")
    rows = _outcomes(tmp_path)
    assert rows and rows[-1]["verdict"] == "approved"
    assert "invariants" in rows[-1]["reason"]


def test_a_reasonless_verdict_still_lands(tmp_path: Path) -> None:
    inbox = _inbox(tmp_path)
    item = inbox.add(operation="x", summary="s")
    inbox.deny(item.id)
    rows = _outcomes(tmp_path)
    assert rows and rows[-1]["verdict"] == "denied"
    assert rows[-1]["reason"] == ""


def test_lifecycle_transitions_are_not_verdicts(tmp_path: Path) -> None:
    """executed/aborted are plumbing, not review outcomes."""
    inbox = _inbox(tmp_path)
    a = inbox.add(operation="x", summary="s1")
    inbox.approve(a.id)
    inbox.mark_executed(a.id)
    rows = _outcomes(tmp_path)
    assert [r["verdict"] for r in rows] == ["approved"]


def test_an_in_memory_inbox_does_not_crash(tmp_path: Path) -> None:
    inbox = ApprovalInbox(path=None)
    item = inbox.add(operation="x", summary="s")
    inbox.deny(item.id)  # nowhere to write — must simply not raise


# ── the reader: the charter selector sees the fate of past work ─────────────

_CHARTER = """# CORPORATE MODEL

1. **Evidence.** Every durable memory record should carry provenance and time.
2. **Roles.** Durable specialised roles carry explicit contracts and budgets.
"""


class _SpyLLM:
    def __init__(self) -> None:
        self.user = ""

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.user = user
        return json.dumps({
            "goal": "Review role budget rules for the durable role contracts",
            "anchor_id": 1, "why_now": "now",
            "success_check": "a reviewer sees the table",
        })


def _workspace(tmp_path: Path) -> Path:
    p = tmp_path / "knowledge" / "doctrine" / "future" / "CORPORATE_MODEL.md"
    p.parent.mkdir(parents=True)
    p.write_text(_CHARTER, encoding="utf-8")
    return tmp_path


def test_the_selector_sees_recent_verdicts(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    inbox = _inbox(ws)
    item = inbox.add(operation="self_apply_lane.run",
                     summary="doctrine draft for GHOST_SCHEMA.md")
    inbox.deny(item.id, reason="premature abstraction, no measured gap")
    llm = _SpyLLM()
    propose_charter_goal(llm, ws)
    assert "verdict" in llm.user.lower() or "VERDICT" in llm.user
    assert "GHOST_SCHEMA" in llm.user
    assert "premature abstraction" in llm.user


def test_no_verdicts_no_noise(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    llm = _SpyLLM()
    propose_charter_goal(llm, ws)
    assert "verdict" not in llm.user.lower()
