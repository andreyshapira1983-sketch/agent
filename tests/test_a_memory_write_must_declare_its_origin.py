"""Запись в память обязана назвать свой исток — умолчания «человек» нет.

Замер, отвергнутые варианты и границы: MIR-150 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import pytest

from core.evidence import evidence_from_memory_record
from core.loop_memory_commands import AgentLoopMemoryCommands
from core.verifier_core import _memory_citation_is_independent


def test_a_write_without_a_declared_origin_is_refused() -> None:
    """Ловушка была в умолчании: забыл исток — получил голос человека.

    Проверка идёт через несвязанный вызов: недостающий обязательный аргумент
    отвергается до входа в тело, поэтому настоящий склад не нужен.
    """
    with pytest.raises(TypeError):
        AgentLoopMemoryCommands.remember(object(), content="запись без истока")


def test_declaring_an_origin_still_works() -> None:
    """Контроль: без него запрет удовлетворялся бы подписью, не берущей ничего."""
    with pytest.raises(AttributeError):
        # Исток назван — связывание аргументов проходит, и вызов доходит до
        # тела, где пустышка ожидаемо не имеет склада.
        AgentLoopMemoryCommands.remember(
            object(), content="запись с истоком", source="agent-auto"
        )


@pytest.mark.parametrize(
    ("source", "independent"),
    [("user-explicit", True), ("agent-auto", False), ("repair", False), (None, False)],
)
def test_only_a_human_assertion_counts_as_independent(source, independent) -> None:
    """Цена умолчания, измеренная на конце цепочки.

    Исток записи доходит до вердикта: `user-explicit` считается независимым
    свидетельством, всё остальное — нет. Поэтому умолчание в записи было не
    удобством, а раздачей человеческого голоса по забывчивости.
    """
    ev = evidence_from_memory_record(
        record_id="r1", content="текст", source=source, created_at=None
    )

    assert _memory_citation_is_independent(ev) is independent
