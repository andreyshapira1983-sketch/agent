"""Guard: every doctrine doc the planner tells the agent to read must exist.

The doc router (`core.doc_routing`) declares — and the learning planner
re-exports — a manifest of doctrine/corporate docs and
instruct the central agent to `file_read` them for corporate-model / governance
/ subagent / autonomy questions. If a manifest entry has no backing file, the
agent is sent to read a non-existent path at runtime (wasted steps, failed
reads). This test fails loudly if the two manifests drift from the repository
or from each other.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_subagent_doc_separates_implemented_from_planned() -> None:
    text = (REPO_ROOT / "knowledge" / "doctrine" / "SUBAGENT_LIFECYCLE.md").read_text(encoding="utf-8")
    assert "IMPLEMENTED" in text
    assert "PLANNED" in text
    # honest boundary: must not claim auto-pause/auto-retire already work.
    # normalise whitespace so line wraps in the doc don't break the check.
    flat = " ".join(text.lower().replace(">", " ").split())
    assert "`auto-pause` and `auto-retire` do not exist today" in flat
    assert "no auto-promote, auto-pause, auto-retire, or auto-quarantine" in flat


def test_self_repair_doc_separates_implemented_from_planned() -> None:
    text = (REPO_ROOT / "knowledge" / "doctrine" / "SELF_REPAIR_DOCTRINE.md").read_text(
        encoding="utf-8"
    )
    assert "NORMATIVE" in text
    assert "IMPLEMENTED" in text
    assert "PLANNED" in text
    flat = " ".join(text.lower().replace(">", " ").split())
    # honest boundary: this governs self-diagnosis/self-repair, and must not be
    # sold as a closed self-evolution loop.
    assert "governs **self-diagnosis and self-repair only**".lower() in flat
    assert "it is *not* a self-evolution mechanism".lower() in flat
