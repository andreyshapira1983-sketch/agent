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
_RELEVANT = f"What does the {_MARKER} protocol require of the operator?"
_UNRELATED = "How do I configure the HTTP timeout for the web fetch tool?"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _agent(workspace: Path):
    return build_agent(workspace, with_memory=True, with_persistent=True)


def test_an_empty_store_injects_nothing(workspace: Path) -> None:
    """Baseline. Without it a later `in` assertion proves nothing."""
    assert _MARKER not in _agent(workspace)._retrieve_persistent(_RELEVANT)


def test_a_remembered_fact_crosses_the_store_and_reaches_the_prompt(
    workspace: Path,
) -> None:
    writer = _agent(workspace)
    decision, record = writer.remember(
        content=_FACT,
        tags=[_MARKER.lower(), "protocol"],
        source="user-explicit",
        record_type="semantic",
        owner="user",
    )
    assert decision.decision == "save", decision.reasons
    assert record is not None

    store = workspace / "data" / "persistent_memory.jsonl"
    assert store.is_file(), "the write policy said save and nothing reached disk"
    assert _MARKER in store.read_text(encoding="utf-8")

    # A SECOND agent, built the way production builds one: it re-reads the file
    # rather than inheriting the writer's in-memory state.
    reader = _agent(workspace)
    injected = reader._retrieve_persistent(_RELEVANT)
    assert _MARKER in injected, (
        "the record persisted but never reached the prompt block — the wire "
        "between the store and cognition is the broken one"
    )
    assert injected.lstrip().startswith("<long_term_memory>")


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
