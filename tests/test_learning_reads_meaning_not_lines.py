"""A source file is learned for what it says about itself, not for its lines.

Found by the agent, on its own work. Asked through the task queue why its
learning cycle had chosen five test files and extracted 34 "claims", it
answered with the addresses — `LearningPlanner.plan`, the `+40` for `tests/`,
the three test files in `_DEFAULT_CORE_FILES` worth another `+45` — and then
framed the question better than the question had: the issue is not whether
tests should be learned from, but what in a test is knowledge.

Its example was the first claim of that run:

    _touch(tmp_path / "core" / "team_plan…

That is scaffolding. `_chunk_text` walks paragraphs and accumulates them up to
`CHUNK_CHARS`, which for prose gives readable passages and for a source file
gives blobs of code — so setup lines became durable facts, held back only by
`auto_write_memory=False`.

What a test carries is the behaviour it pins: its name and its docstring. What
a module carries is its own docstring and those of its definitions. That is
what `_chunk_python` returns, and these tests hold it there.
"""
from __future__ import annotations

import textwrap

from core.ingestion_utils import _chunk_python, _chunk_text

_SOURCE = textwrap.dedent('''
    """The module explains itself in one sentence."""
    import os

    CONSTANT = {"a": 1, "b": 2}

    _CACHE = []

    def helper(path):
        return os.path.join(path, "x")

    def test_the_gate_refuses_an_unsigned_request():
        """An unsigned request must be refused before any effect runs."""
        _touch(tmp_path / "core" / "team_plan.py")
        assert gate(request) == "deny"

    class Store:
        """Holds rows and answers questions about them."""

        def load(self):
            """Every row that parsed, newest last."""
            return []
''').lstrip()


def test_a_setup_line_never_becomes_a_claim():
    """The exact shape the agent found, pinned by its own example."""
    chunks = _chunk_python(_SOURCE, max_chunks=20)

    assert not any("_touch(" in c for c in chunks), chunks
    assert not any("CONSTANT" in c for c in chunks), chunks
    assert not any("os.path.join" in c for c in chunks), chunks


def test_the_behaviour_a_test_pins_survives():
    """Refusing scaffolding must not throw away what the test is FOR."""
    chunks = _chunk_python(_SOURCE, max_chunks=20)
    named = [c for c in chunks if c.startswith("test_the_gate_refuses_an_unsigned_request")]

    assert len(named) == 1, chunks
    assert "unsigned request must be refused" in named[0]


def test_a_claim_says_whose_behaviour_it_describes():
    """`load` alone is meaningless; `Store.load` is a fact about something."""
    chunks = _chunk_python(_SOURCE, max_chunks=20)

    assert any(c.startswith("Store.load: ") for c in chunks), chunks
    assert any(c.startswith("Store: ") for c in chunks), chunks


def test_the_module_docstring_is_kept():
    chunks = _chunk_python(_SOURCE, max_chunks=20)
    assert chunks[0] == "The module explains itself in one sentence."


def test_an_undocumented_source_yields_nothing_rather_than_fragments():
    """Silence beats a claim per code block.

    The caller skips a file with no chunks and records the reason, so an
    undocumented module is visibly not learned from — better than filling
    memory with lines that describe nothing.
    """
    bare = "import os\n\nX = 1\n\ndef f(a):\n    return a + 1\n"

    assert _chunk_python(bare, max_chunks=20) == []
    # The prose chunker keeps it: one chunk of raw code, which is exactly the
    # kind of "claim" this change exists to stop producing. (It accumulates
    # short paragraphs up to CHUNK_CHARS rather than emitting one per blank
    # line — measured, after asserting three from memory and being wrong.)
    prose_view = _chunk_text(bare, max_chunks=20)
    assert len(prose_view) == 1
    assert "def f(a)" in prose_view[0]


def test_a_file_that_will_not_parse_falls_back_to_prose():
    """Not our file to judge: unparseable source is still text.

    Refusing it outright would make a syntax error anywhere in the workspace
    silently remove a source from learning.
    """
    broken = '"""Doc."""\n\ndef f(\n'

    assert _chunk_python(broken, max_chunks=20) == _chunk_text(broken, max_chunks=20)


def test_prose_files_are_untouched_by_this():
    """Markdown and text keep the paragraph chunker they were written for."""
    prose = "First paragraph, with a point.\n\nSecond paragraph, with another.\n"

    kept = _chunk_text(prose, max_chunks=20)

    assert len(kept) == 1, "short paragraphs are accumulated, not split"
    assert "First paragraph" in kept[0] and "Second paragraph" in kept[0]
