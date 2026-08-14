"""A directory listing is a snapshot, not a standing claim about the world.

Background: docs/CODE_NOTES.md, "A listing is not a fact".
"""
from __future__ import annotations

from core.evidence import evidence_from_tool_result
from core.knowledge_pipeline import _NON_ASSERTING_SOURCE_TYPES
from core.source_registry import source_type_from_evidence

_LISTING = "__init__.py capability_tasks.py conftest.py fixtures/ self-audit-lessons.md"


def _listing_evidence():
    ev = evidence_from_tool_result(
        tool_name="list_dir", arguments={"path": "knowledge/doctrine/"}, output=_LISTING,
    )
    assert ev is not None, "precondition: list_dir still produces evidence"
    return ev


def test_a_listing_is_typed_as_a_tool_dump_not_a_document():
    """Measured 2026-08-14 on the operator's store, three records banked as
    `fact`/`source-backed` at 0.85: two "Directory listing of workspace path …"
    and the bare filename "self-audit-lessons.md". All three came from
    `list_dir`, whose evidence carries kind="file" — so the write policy read a
    directory snapshot as a document that asserts things.
    """
    assert source_type_from_evidence(_listing_evidence()) in _NON_ASSERTING_SOURCE_TYPES


def test_the_citation_still_resolves():
    """Only the type moved. `[file:knowledge/doctrine/]` must keep working."""
    assert _listing_evidence().source_id == "file:knowledge/doctrine/"


def test_a_real_file_read_still_asserts():
    """The gate must not swallow prose: a read document still states things."""
    ev = evidence_from_tool_result(
        tool_name="file_read",
        arguments={"path": "docs/GOVERNANCE.md"},
        output="Агент не может слить ветку без решения оператора.",
    )
    assert ev is not None
    assert source_type_from_evidence(ev) not in _NON_ASSERTING_SOURCE_TYPES
