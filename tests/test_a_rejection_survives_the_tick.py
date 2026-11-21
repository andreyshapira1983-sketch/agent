"""3a/3b: a charter rejection becomes memory, and the selector receives it.

The Groundhog-Day series (2026-08-18/19, five+ identical declines): the
novelty gate said «нет», stdout heard it, no memory organ did — so every
tick asked the same Sol the same byte-identical question. Operator's
ladder, bottom rung first: rejection → survives the tick (3a, prove the
WRITE) → the next selector actually receives it (3b, prove the READ) →
only then a matched test of what Sol does differently. No reflection is
built here; a decision simply becomes a fact that outlives its tick.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.charter_goal import DECISIONS_RELPATH, propose_charter_goal

_CHARTER = """# CORPORATE MODEL

1. **Evidence.** Every durable memory record should carry provenance and time.
2. **Roles.** Durable specialised roles carry explicit contracts and budgets.
3. **Learning.** Lessons are falsified by measurement, never assumed working.
"""


def _workspace(tmp_path: Path) -> Path:
    p = tmp_path / "knowledge" / "doctrine" / "future" / "CORPORATE_MODEL.md"
    p.parent.mkdir(parents=True)
    p.write_text(_CHARTER, encoding="utf-8")
    return tmp_path


def _ran_campaign(ws: Path, goal: str) -> None:
    """The novelty gate reads the CAMPAIGN ledger — simulate a run."""
    p = ws / "data" / "campaign_ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"goal": goal}) + "\n")


class _FixedLLM:
    def __init__(self, goal: str) -> None:
        self._goal = goal
        self.user_prompts: list[str] = []

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.user_prompts.append(user)
        return json.dumps({
            "goal": self._goal,
            "anchor_id": 0,
            "why_now": "now",
            "success_check": "a reviewer sees the draft",
        })


def _decisions(tmp_path: Path) -> list[dict]:
    p = tmp_path / DECISIONS_RELPATH
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        out.append(rec.get("payload", rec))
    return out


_GOAL = "Draft a proposal for the structure of the EVIDENCE_RECORD_SCHEMA document"


def test_the_selector_receives_the_rejection(tmp_path: Path) -> None:
    """3b: the NEXT ask's prompt carries the declined goal explicitly."""
    ws = _workspace(tmp_path)
    _ran_campaign(ws, _GOAL)
    propose_charter_goal(_FixedLLM(_GOAL), ws)          # declined, recorded
    llm = _FixedLLM("Review role budget rules for the durable role contracts")
    propose_charter_goal(llm, ws)
    prompt = llm.user_prompts[-1]
    assert "DECLINED" in prompt
    assert _GOAL[:40] in prompt
    assert "DIFFERENT" in prompt


def test_pre_goal_failures_are_recorded_without_a_goal(tmp_path: Path) -> None:
    """A model that returns garbage still leaves a decision row — the tick
    happened, the memory must know it happened."""
    ws = _workspace(tmp_path)

    class _Garbage:
        def complete(self, **_kw) -> str:
            return "not json at all"

    report = propose_charter_goal(_Garbage(), ws)
    assert report.status == "declined"
    rows = _decisions(ws)
    assert rows and rows[-1]["status"] == "declined"
    assert rows[-1]["goal"] == ""
