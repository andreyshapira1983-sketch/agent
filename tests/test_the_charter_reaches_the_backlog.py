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


def test_a_failing_test_goal_becomes_an_engineering_action() -> None:
    a = _candidate_engineering_task(
        "Propose a failing-test task for the proven gap in the retrieval path")
    assert a is not None and a.action == "propose_engineering_task"


def test_a_russian_form_is_also_recognised() -> None:
    a = _candidate_engineering_task(
        "Возьми инженерного кандидата из бэклога и подготовь раскол модуля")
    assert a is not None


def test_the_first_live_engineering_goal_maps(tmp_path: Path) -> None:
    """Verbatim: the first engineering goal Sol ever chose (2026-08-19,
    the tick after the backlog became visible) must reach the hands."""
    a = _candidate_engineering_task(
        "Analyze core/smart_memory.py and produce a human-reviewed "
        "module-split proposal with boundaries, dependency map, API "
        "compatibility constraints, migration steps, and test plan; "
        "make no code changes.")
    assert a is not None and a.action == "propose_engineering_task"


def test_the_scheduled_tick_goal_maps_too(tmp_path: Path) -> None:
    """Verbatim from the 15:58 scheduled tick — the third vocabulary miss in
    a day ('a plan to split the oversized module'). The mapping must be
    structural: a named .py target plus engineering context, not keywords."""
    a = _candidate_engineering_task(
        "Propose a plan to split the oversized module 'core/smart_memory.py' "
        "into focused modules to enhance maintainability and clarity.")
    assert a is not None and a.action == "propose_engineering_task"


def test_a_document_goal_is_not_engineering() -> None:
    assert _candidate_engineering_task(
        "Draft a proposal for the 'EVIDENCE_RECORD_SCHEMA.md' document") is None


def test_a_study_goal_is_not_engineering() -> None:
    assert _candidate_engineering_task(
        "Research existing frameworks for agent identity on the web") is None


def test_engineering_outranks_the_document() -> None:
    from core.best_next_action import _P_CHARTER_DOCUMENT

    a = _candidate_engineering_task(
        "Propose a failing-test task from the engineering backlog")
    assert a is not None and a.priority > _P_CHARTER_DOCUMENT


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


def test_the_charter_vocabulary_allows_engineering(tmp_path: Path) -> None:
    llm = _SpyLLM()
    propose_charter_goal(llm, _workspace(tmp_path))
    assert "failing-test" in llm.system or "engineering" in llm.system


def test_the_charter_prompt_shows_the_backlog(tmp_path: Path, monkeypatch) -> None:
    import core.charter_goal as cg

    monkeypatch.setattr(cg, "_backlog_lines", lambda root: (
        "split of core/smart_memory.py (1930 lines over the 800 limit)",
    ))
    llm = _SpyLLM()
    propose_charter_goal(llm, _workspace(tmp_path))
    assert "smart_memory" in llm.user
    assert "backlog" in llm.user.lower()


def test_an_empty_backlog_adds_no_noise(tmp_path: Path, monkeypatch) -> None:
    import core.charter_goal as cg

    monkeypatch.setattr(cg, "_backlog_lines", lambda root: ())
    llm = _SpyLLM()
    propose_charter_goal(llm, _workspace(tmp_path))
    assert "backlog" not in llm.user.lower()


# ── the campaign hands: the action reaches the self-build producer ─────────


def test_the_engineering_step_calls_the_producer(tmp_path: Path, monkeypatch) -> None:
    import core.campaign_io as cio

    calls: dict = {}

    def _fake_produce(**kw):
        calls.update(kw)
        return SimpleNamespace(status="proposed", approval_id="ain_road_1",
                               reason="", target_path="core/smart_memory.py")

    monkeypatch.setattr(
        "core.self_build_producer.produce_self_apply_proposal", _fake_produce)
    note = cio._propose_engineering_step(
        agent=SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda r: object()),
            log=SimpleNamespace(log=lambda *a, **k: None),
        ),
        workspace=tmp_path,
        approval_inbox=SimpleNamespace(),
    )
    assert "ain_road_1" in note
    assert calls.get("workspace") == tmp_path


def test_a_refusal_is_surfaced_not_hidden(tmp_path: Path, monkeypatch) -> None:
    def _fake_produce(**kw):
        return SimpleNamespace(status="dirty_tree_wait", approval_id=None,
                               reason="git working tree is not clean",
                               target_path=None)

    monkeypatch.setattr(
        "core.self_build_producer.produce_self_apply_proposal", _fake_produce)
    import core.campaign_io as cio

    note = cio._propose_engineering_step(
        agent=SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda r: object()),
            log=SimpleNamespace(log=lambda *a, **k: None),
        ),
        workspace=tmp_path,
        approval_inbox=SimpleNamespace(),
    )
    assert "dirty_tree_wait" in note
