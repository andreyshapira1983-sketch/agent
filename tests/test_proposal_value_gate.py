"""Tests for the deterministic pre-publish proposal value gate (TD-035).

The gate must:
* hard-veto no-effect Python changes (comment-only, whitespace-only,
  formatting-only, identical-after-normalization);
* reproduce the live incident (``WIDEST`` -> ``widest`` comment capitalization
  sold as a "robustness improvement") as a ``value_veto``;
* let a real code change pass;
* NOT hard-veto docs (*.md) text/comment changes;
* raise a soft flag (never a veto) when the summary overclaims value on a
  trivial diff;
* be pure: no LLM/network/git/file/inbox side effects.
"""
from __future__ import annotations

from core.proposal_value_gate import ValueGateResult, evaluate_proposal_value


# ── hard veto: no code effect (Python) ───────────────────────────────────────


def test_comment_only_python_change_is_vetoed():
    current = "VALUE = 1  # WIDEST allowed span\n"
    proposed = "VALUE = 1  # widest allowed span\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "tidy comment")
    assert result.vetoed
    assert result.verdict == "value_veto"
    assert any("comment" in r or "no code effect" in r for r in result.veto_reasons)


def test_whitespace_only_change_is_vetoed():
    current = "def f():\n    return 1\n"
    proposed = "def f():\n\n    return 1\n\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "reflow")
    assert result.vetoed


def test_formatting_only_reindent_is_vetoed():
    # Same logical structure, different indent width -> no code effect.
    current = "if True:\n    x = 1\n"
    proposed = "if True:\n        x = 1\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "reformat")
    assert result.vetoed


def test_identical_after_normalization_is_vetoed():
    current = "A = 1\nB = 2\n"
    proposed = "A = 1\n\n\nB = 2\n   \n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "cosmetic")
    assert result.vetoed


def test_widest_incident_is_vetoed_and_flags_no_inbox_semantics():
    # The exact live incident: comment capitalization dressed as robustness.
    current = "MAX = 80  # WIDEST\n"
    proposed = "MAX = 80  # widest\n"
    result = evaluate_proposal_value(
        "core/redaction.py", current, proposed, "robustness improvement"
    )
    assert result.vetoed
    assert result.verdict == "value_veto"


# ── pass: real changes ───────────────────────────────────────────────────────


def test_real_code_change_passes():
    current = "def f():\n    return 1\n"
    proposed = "def f():\n    return 2\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "fix return value")
    assert not result.vetoed
    assert result.verdict == "ok"
    assert result.flags == []


def test_added_statement_passes():
    current = "def f():\n    return 1\n"
    proposed = "def f():\n    log()\n    return 1\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "add logging")
    assert not result.vetoed


# ── doc exception ────────────────────────────────────────────────────────────


def test_docs_md_text_change_is_not_hard_vetoed():
    current = "# Title\n\nThis is WIDEST.\n"
    proposed = "# Title\n\nThis is widest.\n"
    result = evaluate_proposal_value("docs/self_build.md", current, proposed, "clarify wording")
    assert not result.vetoed


def test_docs_md_whitespace_change_is_not_hard_vetoed():
    current = "# Title\n\nBody.\n"
    proposed = "# Title\n\n\nBody.\n\n"
    result = evaluate_proposal_value("docs/self_build.md", current, proposed, "reflow docs")
    assert not result.vetoed


# ── soft flag: overclaim on a trivial diff ───────────────────────────────────

_HYPE_REASON = "revolutionary breakthrough that dramatically boosts performance"


def test_overclaim_on_trivial_code_change_is_soft_flag_not_veto():
    # A tiny (1-line) real change with a wildly overclaiming summary: passes,
    # but carries a human-visible flag.
    current = "VALUE = 1\n"
    proposed = "VALUE = 2\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, _HYPE_REASON)
    assert not result.vetoed
    assert any("overclaim" in f for f in result.flags)


def test_overclaim_on_docs_trivial_change_is_soft_flag():
    current = "# Title\n\nOne.\n"
    proposed = "# Title\n\nTwo.\n"
    result = evaluate_proposal_value("docs/self_build.md", current, proposed, _HYPE_REASON)
    assert not result.vetoed
    assert any("overclaim" in f for f in result.flags)


def test_no_overclaim_flag_on_large_diff():
    # A big diff is not "trivial", so the overclaim heuristic stays silent.
    current = "".join(f"L{i} = {i}\n" for i in range(20))
    proposed = "".join(f"L{i} = {i + 1}\n" for i in range(20))
    result = evaluate_proposal_value("core/redaction.py", current, proposed, _HYPE_REASON)
    assert not result.vetoed
    assert result.flags == []


def test_substantive_reason_gets_no_flag():
    current = "VALUE = 1\n"
    proposed = "VALUE = 2\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "fix off-by-one")
    assert not result.vetoed
    assert result.flags == []


# ── edge cases / robustness ──────────────────────────────────────────────────


def test_non_python_non_doc_whitespace_only_is_vetoed():
    current = "a\nb\n"
    proposed = "a\n\nb\n\n"
    result = evaluate_proposal_value("data/notes.txt", current, proposed, "reflow")
    assert result.vetoed


def test_non_python_content_change_passes():
    result = evaluate_proposal_value("data/notes.txt", "a\nb\n", "a\nc\n", "edit")
    assert not result.vetoed


def test_unparseable_python_change_does_not_raise_and_passes_if_content_differs():
    # Builder content that is not valid Python must not crash the gate; since it
    # is not whitespace-equal it is not vetoed as no-effect.
    current = "def f(:\n"
    proposed = "def f(:\n    x = 1\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, "edit")
    assert not result.vetoed


def test_empty_reason_never_flags():
    result = evaluate_proposal_value("core/redaction.py", "A = 1\n", "A = 2\n", "")
    assert result.flags == []


def test_result_to_dict_round_trips():
    result = evaluate_proposal_value("core/redaction.py", "A = 1  # X\n", "A = 1  # x\n", "r")
    d = result.to_dict()
    assert d["verdict"] == "value_veto"
    assert isinstance(d["veto_reasons"], list) and d["veto_reasons"]
    assert isinstance(d["flags"], list)


def test_injected_hype_fn_is_used_and_no_default_import_needed():
    calls: list[str] = []

    class _Outcome:
        is_hype = True

    def fake_hype(text: str):
        calls.append(text)
        return _Outcome()

    result = evaluate_proposal_value(
        "core/redaction.py", "A = 1\n", "A = 2\n", "anything", hype_fn=fake_hype
    )
    assert calls == ["anything"]
    assert any("overclaim" in f for f in result.flags)


def test_default_result_is_ok():
    assert ValueGateResult().verdict == "ok"
    assert not ValueGateResult().vetoed
