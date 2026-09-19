"""The memory door opens to the unattended self — with its walls (authority
change by the operator's word «Надо», 2026-09-04).

He closed `memory_bank` to his own unattended path on 2026-09-01 (4321bc4);
0 durable writes in 267 cycles followed — the policy's effect, not a defect.
The word opens the door narrowly. New walls, each with a witness here:
a readback before «stored»; a memory cannot be the source of a memory; a
per-process ceiling on banked records; missing arguments name themselves.
The retrieval side is a separate change; self-written records stay low-trust.
"""
from __future__ import annotations

import pytest

from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore, memory_door_verdict
from tools.memory_bank import MemoryBankTool

_KIND = "[ВЫВОД, проверен боем]"
_PROSE = (
    "Суд выносит вердикт по журналам, а модель лишь предлагает объяснения; "
    "выбор принадлежит проверке, не автору."
)


def test_memory_bank_is_no_longer_blocked_on_the_unattended_goal_path() -> None:
    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS

    assert "memory_bank" not in _AUTONOMOUS_GOAL_BLOCKED_TOOLS
    # Controls: the rest of the freeze stands.
    # python_probe вышел из этого множества 2026-09-19 словом оператора.
    assert {"spawn_subagent", "journal_append"} <= _AUTONOMOUS_GOAL_BLOCKED_TOOLS


def test_a_write_is_stored_only_after_a_readback(tmp_path) -> None:
    store = PersistentMemoryStore(tmp_path / "pm.jsonl")

    mem_id, reason = memory_door_verdict(store, MemoryWritePolicy(), _PROSE, _KIND, "test:door")

    assert mem_id and reason == ""
    assert any(r.id == mem_id for r in store.load())


def test_a_write_that_cannot_be_read_back_is_not_stored(tmp_path) -> None:
    class _Amnesiac(PersistentMemoryStore):
        def load(self):  # the disk «forgets» what was just saved
            return []

    store = _Amnesiac(tmp_path / "pm.jsonl")

    mem_id, reason = memory_door_verdict(store, MemoryWritePolicy(), _PROSE, _KIND, "test:door")

    assert mem_id is None and reason.startswith("not stored: readback")


def test_a_memory_cannot_be_the_source_of_a_memory(tmp_path) -> None:
    store = PersistentMemoryStore(tmp_path / "pm.jsonl")

    mem_id, reason = memory_door_verdict(store, MemoryWritePolicy(), _PROSE, _KIND, "memory:mem_123")

    assert mem_id is None and "memory cannot be the source" in reason
    assert store.load() == []


def test_the_tool_stops_at_its_ceiling_and_says_so(tmp_path) -> None:
    store = PersistentMemoryStore(tmp_path / "pm.jsonl")
    tool = MemoryBankTool(store=store, policy=MemoryWritePolicy(), max_writes_per_process=2)
    texts = [
        "Первый вывод: страж повторов судит предмет работы, а не формулировку цели.",
        "Второй вывод: ожидание внутри смены — исход попытки, а не конец рабочего дня.",
        "Третий вывод: улика без происхождения не становится памятью ни при каком слове.",
    ]

    outs = [tool.run(text=t, kind=_KIND, provenance=f"test:{i}") for i, t in enumerate(texts)]

    assert [o["banked"] for o in outs] == [True, True, False]
    assert outs[2]["refused_by"].startswith("ceiling: 2 records")
    assert len(store.load()) == 2


def test_a_refused_write_does_not_spend_the_ceiling(tmp_path) -> None:
    store = PersistentMemoryStore(tmp_path / "pm.jsonl")
    tool = MemoryBankTool(store=store, policy=MemoryWritePolicy(), max_writes_per_process=1)

    refused = tool.run(text=_PROSE, kind="[НЕТ ТАКОГО ВИДА]", provenance="test:x")
    banked = tool.run(text=_PROSE, kind=_KIND, provenance="test:x")

    assert refused["banked"] is False and banked["banked"] is True


def test_a_missing_argument_names_itself() -> None:
    tool = MemoryBankTool(store=None, policy=None)
    with pytest.raises(ValueError, match="requires \\['provenance'\\]"):
        tool.run(text="x", kind=_KIND)
