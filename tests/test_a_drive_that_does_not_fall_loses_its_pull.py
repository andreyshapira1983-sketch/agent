"""Step 2 of endogenous activation: a drive chooses the need, the model the task.

Pinned from the live passes of 2026-09-19:
* the strongest drives at first were the agent's own unexplained detectors and
  blocked obligations — needs that work does not satisfy; «take the strongest»
  would have looped. A drive that does not fall after its task loses half its
  weight (habituation), and weight comes back with time;
* the model was shown «uncertainty = 0.70» and proposed «find in the logs when
  the drive uncertainty decreased» — a task about the mechanism. It now sees the
  need in words with its material, never the variable name;
* a task the executor would bounce back to a human is refused before it is given.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.drive_goal import _problem, choose_drive, need_text

NOW = datetime(2026, 9, 19, 19, 5, tzinfo=timezone.utc)


def _drives(**values: float) -> dict:
    base = {"idle_time": 0.9, "uncertainty": 0.7, "unfinished_obligations": 0.55,
            "competence_world": 0.54, "competence_math": 0.4, "economic_opportunity": 0.0}
    base.update(values)
    return {k: {"value": v, "why": "t"} for k, v in base.items()}


def test_a_drive_that_did_not_fall_yields_to_the_next() -> None:
    state = {"weights": {}, "last": {"drive": "uncertainty", "value": 0.7, "ts": NOW.isoformat()},
             "updated": NOW.isoformat()}
    drive, state = choose_drive(_drives(uncertainty=0.7), state, NOW)
    assert state["weights"]["uncertainty"] == 0.5
    assert drive == "unfinished_obligations"


def test_a_drive_that_fell_keeps_its_full_weight() -> None:
    state = {"weights": {"competence_math": 0.5}, "last": {"drive": "competence_math", "value": 0.8},
             "updated": NOW.isoformat()}
    _, state = choose_drive(_drives(competence_math=0.1), state, NOW)
    assert state["weights"]["competence_math"] == 1.0


def test_a_plateau_does_not_starve_the_subjects() -> None:
    """Ночь 24→25.09: все четыре предмета 0.00 («было 3 из 4, стало 3 из 4»),
    выше — события о себе; кампания часами объясняла свои детекторы. Как у
    Forestier/Oudeyer (JMLR 2022, 20% случайного выбора пространства целей):
    каждый пятый выбор — предметная область, по кругу, и каждая за круг."""
    night = {"idle_time": 0.44, "maintenance_need": 0.15, "novelty_need": 0.11,
             "uncertainty": 0.11, "competence_math": 0.0, "competence_physics": 0.0,
             "competence_cs": 0.0, "competence_world": 0.0, "economic_opportunity": 0.0}
    state: dict = {"weights": {}}
    picks = []
    for i in range(20):
        drives = {k: {"value": v, "why": "t"} for k, v in night.items()}
        drive, state = choose_drive(drives, state, NOW + timedelta(minutes=i))
        picks.append((drive, drives.get(drive, {}).get("mode")))
    explored = [d for d, mode in picks if mode == "random"]
    assert len(explored) == 4, picks
    assert sorted(explored) == ["competence_cs", "competence_math", "competence_physics", "competence_world"]


def test_the_model_sees_the_need_not_the_variable(tmp_path: Path) -> None:
    text = need_text("competence_physics", {"value": 0.42, "why": "последняя успешная задача: 97 мин назад"}, tmp_path)
    assert "физике" in text and "competence" not in text and "0.42" not in text


def test_a_task_the_executor_would_bounce_back_is_refused(tmp_path: Path) -> None:
    bounced = ("Прочитай core/a.py и core/b.py и напиши в ответе, что стоило бы изменить")
    assert "переспросит человека" in _problem(bounced, tmp_path)
    assert _problem("Найди в math_study/library/txt/Judson_AbstractAlgebra.txt теорему Коши и проверь её расчётом",
                    tmp_path) == ""


def test_a_check_naming_files_that_are_not_there_is_refused(tmp_path: Path) -> None:
    """Live pass of step 3: «Theorem 9.x» in Judson_AbstractAlgebra.txt — the
    theorem was found right, the outcome was recorded «no trace», the drive punished."""
    book = tmp_path / "math_study" / "library" / "txt" / "Judson_AbstractAlgebra.txt"
    book.parent.mkdir(parents=True)
    book.write_text("Theorem 9.12 Cayley", encoding="utf-8")
    goal = "В книге math_study/library/txt/Judson_AbstractAlgebra.txt найди теорему Кэли и проверь её расчётом"
    guessed = "Цитата из Judson_AbstractAlgebra.txt с номером теоремы (например, Theorem 9.x)"
    assert "которых нет на диске" in _problem(goal, tmp_path, guessed)
    assert _problem(goal, tmp_path, "Цитата из math_study/library/txt/Judson_AbstractAlgebra.txt "
                                    "с номером теоремы и страницей") == ""


def test_open_defects_raise_the_need_to_repair_itself(tmp_path) -> None:
    """2026-09-22 18:07: вес самопочинки был 0.0 при семи открытых дефектах —
    считались только структурные доказательства (дубль, раскол), и путь
    самопочинки не запускался ни разу."""
    from datetime import datetime, timezone

    from core.drives import compute_drives
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

    now = datetime(2026, 9, 22, 18, tzinfo=timezone.utc)
    (tmp_path / "data").mkdir(exist_ok=True)
    assert compute_drives(tmp_path, now)["self_improvement_need"]["value"] == 0.0

    registry = SelfImprovementIssueRegistry(tmp_path / DEFAULT_ISSUE_PATH)
    registry.upsert_failure("FileNotFoundError: нет файла core/x.py", "2026-09-22T00:00:00+00:00")
    drive = compute_drives(tmp_path, now)["self_improvement_need"]
    # 24.09: вес — повод × «выходит ли починка»; без истории починок — половина.
    assert drive["value"] > 0.1 and "открытых дефектов в реестре с задачей: 1" in drive["why"]
