"""Исключение по имени инструмента держалось на ложном основании — для всех трёх.

Замер и цепочка: docs/audit/PROSPECTIVE_AUTONOMY_HAZARD_AUDIT.md, §5 и класс H-34.
"""
from __future__ import annotations

from pathlib import Path

from core.injection_guard import scan_for_injection
from core.loop_step_execution import _INJECTION_SCAN_EXEMPT
from core.repo_provenance import block_may_be_annotated
from tools.list_dir import ListDirTool


def test_ordinary_listings_do_not_trip_the_guard() -> None:
    """Контроль, ради которого исключение и заводили: ложных срабатываний нет.

    Замерено на настоящем выводе `list_dir` по `core/` — `clean`. Снимать
    исключение, не проверив это, значило бы заменить дыру на паралич.
    """
    listing = ListDirTool(workspace_root=Path(".")).run("core")

    assert scan_for_injection(str(listing)).verdict == "clean"


def test_run_tests_is_scanned_but_its_block_is_downgraded() -> None:
    """Вывод собственного набора тестов проверяется, но не отнимается.

    Падающий тест печатает исходник, а в репозитории есть файлы, где образцы
    инъекций процитированы намеренно — свидетели. Блокировать такой вывод
    значило бы ослепить агента на его же красных тестах: тот самый дефект,
    что закрыт для документов как MIR-171. Материал здесь тоже свой —
    зафиксированный набор тестов, — поэтому вердикт остаётся, а последствием
    становится пометка.
    """
    assert "run_tests" not in _INJECTION_SCAN_EXEMPT
    assert block_may_be_annotated("run_tests", "step:s1", Path(".")) is True
