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
