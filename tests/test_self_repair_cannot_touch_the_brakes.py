"""Самоправка не трогает тормоза и планки, которыми его меряют.

Журнал исследований оператора (25.09, «Недостающие части пазла»): ошибка
самомодификации не должна уметь сломать тормоза; исполнитель не оценивает сам
себя — нужен набор проверок, которые он читает, но не меняет. Список запретов
пути самопочинки не знал о главных правилах (core/root_principles.py, 24.09),
песочницах и планках.
"""
from __future__ import annotations

import pytest

from core.patch_route import _forbidden
from core.self_apply_lane import PROTECTED_CORE, _is_denied

_BRAKES = ["core/root_principles.py", "tools/python_probe.py", "tools/convert_file.py",
           "tests/test_capability_baseline.py", "scripts/check_function_length_baseline.py",
           # Слово оператора 2026-09-25: код тормозов нельзя даже предложить.
           "core/budget_kill_switch.py", "core/budget_ledger.py", "core/usd_spend.py",
           "core/control_files.py", "core/approval_inbox.py", "core/self_apply_lane.py"]


@pytest.mark.parametrize("path", _BRAKES)
def test_both_repair_paths_refuse_the_brakes(path: str) -> None:
    assert path in PROTECTED_CORE
    assert _forbidden([path]) == [path], "путь починки по дефектам пропустил тормоз"
    assert _is_denied(path), "полоса самоправок пропустила тормоз"


def test_ordinary_code_and_new_tests_stay_open() -> None:
    assert _forbidden(["core/drives.py", "tests/test_new_witness.py"]) == []
    assert not _is_denied("core/drives.py")
    assert not _is_denied("tests/test_new_witness.py")
