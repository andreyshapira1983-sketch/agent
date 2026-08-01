"""Tests for core/evidence_budget.py — per-artifact + total evidence budget."""
from __future__ import annotations

import pytest

from core.evidence_budget import (
    EVIDENCE_FILE_CHARS,
    EVIDENCE_TOTAL_CHARS,
    MEMORY_BLOCK_LABEL,
    _trim_notice,
    apply_total_budget,
    budget_file_content,
    extract_relevant,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_file(n_chars: int, keyword: str = "alpha") -> str:
    """Return a synthetic file of ~n_chars with *keyword* near the middle."""
    half = n_chars // 2
    filler_a = ("x " * 100 + "\n\n")
    filler_b = ("y " * 100 + "\n\n")
    head  = (filler_a * (half // len(filler_a) + 1))[:half]
    mid   = f"\n\n# Section with {keyword}\n\nThis section discusses {keyword} in detail.\n\n"
    tail  = (filler_b * (half // len(filler_b) + 1))[:half]
    return head + mid + tail


# ── extract_relevant — basic contract ────────────────────────────────────────

def test_short_text_returned_unchanged():
    text = "Hello world"
    assert extract_relevant(text, question="hello", budget=200) == text


def test_returns_at_most_budget_chars():
    text = _make_file(50_000, keyword="budget")
    result = extract_relevant(text, question="budget governor config", budget=5_000)
    # The notice adds a little extra; allow 10% headroom for the notice string
    assert len(result) <= 5_000 * 1.1


def test_keyword_section_is_preferred():
    """The paragraph containing the question keyword must appear in the result."""
    text = _make_file(30_000, keyword="governor")
    result = extract_relevant(text, question="how does the governor work?", budget=3_000)
    assert "governor" in result


def test_budget_notice_is_appended():
    text = _make_file(20_000, keyword="alpha")
    result = extract_relevant(text, question="alpha", budget=2_000)
    assert "INTENT-BUDGET" in result


def test_no_keyword_match_fallback():
    """When question has no overlap, head+tail fallback is used."""
    text = "alpha " * 5_000   # 30 000 chars, no keyword matching "zebra"
    result = extract_relevant(text, question="zebra", budget=3_000)
    assert "INTENT-BUDGET" in result
    # Head should start same as original
    assert result.startswith("alpha")


def test_empty_text_returned_unchanged():
    assert extract_relevant("", question="anything", budget=1_000) == ""


def test_budget_zero_returns_empty():
    assert extract_relevant("hello world", question="hello", budget=0) == ""


def test_first_paragraph_always_included():
    """Paragraph 0 (file preamble) must always appear in the result."""
    intro = "# Module intro\nThis file describes the system.\n\n"
    body  = "\n\n".join([f"# Section {i}\nContent about topic_{i}." for i in range(40)])
    text  = intro + body
    result = extract_relevant(text, question="topic_20 topic_21", budget=2_000)
    assert "Module intro" in result


# ── extract_relevant — Realtime Intent property ───────────────────────────────

def test_intent_shapes_selection():
    """Different questions yield different excerpts from the same file."""
    sections = [
        "# Authentication\n\nThe auth module handles JWT tokens and OAuth2.",
        "# Budget\n\nThe budget governor limits LLM calls per hour.",
        "# Logging\n\nAll events are written to JSONL files in the logs/ directory.",
        "# Deployment\n\nUse Docker Compose for local development.",
    ]
    text = "\n\n".join(sections * 20)   # ~8 000 chars

    result_auth   = extract_relevant(text, question="how does JWT authentication work?", budget=800)
    result_budget = extract_relevant(text, question="how does budget governor work?",    budget=800)

    assert "Authentication" in result_auth or "auth" in result_auth.lower()
    assert "Budget" in result_budget or "budget" in result_budget.lower()
    # The two results should differ (different intent → different selection)
    assert result_auth != result_budget


def test_cyrillic_keywords_work():
    """Cyrillic question keywords must match Cyrillic content."""
    text = (
        "# Раздел о бюджете\n\nБюджет ограничивает количество вызовов LLM в час.\n\n"
        + "# Other section\n\nThis is about something else entirely.\n\n" * 50
    )
    result = extract_relevant(text, question="как работает бюджет?", budget=3_000)
    assert "бюджет" in result.lower()


# ── budget_file_content ───────────────────────────────────────────────────────

def test_budget_file_content_passthrough_small():
    small = "x" * 100
    assert budget_file_content(small, question="test") == small


def test_budget_file_content_truncates_large(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_FILE_CHARS", "500")
    large = _make_file(10_000, keyword="alpha")
    result = budget_file_content(large, question="alpha")
    # Must be capped — allow 30% headroom for the INTENT-BUDGET notice string
    assert len(result) <= 700


def test_budget_file_content_default_limit_is_sane():
    """Default EVIDENCE_FILE_CHARS must be > 0 and < typical model context window."""
    assert 1_000 < EVIDENCE_FILE_CHARS < 100_000


# ── apply_total_budget ────────────────────────────────────────────────────────

def test_apply_total_budget_no_trim_needed():
    blocks = [("file:a.py", "x" * 100), ("file:b.py", "y" * 200)]
    result, was_trimmed = apply_total_budget(blocks)
    assert not was_trimmed
    assert len(result) == 2
    assert result[0][1] == "x" * 100


def test_apply_total_budget_trims_largest_first(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    # Block A: 300 chars, Block B: 200 chars → total 500 > 400
    blocks = [("a", "A" * 300), ("b", "B" * 200)]
    result, was_trimmed = apply_total_budget(blocks)
    assert was_trimmed
    total = sum(len(c) for _, c in result)
    assert total <= 450   # 400 + some notice chars


def test_apply_total_budget_adds_notice(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "200")
    blocks = [("file:big.md", "Z" * 500)]
    result, was_trimmed = apply_total_budget(blocks)
    assert was_trimmed
    assert "TOTAL-BUDGET" in result[0][1]


def test_apply_total_budget_preserves_small_blocks(monkeypatch):
    """The small block must be returned intact when the large one is trimmed."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "600")
    small_content = "s" * 100
    blocks = [("big", "B" * 700), ("small", small_content)]
    result, was_trimmed = apply_total_budget(blocks)
    assert was_trimmed
    # Find the small block by label
    small_result = next(c for lbl, c in result if lbl == "small")
    assert small_result == small_content   # small block untouched


def test_apply_total_budget_empty_list():
    result, was_trimmed = apply_total_budget([])
    assert result == []
    assert not was_trimmed


def test_apply_total_budget_single_block_fits(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "1000")
    blocks = [("x", "a" * 999)]
    result, was_trimmed = apply_total_budget(blocks)
    assert not was_trimmed
    assert result[0][1] == "a" * 999


def test_default_total_budget_is_sane():
    assert 5_000 < EVIDENCE_TOTAL_CHARS < 500_000


# ── total budget: recollection is spent before fresh evidence (ROOT B) ───────
#
# "Trim the largest block first" is the right rule among blocks of the SAME
# kind, and the wrong rule across kinds: a freshly read file is almost always
# the largest block, so memory — the one block that may be months stale —
# survived every trim intact. `trim_first_labels` marks the blocks that must be
# spent first regardless of size.

def test_demoted_block_is_trimmed_before_a_larger_fresh_block(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "800")
    blocks = [("file:core/loop.py", "F" * 900), (MEMORY_BLOCK_LABEL, "M" * 500)]

    result, was_trimmed = apply_total_budget(
        blocks, trim_first_labels={MEMORY_BLOCK_LABEL}
    )

    assert was_trimmed
    by_label = dict(result)
    memory = by_label[MEMORY_BLOCK_LABEL]
    fresh = by_label["file:core/loop.py"]
    # The memory block — smaller, and therefore never chosen by "largest
    # first" — is the one that gets spent.
    assert "TOTAL-BUDGET" in memory
    assert memory.count("M") == 50            # trimmed down to the content floor
    assert fresh.count("F") > memory.count("M")


def test_fresh_block_survives_intact_when_memory_absorbs_the_overflow(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    fresh_content = "F" * 700
    blocks = [("file:core/loop.py", fresh_content), (MEMORY_BLOCK_LABEL, "M" * 600)]

    result, _ = apply_total_budget(blocks, trim_first_labels={MEMORY_BLOCK_LABEL})

    by_label = dict(result)
    assert by_label["file:core/loop.py"] == fresh_content   # not a character lost
    assert "TOTAL-BUDGET" in by_label[MEMORY_BLOCK_LABEL]


def test_without_demotion_the_largest_block_still_goes_first(monkeypatch):
    """Default behaviour is unchanged — demotion is opt-in per call."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    blocks = [("file:core/loop.py", "F" * 700), (MEMORY_BLOCK_LABEL, "M" * 600)]

    result, was_trimmed = apply_total_budget(blocks)

    assert was_trimmed
    by_label = dict(result)
    assert "TOTAL-BUDGET" in by_label["file:core/loop.py"]
    assert by_label[MEMORY_BLOCK_LABEL] == "M" * 600


def test_demoted_block_below_the_floor_does_not_stall_the_trim(monkeypatch):
    """A tiny memory block cannot absorb anything — fresh evidence still trims.

    Without this the demotion rule would keep picking a block that can no
    longer shrink, the no-progress guard would fire, and the budget would be
    left violated.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "500")
    blocks = [(MEMORY_BLOCK_LABEL, "M" * 60), ("file:core/loop.py", "F" * 900)]

    result, was_trimmed = apply_total_budget(
        blocks, trim_first_labels={MEMORY_BLOCK_LABEL}
    )

    assert was_trimmed
    assert sum(len(c) for _, c in result) <= 500
    by_label = dict(result)
    assert by_label[MEMORY_BLOCK_LABEL] == "M" * 60
    assert "TOTAL-BUDGET" in by_label["file:core/loop.py"]


def test_block_just_above_the_floor_is_still_trimmed(monkeypatch):
    """A block that can still shrink must not be excluded from the pool.

    The exclusion test used a padded notice-length reserve (120) while the
    notice really renders at ~84, so a block sized inside that ~36-char window
    was skipped: the loop broke on the first pass and returned the input
    unchanged, over budget and with `was_trimmed=False` — no trim event either.
    """
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "137")
    result, was_trimmed = apply_total_budget([("a", "x" * 150)])

    assert was_trimmed
    assert sum(len(c) for _, c in result) <= 137


def test_budget_is_met_when_a_demoted_block_sits_in_the_same_window(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    blocks = [
        (MEMORY_BLOCK_LABEL, "M" * 169),
        ("file:big.py", "F" * 40_000),
        ("t", "T" * 100),
    ]

    result, was_trimmed = apply_total_budget(
        blocks, trim_first_labels={MEMORY_BLOCK_LABEL}
    )

    assert was_trimmed
    assert sum(len(c) for _, c in result) <= 400


def test_a_bare_string_is_one_label_not_a_set_of_characters(monkeypatch):
    """`frozenset("mem")` is {"m","e","s"} — a demotion that demotes nothing."""
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "900")
    fresh = "F" * 700
    blocks = [("file:core/loop.py", fresh), (MEMORY_BLOCK_LABEL, "M" * 600)]

    result, _ = apply_total_budget(blocks, trim_first_labels=MEMORY_BLOCK_LABEL)

    by_label = dict(result)
    assert by_label["file:core/loop.py"] == fresh
    assert "TOTAL-BUDGET" in by_label[MEMORY_BLOCK_LABEL]


def test_unknown_demoted_label_is_harmless(monkeypatch):
    monkeypatch.setenv("AGENT_EVIDENCE_TOTAL_CHARS", "400")
    blocks = [("a", "A" * 300), ("b", "B" * 200)]

    result, was_trimmed = apply_total_budget(blocks, trim_first_labels={"not-here"})

    assert was_trimmed
    # The configured budget itself, not a padded bound: a trim that overshoots
    # the limit is exactly the regression this file exists to catch.
    assert sum(len(c) for _, c in result) <= 400


# ── integration: _format_artifact via loop ───────────────────────────────────

def test_format_artifact_small_file_unchanged():
    """Files smaller than the per-artifact budget pass through untouched."""
    from core.loop import AgentLoop
    small = "# README\n\nShort file.\n"
    result = AgentLoop._format_artifact("file_read", small, question="readme")
    assert result == small


def test_format_artifact_large_file_truncated(monkeypatch):
    """Files larger than AGENT_EVIDENCE_FILE_CHARS are truncated."""
    from core.loop import AgentLoop
    monkeypatch.setenv("AGENT_EVIDENCE_FILE_CHARS", "300")
    large = _make_file(5_000, keyword="governor")
    result = AgentLoop._format_artifact(
        "file_read", large, question="how does the governor work?"
    )
    # budget=300; allow ~120 chars of overhead (notice string + separators)
    assert len(result) <= 420


# ── repairing a memory block the total budget sliced ─────────────────────────
#
# The budget cuts characters; `<long_term_memory>` is a list of records. These
# cover the ways record CONTENT can lie to a parser that reads the cut string
# instead of measuring it against the original.

_ID_A = "mem_" + "a" * 32
_ID_B = "mem_" + "b" * 32
_ID_GHOST = "mem_" + "d" * 32


def _lines(*records: tuple[str, str]) -> list[tuple[str, str]]:
    """(id, formatted line) pairs, exactly as the retrieval hands them over."""
    return [(rid, f"- [{rid} | tags: fact] {text}") for rid, text in records]


def _memory_block(lines: list[tuple[str, str]]) -> str:
    return (
        "<long_term_memory>\n"
        + "\n".join(line for _, line in lines)
        + "\n</long_term_memory>"
    )


def _cut(original: str, at: int) -> str:
    """The block as the budget hands it back, sliced at *at* chars.

    The notice is built by the budget's own helper, so the repair is tested
    against the exact string production writes, not a look-alike.
    """
    return original[:at] + _trim_notice(at, len(original), 800)


def _cut_at(original: str, marker: str) -> str:
    """Same, cut just before the first occurrence of *marker*."""
    return _cut(original, original.index(marker))


def test_record_content_cannot_pose_as_a_record_boundary():
    """Content is free text; boundaries come from the retrieval, not the text.

    A markdown bullet, a quoted record header, an id that does not exist —
    none of them may split a record, because the caller states where every
    record begins and ends.
    """
    from core.loop import AgentLoop
    lines = _lines(
        (_ID_A, f"first record\n- [1] finish the migration\n"
                f"- [{_ID_GHOST} | tags: fact] quoted from an old prompt\n"
                "and A continues AFTER the quote"),
        (_ID_B, "second record"),
    )
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut_at(original, lines[1][1][:20]), original, lines
    )

    assert ids == {_ID_A}
    assert "and A continues AFTER the quote" in block   # not truncated at the quote
    assert _ID_GHOST not in ids                          # no phantom record


def test_a_quoted_header_of_a_co_retrieved_record_cannot_substitute_it():
    """The worst shape: A quotes B's real header, and B was retrieved too.

    An id-filtered pattern search cannot tell that quote from B's own header,
    so it reports B as surviving while what the model actually sees is A's
    paraphrase of B — a substituted record under a citable id.
    """
    from core.loop import AgentLoop
    lines = _lines(
        (_ID_A, f"record A\n- [{_ID_B} | tags: fact] ...as I recorded earlier\n"
                "A continues after the quote"),
        (_ID_B, "the REAL second record"),
    )
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut(original, original.index("\n" + lines[1][1])), original, lines
    )

    assert ids == {_ID_A}                       # B did not survive
    assert "the REAL second record" not in block
    assert "A continues after the quote" in block


def test_a_budget_notice_inside_record_content_does_not_discard_the_block():
    from core.loop import AgentLoop
    lines = _lines(
        (_ID_A, "trace excerpt:\n...[TOTAL-BUDGET: trimmed to 50 of 500 chars]"),
        (_ID_B, "second record"),
    )
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut_at(original, lines[1][1][:20]), original, lines
    )

    assert ids == {_ID_A}
    assert "trace excerpt" in block


def test_a_record_cut_mid_content_is_not_reported_as_surviving():
    """Advertising it whole while half its text is gone is the worse failure."""
    from core.loop import AgentLoop
    lines = _lines(
        (_ID_A, "line one\nline two\nNEVER trust this record, it was superseded"),
        (_ID_B, "second record"),
    )
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut_at(original, "NEVER trust"), original, lines
    )

    assert ids == set()
    assert block == ""


def test_a_record_that_survived_whole_keeps_its_text_and_the_closing_tag():
    from core.loop import AgentLoop
    lines = _lines((_ID_A, "first record"), (_ID_B, "second record"))
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut_at(original, lines[1][1][:20]), original, lines
    )

    assert ids == {_ID_A}
    assert "first record" in block
    assert block.count("<long_term_memory>") == 1
    assert block.endswith("</long_term_memory>")
    assert "TOTAL-BUDGET" in block


def test_the_trim_notice_survives_a_cut_that_lands_on_a_newline():
    """The notice opens with "\\n...[" — the cut point may too.

    Re-deriving the cut by comparing the two strings then runs past it and
    eats the start of the notice, gluing "...[TOTAL-BUDGET" onto the record's
    own line or, at worst, deleting the words that say the block was cut.
    """
    from core.loop import AgentLoop
    lines = _lines((_ID_A, "first record"), (_ID_B, "second record"))
    original = _memory_block(lines)
    cut = original.index("\n" + lines[1][1])          # cut ON the newline
    block, ids = AgentLoop._rebuild_trimmed_memory(_cut(original, cut), original, lines)

    assert ids == {_ID_A}
    assert "\n...[TOTAL-BUDGET: trimmed to" in block


def test_a_notice_quoted_inside_a_record_does_not_replace_the_real_one():
    """The cut can land exactly on a quoted notice inside a record.

    A scan that walks the two strings keeps matching through the quote and
    hands back a truncated notice — in the worst case one missing the words
    TOTAL-BUDGET and "trimmed to", i.e. the prompt no longer says the block
    was shortened at all.
    """
    from core.loop import AgentLoop
    quoted = "\n...[TOTAL-BUDGET: trimmed to 1 of 2 chars to fit 3-char total evidence budget]"
    lines = _lines((_ID_A, "first record"), (_ID_B, f"trace excerpt:{quoted}"))
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut(original, original.index(quoted)), original, lines
    )

    assert ids == {_ID_A}
    assert "\n...[TOTAL-BUDGET: trimmed to" in block
    # The notice the budget actually wrote names the real block size.
    assert f"of {len(original)} chars" in block


def test_a_memory_block_that_cannot_be_accounted_for_is_dropped():
    """Fail closed on every shape the repair cannot explain."""
    from core.loop import AgentLoop
    lines = _lines((_ID_A, "first record"), (_ID_B, "second record"))
    original = _memory_block(lines)
    after_a = original.index(lines[1][1][:20])

    no_notice = AgentLoop._rebuild_trimmed_memory(original[:after_a], original, lines)
    foreign_notice = AgentLoop._rebuild_trimmed_memory(
        original[:after_a] + _trim_notice(after_a, len(original) + 999, 800),
        original,
        lines,
    )
    # A notice claiming more text than the block holds: the cut cannot be
    # trusted, so nothing is kept — not "keep everything".
    impossible_cut = AgentLoop._rebuild_trimmed_memory(
        _trim_notice(len(original) + 500, len(original), 900), original, lines
    )
    # Records that do not reproduce the block (stale retrieval): the offsets
    # would be someone else's.
    stale_records = AgentLoop._rebuild_trimmed_memory(
        _cut_at(original, lines[1][1][:20]),
        original,
        _lines((_ID_A, "a different text entirely")),
    )
    no_records = AgentLoop._rebuild_trimmed_memory(
        _cut_at(original, lines[1][1][:20]), original, []
    )

    assert no_notice == ("", set())
    assert foreign_notice == ("", set())
    assert impossible_cut == ("", set())
    assert stale_records == ("", set())
    assert no_records == ("", set())


def test_a_single_record_block_is_dropped_when_trimmed_at_all():
    """Stated consequence of "whole records only", not an accident.

    The budget always reserves ~120 chars for its notice, so one record can
    never survive its own trim. Dropping it is the honest outcome — the
    alternative is half a record with a citable id.
    """
    from core.loop import AgentLoop
    lines = _lines((_ID_A, "the only record " * 20))
    original = _memory_block(lines)
    block, ids = AgentLoop._rebuild_trimmed_memory(
        _cut(original, len(original) - 121), original, lines
    )

    assert (block, ids) == ("", set())


def test_format_artifact_web_search_unchanged():
    """Web search results are NOT subject to the file budget."""
    from core.loop import AgentLoop
    hits = [{"title": "A", "url": "http://x.com", "snippet": "s", "source": "ddg"}]
    result = AgentLoop._format_artifact("web_search", hits, question="any question")
    assert "http://x.com" in result
