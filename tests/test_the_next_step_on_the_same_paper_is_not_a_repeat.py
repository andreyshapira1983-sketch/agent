"""The repeat guard and the ledger it reads (audit L7, L11, L12; block 3, 2026-09-03).

L7 — the file-identity rule (2026-08-19) equated every two goals on the same
file: «implement X.md» was a repeat of «propose a design for X.md» (the ONE
PAPER RULE's own next step, declined at 03:47), «trace core/self_build_producer.py»
a repeat of «draft a proposal to split core/self_build_producer.py» (five
times). 23 of 80 goal decisions were «repeat». Identity now also compares
the STAGE of work: for a document, paper and code differ; for a module, a
proposal about it and the edit itself are one work, investigation another.

L11 — the recent window was cut by FIRST appearance, so a theme worked
yesterday fell out if it first sounded a month ago.

L12 — `_recent_goals` asked the ledger for `work_done`, which the ledger
never wrote (0 of 479 rows); `blocked`/`failed` runs occupied their theme.
"""
from __future__ import annotations

import inspect
import json

from core import campaign as campaign_mod
from core.campaign_ledger import CampaignCycleRecord
from core.charter_goal import _recent_goals, _repeats_recent

_DESIGN = (
    "Propose a reviewed design for a MEMORY_LIFECYCLE_CONTRACT.md schema draft "
    "covering record states and transitions"
)
_IMPLEMENT = (
    "Implement the MEMORY_LIFECYCLE_CONTRACT.md schema as a validated data "
    "model in core/smart_memory.py with tests"
)
_TRACE = (
    "Trace how the central agent's own material claims are recorded in "
    "core/self_build_producer.py and what the critic reads"
)
_SPLIT = (
    "Draft one reviewed proposal to split core/self_build_producer.py "
    "(1297 code lines) into a producer contract and role modules"
)


def test_implementing_the_paper_is_not_a_repeat_of_designing_it() -> None:
    """The live specimen of 03:47: the paper rule's own next step."""
    assert _repeats_recent(_IMPLEMENT, (_DESIGN,)) == ""


def test_designing_the_paper_twice_is_still_a_repeat() -> None:
    again = "Design a MEMORY_LIFECYCLE_CONTRACT.md schema draft with record states"
    assert _repeats_recent(again, (_DESIGN,)) == _DESIGN


def test_investigating_a_module_is_not_a_repeat_of_splitting_it() -> None:
    """Declined five times between 2026-09-01 and 09-02."""
    assert _repeats_recent(_TRACE, (_SPLIT,)) == ""


def test_proposing_a_split_and_splitting_are_one_work() -> None:
    """For a module, the paper about the edit and the edit are the same hand."""
    doing = "Split core/self_build_producer.py into a producer contract and roles"
    assert _repeats_recent(doing, (_SPLIT,)) == _SPLIT


def test_a_goal_without_a_leading_verb_keeps_the_identity_rule() -> None:
    bare = "MEMORY_LIFECYCLE_CONTRACT.md: record states and their transitions"
    assert _repeats_recent(bare, (_DESIGN,)) == _DESIGN


def _ledger(tmp_path, rows: list[dict]) -> None:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def test_the_window_is_ordered_by_last_work(tmp_path) -> None:
    rows = [{"goal": "old theme", "result": "completed", "work_done": True,
             "ts": "2026-01-01T00:00:00+00:00"}]
    rows += [{"goal": f"theme {i}", "result": "completed", "work_done": True,
              "ts": f"2026-02-0{i}T00:00:00+00:00"} for i in range(1, 9)]
    rows.append({"goal": "old theme", "result": "completed", "work_done": True,
                 "ts": "2026-09-01T00:00:00+00:00"})
    _ledger(tmp_path, rows)

    goals = [g for g, _ in _recent_goals(tmp_path)]

    assert goals[-1] == "old theme", (
        "the theme worked most recently fell out of the window because it "
        f"first appeared earliest: {goals}"
    )


def test_the_ledgers_own_word_decides(tmp_path) -> None:
    _ledger(tmp_path, [
        {"goal": "asked back", "result": "completed", "work_done": False,
         "ts": "2026-09-01T00:00:00+00:00"},
        {"goal": "failed but produced", "result": "failed", "work_done": True,
         "ts": "2026-09-01T01:00:00+00:00"},
    ])

    goals = {g for g, _ in _recent_goals(tmp_path)}

    assert goals == {"failed but produced"}


def test_legacy_rows_without_the_word_use_the_outcome_and_the_product(tmp_path) -> None:
    _ledger(tmp_path, [
        {"goal": "blocked run", "result": "blocked", "ts": "2026-09-01T00:00:00+00:00"},
        {"goal": "blocked with a product", "result": "blocked", "proposal": "ain_1",
         "ts": "2026-09-01T01:00:00+00:00"},
        {"goal": "completed of old", "result": "completed",
         "ts": "2026-09-01T02:00:00+00:00"},
    ])

    goals = {g for g, _ in _recent_goals(tmp_path)}

    assert goals == {"blocked with a product", "completed of old"}


def test_the_campaign_writes_the_word_the_reader_asks_for() -> None:
    """Wiring, not presence: the record carries it and the run fills it."""
    record = CampaignCycleRecord(
        cycle=1, ts="t", goal="g", action="a", action_title="A", severity="low",
        priority=1, risk="read_only", idle=False, llm_calls_spent=0,
        cost_units_spent=0, result="completed", reason="", work_done=True,
    )
    assert record.to_dict()["work_done"] is True
    assert "work_done=outcome.did_work" in inspect.getsource(campaign_mod), (
        "the executed-cycle record no longer writes the outcome's word"
    )
