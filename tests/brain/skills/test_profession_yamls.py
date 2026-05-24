"""Validate every shipped profession YAML against the agent's tooling.

These checks fire on every CI run so a new profession can't be merged with
unknown acceptance kinds, missing capabilities, or references to
non-existent tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.skills.registry import SkillRegistry
from brain.skills.verifier import KNOWN_KINDS


REPO_ROOT = Path(__file__).resolve().parents[3]
PROFESSIONS_DIR = REPO_ROOT / "professions"


# ════════════════════════════════════════════════════════════════════
# Discovery
# ════════════════════════════════════════════════════════════════════

def _all_profession_files() -> list[Path]:
    return sorted(p for p in PROFESSIONS_DIR.iterdir()
                  if p.suffix in {".yaml", ".yml"})


def test_repository_ships_at_least_4_professions():
    files = _all_profession_files()
    assert len(files) >= 4, f"expected ≥4 profession YAMLs, found {files}"


# ════════════════════════════════════════════════════════════════════
# Schema
# ════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def loaded_registry() -> SkillRegistry:
    reg = SkillRegistry()
    n = reg.load_directory(PROFESSIONS_DIR)
    assert n == len(_all_profession_files()), "some YAML failed to load"
    return reg


@pytest.mark.parametrize("path", _all_profession_files(), ids=lambda p: p.stem)
def test_each_yaml_parses_to_profession(path):
    reg = SkillRegistry()
    prof = reg.load_file(path)
    assert prof.id
    assert prof.name
    assert prof.workflow.steps, f"{path.name}: workflow is empty"
    assert prof.required_capabilities, f"{path.name}: required_capabilities is empty"


@pytest.mark.parametrize("path", _all_profession_files(), ids=lambda p: p.stem)
def test_acceptance_kinds_are_known(path):
    reg = SkillRegistry()
    prof = reg.load_file(path)
    for check in prof.acceptance_criteria:
        assert check.kind in KNOWN_KINDS, (
            f"{path.name}: unknown acceptance kind '{check.kind}'. "
            f"Either add a _check_{check.kind} method to Verifier or "
            f"remove the entry from {path.name}."
        )


# ════════════════════════════════════════════════════════════════════
# Tool wiring
# ════════════════════════════════════════════════════════════════════

# Profession capability_id → tool_name mapping the agent commits to.
_CAPABILITY_TO_TOOL = {
    "read_docx":         "docx_reader",
    "write_docx":        "docx_writer",
    "write_pptx":        "pptx_writer",
    "write_xlsx":        "xlsx_writer",
    "file_write":        "file_write",
    "send_email":        "email",
    # llm_text_rewrite is "native" — backed by the LLM, no tool.
    "llm_text_rewrite":  None,
}


@pytest.mark.parametrize("path", _all_profession_files(), ids=lambda p: p.stem)
def test_required_capabilities_have_known_mappings(path):
    """Every capability referenced by a profession must be in our mapping table."""
    reg = SkillRegistry()
    prof = reg.load_file(path)
    for cap in prof.required_capabilities:
        assert cap in _CAPABILITY_TO_TOOL, (
            f"{path.name}: capability '{cap}' has no mapping. "
            f"Either add {cap!r} to _CAPABILITY_TO_TOOL or rename it in the YAML."
        )


@pytest.mark.parametrize("path", _all_profession_files(), ids=lambda p: p.stem)
def test_workflow_tools_exist_in_registry(path):
    """Every workflow step with action=tool_call references a real tool."""
    from tools.builtins import (
        DocxReaderTool, DocxWriterTool, PptxWriterTool, XlsxWriterTool,
        FileWriteTool, EmailTool, WebFetchTool,
    )
    from tools.registry import ToolRegistry
    tr = ToolRegistry()
    tr.register_many(
        DocxReaderTool(), DocxWriterTool(), PptxWriterTool(), XlsxWriterTool(),
        FileWriteTool(), EmailTool(), WebFetchTool(),
    )

    reg = SkillRegistry()
    prof = reg.load_file(path)
    for step in prof.workflow.steps:
        if step.action != "tool_call":
            continue
        assert step.tool, f"{path.name}/{step.id}: action=tool_call but no `tool` named"
        assert tr.has(step.tool), (
            f"{path.name}/{step.id}: references unknown tool '{step.tool}'. "
            f"Available: {tr.names()}"
        )
