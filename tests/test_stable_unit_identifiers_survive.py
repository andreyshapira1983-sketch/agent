"""Идентификатор, объявленный оператором, обязан пережить весь ход.

ЖИВОЙ СЛУЧАЙ 2026-08-10, прогон `run_a1aee861`. Задание прямо сказало:

    Each unit has a stable identifier `U01` through `U12`.
    These identifiers are part of the user contract and must survive unchanged.

Контракт сохранил СЕМЬ единиц — `A. Contract intake` … `G. Safety`, то есть
разделы ФИНАЛЬНОГО ОТЧЁТА, — и ни одной из двенадцати рабочих. Мой вчерашний
образец требовал номер или букву С ТОЧКОЙ, а заголовки были вида
`# U01 — Verifier implementation`: буква с цифрами и длинное тире.

Следствие оператор назвал точно: система подменила авторитетную декомпозицию
пользователя своей. Отчёт задним числом перечислил U01–U12 и объявил
непрерывность подтверждённой, тогда как внутреннее представление их никогда не
несло.

ЗДЕСЬ ЧИНИТСЯ РОВНО ЭТО: помеченные единицы распознаются наравне с
нумерованными, а разделы отчёта остаются в списке — оператор просил и работу,
и отчёт, и терять ни то ни другое нельзя.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

_LIVE = """Perform a live read-only contract-continuity stress test.

Each unit has a stable identifier `U01` through `U03`.

# U01 — Verifier implementation
Read the actual current core/verifier.py.

# U02 — Smart-memory implementation
Read the actual current core/smart_memory.py.

# U03 — Learning planner boundary
Determine narrowly what this module selects.

## A. Contract intake
## B. Plan linkage
"""


def test_the_units_reach_the_contract_payload() -> None:
    """Различение обязано пережить переход в журнал."""
    payload = derive_completion_contract(_LIVE).to_log_payload()
    titles = [u["title"] for u in payload["requested_units"]]
    assert sum(1 for t in titles if t.startswith("U")) == 3, titles


def test_an_answer_addressing_by_identifier_covers_the_unit() -> None:
    """Ответ покрывает единицу МЕТКОЙ, а не дословным заголовком.

    Без этого сверка помечала непокрытыми все единицы разом: заголовок
    `U01 — Verifier implementation` в ответе дословно не повторяют, о нём
    пишут «U01: точка входа — verify()». Замер 2026-08-10.
    """
    from core.completion_contract import unaddressed_units

    contract = derive_completion_contract(_LIVE)
    answer = (
        "U01: точка входа — verify(). U02: EpisodeRecord и EpisodicMemoryStore. "
        "U03: модуль выбирает источники.\n"
        "## A. Contract intake\nда\n## B. Plan linkage\nда\n"
    )
    assert not unaddressed_units(contract, answer)


def test_a_dropped_unit_is_named() -> None:
    """КОНЕЦ ЦЕПИ: потерянная единица называется, а не растворяется."""
    from core.completion_contract import unaddressed_units

    contract = derive_completion_contract(_LIVE)
    answer = (
        "U01: точка входа — verify(). U03: модуль выбирает источники.\n"
        "## A. Contract intake\nда\n## B. Plan linkage\nда\n"
    )
    missing = unaddressed_units(contract, answer)
    assert len(missing) == 1
    assert missing[0].startswith("U02")
