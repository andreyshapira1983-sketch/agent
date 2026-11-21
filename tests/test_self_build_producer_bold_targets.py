"""Tests for Provod #2: widened grounded self-build target acceptance.

The producer no longer limits grounded candidates to a hardcoded trio. A
grounded backlog candidate may now target any file that clears BOTH hard safety
layers — the critical-organ denylist and the self-apply lane's low-risk
classifier — while apply authority (dry-run + human approval + passing tests)
stays exactly the same.
"""
from __future__ import annotations


class _Candidate:
    def __init__(
        self,
        target_path: str,
        problem_quote: str = "a grounded problem",
        evidence_ref: str = "architecture_audit:x",
        signal_source: str = "architecture_audit",
    ):
        self.target_path = target_path
        self.signal_source = signal_source
        self.problem_quote = problem_quote
        self.evidence_ref = evidence_ref


# ── selector prefers the first ACTIONABLE ranked candidate ────────────────────


def test_default_selector_falls_back_to_top_when_none_actionable(tmp_path, monkeypatch):
    import core.backlog_selector as bs
    from core.self_build_producer import _default_grounded_selector

    denied1 = _Candidate(".github/workflows/ci.yml", "gap one")
    denied2 = _Candidate("requirements.lock", "gap two")
    monkeypatch.setattr(bs, "load_backlog", lambda *a, **k: [denied1, denied2])
    monkeypatch.setattr(bs, "select_top", lambda c: c[0] if c else None)

    selected = _default_grounded_selector(tmp_path)()

    assert selected.target_path == ".github/workflows/ci.yml"  # honest #1 refusal
