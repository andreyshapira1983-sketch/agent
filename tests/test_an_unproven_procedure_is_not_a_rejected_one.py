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
