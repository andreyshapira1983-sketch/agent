"""Error/validation branches of the online ingestion contour (web + rss).

test_online_ingestion.py pins the happy paths. This file closes the
load-bearing *failure* seams that only ever ran when a real network call
misbehaved: input validation, search/fetch validation failures, tool
exceptions, warning propagation, URL dedup, and the honest log emission.
All driven with fake tools — no network, no LLM.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.ingestion import ingest_rss_feed, ingest_web_topic
from core.knowledge_pipeline import KnowledgePipeline
from core.source_registry_store import SourceRegistryStore
from tools.base import Tool, ToolRegistry


class _RecordingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload):
        self.events.append((event, payload))


def _agent(workspace: Path, registry: ToolRegistry, *, logger=None):
    return SimpleNamespace(
        registry=registry,
        knowledge_pipeline=KnowledgePipeline(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        knowledge_auto_write=False,
        log=logger,
    )


# ── input validation ─────────────────────────────────────────────────────────

class TestWebInputValidation:
    def test_empty_topic_raises(self, workspace: Path):
        agent = _agent(workspace, ToolRegistry())
        with pytest.raises(ValueError, match="topic is required"):
            ingest_web_topic(agent=agent, topic="   ")

    def test_limit_below_one_raises(self, workspace: Path):
        agent = _agent(workspace, ToolRegistry())
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ingest_web_topic(agent=agent, topic="x", limit=0)

    def test_per_source_below_one_raises(self, workspace: Path):
        agent = _agent(workspace, ToolRegistry())
        with pytest.raises(ValueError, match="per_source must be >= 1"):
            ingest_web_topic(agent=agent, topic="x", per_source=0)

    def test_missing_registry_raises(self, workspace: Path):
        agent = SimpleNamespace(
            registry=None,
            knowledge_pipeline=KnowledgePipeline(),
            source_registry_store=SourceRegistryStore(workspace / "data" / "s.jsonl"),
            knowledge_auto_write=False,
            log=None,
        )
        with pytest.raises(ValueError, match="no tool registry"):
            ingest_web_topic(agent=agent, topic="autonomous agent", source_selection="wikipedia")


# ── web search / fetch failure branches ──────────────────────────────────────

class _SearchValidationFails(Tool):
    name = "web_search"
    description = "search whose output fails validation"
    risk = "read_only"

    def run(self, query: str, max_results: int = 3):
        return "not a list"

    def validate_output(self, output):
        return (False, ["output is not a list"])


class _SearchRaises(Tool):
    name = "web_search"
    description = "search that raises"
    risk = "read_only"

    def run(self, query: str, max_results: int = 3):
        raise RuntimeError("network down")

    def validate_output(self, output):
        return (True, [])


class _SearchWithWarning(Tool):
    name = "web_search"
    description = "search that returns a non-fatal warning"
    risk = "read_only"

    def run(self, query: str, max_results: int = 3):
        return [{
            "title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Agent",
            "snippet": "ok", "source": "fake",
        }]

    def validate_output(self, output):
        return (True, ["thin result set"])


class _OkFetch(Tool):
    name = "web_fetch"
    description = "good fetch"
    risk = "read_only"

    def run(self, url: str):
        text = "Autonomous agent acts independently. Autonomous agent pursues goals."
        return {
            "url": url, "status_code": 200, "content_type": "text/html",
            "fetched_at": "2026-05-29T18:00:00+00:00", "content_hash": "c" * 64,
            "text": text, "text_truncated": False, "bytes": len(text),
            "elapsed_ms": 1,
            "compensation_plan": {"id": "noop", "actions": [], "tool_name": "web_fetch"},
        }

    def validate_output(self, output):
        return (isinstance(output, dict) and bool(output.get("text")), [])


class _FetchValidationFails(Tool):
    name = "web_fetch"
    description = "fetch whose output fails validation"
    risk = "read_only"

    def run(self, url: str):
        return {"url": url, "text": ""}

    def validate_output(self, output):
        return (False, ["no text"])


class _FetchRaises(Tool):
    name = "web_fetch"
    description = "fetch that raises"
    risk = "read_only"

    def run(self, url: str):
        raise RuntimeError("fetch timeout")

    def validate_output(self, output):
        return (True, [])


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


class TestWebFailureBranches:
    def test_search_validation_failure_is_recorded_and_no_fetch(self, workspace: Path):
        agent = _agent(workspace, _registry(_SearchValidationFails(), _OkFetch()))
        report = ingest_web_topic(
            agent=agent, topic="autonomous agent", source_selection="wikipedia", limit=2,
        )
        assert report.pages_fetched == 0
        assert any("search failed" in e for e in report.errors)

    def test_search_exception_is_recorded(self, workspace: Path):
        agent = _agent(workspace, _registry(_SearchRaises(), _OkFetch()))
        report = ingest_web_topic(
            agent=agent, topic="autonomous agent", source_selection="wikipedia", limit=2,
        )
        assert report.pages_fetched == 0
        assert any("search failed" in e and "network down" in e for e in report.errors)

    def test_search_warning_is_surfaced(self, workspace: Path):
        agent = _agent(workspace, _registry(_SearchWithWarning(), _OkFetch()))
        report = ingest_web_topic(
            agent=agent, topic="autonomous agent", source_selection="wikipedia", limit=1,
        )
        assert any("search warning" in e for e in report.errors)
        assert report.pages_fetched == 1

    def test_fetch_validation_failure_skips_url(self, workspace: Path):
        agent = _agent(workspace, _registry(_SearchWithWarning(), _FetchValidationFails()))
        report = ingest_web_topic(
            agent=agent, topic="autonomous agent", source_selection="wikipedia", limit=1,
        )
        assert report.pages_fetched == 0
        assert any("fetch failed" in e for e in report.errors)
        assert "https://en.wikipedia.org/wiki/Agent" in report.skipped_urls

    def test_fetch_exception_skips_url(self, workspace: Path):
        agent = _agent(workspace, _registry(_SearchWithWarning(), _FetchRaises()))
        report = ingest_web_topic(
            agent=agent, topic="autonomous agent", source_selection="wikipedia", limit=1,
        )
        assert report.pages_fetched == 0
        assert any("fetch failed" in e and "fetch timeout" in e for e in report.errors)
        assert "https://en.wikipedia.org/wiki/Agent" in report.skipped_urls

    def test_logger_receives_ingest_events(self, workspace: Path):
        logger = _RecordingLogger()
        agent = _agent(
            workspace, _registry(_SearchWithWarning(), _OkFetch()), logger=logger,
        )
        ingest_web_topic(
            agent=agent, topic="autonomous agent", source_selection="wikipedia", limit=1,
        )
        names = {e for e, _ in logger.events}
        assert {"ingest", "evidence_collected", "source_ranking",
                "source_registry", "knowledge_pipeline"} <= names


# ── rss failure branches ─────────────────────────────────────────────────────

class _RssValidationFails(Tool):
    name = "rss_fetch"
    description = "rss output fails validation"
    risk = "read_only"

    def run(self, url: str, max_entries: int = 10):
        return {"url": url, "entries": "not-a-list"}

    def validate_output(self, output):
        return (False, ["entries is not a list"])


class _RssWithWarningAndJunkEntries(Tool):
    name = "rss_fetch"
    description = "rss with a warning + a non-dict entry mixed in"
    risk = "read_only"

    def run(self, url: str, max_entries: int = 10):
        return {
            "url": url, "status_code": 200, "content_type": "application/rss+xml",
            "fetched_at": "2026-05-29T18:00:00+00:00", "content_hash": "d" * 64,
            "title": "Feed", "feed_type": "rss",
            "entries": [
                "junk-non-dict-entry",
                {
                    "title": "Release notes", "url": "https://example.com/release",
                    "summary": "A tested change.", "published_at": "Fri, 29 May 2026 10:00:00 GMT",
                    "id": "release",
                },
            ][:max_entries],
            "text_truncated": False, "bytes": 100, "elapsed_ms": 1,
            "compensation_plan": {"id": "noop", "actions": [], "tool_name": "rss_fetch"},
        }

    def validate_output(self, output):
        return (True, ["feed had encoding quirks"])


class TestRssBranches:
    def test_empty_url_raises(self, workspace: Path):
        agent = _agent(workspace, ToolRegistry())
        with pytest.raises(ValueError, match="url is required"):
            ingest_rss_feed(agent=agent, url="  ")

    def test_limit_below_one_raises(self, workspace: Path):
        agent = _agent(workspace, ToolRegistry())
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ingest_rss_feed(agent=agent, url="https://x/feed.xml", limit=0)

    def test_missing_registry_raises(self, workspace: Path):
        agent = SimpleNamespace(
            registry=None,
            knowledge_pipeline=KnowledgePipeline(),
            source_registry_store=SourceRegistryStore(workspace / "data" / "s.jsonl"),
            knowledge_auto_write=False,
            log=None,
        )
        with pytest.raises(ValueError, match="no tool registry"):
            ingest_rss_feed(agent=agent, url="https://x/feed.xml")

    def test_validation_failure_raises(self, workspace: Path):
        agent = _agent(workspace, _registry(_RssValidationFails()))
        with pytest.raises(ValueError, match="entries is not a list"):
            ingest_rss_feed(agent=agent, url="https://x/feed.xml")

    def test_warning_surfaced_and_non_dict_entry_skipped(self, workspace: Path):
        logger = _RecordingLogger()
        agent = _agent(workspace, _registry(_RssWithWarningAndJunkEntries()), logger=logger)
        report = ingest_rss_feed(agent=agent, url="https://example.com/feed.xml", limit=10)
        # The junk string entry is skipped; only the real dict entry ingested.
        assert report.entries_seen == 2
        assert report.entries_ingested == 1
        assert report.entry_urls == ["https://example.com/release"]
        assert any("rss warning" in e for e in report.errors)
        assert {"ingest", "evidence_collected"} <= {e for e, _ in logger.events}
