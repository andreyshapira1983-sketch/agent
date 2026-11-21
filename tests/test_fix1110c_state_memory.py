"""Memory hygiene and bulk delete must not lose a record saved while they run."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import core.memory_hygiene as hygiene
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.state_integrity import append_state_jsonl, read_state_jsonl

OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _rec(text: str, **kwargs) -> MemoryRecord:
    return MemoryRecord(content=text, tags=kwargs.pop("tags", ["fact"]), owner="user", **kwargs)


def test_hygiene_rewrite_keeps_a_row_the_model_cannot_validate(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "memory.jsonl")
    stale = _rec("short lived", ttl_seconds=60, created_at=OLD)
    odd = {"id": "mem_odd", "type": "unknown-type", "content": "ручная запись"}
    append_state_jsonl(store.path, [stale.model_dump(mode="json"), odd])

    hygiene.expire_memory(store)

    assert read_state_jsonl(store.path) == [odd]
