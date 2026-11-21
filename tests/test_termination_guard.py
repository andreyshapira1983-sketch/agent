"""Tests for core.termination_guard (MAST FM-1.5, FM-3.1)."""

from core.termination_guard import TerminationGuard


def test_premature_completion_flagged_for_tool_demanding_question():
    g = TerminationGuard()
    ev = g.check_completion(
        question="прочитай core/loop.py и расскажи что там",
        chain_size=0,
        had_any_artifacts=False,
    )
    assert ev is not None
    assert any("прочит" in kw for kw in ev.matched_keywords)
