"""A block spent to nothing must say so; silence reads as absence.

Background: docs/CODE_NOTES.md, "Dropped memory looked like no memory".
"""
from __future__ import annotations

from core.evidence_budget import (
    MEMORY_BLOCK_LABEL,
    MEMORY_OPEN_TAG,
    apply_total_budget,
    total_trims,
)


# Budget 900, not 2000: since 2026-08-15 memory keeps one WHOLE record in the
# first pass instead of vanishing, so 2000 no longer produces a drop at all.
# The premise moved; the contract — a dropped block says so — did not.
def _blocks(memory_chars: int, file_chars: int) -> list[tuple[str, str]]:
    memory = f"{MEMORY_OPEN_TAG}\n" + ("m" * memory_chars)
    return [("file:big.py", "f" * file_chars), (MEMORY_BLOCK_LABEL, memory)]


def test_a_dropped_block_is_not_an_empty_string(monkeypatch):
    """Measured live 2026-08-14: 822 chars of memory in, 0 out, no trace.

    Asked «что ты помнишь», the agent had three records retrieved and formatted
    — and answered that it has no access to its memory. It was right about the
    prompt: the block had been demoted, spent first, fallen below one whole
    record and dropped to `""`. Nothing said it had ever existed.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    trimmed, was_trimmed = apply_total_budget(
        _blocks(800, 4000),
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: 700},
    )
    assert was_trimmed
    memory = dict(trimmed)[MEMORY_BLOCK_LABEL]
    assert memory != "", (
        "the memory block vanished without a word; the agent cannot tell "
        "'I have no memory' from 'my memory did not fit this turn'"
    )
    assert "budget" in memory.lower()


def test_the_notice_says_how_much_was_dropped(monkeypatch):
    """Enough for the agent to say what it lost, not merely that it lost."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    trimmed, _ = apply_total_budget(
        _blocks(800, 4000),
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: 700},
    )
    original_len = len(dict(_blocks(800, 4000))[MEMORY_BLOCK_LABEL])
    assert str(original_len) in dict(trimmed)[MEMORY_BLOCK_LABEL]


def test_the_notice_costs_less_than_one_record(monkeypatch):
    """The drop existed to stop paying for a useless stub. Keep that."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    trimmed, _ = apply_total_budget(
        _blocks(800, 4000),
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: 700},
    )
    assert len(dict(trimmed)[MEMORY_BLOCK_LABEL]) < 700


def test_the_dropped_block_is_reported_as_kept_nothing(monkeypatch):
    """`total_trims` still reports zero kept: the notice is not content."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    trimmed, _ = apply_total_budget(
        _blocks(800, 4000),
        trim_first_labels={MEMORY_BLOCK_LABEL},
        min_useful={MEMORY_BLOCK_LABEL: 700},
    )
    trims = {label: (kept, original) for label, kept, original in total_trims(trimmed)}
    assert trims[MEMORY_BLOCK_LABEL][0] == 0
