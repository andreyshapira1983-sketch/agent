"""The agent has a need to improve its OWN code, and it is measured.

Day run 2026-09-19/20, after drives were switched on: the agent read its own
code in 133 of 198 tasks and filed NOT ONE proposal to change it. Three causes,
all in the code: the route to a change lived in the action menu and `goal_first`
bypasses the menu; there was no drive for «make myself better» at all; and a
drive task is read-only by construction. This drive is measured like the
others — time since its own last applied change — and it only counts when there
is material: a module of its own that no inbox item is waiting on and no
rollback lesson forbids.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.best_next_action_helpers import _is_engineering_goal, resolve_goal_subject
from core.drive_goal import _engineering_goal
from core.drives import compute_drives, last_self_change, self_improvement_targets
from core.self_build_rules import Lesson, LessonStore, default_lessons_path

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path, inbox_rows: list[dict]) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "big.py").write_text("x = 1\n" * 900, encoding="utf-8")
    (tmp_path / "core" / "waiting.py").write_text("y = 2\n" * 800, encoding="utf-8")
    (tmp_path / "core" / "punished.py").write_text("z = 3\n" * 700, encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "approval_inbox.jsonl").write_text(
        "\n".join(json.dumps({"payload": r}) for r in inbox_rows), encoding="utf-8")
    LessonStore(default_lessons_path(tmp_path)).add(Lesson(
        created_at="2026-09-19T11:00:00+00:00", origin="rule_approved_apply", proposal_id="p",
        failure="targeted tests failed", change="split", verification="red",
        outcome="rolled_back", scope=("core/punished.py",)))
    return tmp_path


_WAITING = {"id": "a", "status": "pending", "operation": "self_apply_lane.run",
            "payload": {"files": [{"path": "core/waiting.py"}]}}
_APPLIED = {"id": "b", "status": "executed", "operation": "self_apply_lane.run",
            "created_at": "2026-09-20T00:00:00+00:00", "updated_at": "2026-09-20T00:00:00+00:00",
            "payload": {"files": [{"path": "core/old.py"}]}}


def test_material_is_only_what_it_can_act_on_now(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_WAITING, _APPLIED])
    names = [rel for rel, _lines in self_improvement_targets(ws)]
    assert names == ["core/big.py"], names
    assert last_self_change(ws) == datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def test_the_need_grows_with_time_and_dies_without_material(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_WAITING, _APPLIED])
    value = compute_drives(ws, NOW + timedelta(hours=6))["self_improvement_need"]["value"]
    assert 0.5 < value < 0.999, value
    (ws / "core" / "big.py").unlink()
    blocked = compute_drives(ws, NOW + timedelta(hours=6))["self_improvement_need"]
    assert blocked["value"] == 0.0 and "свободных модулей нет" in blocked["why"]


def test_the_goal_names_the_module_and_its_own_action(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_WAITING, _APPLIED])
    pick = _engineering_goal(ws)
    assert pick is not None
    assert pick.action == "propose_engineering_task", "прозой свой код не чинится"
    assert _is_engineering_goal(pick.goal), "машина должна признать цель инженерной"
    assert resolve_goal_subject(pick.goal, exists=lambda rel: (ws / rel).is_file()) == "core/big.py"
    assert "approval_inbox.jsonl" in pick.success_check
    (ws / "core" / "big.py").unlink()
    assert _engineering_goal(ws) is None, "нет материала — нет цели"
