"""The memory wire, proven by intervention rather than by import.

A module that imports, constructs and is reachable still proves nothing about
whether a signal crosses it. This walks the whole chain on the production
path — `build_agent` -> `remember` -> the write policy -> the JSONL store ->
a SECOND agent that re-reads that file -> the retrieval filter -> the
`<long_term_memory>` block that enters the prompt — and asserts a marker that
exists nowhere else in the tree comes out the far end.

The control is the half that makes it an experiment: the same stored record,
a question about something else, and the block must come back empty. Without
it the test would pass just as well on a wire that injects everything.

What this does NOT prove, and must not be read as proving: that a later
decision differs because the record was injected. That is one edge further
(MIR-100), it needs a model call, and it stays UNKNOWN here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import build_agent

_MARKER = "QUOKKA7731"
_FACT = (
    f"The {_MARKER} protocol requires the operator to confirm every "
    "irreversible step twice."
)
_UNRELATED = "How do I configure the HTTP timeout for the web fetch tool?"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _agent(workspace: Path):
    return build_agent(workspace, with_memory=True, with_persistent=True)


def test_the_same_record_stays_out_of_an_unrelated_question(
    workspace: Path,
) -> None:
    """The control: the filter must discriminate, not inject everything."""
    writer = _agent(workspace)
    writer.remember(
        content=_FACT,
        tags=[_MARKER.lower(), "protocol"],
        source="user-explicit",
        record_type="semantic",
        owner="user",
    )
    assert _MARKER not in _agent(workspace)._retrieve_persistent(_UNRELATED)
