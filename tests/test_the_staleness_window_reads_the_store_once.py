"""Окно свежести читает склад один раз на план, а не на каждый файл.

Замер, отвергнутые варианты и границы: MIR-153 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.learning_planner import LearningPlanner
from core.source_registry import SourceRecord, SourceRegistry
from core.source_registry_store import SourceRegistryStore


class _CountingStore(SourceRegistryStore):
    """Настоящий склад, считающий полные проходы по файлу.

    Считается именно `load_sources`: это единственная дорогая операция —
    чтение и разбор всего реестра. Замер формы, а не миллисекунд: цена в
    секундах зависит от машины, число проходов — нет.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.full_scans = 0

    def load_sources(self):  # type: ignore[override]
        self.full_scans += 1
        return super().load_sources()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    for name in ("loop.py", "planner.py", "runtime.py", "store.py", "guard.py"):
        (tmp_path / "core" / name).write_text("x = 1\n" * 40, encoding="utf-8")
    (tmp_path / "README.md").write_text("overview\n" * 40, encoding="utf-8")
    return tmp_path


def _store(workspace: Path) -> _CountingStore:
    store = _CountingStore(workspace / "data" / "source_registry.jsonl")
    registry = SourceRegistry()
    now = datetime.now(timezone.utc).isoformat()
    registry.add_source(
        SourceRecord(
            id="file:core/loop.py", type="file", title="loop",
            locator="core/loop.py", added_at=now, last_read_at=now,
        )
    )
    store.save_registry(registry)
    store.full_scans = 0  # сохранение тоже читает файл; считаем только планирование
    return store


def test_the_deprioritisation_still_happens(workspace: Path) -> None:
    """Контроль: дешевизна не должна достигаться отключением работы."""
    store = _store(workspace)

    plan = LearningPlanner().plan(
        workspace=workspace, limit=6, source_registry=store, stale_hours=6.0
    )
    paths = list(plan.source_paths)

    assert "core/loop.py" in paths
    assert paths.index("core/loop.py") == len(paths) - 1 or any(
        paths.index(other) < paths.index("core/loop.py")
        for other in paths if other != "core/loop.py"
    ), "недавно прочитанный файл больше не понижается"
