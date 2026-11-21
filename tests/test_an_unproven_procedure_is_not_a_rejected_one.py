"""Ни разу не запускавшаяся процедура — не отвергнутая.

Замер, отвергнутые варианты и границы: H-44 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

from core.smart_memory import _procedure_status_for, _smoothed_confidence


def test_a_newborn_procedure_stays_a_candidate() -> None:
    """Ноль запусков — это незнание, а не отрицательный опыт."""
    conf = _smoothed_confidence(0, 0)

    assert _procedure_status_for(0, conf, failure_count=0) == "candidate", (
        "ни разу не запускавшаяся процедура помечена needs_review и тем самым "
        "исключена из выдачи — незнание переименовано в приговор"
    )


def test_the_repair_pass_uses_the_same_authority() -> None:
    """Третья власть над полем убрана: починка не изобретает свой статус.

    Замер показал, что `recompute_legacy_confidence` знала только
    `active`/`needs_review` и `candidate` не производила вовсе — то есть проход
    по новорождённым переименовал бы их все.
    """
    import inspect

    from core.smart_memory import ProceduralMemoryStore

    src = inspect.getsource(ProceduralMemoryStore.recompute_legacy_confidence)
    assert "_procedure_status_for" in src, (
        "починка по-прежнему считает статус своим правилом — у одного поля "
        "снова две власти, и та, что запустится последней, победит"
    )
