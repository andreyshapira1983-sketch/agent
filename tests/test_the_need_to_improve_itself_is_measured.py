"""The agent has a need to improve its OWN code, and it is measured.

Day run 2026-09-19/20, after drives were switched on: the agent read its own
code in 133 of 198 tasks and filed NOT ONE proposal to change it — the route to
a change lived in the action menu, `goal_first` bypasses the menu, there was no
drive for «make myself better», and drive tasks are read-only by construction.

First live run of the drive, the same morning, refused twice and both refusals
are pinned here: the biggest module was `core/self_build_producer.py`, which the
producer denies as a critical organ, and the producer stays silent entirely
while an undecided lane proposal waits for a human. Material the machine will
not take is not material.
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
_APPLIED = {"id": "b", "status": "executed", "operation": "self_apply_lane.run",
            "created_at": "2026-09-20T00:00:00+00:00", "updated_at": "2026-09-20T00:00:00+00:00",
            "payload": {"files": [{"path": "core/old.py"}]}}
_PENDING = {"id": "a", "status": "pending", "operation": "self_apply_lane.run",
            "payload": {"files": [{"path": "core/waiting.py"}]}}


#: Материал — модуль с ДОКАЗАТЕЛЬСТВОМ правки (core/split_proof.py), а не
#: толстый. До 2026-09-21 здесь стояло «x = 1» девятьсот раз, и тест требовал
#: считать это материалом: толщина была целью. Слово оператора того дня:
#: «надо доказать, что так надо сделать»; размер — только повод посмотреть.
#: Доказательство в фикстуре — дубль: одна и та же пара функций в нескольких
#: модулях. Исключения (критический орган, урок отката) проверяются на
#: модулях, у которых доказательство ЕСТЬ, иначе они проверяли бы пустоту.
_DUPLICATED = (
    "def _norm(text):\n    text = text.strip()\n    text = text.lower()\n"
    "    text = text.replace('ё', 'е')\n    parts = text.split()\n"
    "    return ' '.join(parts)\n\n\n"
    "def _clip(value, low, high):\n    if value < low:\n        return low\n"
    "    if value > high:\n        return high\n    return value\n\n\n"
)


def _workspace(tmp_path: Path, inbox_rows: list[dict]) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "big.py").write_text(_DUPLICATED + "x = 1\n" * 900, encoding="utf-8")
    (tmp_path / "core" / "twin.py").write_text(_DUPLICATED, encoding="utf-8")
    (tmp_path / "core" / "punished.py").write_text(_DUPLICATED + "z = 3\n" * 1500,
                                                   encoding="utf-8")
    # В списке критических органов производителя заявок — его полоса не берёт.
    (tmp_path / "core" / "self_apply_lane.py").write_text(_DUPLICATED + "c = 4\n" * 2000,
                                                          encoding="utf-8")
    # Самый толстый файл дерева без единого доказательства.
    (tmp_path / "core" / "fat.py").write_text("f = 5\n" * 5000, encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "approval_inbox.jsonl").write_text(
        "\n".join(json.dumps({"payload": r}) for r in inbox_rows), encoding="utf-8")
    LessonStore(default_lessons_path(tmp_path)).add(Lesson(
        created_at="2026-09-19T11:00:00+00:00", origin="rule_approved_apply", proposal_id="p",
        failure="targeted tests failed", change="split", verification="red",
        outcome="rolled_back", scope=("core/punished.py",)))
    return tmp_path


def test_material_is_only_what_the_lane_would_take(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_APPLIED])
    names = [rel for rel, _lines in self_improvement_targets(ws)]
    assert names[0] == "core/big.py", names
    # критический и наказанный уроком — не материал, хотя доказательство у них есть
    assert "core/self_apply_lane.py" not in names and "core/punished.py" not in names, names
    assert last_self_change(ws) == datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def test_thickness_alone_is_not_material(tmp_path: Path) -> None:
    """Самый толстый файл без доказательства — не цель (2026-09-21)."""
    ws = _workspace(tmp_path, [_APPLIED])
    assert "core/fat.py" not in [rel for rel, _lines in self_improvement_targets(ws)]


def test_an_undecided_proposal_means_no_material_at_all(tmp_path: Path) -> None:
    """Производитель заявок молчит, пока человек не решил предыдущую."""
    ws = _workspace(tmp_path, [_APPLIED, _PENDING])
    assert self_improvement_targets(ws) == []
    drive = compute_drives(ws, NOW + timedelta(hours=6))["self_improvement_need"]
    assert drive["value"] == 0.0 and "занято ящиком" in drive["why"]
    assert _engineering_goal(ws) is None, "нет материала — нет цели"


def test_the_need_grows_with_time_when_there_is_material(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_APPLIED])
    value = compute_drives(ws, NOW + timedelta(hours=6))["self_improvement_need"]["value"]
    assert 0.5 < value < 0.999, value


def test_the_goal_names_the_module_and_its_own_action(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, [_APPLIED])
    pick = _engineering_goal(ws)
    assert pick is not None
    assert pick.action == "propose_engineering_task", "прозой свой код не чинится"
    assert _is_engineering_goal(pick.goal), "машина должна признать цель инженерной"
    assert resolve_goal_subject(pick.goal, exists=lambda rel: (ws / rel).is_file()) == "core/big.py"
    # Успех — след правки в коде, а не появление заявки в ящике: тот критерий
    # мерил бумажку и не отличал сделанное от несделанного (2026-09-21).
    assert "undefined:" in pick.success_check and "core/big.py" in pick.success_check
    assert "approval_inbox" not in pick.success_check
