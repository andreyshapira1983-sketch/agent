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
