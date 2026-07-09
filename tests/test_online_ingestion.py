"""Direct tests for online ingestion helpers.

The CLI already exercises the commands. These tests pin the lower-level helper
contracts without touching the network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.ingestion import ingest_rss_feed, ingest_web_topic
from core.knowledge_pipeline import KnowledgePipeline
from core.source_registry_store import SourceRegistryStore
from tools.base import Tool, ToolRegistry


class RecordingWebSearch(Tool):
    name = "web_search"
    description = "recording web search"
    risk = "read_only"

    def __init__(self):
        self.queries: list[str] = []

    def run(self, query: str, max_results: int = 3):
        self.queries.append(query)
        return [
            {
                "title": "Wikipedia article",
                "url": "https://en.wikipedia.org/wiki/Autonomous_agent",
                "snippet": "Autonomous agent overview.",
                "source": "fake",
            },
            {
                "title": "Wrong domain",
                "url": "https://example.com/wiki/Autonomous_agent",
                "snippet": "Should not be fetched.",
                "source": "fake",
            },
        ]

    def validate_output(self, output):
        return (isinstance(output, list), [])


class RecordingWebFetch(Tool):
    name = "web_fetch"
    description = "recording web fetch"
    risk = "read_only"

    def __init__(self):
        self.urls: list[str] = []

    def run(self, url: str):
        self.urls.append(url)
        text = (
            "Autonomous agent is a system situated in an environment. "
            "Autonomous agent acts independently to pursue goals."
        )
        return {
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "fetched_at": "2026-05-29T18:00:00+00:00",
            "content_hash": "c" * 64,
            "text": text,
            "text_truncated": False,
            "bytes": len(text),
            "elapsed_ms": 1,
            "compensation_plan": {"id": "noop", "actions": [], "tool_name": "web_fetch"},
        }

    def validate_output(self, output):
        return (isinstance(output, dict) and bool(output.get("text")), [])


class FakeRss(Tool):
    name = "rss_fetch"
    description = "fake rss"
    risk = "read_only"

    def run(self, url: str, max_entries: int = 10):
        return {
            "url": url,
            "status_code": 200,
            "content_type": "application/rss+xml",
            "fetched_at": "2026-05-29T18:00:00+00:00",
            "content_hash": "d" * 64,
            "title": "Feed",
            "feed_type": "rss",
            "entries": [
                {
                    "title": "Release notes",
                    "url": "https://example.com/release",
                    "summary": "Release notes describe a tested change.",
                    "published_at": "Fri, 29 May 2026 10:00:00 GMT",
                    "id": "release",
                }
            ][:max_entries],
            "text_truncated": False,
            "bytes": 100,
            "elapsed_ms": 1,
            "compensation_plan": {"id": "noop", "actions": [], "tool_name": "rss_fetch"},
        }

    def validate_output(self, output):
        return (isinstance(output, dict) and isinstance(output.get("entries"), list), [])


def _agent(workspace: Path, registry: ToolRegistry):
    return SimpleNamespace(
        registry=registry,
        knowledge_pipeline=KnowledgePipeline(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        knowledge_auto_write=False,
        log=None,
    )


def test_ingest_web_topic_filters_domains_and_persists_registry(workspace: Path):
    registry = ToolRegistry()
    search = RecordingWebSearch()
    fetch = RecordingWebFetch()
    registry.register(search)
    registry.register(fetch)
    agent = _agent(workspace, registry)

    report = ingest_web_topic(
        agent=agent,
        topic="autonomous agent",
        source_selection="wikipedia",
        limit=2,
    )

    assert search.queries == ["site:wikipedia.org autonomous agent"]
    assert fetch.urls == ["https://en.wikipedia.org/wiki/Autonomous_agent"]
    assert report.pages_fetched == 1
    assert report.source_store["sources_saved"] >= 1
    assert agent.source_registry_store.count()["claims"] >= 1
    assert agent.last_provenance.by_kind("web_page")


def test_ingest_web_topic_dry_run_does_not_persist(workspace: Path):
    registry = ToolRegistry()
    registry.register(RecordingWebSearch())
    registry.register(RecordingWebFetch())
    agent = _agent(workspace, registry)

    report = ingest_web_topic(
        agent=agent,
        topic="autonomous agent",
        source_selection="wikipedia",
        limit=1,
        dry_run=True,
    )

    assert report.pages_fetched == 1
    assert report.source_store == {}
    assert agent.source_registry_store.count() == {"sources": 0, "claims": 0}


def test_ingest_rss_feed_persists_entries_as_sources(workspace: Path):
    registry = ToolRegistry()
    registry.register(FakeRss())
    agent = _agent(workspace, registry)

    report = ingest_rss_feed(
        agent=agent,
        url="https://example.com/feed.xml",
        limit=1,
    )

    assert report.entries_ingested == 1
    assert report.entry_urls == ["https://example.com/release"]
    assert report.source_store["sources_saved"] == 1
    assert agent.source_registry_store.count()["claims"] >= 1
