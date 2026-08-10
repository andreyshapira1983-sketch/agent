"""На вопрос «кто ты» отвечать надо ИЗМЕРЕНИЕМ, а не подсказкой.

ЖИВОЙ СЛУЧАЙ 2026-08-10. Оператор спросил, что агент знает о себе. Ответ:
«я исследовательский агент с модульной архитектурой», верификация 10 из 10,
`evidence_score=1.0`, соответствие вопросу 0.00.

ПРИЧИНА НЕ В ПАПКЕ `tools/`. `core/answer_format.py:27` начинается словами
`You are a careful research analyst.` — личность НАЗНАЧЕНА контрактом вывода.
Агент ответил верно относительно назначенной роли; беспочвенна сама роль.

ЧЕГО НЕ ХВАТАЛО. В синтез приходит `profile_to_prompt_block` — профиль
ОПЕРАТОРА (язык, многословие). Состава собственного организма модель не
получала вовсе, хотя `app/bootstrap.py` его знает: какие хранилища подняты,
какие долговечные записи разрешены, под каким прогоном идёт ход.

ЧТО ЗДЕСЬ ЗАВОДИТСЯ. Блок ПРОВЕРЯЕМЫХ фактов о рантайме: идентичность прогона
и состав подключённых органов. Не персона, не самооценка, не «кем я себя
считаю» — только то, что можно сверить с журналом.

ЧЕГО НЕ ЗАВОДИТСЯ. Строка `You are a careful research analyst` не трогается:
чем агент себя ОБЪЯВЛЯЕТ — решение оператора, а не проводка.
"""
from __future__ import annotations

from core.runtime_self import runtime_self_block


class _Store:
    """Достаточная заглушка: блок спрашивает только о наличии."""


def test_the_block_names_the_run_identity() -> None:
    """ГЛАВНОЕ: «кто исполняется» берётся из ребра происхождения, не из папки."""
    block = runtime_self_block(
        trace_id="trace_abc", run_id="run_def", session_id="sess_ghi",
        stores={}, durable_writes=frozenset(),
    )
    assert "trace_abc" in block
    assert "run_def" in block
    assert "sess_ghi" in block


def test_wired_organs_are_listed_by_name() -> None:
    """Состав — факт, а не догадка по содержимому каталога."""
    block = runtime_self_block(
        trace_id="t", run_id="r", session_id=None,
        stores={"working_memory": _Store(), "persistent_store": _Store(),
                "episodic_store": None, "procedural_store": None},
        durable_writes=frozenset({"episodes"}),
    )
    assert "working_memory" in block
    assert "persistent_store" in block
    assert "episodic_store" in block, "неподключённый орган обязан быть НАЗВАН"


def test_an_absent_organ_is_marked_absent_not_omitted() -> None:
    """Молчание об органе неотличимо от его наличия — поэтому не молчим.

    Ровно тот порок, что вся сессия: пропуск читается как «всё на месте».
    """
    block = runtime_self_block(
        trace_id="t", run_id="r", session_id=None,
        stores={"persistent_store": None}, durable_writes=frozenset(),
    )
    line = next(ln for ln in block.splitlines() if "persistent_store" in ln)
    assert "нет" in line or "not wired" in line.lower(), line


def test_durable_write_permissions_are_stated() -> None:
    """Что агенту РАЗРЕШЕНО менять — часть ответа на «кто ты»."""
    block = runtime_self_block(
        trace_id="t", run_id="r", session_id=None,
        stores={}, durable_writes=frozenset({"episodes", "procedures"}),
    )
    assert "episodes" in block
    assert "procedures" in block


def test_no_durable_writes_is_said_explicitly() -> None:
    """Пустое разрешение — тоже факт, и оно называется."""
    block = runtime_self_block(
        trace_id="t", run_id="r", session_id=None,
        stores={}, durable_writes=frozenset(),
    )
    assert "durable_writes" in block


def test_the_block_claims_no_persona() -> None:
    """Блок сообщает ИЗМЕРЕННОЕ и не объявляет, кем агент является.

    Персона живёт в `SYSTEM_ANSWER` и остаётся решением оператора. Если бы блок
    начал утверждать «ты исследователь» или «ты оркестратор», он подменил бы
    измерение второй беспочвенной ролью.
    """
    block = runtime_self_block(
        trace_id="t", run_id="r", session_id=None,
        stores={}, durable_writes=frozenset(),
    ).lower()
    for persona in ("аналитик", "исследоват", "analyst", "researcher", "assistant"):
        assert persona not in block, f"блок объявил личность: {persona}"


def test_it_degrades_to_nothing_without_identity() -> None:
    """Без идентичности прогона блок не выдумывает её, а молчит."""
    assert runtime_self_block(
        trace_id="", run_id="", session_id=None,
        stores={}, durable_writes=frozenset(),
    ) == ""
