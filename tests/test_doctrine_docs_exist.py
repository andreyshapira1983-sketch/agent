"""Guard: every doctrine doc the planner tells the agent to read must exist.

The planner (`core.planner`) and the learning planner
(`core.learning_planner`) declare a manifest of doctrine/corporate docs and
instruct the central agent to `file_read` them for corporate-model / governance
/ subagent / autonomy questions. If a manifest entry has no backing file, the
agent is sent to read a non-existent path at runtime (wasted steps, failed
reads). This test fails loudly if the two manifests drift from the repository
or from each other.
"""

from __future__ import annotations

from pathlib import Path

from core.learning_planner import (
    _DOCTRINE_CORPORATE_DOC_PATHS as LEARNING_MANIFEST,
)
from core.planner import _DOCTRINE_CORPORATE_DOC_PATHS as PLANNER_MANIFEST
from core.planner import _MEMORY_GOVERNANCE_DOC_PATHS as MEMORY_MANIFEST
from core.planner import _SUBAGENT_GOVERNANCE_DOC_PATHS as SUBAGENT_MANIFEST

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_planner_doctrine_docs_all_exist() -> None:
    missing = [p for p in PLANNER_MANIFEST if not (REPO_ROOT / p).is_file()]
    assert not missing, (
        "planner doctrine manifest references files that do not exist: "
        f"{missing}. Create the doc or remove it from "
        "core.planner._DOCTRINE_CORPORATE_DOC_PATHS."
    )


def test_learning_planner_doctrine_docs_all_exist() -> None:
    missing = [p for p in LEARNING_MANIFEST if not (REPO_ROOT / p).is_file()]
    assert not missing, (
        "learning_planner doctrine manifest references files that do not "
        f"exist: {missing}. Create the doc or remove it from "
        "core.learning_planner._DOCTRINE_CORPORATE_DOC_PATHS."
    )


def test_planner_and_learning_manifests_agree() -> None:
    assert tuple(PLANNER_MANIFEST) == tuple(LEARNING_MANIFEST), (
        "planner and learning_planner doctrine manifests have drifted apart; "
        "keep them identical so the agent is told to read the same docs."
    )


def test_readme_roadmap_reference_resolves() -> None:
    """README's source-of-truth #3 must point at a real roadmap file."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/ROADMAP.md" in readme
    assert (REPO_ROOT / "docs" / "ROADMAP.md").is_file()


def test_subagent_governance_docs_all_exist() -> None:
    missing = [p for p in SUBAGENT_MANIFEST if not (REPO_ROOT / p).is_file()]
    assert not missing, (
        "subagent governance manifest references files that do not exist: "
        f"{missing}. Create the doc or remove it from "
        "core.planner._SUBAGENT_GOVERNANCE_DOC_PATHS."
    )


def test_subagent_doc_is_thematic_not_in_corporate_manifest() -> None:
    """The lifecycle doc is conditionally routed, not a universal doctrine doc.

    Keeping it out of the corporate manifest is what stops it from being read on
    every architecture question. If it leaks back into the corporate list the
    conditional routing is silently defeated.
    """
    for path in SUBAGENT_MANIFEST:
        assert path not in PLANNER_MANIFEST, path
        assert path not in LEARNING_MANIFEST, path


def test_subagent_doc_separates_implemented_from_planned() -> None:
    text = (REPO_ROOT / "docs" / "SUBAGENT_LIFECYCLE.md").read_text(encoding="utf-8")
    assert "IMPLEMENTED" in text
    assert "PLANNED" in text
    # honest boundary: must not claim auto-pause/auto-retire already work.
    # normalise whitespace so line wraps in the doc don't break the check.
    flat = " ".join(text.lower().replace(">", " ").split())
    assert "`auto-pause` and `auto-retire` do not exist today" in flat
    assert "no auto-promote, auto-pause, auto-retire, or auto-quarantine" in flat


def test_memory_governance_docs_all_exist() -> None:
    missing = [p for p in MEMORY_MANIFEST if not (REPO_ROOT / p).is_file()]
    assert not missing, (
        "memory governance manifest references files that do not exist: "
        f"{missing}. Create the doc or remove it from "
        "core.planner._MEMORY_GOVERNANCE_DOC_PATHS."
    )


def test_memory_docs_are_thematic_not_in_corporate_manifest() -> None:
    """Conditionally routed, never universal — same rule as the sub-agent doc.

    If a memory doc leaks into the corporate manifest it is read on every
    architecture question and the conditional routing is silently defeated.
    """
    for path in MEMORY_MANIFEST:
        assert path not in PLANNER_MANIFEST, path
        assert path not in LEARNING_MANIFEST, path
        assert path not in SUBAGENT_MANIFEST, path


def test_memory_manifest_excludes_unapproved_and_superseded_docs() -> None:
    """The two documents deliberately kept out must stay out.

    `MEMORY_LIFECYCLE_CONTRACT.md` is an unapproved v2-draft that no code
    implements; `MEMORY_FIX_PLAN.md` is partly superseded (its A3 prescription
    was never applied as written). Injecting either as doctrine would teach the
    agent a rule that does not hold in the code it is reading.
    """
    for path in (
        "docs/audit/MEMORY_LIFECYCLE_CONTRACT.md",
        "docs/MEMORY_FIX_PLAN.md",
    ):
        assert path not in MEMORY_MANIFEST, (
            f"{path} was added to the memory manifest. It is unapproved or "
            "partly superseded; if that changed, update this test and say why."
        )
