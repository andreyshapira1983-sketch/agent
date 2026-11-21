"""The road from the charter to the engineering backlog exists.

Documentation attractor, measured live 2026-08-18/19: 100% of self-chosen
goals were *_CONTRACT/*_SCHEMA documents while 12 real engineering
candidates sat untouched and self-build never ran autonomously. The cage
had two bars (my own): the charter vocabulary allowed only «reading,
analysing and proposing», and no action candidate could turn an
engineering goal into Stage A / self-build work. This opens the road —
LANGUAGE and WIRING, not rights: every product is still an approval item
a human blesses.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import _candidate_engineering_task
from core.charter_goal import propose_charter_goal

# ── the action candidate: engineering goals become engineering actions ─────


def test_a_backlog_goal_becomes_an_engineering_action() -> None:
    a = _candidate_engineering_task(
        "Turn the top engineering backlog candidate (split of "
        "core/smart_memory.py) into a self-build proposal")
    assert a is not None and a.action == "propose_engineering_task"


# ── the charter sees the backlog and may speak engineering ─────────────────


_CHARTER = """# CORPORATE MODEL

1. **Evidence.** Every durable memory record should carry provenance and time.
2. **Engineering.** The system maintains itself through measured, reviewed change.
"""


class _SpyLLM:
    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.system, self.user = system, user
        return json.dumps({
            "goal": "Turn the top backlog candidate into a self-build proposal",
            "anchor_id": 1, "why_now": "now",
            "success_check": "an approval item exists",
        })


def _workspace(tmp_path: Path) -> Path:
    p = tmp_path / "knowledge" / "doctrine" / "future" / "CORPORATE_MODEL.md"
    p.parent.mkdir(parents=True)
    p.write_text(_CHARTER, encoding="utf-8")
    return tmp_path


def test_the_charter_prompt_shows_the_backlog(tmp_path: Path, monkeypatch) -> None:
    import core.charter_goal as cg

    monkeypatch.setattr(cg, "_backlog_lines", lambda root: (
        "split of core/smart_memory.py (1930 lines over the 800 limit)",
    ))
    llm = _SpyLLM()
    propose_charter_goal(llm, _workspace(tmp_path))
    assert "smart_memory" in llm.user
    assert "backlog" in llm.user.lower()


# ── the campaign hands: the action reaches the self-build producer ─────────


def test_a_refusal_is_surfaced_not_hidden(tmp_path: Path, monkeypatch) -> None:
    def _fake_produce(**kw):
        return SimpleNamespace(status="dirty_tree_wait", approval_id=None,
                               reason="git working tree is not clean",
                               target_path=None)

    monkeypatch.setattr(
        "core.self_build_producer.produce_self_apply_proposal", _fake_produce)
    import core.campaign_io as cio

    events: list[tuple[str, dict]] = []

    note = cio._propose_engineering_step(
        agent=SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda r: object()),
            log=SimpleNamespace(
                log=lambda event, payload=None, **k: events.append((event, payload or {}))),
        ),
        workspace=tmp_path,
        approval_inbox=SimpleNamespace(),
    )
    # Block 3 (L9, 2026-09-03): surfaced by name in the LOG — and not counted.
    # Until then the string `engineering_declined:dirty_tree_wait` travelled
    # as the cycle's proposal and made a cycle of waiting «useful».
    assert note is None
    assert any(p.get("status") == "dirty_tree_wait"
               for e, p in events if e == "campaign_engineering_proposed"), events
