"""Канал, которым оператор видит и чинит агента, агенту недоступен.

Замер, отвергнутые варианты и границы: H-49 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pytest

from core.self_apply_lane import _is_allowed

#: Канал управления: чем оператор ЗАПУСКАЕТ агента и чем ВИДИТ его состояние.
#: Правка любого из них самим агентом воспроизводит форму Rogers.
_MANAGEMENT_PLANE = [
    ("agent_tick.py", "тик и строка состояния --status"),
    ("main.py", "вход приложения"),
    ("app/bootstrap.py", "сборка агента"),
    ("app/single_instance.py", "замок одиночного экземпляра"),
    ("scripts/install_daemon.ps1", "установщик планового запуска"),
    ("scripts/check_ceo_file_baseline.py", "ратчет размеров"),
    (".github/workflows/ci.yml", "проверка в CI"),
    ("config/budget_limits.json", "денежный потолок"),
    ("config/vetoed_goals.txt", "список целей, отозванных оператором"),
]


@pytest.mark.parametrize(("path", "what"), _MANAGEMENT_PLANE)
def test_the_lane_cannot_touch_the_management_plane(path: str, what: str) -> None:
    assert not _is_allowed(path), (
        f"лента получила доступ к «{what}» ({path}) — агент может править "
        f"канал, которым оператор его видит и запускает. Если это осознанное "
        f"решение оператора, перепишите H-49 и этот тест, а не список"
    )


def test_ordinary_work_is_still_reachable() -> None:
    """Контроль: без него тест проходил бы и на ленте, запрещающей ВСЁ."""
    assert _is_allowed("core/verifier_absence.py")
    assert _is_allowed("tests/test_something.py")


def test_the_lane_can_still_edit_itself_and_that_is_known() -> None:
    """Замер, а не пожелание: это ось MIR-139, открытая решением оператора.

    Тест не требует запрета — он требует, чтобы факт не потерялся. Если лента
    однажды перестанет править саму себя, MIR-139 меняет состояние, и запись
    обязана это отразить.
    """
    assert _is_allowed("core/self_apply_lane.py"), (
        "лента больше не правит саму себя — это меняет MIR-139, "
        "перечитайте запись"
    )
