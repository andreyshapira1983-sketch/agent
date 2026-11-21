"""Метка даёт право выжить, но не право идти первым.

Замер, отвергнутые варианты и границы: MIR-164 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore

_QUERY = (
    "как устроен разбор кампании и где кампания читает журнал циклов "
    "и какие поля кампания пишет в леденец кампании"
)


def _episode(goal: str, *, verified: int, tags: tuple[str, ...]) -> EpisodeRecord:
    return EpisodeRecord(
        goal=goal,
        question=goal,
        outcome="success",
        summary=goal,
        verified_chunks=verified,
        tags=tags,
        usage_eligible=True,
    )


_RELEVANT = _episode(_QUERY, verified=3, tags=("episode", "success"))
# Делит с запросом ровно одно значащее слово — «кампания».
_MINTED = _episode(
    "produce self-build patch for кампания",
    verified=0,
    tags=("lesson", "self-build", "partial"),
)


def _store(tmp_path, *episodes: EpisodeRecord) -> EpisodicMemoryStore:
    store = EpisodicMemoryStore(tmp_path / "episodic_memory.jsonl")
    for ep in episodes:
        store.save(ep)
    return store


def test_an_unverified_lesson_is_still_reachable(tmp_path) -> None:
    """Дверь MIR-096 остаётся открытой: урок провалившегося прогона доступен.

    Отвергнут вариант «не допускать неподтверждённое»: это закрыло бы
    единственный канал, которым урок из ПРОВАЛА доходит до читателя.
    Неподтверждённая запись не исключается — она лишь перестаёт вставать
    впереди подтверждённой, когда спрашивали не про неё.
    """
    store = _store(tmp_path, _RELEVANT, _MINTED)

    top = store.search("produce self-build patch", limit=1)

    assert top and top[0].goal == _MINTED.goal
