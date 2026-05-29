"""MVP-14.1 — unit tests for core/evidence.py.

These pin the contract the Verifier (MVP-14.4) and SourceRanker
(deferred) will build on. Every test is hermetic — no I/O, no LLM, no
network. The factory's branch matrix is exhaustive because the loop
trusts it to never crash on a malformed `output`.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

from core.evidence import (
    ALL_EVIDENCE_KINDS,
    DEFAULT_CONFIDENCE,
    MAX_EXCERPT_CHARS,
    Evidence,
    ProvenanceChain,
    compute_content_hash,
    evidence_from_llm_claim,
    evidence_from_memory_record,
    evidence_from_tool_result,
    evidence_from_user_directive,
    make_evidence,
)


# ============================================================
# Taxonomy
# ============================================================

class TestTaxonomy:
    def test_default_confidence_covers_every_kind(self):
        assert set(DEFAULT_CONFIDENCE.keys()) == set(ALL_EVIDENCE_KINDS)

    def test_confidence_values_in_unit_interval(self):
        for kind, val in DEFAULT_CONFIDENCE.items():
            assert 0.0 <= val <= 1.0, f"{kind}={val} out of [0,1]"

    def test_hierarchy_user_explicit_strictly_above_llm_claim(self):
        """The whole point of MVP-14 — LLM is not the highest source."""
        assert DEFAULT_CONFIDENCE["user_explicit"] > DEFAULT_CONFIDENCE["llm_claim"]
        assert DEFAULT_CONFIDENCE["file"] > DEFAULT_CONFIDENCE["llm_claim"]
        assert DEFAULT_CONFIDENCE["test_result"] > DEFAULT_CONFIDENCE["llm_claim"]
        assert DEFAULT_CONFIDENCE["log_event"] > DEFAULT_CONFIDENCE["llm_claim"]
        assert DEFAULT_CONFIDENCE["web_page"] > DEFAULT_CONFIDENCE["web_search_hit"]
        assert DEFAULT_CONFIDENCE["web_search_hit"] > DEFAULT_CONFIDENCE["llm_claim"]

    def test_hierarchy_tested_code_above_workspace_file(self):
        """A green test is stronger evidence than a workspace file —
        the file might be stale, the test ran NOW."""
        assert DEFAULT_CONFIDENCE["test_result"] >= DEFAULT_CONFIDENCE["file"]

    def test_hierarchy_memory_below_file(self):
        assert DEFAULT_CONFIDENCE["memory"] < DEFAULT_CONFIDENCE["file"]


# ============================================================
# compute_content_hash
# ============================================================

class TestContentHash:
    def test_known_value(self):
        # Hand-computed: sha256("hello") = 2cf24dba5fb0a3...
        h = compute_content_hash("hello")
        assert h == hashlib.sha256(b"hello").hexdigest()

    def test_deterministic(self):
        assert compute_content_hash("abc") == compute_content_hash("abc")

    def test_unicode_stable(self):
        a = compute_content_hash("Привет, мир!")
        b = compute_content_hash("Привет, мир!")
        assert a == b
        assert len(a) == 64

    def test_different_inputs_different_hashes(self):
        assert compute_content_hash("a") != compute_content_hash("b")

    def test_non_utf8_safe(self):
        """Garbage encoding must not crash — we use errors='replace'."""
        # Build a string with a surrogate that would normally fail UTF-8.
        bad = "valid\ud800part"
        h = compute_content_hash(bad)
        assert isinstance(h, str)
        assert len(h) == 64


# ============================================================
# make_evidence
# ============================================================

class TestMakeEvidence:
    def test_fills_id_hash_timestamp_confidence(self):
        ev = make_evidence(
            kind="file",
            source_id="file:foo.txt",
            obtained_via="file_read",
            claim="contents of foo.txt",
            excerpt="hello world",
        )
        assert ev.id.startswith("ev_")
        assert ev.kind == "file"
        assert ev.content_hash == compute_content_hash("hello world")
        # ISO-8601 with timezone
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ev.fetched_at)
        assert ev.confidence == DEFAULT_CONFIDENCE["file"]

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

    def test_oversize_excerpt_truncated(self):
        big = "x" * (MAX_EXCERPT_CHARS + 1000)
        ev = make_evidence(
            kind="file", source_id="x", obtained_via="x",
            claim="x", excerpt=big,
        )
        assert len(ev.excerpt) <= MAX_EXCERPT_CHARS + len("...[truncated]")
        assert ev.excerpt.endswith("...[truncated]")

    def test_evidence_is_frozen(self):
        ev = make_evidence(
            kind="file", source_id="x", obtained_via="x",
            claim="x", excerpt="x",
        )
        with pytest.raises(Exception):
            ev.confidence = 0.0  # type: ignore[misc]

    def test_to_dict_roundtrip(self):
        ev = make_evidence(
            kind="file", source_id="file:x", obtained_via="file_read",
            claim="x", excerpt="hello",
        )
        d = ev.to_dict()
        back = Evidence.from_dict(d)
        assert back == ev


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

    def test_add(self):
        c = ProvenanceChain()
        c.add(self._ev())
        assert len(c) == 1

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

    def test_by_kind(self):
        c = ProvenanceChain()
        c.add(self._ev(kind="file"))
        c.add(self._ev(kind="web_page", source="web_page:x"))
        c.add(self._ev(kind="file", source="file:y"))
        files = c.by_kind("file")
        assert len(files) == 2
        assert all(e.kind == "file" for e in files)

    def test_highest_confidence(self):
        c = ProvenanceChain()
        c.add(self._ev(kind="memory", conf=0.5, source="memory:x"))
        c.add(self._ev(kind="file", conf=0.9, source="file:y"))
        c.add(self._ev(kind="llm_claim", conf=0.2, source="llm_claim:z"))
        top = c.highest_confidence()
        assert top is not None and top.confidence == 0.9

    def test_log_payload_drops_excerpt(self):
        c = ProvenanceChain()
        c.add(self._ev(excerpt="a very long excerpt"))
        payload = c.to_log_payload()
        assert len(payload) == 1
        assert "excerpt" not in payload[0]
        assert payload[0]["excerpt_len"] == len("a very long excerpt")


# ============================================================
# Factory: per-tool dispatch
# ============================================================

class TestFactoryFileRead:
    def test_string_output_becomes_file_evidence(self):
        ev = evidence_from_tool_result(
            tool_name="file_read",
            arguments={"path": "foo.txt"},
            output="hello world",
        )
        assert ev is not None
        assert ev.kind == "file"
        assert ev.source_id == "file:foo.txt"
        assert ev.obtained_via == "file_read"
        assert "foo.txt" in ev.claim
        assert ev.excerpt == "hello world"
        assert ev.confidence == DEFAULT_CONFIDENCE["file"]

    def test_empty_output_none(self):
        assert evidence_from_tool_result(
            tool_name="file_read", arguments={"path": "x"}, output=""
        ) is None

    def test_non_string_output_none(self):
        assert evidence_from_tool_result(
            tool_name="file_read", arguments={"path": "x"}, output={"x": 1},
        ) is None

    def test_missing_path_argument_falls_back_unknown(self):
        ev = evidence_from_tool_result(
            tool_name="file_read", arguments={}, output="abc",
        )
        assert ev is not None
        assert "unknown" in ev.source_id


class TestFactoryWebSearch:
    def test_list_of_hits_becomes_search_hit_evidence(self):
        hits = [
            {"title": "A", "url": "https://a", "snippet": "..."},
            {"title": "B", "url": "https://b", "snippet": "..."},
        ]
        ev = evidence_from_tool_result(
            tool_name="web_search",
            arguments={"query": "python"},
            output=hits,
        )
        assert ev is not None
        assert ev.kind == "web_search_hit"
        assert ev.source_id == "web_search:python"
        assert "https://a" in ev.excerpt
        assert "https://b" in ev.excerpt

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

    def test_failed_summary(self):
        out = {
            "command": ["python", "-m", "pytest"],
            "passed": 3, "failed": 2, "errors": 1,
            "stdout_tail": "FAILED tests/test_x.py::test_one",
            "timed_out": False,
        }
        ev = evidence_from_tool_result(
            tool_name="run_tests", arguments={}, output=out,
        )
        assert ev is not None
        assert "failed=2" in ev.claim
        assert "FAILED" in ev.excerpt

    def test_timeout_summary(self):
        out = {
            "command": ["pytest"], "passed": 0, "failed": 0, "errors": 0,
            "stdout_tail": "", "timed_out": True,
        }
        ev = evidence_from_tool_result(
            tool_name="run_tests", arguments={}, output=out,
        )
        assert ev is not None
        assert "timed_out=True" in ev.claim

    def test_non_dict_output_none(self):
        assert evidence_from_tool_result(
            tool_name="run_tests", arguments={}, output="oops",
        ) is None


class TestFactoryReadLogs:
    def test_normal_events(self):
        out = {
            "trace_id": "run_abc",
            "events": [
                {"event": "planner", "ts": "2026-01-01T00:00:00Z"},
                {"event": "error", "ts": "2026-01-01T00:00:01Z"},
            ],
        }
        ev = evidence_from_tool_result(
            tool_name="read_logs", arguments={"trace_id": "run_abc"}, output=out,
        )
        assert ev is not None
        assert ev.kind == "log_event"
        assert "run_abc" in ev.source_id
        assert "planner" in ev.excerpt
        assert "error" in ev.excerpt

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
    def test_normal_output(self):
        out = {
            "argv": ["whoami"], "exit_code": 0,
            "stdout": "alice", "stderr": "",
        }
        ev = evidence_from_tool_result(
            tool_name="shell_exec", arguments={"argv": ["whoami"]}, output=out,
        )
        assert ev is not None
        assert ev.kind == "shell_output"
        # MVP-14.5b: source_id is now
        #   shell_output:shell_exec:<argv0_basename>:<short_cmd>
        # so the LLM's typical citations like [shell:whoami] or
        # [shell:shell_exec] both substring-match.
        assert ev.source_id.startswith("shell_output:shell_exec:whoami:")
        assert "whoami" in ev.source_id
        assert "alice" in ev.excerpt
        assert "exit_code=0" in ev.claim

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


class TestFactoryDiffFile:
    def test_normal_diff(self):
        out = {
            "path": "a.py", "additions": 2, "deletions": 1,
            "diff": "--- a/a.py\n+++ b/a.py\n@@\n+x\n+y\n-z\n",
        }
        ev = evidence_from_tool_result(
            tool_name="diff_file",
            arguments={"path": "a.py", "proposed_content": "..."},
            output=out,
        )
        assert ev is not None
        assert ev.kind == "diff_preview"
        assert ev.source_id == "diff_preview:a.py"
        assert "+2 -1" in ev.claim
        assert "@@" in ev.excerpt


class TestFactoryWebFetchPlaceholder:
    """`web_fetch` ships in MVP-14.2. The factory branch must
    nonetheless exist now so the loop is forward-compatible."""

    def test_well_formed_output(self):
        out = {
            "url": "https://example.com",
            "text": "Hello from the page body.",
            "content_hash": "abc123",
            "fetched_at": "2026-01-01T00:00:00Z",
        }
        ev = evidence_from_tool_result(
            tool_name="web_fetch",
            arguments={"url": "https://example.com"},
            output=out,
        )
        assert ev is not None
        assert ev.kind == "web_page"
        assert ev.source_id == "web_page:https://example.com"
        assert ev.content_hash == "abc123"      # tool's own hash respected
        assert ev.fetched_at == "2026-01-01T00:00:00Z"
        assert "Hello" in ev.excerpt

    def test_empty_text_none(self):
        out = {"url": "https://x", "text": "", "content_hash": "0"}
        assert evidence_from_tool_result(
            tool_name="web_fetch", arguments={"url": "https://x"}, output=out,
        ) is None


class TestFactoryFileWriteNoEvidence:
    def test_file_write_never_emits_evidence(self):
        """An action is not a source. The audit event already records
        the write; we must not double-count it as a source of truth."""
        out = {"path": "x.txt", "mode": "create", "bytes_written": 5}
        ev = evidence_from_tool_result(
            tool_name="file_write",
            arguments={"path": "x.txt", "content": "hello"},
            output=out,
        )
        assert ev is None


class TestFactoryGenericFallback:
    def test_unknown_tool_produces_tool_output(self):
        ev = evidence_from_tool_result(
            tool_name="some_new_tool",
            arguments={"x": 1},
            output={"data": "result"},
        )
        assert ev is not None
        assert ev.kind == "tool_output"
        assert "some_new_tool" in ev.obtained_via

    def test_unknown_tool_with_unserialisable_none(self):
        class Weird:
            def __repr__(self):
                raise RuntimeError("boom")
        ev = evidence_from_tool_result(
            tool_name="new_tool", arguments={}, output=Weird(),
        )
        # Either None or generic tool_output — both are acceptable
        # as long as the factory does NOT raise.
        assert ev is None or ev.kind == "tool_output"


# ============================================================
# Factory: failure-mode contracts
# ============================================================

class TestFactoryFailureContracts:
    def test_error_status_returns_none(self):
        ev = evidence_from_tool_result(
            tool_name="file_read",
            arguments={"path": "x"},
            output="hello",
            status="error",
        )
        assert ev is None

    def test_empty_tool_name_none(self):
        ev = evidence_from_tool_result(
            tool_name="", arguments={}, output="x",
        )
        assert ev is None

    def test_none_arguments_safe(self):
        """A missing argument dict must not crash the factory."""
        ev = evidence_from_tool_result(
            tool_name="file_read", arguments=None, output="hello",
        )
        assert ev is not None  # falls back to path=<unknown>


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

    def test_without_source_reduced(self):
        """A memory record with no provenance is worth less."""
        ev = evidence_from_memory_record(
            record_id="mem_x", content="fact", source=None, created_at=None,
        )
        assert ev.confidence < DEFAULT_CONFIDENCE["memory"]
        assert ev.confidence >= 0.25  # floor

    def test_carries_record_id(self):
        ev = evidence_from_memory_record(
            record_id="mem_abc", content="x", source="user", created_at=None,
        )
        assert "mem_abc" in ev.source_id


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
