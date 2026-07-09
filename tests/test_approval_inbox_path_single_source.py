"""Regression: the approval-inbox path has a single source of truth.

The knowledge-ingest conflict resolver previously flagged that
``APPROVAL_INBOX_PATH`` / ``DEFAULT_APPROVAL_INBOX_PATH`` was declared
independently in several modules (once as a bare ``str`` in ``agent_tick``,
elsewhere as a ``Path``).  They all now import the canonical constant from
``core.approval_inbox`` so the location can never drift between callers.
"""
from __future__ import annotations

from pathlib import Path

import agent_tick
from cli import commands_approval, commands_health
from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH


def test_canonical_constant_is_expected_path() -> None:
    assert DEFAULT_APPROVAL_INBOX_PATH == Path("data") / "approval_inbox.jsonl"


def test_all_callers_reference_the_same_object() -> None:
    # Same identity (imported, not re-declared) across every caller.
    assert agent_tick.APPROVAL_INBOX_PATH is DEFAULT_APPROVAL_INBOX_PATH
    assert commands_health.APPROVAL_INBOX_PATH is DEFAULT_APPROVAL_INBOX_PATH
    assert commands_approval.DEFAULT_APPROVAL_INBOX_PATH is DEFAULT_APPROVAL_INBOX_PATH


def test_workspace_join_still_resolves(tmp_path: Path) -> None:
    # Historic usage in every caller is ``workspace / APPROVAL_INBOX_PATH``.
    joined = tmp_path / agent_tick.APPROVAL_INBOX_PATH
    assert joined == tmp_path / "data" / "approval_inbox.jsonl"
