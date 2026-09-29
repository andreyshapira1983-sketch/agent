"""A budgeted excerpt holds whole sections only: a cut one can read as a different fact («4812» → «48»).

The selection counted one character per separator while the text is joined with
two and carries «[... N sections omitted ...]» notices, then the overflow was cut
off the end — through the middle of the section the question was about.
"""
from __future__ import annotations

from core.evidence_budget import _split_paragraphs, extract_relevant

FIRST = "Intro line."
TARGET = "The invoice total for Globex is 4812 dollars."
FILLER = [f"Filler paragraph number {i} about nothing." for i in range(12)]
TEXT = "\n\n".join([FIRST, *FILLER, TARGET, "Closing words."])
QUESTION = "What is the invoice total for Globex?"


def _body(out: str) -> str:
    return out.split("\n...[INTENT-BUDGET:", 1)[0]


def test_every_section_in_the_excerpt_is_whole_at_every_budget():
    paras = set(_split_paragraphs(TEXT))
    for budget in range(60, len(TEXT)):
        body = _body(extract_relevant(TEXT, question=QUESTION, budget=budget))
        assert len(body) <= budget, (budget, body)
        for chunk in body.split("\n\n"):
            assert chunk in paras or chunk.startswith("[... "), (budget, chunk)


def test_the_matching_section_is_kept_when_it_fits_whole():
    out = extract_relevant(TEXT, question=QUESTION, budget=130)
    assert TARGET in out, out
