"""Приговор памяти пишется вместе с основанием.

Замер 2026-09-21 по `data/episodic_memory.jsonl`: `usage_eligible` записан в
200 эпизодов из 200, `relevance_score` — в 0 из 200. Ось соответствия
вопросу решала, попадёт ли разговор в опыт, и её число не доживало до диска:
`to_dict` его не писал, `from_dict` не читал. По журналу нельзя было узнать,
чем убит эпизод, — даже агенту, которого об этом спросили. Он так и ответил:
«в доказательствах нет журнальных записей с этими полями», и был прав.

`answer_quality_score` сюда не входит намеренно: он выводится из счётчиков
при чтении (`from_dict` пересчитывает его), значит основание у него на диске
есть. У соответствия выводить не из чего — его можно только сохранить.

Отсутствующий ключ по-прежнему означает «не мерили»: строка, записанная до
этой правки, остаётся побайтно прежней.
"""
from __future__ import annotations

from core.smart_memory import EpisodeRecord


def _episode(**kw) -> EpisodeRecord:
    base = {"goal": "g", "question": "q", "outcome": "success",
            "summary": "s", "verified_chunks": 3}
    base.update(kw)
    return EpisodeRecord(**base)


def test_the_relevance_reaches_the_disk() -> None:
    row = _episode(relevance_score=0.221).to_dict()
    assert row.get("relevance_score") == 0.221


def test_the_relevance_comes_back_from_the_disk() -> None:
    row = _episode(relevance_score=0.221).to_dict()
    assert EpisodeRecord.from_dict(row).relevance_score == 0.221


def test_an_unmeasured_episode_writes_no_key() -> None:
    """Не мерили — ключа нет, как у всех строк до этой правки."""
    assert "relevance_score" not in _episode().to_dict()


def test_a_legacy_row_reads_back_as_unmeasured() -> None:
    legacy = _episode().to_dict()
    assert EpisodeRecord.from_dict(legacy).relevance_score is None
