"""Одобрение цели не теряет силу, когда кампания дописывает к цели свои заметки."""
from __future__ import annotations

from core.autonomous_runtime import AutonomousRuntime

GOAL = "Посчитай выручку за неделю и запиши итог одной строкой."
NOTE = "\n\nThis is your own chosen goal. Do the work yourself with your tools."
PROGRESS = "\n\nПрогресс прошлых заходов по этой цели (data/goal_progress/782923b1221c.jsonl):\n- 2026-11-10T15:25: записано: ничего"


def test_progress_notes_do_not_change_the_permission_key() -> None:
    first = AutonomousRuntime._effects_dedup_key(GOAL + NOTE)
    later = AutonomousRuntime._effects_dedup_key(GOAL + NOTE + PROGRESS)
    assert first == later == AutonomousRuntime._effects_dedup_key(GOAL)


def test_a_different_goal_still_needs_its_own_permission() -> None:
    other = "Удали все файлы в рабочей папке."
    assert AutonomousRuntime._effects_dedup_key(GOAL + NOTE) != AutonomousRuntime._effects_dedup_key(other + NOTE)


def test_a_file_named_only_in_the_notes_is_not_the_subject() -> None:
    plain = AutonomousRuntime._effects_dedup_key(GOAL)
    assert AutonomousRuntime._effects_dedup_key(GOAL + PROGRESS) == plain
