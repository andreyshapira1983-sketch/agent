"""A file cited by another spelling is the same file — and a coincidence is not.

Measured 2026-08-14 on one file written three ways. The rule it replaces was
substring containment on the raw label, so it failed 4 of 9 spelling pairs and
failed asymmetrically: the short form is found inside the long one, never the
reverse.

Live consequence the same day: the agent wrote `README.md`, was asked about
`C:\\Users\\andre\\Projects\\agent\\README.md`, and answered that it had
evidence only for the first — about a file it had itself just created.

The replacement is suffix at a SEGMENT boundary. It is looser about spelling
and STRICTER about coincidence, which is the half worth guarding: `main.py`
used to be credited with evidence about `domain.py`, because the characters of
one sit inside the other.

Not resolved against the workspace root: the sanitiser drops absolute paths
from tool arguments, so a label is always relative and only the citation
varies. A suffix test settles that without new plumbing.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.verifier_utils import match_citation, parse_citations, same_file

_SPELLINGS = (
    "present.txt",
    "./present.txt",
    r"C:\Users\andre\Projects\agent\present.txt",
)


def _chain_for(path: str) -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(evidence_from_tool_result(
        tool_name="file_read", arguments={"path": path},
        output="alpha=1", status="success",
    ))
    return chain


@pytest.mark.parametrize("cited", _SPELLINGS)
@pytest.mark.parametrize("gathered", _SPELLINGS)
def test_every_spelling_of_one_file_resolves_against_every_other(cited, gathered):
    """All nine pairs. Four of them failed before, and never symmetrically."""
    citation = parse_citations(f"факт [file:{cited}].")[0]
    assert match_citation(citation, _chain_for(gathered)) is not None, (
        f"cited as {cited!r}, gathered as {gathered!r} — one file, one identity"
    )


@pytest.mark.parametrize(
    "cited,source_id",
    [
        # Each of these matched under plain containment. The characters of the
        # cited name sit inside the label, mid-segment.
        ("notes.md", "file:my_notes.md"),
        ("main.py", "file:domain.py"),
        ("test.py", "file:latest.py"),
        # Same basename, different directory: never the same file.
        ("sub/x.txt", "file:other/x.txt"),
    ],
)
def test_a_coincidence_of_characters_is_not_the_same_file(cited, source_id):
    assert not same_file(cited, source_id), (
        f"{cited!r} was credited with evidence about {source_id!r}"
    )


def test_a_path_suffix_is_the_same_file():
    """The half that makes spellings interchangeable, stated on its own."""
    assert same_file("present.txt", "file:./present.txt")
    assert same_file("./present.txt", "file:present.txt")
    assert same_file(r"C:\ws\present.txt", "file:present.txt")
    assert same_file("present.txt", r"file:C:\ws\present.txt")


def test_only_file_citations_are_compared_as_paths():
    """A web query or a memory id is a string, not a path — rule unchanged.

    Without this the change would quietly alter matching for every prefix; the
    web pool in particular relies on substring behaviour over query labels.
    """
    chain = ProvenanceChain()
    chain.add(evidence_from_tool_result(
        tool_name="web_search",
        arguments={"query": "autonomous agent memory"},
        output=[{"title": "t", "url": "https://example.org/a", "snippet": "s"}],
        status="success",
    ))
    citation = parse_citations("факт [search:autonomous agent].")[0]
    assert match_citation(citation, chain) is not None, (
        "substring matching over non-path labels must be untouched"
    )
