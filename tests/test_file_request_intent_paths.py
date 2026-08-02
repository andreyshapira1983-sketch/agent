"""Pin the path-mention parsers at their new home (loop decomposition, piece 3).

These two had no direct tests while they lived on ``AgentLoop`` — they were
covered only through the multi-file-review flows. The move gave them a public
seam, and the equivalence check run before deleting the originals caught a real
transcription error on exactly one shape: a backslash-separated path collapsed
to its basename. That case is pinned here permanently, because it is the one
that would have shipped broken.
"""
from __future__ import annotations

from core.file_request_intent import extract_path_mentions, normalize_path_mention


# ---------------------------------------------------------------------------
# extract_path_mentions
# ---------------------------------------------------------------------------

def test_finds_paths_in_first_mention_order():
    text = "review core/loop.py and docs/ROADMAP.md please"
    assert extract_path_mentions(text) == ["core/loop.py", "docs/ROADMAP.md"]


def test_backslash_separated_path_survives_whole():
    """The transcription-error case: must be the full path, not `file.md`."""
    text = "прочитай backslash\\style\\file.md"
    assert extract_path_mentions(text) == ["backslash\\style\\file.md"]


def test_case_duplicates_collapse_to_the_first_spelling():
    assert extract_path_mentions("core/loop.py core/LOOP.py") == ["core/loop.py"]


def test_trailing_sentence_punctuation_is_stripped():
    assert extract_path_mentions("see (docs/a.md), 'b.txt'!") == [
        "docs/a.md", "b.txt",
    ]


def test_unknown_extensions_are_not_paths():
    assert extract_path_mentions("run file.exe and config.toml") == []


def test_no_paths_means_empty_list():
    assert extract_path_mentions("никаких путей здесь нет") == []


def test_a_wall_of_dashes_is_part_of_the_path_and_completes():
    """Dashes are legal segment characters, so the wall IS the path's first
    segment — pinned so nobody "fixes" it into a split. The leading guard's
    job on this input is cost, not exclusion: the run completes instantly
    instead of rescanning quadratically (measured when the guard was added).
    """
    wall = "-" * 10_000
    assert extract_path_mentions(wall + "/a.py") == [wall + "/a.py"]


# ---------------------------------------------------------------------------
# normalize_path_mention
# ---------------------------------------------------------------------------

def test_normalize_unifies_separators_and_case():
    assert normalize_path_mention("Core/Loop.PY") == "core\\loop.py"


def test_normalize_strips_quotes_and_leading_dot_slash():
    assert normalize_path_mention("  './x.md'  ") == "x.md"


def test_normalized_forms_compare_equal_across_styles():
    assert normalize_path_mention("core/loop.py") == normalize_path_mention(
        ".\\core\\loop.py"
    )
