"""Paying first is not the same as paying everything.

Background: docs/CODE_NOTES.md, "Memory pays first, not last rites".
"""
from __future__ import annotations

from core.evidence_budget import (
    MEMORY_BLOCK_LABEL,
    MEMORY_CLOSE_TAG,
    MEMORY_OPEN_TAG,
    apply_total_budget,
    rebuild_trimmed_memory,
)

_RECORDS = [(f"mem_{i}", f"[mem_{i}] " + "x" * 180) for i in range(4)]


def _memory_block() -> str:
    body = "\n".join(line for _, line in _RECORDS)
    return f"{MEMORY_OPEN_TAG}\n{body}\n{MEMORY_CLOSE_TAG}"


def _one_record_floor() -> int:
    return len(f"{MEMORY_OPEN_TAG}\n{_RECORDS[0][1]}")


def test_memory_keeps_one_whole_record_instead_of_vanishing(monkeypatch):
    """Measured live 2026-08-14: 822 chars of memory in, 0 out.

    Asked «что ты помнишь», three records were retrieved and formatted and the
    agent answered that it has no access to its memory — true of the prompt.
    Memory is demoted for a real reason (a stale "Bug fixed…" record once
    outlived the code disproving it), but demotion was implemented as "may be
    spent to nothing", and a block spent to nothing teaches the agent it has no
    past.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "2500")
    memory = _memory_block()
    blocks = [("file:big.py", "y" * 6000), (MEMORY_BLOCK_LABEL, memory)]

    trimmed, was_trimmed = apply_total_budget(
        blocks,
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: _one_record_floor()},
    )

    assert was_trimmed
    kept = dict(trimmed)[MEMORY_BLOCK_LABEL]
    _block, ids = rebuild_trimmed_memory(kept, memory, _RECORDS)
    assert ids, (
        "memory was spent to nothing; the agent reaches the writer with no "
        "recollection at all and honestly reports having none"
    )


def test_memory_still_pays_before_fresh_evidence(monkeypatch):
    """The original intent is preserved: recollection never outranks a file.

    This is the guard against over-correcting — the incident that introduced
    demotion must stay fixed.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "2500")
    memory = _memory_block()
    blocks = [("file:big.py", "y" * 6000), (MEMORY_BLOCK_LABEL, memory)]

    trimmed, _ = apply_total_budget(
        blocks,
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: _one_record_floor()},
    )
    memory_kept = len(dict(trimmed)[MEMORY_BLOCK_LABEL])
    file_kept = len(dict(trimmed)["file:big.py"])

    assert memory_kept < len(memory), "memory did not pay at all"
    assert memory_kept < file_kept, "memory outranked the freshly read file"


def test_a_budget_too_small_for_one_record_still_drops_it(monkeypatch):
    """The floor is a floor, not a guarantee. When even one record cannot fit
    beside the evidence, dropping it whole is still right — and the drop notice
    (MIR-092) says so out loud.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    memory = _memory_block()
    trimmed, _ = apply_total_budget(
        [("file:big.py", "y" * 6000), (MEMORY_BLOCK_LABEL, memory)],
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: _one_record_floor()},
    )
    kept = dict(trimmed)[MEMORY_BLOCK_LABEL]
    _block, ids = rebuild_trimmed_memory(kept, memory, _RECORDS)
    assert not ids
    assert "dropped whole" in kept
