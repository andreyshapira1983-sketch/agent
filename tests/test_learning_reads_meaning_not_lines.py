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

# Через `core.ingestion`, а не `core.ingestion_utils`: соседний
# `tests/test_ingestion_helpers.py` берёт эти же швы оттуда, и это тот
# слой, который тесты поддерживают. Заодно проверка, что шов реэкспорта
# жив — его уже однажды снесла автоправка ruff (§24).
from core.ingestion import CHUNK_CHARS, _chunk_python, _chunk_text

#: More than the fixtures below can spend, so these tests measure what the
#: chunker CHOOSES rather than where a budget cut it off. The budget itself is
#: pinned separately, at the bottom of the file.
_AMPLE = 100_000

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
    chunks = _chunk_python(_SOURCE, max_chars=_AMPLE)

    assert not any("_touch(" in c for c in chunks), chunks
    assert not any("CONSTANT" in c for c in chunks), chunks
    assert not any("os.path.join" in c for c in chunks), chunks


def test_the_behaviour_a_test_pins_survives():
    """Refusing scaffolding must not throw away what the test is FOR."""
    chunks = _chunk_python(_SOURCE, max_chars=_AMPLE)
    named = [c for c in chunks if c.startswith("test_the_gate_refuses_an_unsigned_request")]

    assert len(named) == 1, chunks
    assert "unsigned request must be refused" in named[0]


def test_a_claim_says_whose_behaviour_it_describes():
    """`load` alone is meaningless; `Store.load` is a fact about something."""
    chunks = _chunk_python(_SOURCE, max_chars=_AMPLE)

    assert any(c.startswith("Store.load: ") for c in chunks), chunks
    assert any(c.startswith("Store: ") for c in chunks), chunks


def test_the_module_docstring_is_kept():
    chunks = _chunk_python(_SOURCE, max_chars=_AMPLE)
    assert chunks[0] == "The module explains itself in one sentence."


def test_an_undocumented_source_yields_nothing_rather_than_fragments():
    """Silence beats a claim per code block.

    The caller skips a file with no chunks and records the reason, so an
    undocumented module is visibly not learned from — better than filling
    memory with lines that describe nothing.
    """
    bare = "import os\n\nX = 1\n\ndef f(a):\n    return a + 1\n"

    assert _chunk_python(bare, max_chars=_AMPLE) == []
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

    # Also pins the conversion between the two budgets: a prose chunk runs to
    # CHUNK_CHARS, so `n` of them IS `n * CHUNK_CHARS` characters. Handing the
    # fallback a different allowance than the caller granted would make an
    # unparseable file quietly cheaper or dearer than a parseable one.
    assert (_chunk_python(broken, max_chars=20 * CHUNK_CHARS)
            == _chunk_text(broken, max_chunks=20))


def test_prose_files_are_untouched_by_this():
    """Markdown and text keep the paragraph chunker they were written for."""
    prose = "First paragraph, with a point.\n\nSecond paragraph, with another.\n"

    kept = _chunk_text(prose, max_chunks=20)

    assert len(kept) == 1, "short paragraphs are accumulated, not split"
    assert "First paragraph" in kept[0] and "Second paragraph" in kept[0]


def test_a_documented_definition_inside_a_block_is_not_lost():
    """`if` / `try` / `with` are not walls, and this repository writes all three.

    The first version recursed only into definition bodies, so a function under
    `if TYPE_CHECKING:`, one in an `except ImportError:` fallback, and a class
    inside a `with` were dropped without a trace — the shape a reader would
    never notice, because the file still produced chunks.
    """
    source = textwrap.dedent('''
        """Module."""
        import sys

        if sys.version_info >= (3, 11):
            def modern():
                """Behaviour available on new runtimes."""

        try:
            from x import y
        except ImportError:
            def fallback():
                """What we do when the optional dependency is missing."""

        with open("f") as fh:
            class Inline:
                """Defined inside a with-block, documented all the same."""
    ''').lstrip()

    chunks = _chunk_python(source, max_chars=_AMPLE)

    assert any(c.startswith("modern: ") for c in chunks), chunks
    assert any(c.startswith("fallback: ") for c in chunks), chunks
    assert any(c.startswith("Inline: ") for c in chunks), chunks


def test_a_block_is_not_a_namespace():
    """`Inline` inside a `with` is `Inline`, not `with.Inline`.

    The prefix grows through classes and functions only — those are the things
    a reader would name when saying whose behaviour a claim describes.
    """
    source = textwrap.dedent('''
        class Store:
            """Holds rows."""

            if True:
                def load(self):
                    """Every row that parsed."""
    ''').lstrip()

    chunks = _chunk_python(source, max_chars=_AMPLE)

    assert any(c.startswith("Store.load: ") for c in chunks), chunks
    assert not any("True" in c.split(":")[0] for c in chunks), chunks


def _module_of(docstring_lengths: list[int]) -> str:
    """A module documenting one function per requested docstring length."""
    parts = ['"""M."""']
    for i, length in enumerate(docstring_lengths):
        parts.append(f'def f{i}():\n    """{"x" * length}"""')
    return "\n\n".join(parts) + "\n"


def test_the_budget_holds_however_heavy_the_docstrings_are():
    """A count cannot express a budget when the items have no size.

    The first version of this cap was a flat `8` definitions for `.py`, chosen
    to match `3 * CHUNK_CHARS` on an assumed docstring of a few hundred
    characters. Measured afterwards across `core/*.py`, it OVERSHOT that budget
    on 69 of 181 files: `core/completion_contract.py` documents one thing in
    3788 characters, nearly five prose chunks in a single item.

    So the guard is on the quantity that was always meant — characters — and
    it is checked with docstrings far past the assumed size, which is the case
    the counting version got wrong.
    """
    fat = _module_of([1500, 1500, 1500, 1500])

    chunks = _chunk_python(fat, max_chars=2400)

    assert sum(len(c) for c in chunks) <= 2400, [len(c) for c in chunks]
    assert len(chunks) < 4, "потолок в символах не остановил тяжёлые докстроки"


def test_the_caller_s_budget_is_the_one_that_applies():
    """`:ingest-source` asks for more than `:ingest-project` and must get it.

    The constant ignored both callers: `ingest_source` passes
    `SOURCE_MAX_CHUNKS = 16` and Python files were held to 8 regardless, so the
    mode's whole point — read this ONE file properly — was overridden by a
    number meant for bulk project sweeps.
    """
    source = _module_of([400] * 12)

    small = _chunk_python(source, max_chars=3 * CHUNK_CHARS)
    large = _chunk_python(source, max_chars=16 * CHUNK_CHARS)

    assert len(large) > len(small), (
        f"больший бюджет не дал большего: {len(large)} против {len(small)}"
    )
    assert len(small) < 12, "маленький бюджет обязан ограничивать"


def test_one_huge_docstring_is_kept_rather_than_making_the_file_invisible():
    """Returning nothing means "documents nothing" to the caller, which skips it.

    A module whose single docstring outweighs the entire budget would then be
    dropped from learning for explaining itself at length — the opposite of
    what the budget defends. The first item is taken whatever it weighs.
    """
    huge = '"""' + "x" * 5000 + '"""\n'

    chunks = _chunk_python(huge, max_chars=2400)

    assert len(chunks) == 1
    assert len(chunks[0]) == 5000
