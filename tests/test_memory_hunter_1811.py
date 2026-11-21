"""Four gaps the silent-failure hunter found inside the 18.11 medium fixes."""
from __future__ import annotations

import pytest

from core.evidence import evidence_from_tool_result, evidence_is_independent
from core.knowledge_pipeline import ConflictResolver, _claim_id
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


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
