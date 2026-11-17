"""Four gaps the silent-failure hunter found inside the 18.11 medium fixes."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from core import failure_cards as fc
from core.evidence import evidence_from_tool_result, evidence_is_independent
from core.knowledge_pipeline import ConflictResolver, _claim_id
from core.smart_memory import _repaired_status
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


# 1 -- a stored review is never raised beyond the 1:1 tie: an old-rule retirement
# cannot be told from a deliberate MIR-058 demotion (left open on purpose, 18.11)
@pytest.mark.parametrize("success,failure,conf", [(5, 0, 0.857), (3, 2, 0.571), (1, 2, 0.4), (0, 1, 0.333)])
def test_repair_keeps_stored_reviews(success, failure, conf):
    assert _repaired_status("needs_review", success, failure, conf) == "needs_review"


def test_repair_frees_the_one_tie():
    assert _repaired_status("needs_review", 1, 1, 0.5) == "candidate"


# 2 -- a fresh card never pushes out a reviewer's rejection
def _card(sig: str, status: str, seen: int = 1) -> fc.Card:
    names = {f.name for f in dataclasses.fields(fc.Card)}
    kw = {"sig": sig, "error": "e", "tool": "t", "status": status, "seen": seen}
    return fc.Card(**{k: v for k, v in kw.items() if k in names})


def _full_of_rejections(tmp_path: Path) -> None:
    store = fc.CardStore(tmp_path)
    for i in range(fc.MAX_CARDS):
        store.cards[f"g{i}"] = _card(f"g{i}", "rejected", 5)
    store.save()


def _rejected(tmp_path: Path) -> int:
    return sum(c.status == "rejected" for c in fc.CardStore(tmp_path).cards.values())


def test_new_card_does_not_evict_a_rejection(tmp_path):
    _full_of_rejections(tmp_path)
    store = fc.CardStore(tmp_path)
    store.cards["new"] = _card("new", "retired")
    store.save()
    assert _rejected(tmp_path) == fc.MAX_CARDS


def test_reseen_and_new_cards_do_not_evict_rejections(tmp_path):
    _full_of_rejections(tmp_path)
    store = fc.CardStore(tmp_path)
    for sig in list(store.cards)[:50]:
        store.cards[sig].seen += 1
    for i in range(50):
        store.cards[f"n{i}"] = _card(f"n{i}", "unlearned")
    store.save()
    assert _rejected(tmp_path) == fc.MAX_CARDS


# 3 -- shell words that expand into own state, and own paths in the output
@pytest.mark.parametrize("argv", [
    ["grep", "-r", "foo", "."], ["cat", "d*/x"], ["cat", "dat?/x"], ["sh", "-c", "cat $(pwd)/data/x"],
    ["find", ".", "-name", "*.jsonl"], ["cat", "{data,x}/a"],
])
def test_expanding_shell_words_are_not_independent(argv):
    ev = evidence_from_tool_result(tool_name="shell_exec", arguments={},
                                   output={"argv": argv, "stdout": "budget is 5", "exit_code": 0})
    assert evidence_is_independent(ev) is False


def test_shell_output_listing_own_files_is_not_independent():
    ev = evidence_from_tool_result(tool_name="shell_exec", arguments={},
                                   output={"argv": ["grep", "-rn", "budget", "src"],
                                           "stdout": "data/state.json:3: budget is 5", "exit_code": 0})
    assert evidence_is_independent(ev) is False


# 4 -- a claim from a source too weak to save is not promoted by a strong witness
@pytest.mark.parametrize("weak_type,trust", [("forum", 0.9), ("web_page", 0.3), ("unknown", 0.9)])
def test_weak_claim_is_not_promoted(weak_type, trust):
    reg = SourceRegistry()
    strong = SourceRecord(id="s:docs", type="documentation", title="d",
                          locator="https://docs.python.org/a", trust_level=0.9)
    weak = SourceRecord(id="s:weak", type=weak_type, title="w", locator="https://weak.example/t", trust_level=trust)
    text = "The default port is 8080."
    for src in (strong, weak):
        reg.add_source(src)
        reg.add_claim(ClaimRecord(id=_claim_id(src.locator, text), source_id=src.id, text=text,
                                  locator=src.locator, confidence=0.9, status="extracted"))
    out, _ = ConflictResolver().resolve(reg)
    assert {c.source_id: c.status for c in out.claims}["s:weak"] == "extracted"
