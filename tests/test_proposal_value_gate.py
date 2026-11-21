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

from core.proposal_value_gate import evaluate_proposal_value

_HYPE_REASON = "revolutionary breakthrough that dramatically boosts performance"

# ── hard veto: no code effect (Python) ───────────────────────────────────────


def test_widest_incident_with_overclaiming_reason_is_vetoed_and_flagged():
    """A comment-only change sold with hype is vetoed and still carries the overclaim flag.

    The veto says the diff is empty; the flag tells the reviewer the summary oversold it.
    """
    current = "MAX = 80  # WIDEST\n"
    proposed = "MAX = 80  # widest\n"
    result = evaluate_proposal_value("core/redaction.py", current, proposed, _HYPE_REASON)
    assert result.vetoed
    assert result.verdict == "value_veto"
    assert any("overclaim" in f for f in result.flags)


# ── soft flag: overclaim on a trivial diff ───────────────────────────────────


def test_overclaim_on_docs_trivial_change_is_soft_flag():
    current = "# Title\n\nOne.\n"
    proposed = "# Title\n\nTwo.\n"
    result = evaluate_proposal_value("docs/self_build.md", current, proposed, _HYPE_REASON)
    assert not result.vetoed
    assert any("overclaim" in f for f in result.flags)


# ── edge cases / robustness ──────────────────────────────────────────────────


def test_non_python_non_doc_whitespace_only_is_vetoed():
    current = "a\nb\n"
    proposed = "a\n\nb\n\n"
    result = evaluate_proposal_value("data/notes.txt", current, proposed, "reflow")
    assert result.vetoed


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
