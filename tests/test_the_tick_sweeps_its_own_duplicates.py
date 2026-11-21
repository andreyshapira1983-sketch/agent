"""The unattended tick collapses byte-identical episodes itself.

WHY THIS EXISTS. MIR-131 measured thirteen maintenance actions whose only
caller is a typed command — and the autonomous path is precisely the path that
generates the most repeats and could not clean up after itself. The bill came
due twice on one day: 43 identical `approval_wait` episodes accumulated during
the 2026-08-16 unattended run because `collapse_duplicate_episodes` (built for
exactly that, MIR-090) only ran from `:memory hygiene`, and draining them
afterwards took a one-time migration script.

THE LINE THIS DRAWS, deliberately. Of the thirteen CLI-only actions, exactly
ONE crosses to the autonomous path here: duplicate collapse. It is mechanical —
it removes only byte-identical copies and keeps the newest, so no judgement is
exercised and no information is lost. The other twelve (staleness pruning,
archiving, summarising) decide which memories are WORTH keeping, which is the
resolver-seat hazard MIR-128 records: an agent judging which of its own records
survive needs the same discipline as a judge scoring its own work. Those stay
with the operator. Widening this sweep is a decision, not a refactor.

THE IN-HOUSE PATTERN. `ApprovalInbox.expire_stale()` runs on every read of the
inbox, expressly so the queue does not rot "when the operator goes offline".
This applies the same shape to episodic memory: maintenance invoked by the
path that needs it, not by a keyboard.
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


def _dup(question: str = "self-build-produce",
         summary: str = "self-build approval_wait: pending item exists",
         tags: tuple[str, ...] = ("self-build",)) -> EpisodeRecord:
    return EpisodeRecord(goal="g", question=question, outcome="partial",
                         summary=summary, tags=tags)


def _store(workspace: Path) -> EpisodicMemoryStore:
    return EpisodicMemoryStore(path=workspace / "data" / "episodic_memory.jsonl")


def test_protected_rows_are_not_touched(workspace: Path) -> None:
    """The collapser's own boundary, re-pinned at the tick's call site: a
    hand-tagged lesson outranks the sweep even when byte-identical."""
    from agent_tick import _sweep_episodic_duplicates

    store = _store(workspace)
    for _ in range(2):
        store.save(_dup(summary="real lesson", tags=("self-build", "lesson")))

    dropped = _sweep_episodic_duplicates(workspace)

    assert dropped == 0
    assert len(store.load()) == 2


def test_run_tick_actually_reaches_the_sweep() -> None:
    """The MIR-131 shape itself, pinned: an organ that exists but is not on
    the live path is exactly what this repair exists to end."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("agent_tick.py").read_text(encoding="utf-8"))
    by_name = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "run_tick" in by_name
    called = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
              for n in ast.walk(by_name["run_tick"]) if isinstance(n, ast.Call)}
    assert "_sweep_episodic_duplicates" in called, (
        "the sweep exists but run_tick never calls it — thirteen organs "
        "already have that exact defect"
    )
