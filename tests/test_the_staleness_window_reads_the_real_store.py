"""Окно свежести кормится настоящим складом, а не заглушкой.

Замер, отвергнутые варианты и границы: MIR-152 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.learning_planner import LearningPlanner
from core.source_registry import SourceRecord, SourceRegistry
from core.source_registry_store import SourceRegistryStore


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "loop.py").write_text("loop", encoding="utf-8")
    (tmp_path / "README.md").write_text("overview", encoding="utf-8")
    return tmp_path


def _store_saying_read_at(workspace: Path, when: datetime) -> SourceRegistryStore:
    """Настоящий склад с настоящей записью — тот самый объект, что и в бою."""
    store = SourceRegistryStore(workspace / "data" / "source_registry.jsonl")
    registry = SourceRegistry()
    registry.add_source(
        SourceRecord(
            id="file:core/loop.py",
            type="file",
            title="loop",
            locator="core/loop.py",
            added_at=when.isoformat(),
            last_read_at=when.isoformat(),
        )
    )
    store.save_registry(registry)
    return store


def test_a_file_read_an_hour_ago_is_deprioritised_through_the_store(
    workspace: Path,
) -> None:
    """Живой путь целиком: склад -> планировщик -> порядок целей обучения.

    Прежняя проверка кормила планировщик `MagicMock`, который соглашается на
    любое имя метода. Переименуй `get_source` у склада — та проверка осталась
    бы зелёной, а понижение на безнадзорном пути молча перестало бы работать.
    """
    store = _store_saying_read_at(workspace, datetime.now(timezone.utc) - timedelta(hours=1))

    plan = LearningPlanner().plan(
        workspace=workspace, limit=2, source_registry=store, stale_hours=6.0
    )

    paths = list(plan.source_paths)
    assert "README.md" in paths and "core/loop.py" in paths
    assert paths.index("README.md") < paths.index("core/loop.py"), (
        "файл, прочитанный час назад, не понижен: склад больше не отвечает на "
        "запрос планировщика, и окно свежести перестало на что-либо влиять"
    )


def test_a_file_read_a_month_ago_keeps_its_place(workspace: Path) -> None:
    """Контроль: без него порядок мог бы объясняться базовой оценкой, не окном."""
    store = _store_saying_read_at(workspace, datetime.now(timezone.utc) - timedelta(days=30))

    with_store = LearningPlanner().plan(
        workspace=workspace, limit=2, source_registry=store, stale_hours=6.0
    )
    without = LearningPlanner().plan(workspace=workspace, limit=2)

    assert list(with_store.source_paths) == list(without.source_paths)


def test_the_store_answers_the_question_the_planner_asks(workspace: Path) -> None:
    """Граница между типами: живой вызывающий передаёт СКЛАД, а не реестр.

    Аннотация обещает `SourceRegistry`, а `core/autonomous_runtime._task_learn`
    передаёт `SourceRegistryStore`. Работает по совпадению имён, поэтому
    совпадение зафиксировано проверкой, а не надеждой.
    """
    when = datetime.now(timezone.utc)
    store = _store_saying_read_at(workspace, when)

    record = store.get_source("file:core/loop.py")

    assert record is not None, "склад не знает источника, который сам же сохранил"
    assert record.last_read_at, "у записи нет отметки чтения — окну свежести нечего читать"
