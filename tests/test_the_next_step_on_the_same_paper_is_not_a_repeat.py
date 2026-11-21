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

import json

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


def test_investigating_a_module_is_not_a_repeat_of_splitting_it() -> None:
    """Declined five times between 2026-09-01 and 09-02."""
    assert _repeats_recent(_TRACE, (_SPLIT,)) == ""


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
