"""Fifteen templates, a table naming six: the class, not the samples.

Background: docs/CODE_NOTES.md, "A framework label is not an assertion".
"""
from __future__ import annotations

import pytest

from core.evidence import evidence_from_tool_result
from core.knowledge_pipeline import ClaimExtractor, KnowledgeWritePolicy
from core.source_registry import SourceRecord, source_type_from_evidence

# `list_dir` is the one the 2026-08-14 repair was fitted to. The rest are the
# same class in forms it never saw — the operator's rule for calling a repair
# real. Each produces a claim WE wrote to label a tool result.
_TOOL_CALLS = [
    ("list_dir", {"path": "knowledge/"}, "a.md b.md c/"),
    ("run_tests", {"paths": ["tests"]}, {"exit_code": 0, "stdout": "7 passed"}),
    ("read_logs", {"last_n": 5}, {"trace_id": "trace_a", "events": [], "events_returned": 0,
                                  "total_events": 0, "filtered": False, "log_file": "logs/a.jsonl",
                                  "is_live_session": False, "skipped_live": False,
                                  "compensation_plan": {}}),
    ("file_read", {"path": "docs/x.md"}, "Оператор решает о слиянии."),
]


def _decide(tool: str, args: dict, output: object):
    evidence = evidence_from_tool_result(
        tool_name=tool, arguments=args, output=output,
    )
    assert evidence is not None, f"{tool} produced no evidence"
    source = SourceRecord(
        id=evidence.source_id, type=source_type_from_evidence(evidence),
        locator="x", title="x", trust_level=0.95,
    )
    policy = KnowledgeWritePolicy()
    out = []
    for claim in ClaimExtractor().extract(evidence, source=source):
        out.append((claim, policy.decide(claim, source=source)))
    return evidence, out


@pytest.mark.parametrize("tool,args,output", _TOOL_CALLS)
def test_the_label_we_wrote_never_becomes_a_fact(tool, args, output):
    """Measured 2026-08-14: «Directory listing of workspace path knowledge/»
    was banked at confidence 0.85 tagged fact/source-backed. `_is_meaningful_claim`
    blacklists six opening phrases and there are fifteen templates, so which
    label survived depended on whether two authors happened to choose the same
    first words.
    """
    evidence, decided = _decide(tool, args, output)

    for claim, decision in decided:
        if claim.text.strip() == evidence.claim.strip():
            assert decision.decision == "reject", (
                f"the label we wrote for {tool} was accepted as a durable fact: "
                f"{claim.text!r}"
            )


def test_the_sources_own_words_still_assert():
    """The gate must not swallow the document it protects."""
    _evidence, decided = _decide(
        "file_read", {"path": "docs/x.md"},
        "Агент не может слить ветку без решения оператора.",
    )
    saved = [c for c, d in decided if d.decision == "save"]
    assert saved, "prose read from a file stopped producing any claim at all"
    assert any("слить ветку" in c.text for c in saved)


# The two the other gates never caught: neither the word table nor the
# source-type rule stops them. Measured 2026-08-15, both reached `save`.
_UNGUARDED = [
    ("web_page", "rss_fetch", "article", "Fetched RSS/Atom feed http://e/rss"),
    ("user_explicit", "user_input", "user", "User explicitly directed"),
]


@pytest.mark.parametrize("kind,via,stype,label", _UNGUARDED)
def test_the_labels_no_other_gate_caught(kind, via, stype, label):
    """The unseen form: same class, a template the repair was not fitted to."""
    from core.evidence import make_evidence

    evidence = make_evidence(
        kind=kind, source_id=f"{kind}:x", obtained_via=via, claim=label,
        excerpt="", confidence=0.9,
    )
    source = SourceRecord(
        id=evidence.source_id, type=source_type_from_evidence(evidence),
        locator="x", title="x", trust_level=0.95,
    )
    assert source.type == stype, "precondition: this source type does assert"

    policy = KnowledgeWritePolicy()
    for claim in ClaimExtractor().extract(evidence, source=source):
        if claim.text.strip() == label:
            assert policy.decide(claim, source=source).decision == "reject", (
                f"our own label reached durable memory: {label!r}"
            )
