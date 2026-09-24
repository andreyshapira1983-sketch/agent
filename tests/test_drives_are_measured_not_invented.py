"""Drives are measured from the journals, never invented by the model.

Step 1 of the endogenous-activation work (operator, 2026-09-19): before any
behaviour changes, the agent's inner variables must read true against its own
records. Two traps found on the live data the same evening are pinned here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.drives import compute_drives, strongest

NOW = datetime(2026, 9, 19, 19, 0, tzinfo=timezone.utc)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_idle_is_the_only_clock(tmp_path: Path) -> None:
    """24.09: интерес — от прогресса, а не от часов; часы остались только у простоя,
    и содержания он не даёт (core/drives.py)."""
    _write(tmp_path / "data" / "campaign_ledger.jsonl", [
        {"ts": (NOW - timedelta(hours=2)).isoformat(), "result": "completed", "goal": "a"},
        {"ts": (NOW - timedelta(minutes=5)).isoformat(), "result": "idle", "goal": "a"},
    ])
    d = compute_drives(tmp_path, now=NOW)
    assert d["idle_time"]["value"] > 0.9, "two hours without useful work is a strong urge"
    assert d["competence_physics"]["value"] == 0.3 and d["competence_physics"]["mode"] == "explore",         "never tried physics — worth trying, not a full need"
    assert d["economic_opportunity"]["value"] == 0.0, "no source, honest zero"
    assert strongest(d)[0] == "idle_time"


def test_idling_is_not_counted_as_breakage(tmp_path: Path) -> None:
    """First live read: 14 of 20 cycles «broken» — they were idle, not broken."""
    _write(tmp_path / "data" / "campaign_ledger.jsonl",
           [{"ts": NOW.isoformat(), "result": "idle", "goal": "g"}] * 10
           + [{"ts": NOW.isoformat(), "result": "completed", "goal": "g"},
              {"ts": NOW.isoformat(), "result": "failed", "goal": "g"}])
    assert compute_drives(tmp_path, now=NOW)["maintenance_need"]["value"] == 0.5


def test_a_locked_obligation_is_not_an_obligation(tmp_path: Path) -> None:
    """Live pass of step 3: every «unfinished» item was locked — rollback lessons
    or a permission only a human grants. The drive called to them; the agent hit
    approval_wait."""
    import json

    from core.drives import open_obligations
    from core.self_build_rules import Lesson, LessonStore, default_lessons_path

    inbox = tmp_path / "data" / "approval_inbox.jsonl"
    inbox.parent.mkdir(parents=True)
    rows = [
        {"id": "a", "status": "pending", "operation": "autonomous_runtime.allow_effects", "payload": {}},
        {"id": "b", "status": "pending", "operation": "self_apply_lane.run",
         "payload": {"files": [{"path": "core/x.py"}]}},
        {"id": "c", "status": "pending", "operation": "self_apply_lane.run",
         "payload": {"files": [{"path": "core/y.py"}]}},
    ]
    inbox.write_text("\n".join(json.dumps({"payload": r}) for r in rows), encoding="utf-8")
    LessonStore(default_lessons_path(tmp_path)).add(Lesson(
        created_at="2026-09-19T11:00:00+00:00", origin="rule_approved_apply", proposal_id="b",
        failure="targeted tests failed", change="split core/x.py", verification="tests red",
        outcome="rolled_back", scope=("core/x.py",)))
    assert [r["id"] for r in open_obligations(tmp_path)] == ["c"]
