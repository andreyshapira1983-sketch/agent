"""Интерес — от прогресса и событий, а не от часов (слово оператора 24.09).

До того предметный драйв рос формулой 1 - exp(-часов/tau): три часа без
математики — «хочется математики», освоенной или проваливаемой. Литература
(MAGELLAN, ICML 2025; Oudeyer — learning progress; OMNI): интерес там, где
умение меняется. Замер 24.09 по 388 целям: самоулучшение — 69 попыток, 1% удач,
последние 8 пустые; это беговая дорожка, а не учёба.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.drives import compute_drives, drive_outcomes, learning_interest

NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def _history(ws: Path, drive: str, outcomes: str, start: datetime = NOW - timedelta(hours=10)) -> None:
    """Попытки драйва: «+» — цель засчитана, «-» — пустой исход."""
    data = ws / "data"
    data.mkdir(parents=True, exist_ok=True)
    dec, led = [], []
    for i, mark in enumerate(outcomes):
        ts = start + timedelta(minutes=10 * i)
        goal = f"{drive} goal {i}"
        dec.append({"ts": ts.isoformat(), "status": "proposed", "drive": drive, "goal": goal})
        led.append({"ts": (ts + timedelta(minutes=2)).isoformat(), "goal": goal,
                    "result": "completed" if mark == "+" else "empty"})
    with (data / "drive_decisions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in dec))
    with (data / "campaign_ledger.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in led))


def test_time_alone_does_not_raise_interest(tmp_path: Path) -> None:
    _history(tmp_path, "competence_math", "--++++++")
    early = compute_drives(tmp_path, NOW)["competence_math"]["value"]
    later = compute_drives(tmp_path, NOW + timedelta(hours=30))["competence_math"]["value"]
    assert early == later, "hours without math are not a reason to study math"


def test_mastered_and_hopeless_areas_are_not_interesting_changing_ones_are() -> None:
    assert learning_interest([True] * 8)[0] == 0.0, "all solved — nothing left to learn here"
    assert learning_interest([False] * 8)[0] == 0.0, "nothing ever works — not learnable now"
    value, mode, why = learning_interest([False, False, False, True, True, True, True, True])
    assert value > 0.5 and mode == "progress" and "было 1 из 4" in why and "стало 4 из 4" in why


def test_a_barely_tried_area_is_worth_trying_and_the_pull_fades_with_tries() -> None:
    values = [learning_interest([True] * n)[0] for n in range(4)]
    assert values[0] == 0.3 and values == sorted(values, reverse=True)
    assert learning_interest([])[1] == "explore"


def test_only_outcomes_of_the_drive_own_goals_count(tmp_path: Path) -> None:
    _history(tmp_path, "competence_physics", "+-+-")
    _history(tmp_path, "competence_cs", "++++")
    out = drive_outcomes(tmp_path, NOW)
    assert out["competence_physics"] == [True, False, True, False]
    assert out["competence_cs"] == [True] * 4
    # Замер можно переиграть в прошлом: будущие исходы не видны.
    past = drive_outcomes(tmp_path, NOW - timedelta(hours=10) + timedelta(minutes=5))
    assert past == {"competence_physics": [True], "competence_cs": [True]}


def test_old_attempts_are_forgotten_and_the_area_is_new_again(tmp_path: Path) -> None:
    _history(tmp_path, "competence_math", "++++++++", start=NOW - timedelta(days=10))
    drive = compute_drives(tmp_path, NOW)["competence_math"]
    assert drive["mode"] == "explore" and drive["value"] == 0.3


def test_an_event_drive_whose_goals_never_work_loses_its_pull(tmp_path: Path) -> None:
    """Живые данные 24.09: после починки самоулучшения сверху встало
    «застревание» — 8 последних его целей пустые."""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    rows = [{"ts": (NOW - timedelta(minutes=i)).isoformat(), "result": r, "goal": "g"}
            for i, r in enumerate(["failed"] * 6 + ["completed"] * 3)]
    (data / "campaign_ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                                encoding="utf-8")
    fresh = compute_drives(tmp_path, NOW)["maintenance_need"]["value"]
    _history(tmp_path, "maintenance_need", "--------", start=NOW - timedelta(hours=5))
    stuck = compute_drives(tmp_path, NOW)["maintenance_need"]
    assert stuck["value"] < fresh / 3 and "выходит: 0 из 8" in stuck["why"]


def _tasked_defect(ws: Path) -> None:
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

    SelfImprovementIssueRegistry(ws / DEFAULT_ISSUE_PATH).upsert_failure(
        "FileNotFoundError: нет файла core/x.py", "2026-09-24T00:00:00+00:00")


def test_a_repair_treadmill_loses_its_pull(tmp_path: Path) -> None:
    """24.09: 69 попыток самоулучшения, 1% удач. Повод есть, а выхода нет —
    интерес падает; удачные починки его возвращают."""
    _tasked_defect(tmp_path)
    fresh = compute_drives(tmp_path, NOW)["self_improvement_need"]["value"]
    _history(tmp_path, "self_improvement_need", "--------")
    treadmill = compute_drives(tmp_path, NOW)["self_improvement_need"]
    assert treadmill["value"] < fresh / 3, (fresh, treadmill)
    assert "удач в последних починках: 0 из 8" in treadmill["why"]

    assert learning_interest([])[0] == 0.3  # a fresh area is still worth a try

    good = tmp_path / "good"
    _tasked_defect(good)
    _history(good, "self_improvement_need", "+-++-+++")
    assert compute_drives(good, NOW)["self_improvement_need"]["value"] > treadmill["value"] * 4
