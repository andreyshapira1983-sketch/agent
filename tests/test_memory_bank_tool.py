import pytest

from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from tools.memory_bank import MemoryBankTool


def _fresh(tmp_path):
    return PersistentMemoryStore(tmp_path / "pm.jsonl")


def _tool(tmp_path):
    store = _fresh(tmp_path)
    policy = MemoryWritePolicy()
    return MemoryBankTool(store=store, policy=policy), store


def test_valid_conclusion_is_banked(tmp_path):
    tool, store = _tool(tmp_path)
    result = tool.run(
        text="Суд выносит вердикт по журналам а модель лишь предлагает объяснения",
        kind="[ВЫВОД, проверен боем]",
        provenance="test",
    )
    assert result["banked"] is True
    assert result["mem_id"].startswith("mem_")
    records = store.load()
    assert len(records) == 1
    assert result["mem_id"] in [r.id for r in records]
    assert len(records[0].tags) == 3


def test_hypothesis_is_rejected(tmp_path):
    tool, store = _tool(tmp_path)
    result = tool.run(
        text="Возможно планировщик читает урок только при свежем провале а не всегда",
        kind="[ГИПОТЕЗА, не проверена]",
        provenance="test",
    )
    assert result["banked"] is False
    assert result["mem_id"] is None
    assert store.load() == []


def test_extra_argument_raises_permission_error(tmp_path):
    tool, _ = _tool(tmp_path)
    with pytest.raises(PermissionError):
        tool.run(
            text="Любое годное предложение достаточной длины для прохода ворот двери",
            kind="[ВЫВОД, проверен боем]",
            provenance="test",
            extra="unexpected",
        )


def test_output_validates(tmp_path):
    tool, _ = _tool(tmp_path)
    result = tool.run(
        text="Свидетель держит контракт инструмента а не только его счастливый путь",
        kind="[ВЫВОД, проверен боем]",
        provenance="test",
    )
    ok, errors = tool.validate_output(result)
    assert ok is True
    assert errors == []
