"""Guard for diagnostic tooling: a state row on disk is an envelope, not the record.

This exists because of a near-miss during the D-1 measurement (2026-07-20). An
analysis script read `data/procedural_memory.jsonl` with a plain `json.loads`
per line and reported, confidently:

    65 procedures, 1 distinct workflow_key, 0 episodes carrying tools

Every number was wrong. Rows are wrapped in the state-integrity envelope, so
top-level `.get("workflow_key")` returned None for all of them and the script
was measuring the wrapper. The conclusion it invited — "procedures are not
being formed at all" — would have sent the next fix in a completely wrong
direction, and nothing about the output looked broken.

So the trap is pinned here: the on-disk shape and the decoded shape differ, and
any tool that skips the decoder sees the wrapper. Product code already reads
through `read_state_jsonl_unlocked` and is unaffected; this protects the
analysis scripts that read files directly.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from core.state_integrity import read_state_jsonl_unlocked


def _episode() -> EpisodeRecord:
    return EpisodeRecord(
        goal="ship the release", question="how do I deploy", outcome="success",  # type: ignore[arg-type]
        summary="s", tools_used=("file_read", "list_dir"),
        verified_chunks=2, unverified_chunks=0, id="ep-guard",
    )


def test_raw_line_is_an_envelope_not_the_record(tmp_path: Path) -> None:
    """The exact shape that fooled the measurement."""
    path = tmp_path / "episodic.jsonl"
    EpisodicMemoryStore(path).save(_episode())

    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert set(raw) == {"_integrity", "payload"}, (
        "a raw state row is the envelope; if this ever becomes the record "
        "itself, update the diagnostic guidance in this file"
    )
    # The precise failure mode: fields read at top level are silently absent.
    assert raw.get("tools_used") is None
    assert raw.get("goal") is None
    assert raw.get("outcome") is None


def test_the_record_lives_under_payload(tmp_path: Path) -> None:
    path = tmp_path / "episodic.jsonl"
    EpisodicMemoryStore(path).save(_episode())

    raw = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert raw["payload"]["goal"] == "ship the release"
    assert raw["payload"]["tools_used"] == ["file_read", "list_dir"]


def test_the_standard_reader_returns_payloads(tmp_path: Path) -> None:
    """`read_state_jsonl_unlocked` is what analysis tools should use."""
    path = tmp_path / "episodic.jsonl"
    EpisodicMemoryStore(path).save(_episode())

    rows = read_state_jsonl_unlocked(path)

    assert len(rows) == 1
    assert "_integrity" not in rows[0], "the decoder must strip the envelope"
    assert rows[0]["goal"] == "ship the release"
    assert rows[0]["tools_used"] == ["file_read", "list_dir"]


def test_a_naive_reader_produces_empty_aggregates(tmp_path: Path) -> None:
    """Reproduces the near-miss, so the failure signature stays recognisable.

    The point is that naive reading does not raise, does not warn, and returns
    a plausible-looking zero. That is what made the wrong numbers convincing.
    """
    path = tmp_path / "episodic.jsonl"
    store = EpisodicMemoryStore(path)
    for i in range(3):
        store.save(
            EpisodeRecord(
                goal=f"goal {i}", question="q", outcome="success",  # type: ignore[arg-type]
                summary="s", tools_used=("file_read",), id=f"ep-{i}",
            )
        )

    naive = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    decoded = read_state_jsonl_unlocked(path)

    assert len(naive) == len(decoded) == 3, "both see three rows — only the fields differ"
    assert {r.get("goal") for r in naive} == {None}, "naive reading loses every goal"
    assert len({r["goal"] for r in decoded}) == 3, "decoded reading sees three distinct goals"
