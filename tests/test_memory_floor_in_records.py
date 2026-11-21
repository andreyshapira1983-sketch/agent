"""A floor measured in characters keeps no memory at all.

Live run 2026-08-04. The memory policy selected three records as relevant to
the operator's question; the evidence budget cut memory first, down to its
50-character floor; memory is rebuilt from WHOLE records and none is that
short, so the prompt received none. The journal read
`memory_trimmed=True, memory_chars=694, memory_chars_kept=0` — the word was
"trimmed", the effect was "erased".

Memory paying first is deliberate and stays. What changes here: the floor is
sized so that whatever survives is a whole record, and when not even one fits,
the block is dropped outright instead of leaving an unusable stub that still
costs its trim notice.
"""
from __future__ import annotations

from core.evidence_budget import (
    MEMORY_CLOSE_TAG,
    MEMORY_OPEN_TAG,
    apply_total_budget,
    rebuild_trimmed_memory,
)

MEMORY = "memory"


def _records(count: int, size: int = 200) -> list[tuple[str, str]]:
    return [(f"mem_{i}", f"[mem_{i}] " + "x" * size) for i in range(count)]


def _memory_block(record_lines: list[tuple[str, str]]) -> str:
    body = "\n".join(line for _, line in record_lines)
    return f"{MEMORY_OPEN_TAG}\n{body}\n{MEMORY_CLOSE_TAG}"


def test_without_the_hint_the_old_stub_still_appears(monkeypatch):
    """Proof the hint is what fixes it — not some unrelated change."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1200")
    lines = _records(3)
    memory = _memory_block(lines)
    blocks = [("file:big.py", "y" * 1500), (MEMORY, memory)]

    trimmed, _ = apply_total_budget(blocks, trim_first_labels={MEMORY})
    kept = dict(trimmed)[MEMORY]

    assert kept, "without min_useful the block is still cut to a stub"
    _block, ids = rebuild_trimmed_memory(kept, memory, lines)
    assert not ids, "the stub was expected to rebuild into nothing"
