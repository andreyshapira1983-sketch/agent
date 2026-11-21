"""MVP-14.1 — unit tests for core/evidence.py.

These pin the contract the Verifier (MVP-14.4) and SourceRanker
(deferred) will build on. Every test is hermetic — no I/O, no LLM, no
network. The factory's branch matrix is exhaustive because the loop
trusts it to never crash on a malformed `output`.
"""
from __future__ import annotations

import pytest

from core.evidence import (
    DEFAULT_CONFIDENCE,
    Evidence,
    ProvenanceChain,
    evidence_from_llm_claim,
    evidence_from_memory_record,
    evidence_from_tool_result,
    evidence_from_user_directive,
    make_evidence,
)

# ============================================================
# make_evidence
# ============================================================

class TestMakeEvidence:
    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError, match="unknown evidence kind"):
            make_evidence(
                kind="not_a_kind",  # type: ignore[arg-type]
                source_id="x", obtained_via="x", claim="x", excerpt="x",
            )

    def test_explicit_confidence_overrides_baseline(self):
        ev = make_evidence(
            kind="memory", source_id="memory:x", obtained_via="memory",
            claim="x", excerpt="x", confidence=0.42,
        )
        assert ev.confidence == 0.42

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            make_evidence(
                kind="file", source_id="x", obtained_via="x",
                claim="x", excerpt="x", confidence=1.5,
            )
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            make_evidence(
                kind="file", source_id="x", obtained_via="x",
                claim="x", excerpt="x", confidence=-0.1,
            )


# ============================================================
# ProvenanceChain
# ============================================================

class TestProvenanceChain:
    def _ev(self, kind="file", source="file:x", excerpt="x", conf=None) -> Evidence:
        return make_evidence(
            kind=kind, source_id=source, obtained_via="t",
            claim="c", excerpt=excerpt, confidence=conf,
        )

    def test_empty(self):
        c = ProvenanceChain()
        assert len(c) == 0
        assert c.highest_confidence() is None
        assert c.by_kind("file") == []
        assert c.by_source_id("nope") is None
        assert c.to_log_payload() == []


    def test_extend(self):
        c = ProvenanceChain()
        c.extend([self._ev(), self._ev()])
        assert len(c) == 2

    def test_by_source_id_returns_first(self):
        c = ProvenanceChain()
        e1 = self._ev(source="file:a", excerpt="first")
        e2 = self._ev(source="file:a", excerpt="second")
        c.add(e1)
        c.add(e2)
        # by_source_id returns the FIRST match (primary, not later one).
        assert c.by_source_id("file:a") is e1


    def test_highest_confidence(self):
        c = ProvenanceChain()
        c.add(self._ev(kind="memory", conf=0.5, source="memory:x"))
        c.add(self._ev(kind="file", conf=0.9, source="file:y"))
        c.add(self._ev(kind="llm_claim", conf=0.2, source="llm_claim:z"))
        top = c.highest_confidence()
        assert top is not None and top.confidence == 0.9


# ============================================================
# Factory: per-tool dispatch
# ============================================================

class TestFactoryFileRead:
    def test_empty_output_none(self):
        assert evidence_from_tool_result(
            tool_name="file_read", arguments={"path": "x"}, output=""
        ) is None

    def test_non_string_output_none(self):
        assert evidence_from_tool_result(
            tool_name="file_read", arguments={"path": "x"}, output={"x": 1},
        ) is None


class TestFactoryWebSearch:
    def test_empty_list_none(self):
        """Empty search is a `web_empty` failure mode, not weak evidence."""
        ev = evidence_from_tool_result(
            tool_name="web_search", arguments={"query": "x"}, output=[],
        )
        assert ev is None

    def test_non_list_none(self):
        ev = evidence_from_tool_result(
            tool_name="web_search", arguments={"query": "x"}, output="not a list",
        )
        assert ev is None

    def test_malformed_hits_skipped(self):
        ev = evidence_from_tool_result(
            tool_name="web_search",
            arguments={"query": "x"},
            output=["not a dict", {"title": "good", "url": "https://g"}],
        )
        assert ev is not None
        assert "good" in ev.excerpt
        assert "not a dict" not in ev.excerpt

    def test_all_malformed_none(self):
        ev = evidence_from_tool_result(
            tool_name="web_search",
            arguments={"query": "x"},
            output=["a", "b", 42],
        )
        assert ev is None


class TestFactoryRunTests:
    def test_success_summary(self):
        out = {
            "command": ["python", "-m", "pytest"],
            "passed": 5, "failed": 0, "errors": 0,
            "stdout_tail": "5 passed",
            "timed_out": False,
        }
        ev = evidence_from_tool_result(
            tool_name="run_tests", arguments={}, output=out,
        )
        assert ev is not None
        assert ev.kind == "test_result"
        assert "passed=5" in ev.claim
        assert "5 passed" in ev.excerpt


    def test_non_dict_output_none(self):
        assert evidence_from_tool_result(
            tool_name="run_tests", arguments={}, output="oops",
        ) is None


class TestFactoryReadLogs:
    def test_empty_events_is_weak_evidence(self):
        """Empty logs ARE evidence of 'nothing happened' but weak."""
        ev = evidence_from_tool_result(
            tool_name="read_logs",
            arguments={"trace_id": "run_empty"},
            output={"trace_id": "run_empty", "events": []},
        )
        assert ev is not None
        assert ev.confidence < DEFAULT_CONFIDENCE["log_event"]

    def test_non_list_events_none(self):
        out = {"trace_id": "x", "events": "not a list"}
        assert evidence_from_tool_result(
            tool_name="read_logs", arguments={}, output=out,
        ) is None


class TestFactoryShellExec:
    def test_missing_argv_none(self):
        ev = evidence_from_tool_result(
            tool_name="shell_exec",
            arguments={},
            output={"exit_code": 0, "stdout": "x", "stderr": ""},
        )
        assert ev is None

    def test_non_dict_none(self):
        assert evidence_from_tool_result(
            tool_name="shell_exec", arguments={}, output="oops",
        ) is None


class TestFactoryWebFetchPlaceholder:
    """`web_fetch` ships in MVP-14.2. The factory branch must
    nonetheless exist now so the loop is forward-compatible."""


    def test_empty_text_none(self):
        out = {"url": "https://x", "text": "", "content_hash": "0"}
        assert evidence_from_tool_result(
            tool_name="web_fetch", arguments={"url": "https://x"}, output=out,
        ) is None


class TestFactoryRssFetch:
    def test_well_formed_feed_output(self):
        out = {
            "url": "https://example.com/feed.xml",
            "fetched_at": "2026-05-29T10:00:00Z",
            "content_hash": "rsshash",
            "entries": [
                {
                    "title": "First entry",
                    "url": "https://example.com/first",
                    "published_at": "Fri, 29 May 2026 10:00:00 GMT",
                }
            ],
        }

        ev = evidence_from_tool_result(
            tool_name="rss_fetch",
            arguments={"url": "https://example.com/feed.xml"},
            output=out,
        )

        assert ev is not None
        assert ev.kind == "web_page"
        assert ev.source_id == "web_page:https://example.com/feed.xml"
        assert ev.obtained_via == "rss_fetch"
        assert ev.content_hash == "rsshash"
        assert "First entry" in ev.excerpt

    def test_empty_or_malformed_entries_none(self):
        assert evidence_from_tool_result(
            tool_name="rss_fetch",
            arguments={"url": "https://example.com/feed.xml"},
            output={"url": "https://example.com/feed.xml", "entries": []},
        ) is None
        assert evidence_from_tool_result(
            tool_name="rss_fetch",
            arguments={"url": "https://example.com/feed.xml"},
            output={"url": "https://example.com/feed.xml", "entries": "bad"},
        ) is None


class TestFactoryFileWriteReceipt:
    def test_failed_file_write_emits_nothing(self):
        assert evidence_from_tool_result(
            tool_name="file_write",
            arguments={"path": "x.txt", "content": "hello"},
            output=None,
            status="error",
        ) is None


class TestFactoryGenericFallback:
    def test_unserialisable_output_becomes_a_placeholder_record(self):
        """A raising __repr__ still leaves a tool_output record, so the chain keeps the call."""
        class Weird:
            def __repr__(self):
                raise RuntimeError("boom")
        ev = evidence_from_tool_result(
            tool_name="new_tool", arguments={}, output=Weird(),
        )
        assert ev is not None
        assert ev.kind == "tool_output"
        assert ev.obtained_via == "new_tool"
        assert ev.excerpt == "<unserialisable tool output>"


# ============================================================
# Factory: failure-mode contracts
# ============================================================

class TestFactoryFailureContracts:
    def test_empty_tool_name_none(self):
        ev = evidence_from_tool_result(
            tool_name="", arguments={}, output="x",
        )
        assert ev is None


# ============================================================
# Convenience constructors
# ============================================================

class TestUserDirective:
    def test_user_explicit_top_confidence(self):
        ev = evidence_from_user_directive(
            directive="prefer short answers in Russian",
            request_id="req_abc",
        )
        assert ev.kind == "user_explicit"
        assert ev.confidence == DEFAULT_CONFIDENCE["user_explicit"]
        assert ev.source_id == "user_explicit:req_abc"


class TestMemoryRecord:
    def test_with_source_keeps_baseline(self):
        ev = evidence_from_memory_record(
            record_id="mem_x", content="fact", source="user", created_at=None,
        )
        assert ev.kind == "memory"
        assert ev.confidence == DEFAULT_CONFIDENCE["memory"]


class TestLLMClaim:
    def test_llm_claim_lowest_kind(self):
        ev = evidence_from_llm_claim(claim_text="I think it's correct.")
        assert ev.kind == "llm_claim"
        # llm_claim must be strictly below memory (which is below file).
        assert ev.confidence < DEFAULT_CONFIDENCE["memory"]
        assert ev.confidence == DEFAULT_CONFIDENCE["llm_claim"]

    def test_llm_claim_with_model_in_source_id(self):
        ev = evidence_from_llm_claim(
            claim_text="...", model="claude-sonnet-4-5",
        )
        assert "claude-sonnet-4-5" in ev.obtained_via
