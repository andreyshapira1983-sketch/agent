"""Метка «урок» — за сделанную работу, не за пустой ход.

Слово оператора 2026-09-23: «надо почистить всё враньё, потому что если это
враньё, оно нам не нужно». Замер того дня: 287 записей опыта из 300 — пустые
ходы производителя заявок, и КАЖДАЯ носила метку `lesson`. Метка защищает от
вытеснения (`EpisodicMemoryStore.PROTECTED_TAGS`), поэтому штатная чистка
дублей отказывалась их трогать — 0 из 287. Опыт показывал «успешной задачи ни
разу», драйв компетентности стоял на 1.0 и вечно перебивал самопочинку.

Дедуп при записи (2026-09-22) останавливал приток, но метку оставлял, а
значит и защиту: настоящее лекарство — не выдавать метку за отсутствие работы.
"""
from __future__ import annotations

from core.self_build_memory import build_self_build_episode


def _episode(status: str):
    return build_self_build_episode(
        "self-build-produce",
        {"status": status, "reason": f"{status}: ходу не нашлось цели"},
    )


def test_the_standing_dedup_can_now_collapse_empty_runs() -> None:
    """Раньше метка защищала повтор от штатной чистки — теперь нет."""
    from core.episodic_hygiene import select_duplicate_episodes

    twins = [_episode("no_grounded_target") for _ in range(3)]
    for index, episode in enumerate(twins):
        object.__setattr__(episode, "id", f"ep_{index}")

    assert len(select_duplicate_episodes(twins)) == 2
