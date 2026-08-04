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


def _min_useful(lines: list[tuple[str, str]]) -> int:
    """Chars at which the first whole record still survives the rebuild."""
    return len(f"{MEMORY_OPEN_TAG}\n{lines[0][1]}")


def test_a_trim_that_keeps_no_record_drops_the_block(monkeypatch):
    """The stub is worse than nothing: it survives as text, not as memory."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1200")
    lines = _records(3)
    memory = _memory_block(lines)
    blocks = [("file:big.py", "y" * 1500), (MEMORY, memory)]

    trimmed, was_trimmed = apply_total_budget(
        blocks, trim_first_labels={MEMORY}, min_useful={MEMORY: _min_useful(lines)},
    )

    assert was_trimmed
    kept = dict(trimmed)[MEMORY]
    if kept:
        # Whatever survived must rebuild into at least one whole record.
        block, ids = rebuild_trimmed_memory(kept, memory, lines)
        assert ids, "memory kept characters but no record — an unusable stub"
        assert block


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


def test_memory_still_pays_before_fresh_evidence(monkeypatch):
    """The deliberate part is untouched: recollection is spent first."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1200")
    lines = _records(3)
    memory = _memory_block(lines)
    fresh = "y" * 1000
    blocks = [("file:fresh.py", fresh), (MEMORY, memory)]

    trimmed, _ = apply_total_budget(blocks, trim_first_labels={MEMORY})
    by_label = dict(trimmed)

    assert by_label["file:fresh.py"] == fresh, "fresh evidence was cut before memory"
    assert len(by_label[MEMORY]) < len(memory), "memory did not pay first"


def test_a_generous_budget_leaves_memory_whole(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "100000")
    lines = _records(3)
    memory = _memory_block(lines)
    blocks = [("file:small.py", "y" * 100), (MEMORY, memory)]

    trimmed, was_trimmed = apply_total_budget(blocks, trim_first_labels={MEMORY})

    assert not was_trimmed
    assert dict(trimmed)[MEMORY] == memory


def test_a_small_overflow_keeps_at_least_one_whole_record(monkeypatch):
    """Characterisation of the boundary, written after measuring it.

    Measured 2026-08-04 on a 425-char block of three 128-char records, useful
    floor 147, budget 1500: overflow 40/80/150 chars → the block survives at
    351/311/241 chars and rebuilds into ONE whole record; overflow 250 and up →
    the block is dropped whole and rebuilds into none.

    Only one record survives a small overflow because the trim notice is
    reserved at ~120 chars, roughly the size of a record. That is a deliberate,
    documented trade in `rebuild_trimmed_memory` — "whole records only" applied
    honestly — not a defect. This test exists so a future change that quietly
    drops everything on a tiny overflow is caught.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1500")
    lines = _records(3, size=120)
    memory = _memory_block(lines)
    fresh = "y" * (1500 - len(memory) + 80)      # overflow ≈ 80 chars
    blocks = [("file:fresh.py", fresh), (MEMORY, memory)]

    trimmed, _ = apply_total_budget(
        blocks, trim_first_labels={MEMORY}, min_useful={MEMORY: _min_useful(lines)}
    )
    kept = dict(trimmed)[MEMORY]

    assert kept, "an 80-char overflow erased every record"
    survivors, ids = rebuild_trimmed_memory(kept, memory, lines)
    assert survivors, "what survived the trim rebuilds into no whole record"
    assert len(ids) == 1, f"expected exactly one whole record, kept {sorted(ids)}"
