
from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore, memory_door_write

GOOD_TEXT = "Суд без модели на любом пути - конституция"
GOOD_KIND = "[ВЫВОД, проверен боем]"
GOOD_PROVENANCE = "INVARIANT_CLASH.md"


def _fresh_store(tmp_path):
    return PersistentMemoryStore(tmp_path / "pm.jsonl")


def test_raw_code_is_rejected(tmp_path):
    store = _fresh_store(tmp_path)
    policy = MemoryWritePolicy()
    result = memory_door_write(
        store, policy, "if signature in attempted_signatures:", "[ВЫВОД, проверен боем]", GOOD_PROVENANCE
    )
    assert result is None
    assert store.load() == []


def test_duplicate_is_rejected(tmp_path):
    store = _fresh_store(tmp_path)
    policy = MemoryWritePolicy()
    first = memory_door_write(store, policy, GOOD_TEXT, GOOD_KIND, GOOD_PROVENANCE)
    assert first is not None
    second = memory_door_write(store, policy, GOOD_TEXT, GOOD_KIND, GOOD_PROVENANCE)
    assert second is None
    assert len(store.load()) == 1


def test_frozen_source_is_silently_rejected(tmp_path):
    store = _fresh_store(tmp_path)
    policy = MemoryWritePolicy(frozen_sources={"agent-auto"})
    result = memory_door_write(store, policy, GOOD_TEXT, GOOD_KIND, GOOD_PROVENANCE)
    assert result is None
    assert store.load() == []
