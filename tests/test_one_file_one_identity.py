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
from core.verifier_utils import match_citation, parse_citations

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
