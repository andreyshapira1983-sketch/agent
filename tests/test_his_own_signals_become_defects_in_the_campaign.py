"""Свои сигналы становятся дефектами и в автомате, а не только в команде CLI.

Проверка 2026-09-22 перед восьмичасовым прогоном: приём наблюдений
(`core/defect_intake.intake_observations`) висел на
`core.self_build_memory.sync_self_improvement_issue_registry`, а её зовёт
только `cli/commands_approval.py`. В кампании этот путь не проходит ни разу,
поэтому починенный накануне корень в автомате был мёртв. Приём перенесён
туда, где дефекты и берутся в работу, — в `core.patch_route.defect_goal`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.patch_route import defect_goal
from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry


def _observation(root: Path) -> None:
    path = root / "data" / "causal_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = datetime(2026, 9, 22, 21, tzinfo=timezone.utc).isoformat()
    row = {
        "fingerprint": "obs-blind-write",
        "observed_mismatch": "запись сочинена до чтения: тест импортировал несуществующую функцию",
        "defect_signals": ["blind_write"],
        "evidence_refs": ["logs/trace_x.jsonl:12"],
        "occurrences": 3,
        "last_seen": seen,
        "first_seen": seen,
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def test_the_campaign_goal_path_files_repeated_observations(tmp_path: Path) -> None:
    _observation(tmp_path)
    assert not SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).list()

    goal = defect_goal(tmp_path)

    filed = SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).list()
    assert filed, "повторное наблюдение не стало дефектом на пути кампании"
    assert goal is not None and goal.drive == "self_improvement_need"


def test_a_broken_observation_store_does_not_break_goal_choice(tmp_path: Path) -> None:
    path = tmp_path / "data" / "causal_observations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{не json\n", encoding="utf-8")

    assert defect_goal(tmp_path) is None


def test_the_same_observation_is_not_filed_twice(tmp_path: Path) -> None:
    """Приём узнаёт принятое наблюдение по своей улике, а не по тексту дефекта."""
    _observation(tmp_path)
    defect_goal(tmp_path)
    first = SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).list()
    assert len(first) == 1

    from core.defect_intake import intake_observations

    assert intake_observations(tmp_path) == [], "то же наблюдение принято во второй раз"
    again = SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH).list()
    assert [i.last_seen for i in again] == [i.last_seen for i in first]
