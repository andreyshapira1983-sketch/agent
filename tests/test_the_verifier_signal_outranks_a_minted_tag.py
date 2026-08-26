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


def test_a_minted_lesson_does_not_displace_verified_relevant_experience(tmp_path) -> None:
    """Красный свидетель: прибавка за метку перебивает разрыв релевантности.

    Живой замер 2026-08-26 на 142 эпизодах: из 73 допущенных к выдаче 64 не
    несут подтверждения, и все 64 прошли меткой. В очной ставке подтверждённую
    запись спрашивали ЕЁ ЖЕ словами — и все 9 раз сверху оказывалась
    неподтверждённая. Разбор одной ставки: победители делили с запросом 2 слова
    и брали 52 очка меткой, а записи с 41 общим словом оставались ниже.
    """
    store = _store(tmp_path, _RELEVANT, _MINTED)

    top = store.search(_QUERY, limit=1)

    assert top and top[0].goal == _QUERY, (
        "запись с двумя общими словами обошла запись с сорока одним — "
        "метка перебивает релевантность, а не разрешает спор при равенстве"
    )


def test_the_signal_decides_between_equally_relevant_records(tmp_path) -> None:
    """Власть сигнала — при РАВНОЙ релевантности, а не вместо неё (ось MemGuard).

    Неподтверждённая запись здесь СВЕЖЕЕ: без правила о сигнале спор решает
    свежесть, и она побеждает. Метки времени заданы явно — иначе тест проходил
    бы по совпадению, не проверяя ничего.
    """
    verified = _episode(_QUERY, verified=3, tags=("episode",))
    unverified = _episode(_QUERY, verified=0, tags=("episode",))
    object.__setattr__(verified, "created_at", "2026-08-01T00:00:00+00:00")
    object.__setattr__(unverified, "created_at", "2026-08-20T00:00:00+00:00")
    store = _store(tmp_path, verified, unverified)

    top = store.search(_QUERY, limit=1)

    assert top and (top[0].verified_chunks or 0) > 0


def test_unmeasured_is_not_the_same_as_measured_and_failed(tmp_path) -> None:
    """«Улик не было» — незнание, «улики не сошлись» — приговор. Разные места.

    Живой замер 2026-08-26: все 64 неподтверждённые допущенные записи — это
    «улик не было вовсе». Правило, наказывающее нулевое подтверждение, било бы
    ровно по незнанию и ни разу по провалу.
    """
    unmeasured = _episode(_QUERY, verified=0, tags=("episode",))
    failed = _episode(_QUERY, verified=0, tags=("episode",))
    object.__setattr__(failed, "unverified_chunks", 4)
    object.__setattr__(unmeasured, "created_at", "2026-08-01T00:00:00+00:00")
    object.__setattr__(failed, "created_at", "2026-08-20T00:00:00+00:00")
    store = _store(tmp_path, unmeasured, failed)

    top = store.search(_QUERY, limit=1)

    assert top and (top[0].unverified_chunks or 0) == 0


def test_a_lesson_still_wins_at_equal_relevance(tmp_path) -> None:
    """Контроль: прежнее решение о метке сохранено — урок обходит рядовой эпизод.

    Так же читается и живой тест-старожил `test_search_boosts_lesson_episodes`:
    он защищает первенство урока ПРИ РАВНОМ перекрытии, и это первенство здесь
    не отменяется — отменяется только право метки перебивать релевантность.
    """
    ordinary = _episode(_QUERY, verified=0, tags=("episode",))
    lesson = _episode(_QUERY, verified=0, tags=("lesson", "bug-fix"))
    store = _store(tmp_path, ordinary, lesson)

    top = store.search(_QUERY, limit=1)

    assert top and "lesson" in top[0].tags


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


def test_the_tag_still_protects_the_record_from_eviction(tmp_path) -> None:
    """Право выжить и право идти первым — разные права, и первое не тронуто."""
    store = EpisodicMemoryStore(tmp_path / "episodic_memory.jsonl", max_episodes=3)
    store.save(_MINTED)
    for i in range(6):
        store.save(_episode(f"рядовой эпизод {i}", verified=0, tags=("episode",)))

    kept = store.load()

    assert any(e.goal == _MINTED.goal for e in kept), (
        "защита от вытеснения не должна была пострадать"
    )
