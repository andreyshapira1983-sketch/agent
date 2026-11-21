"""Direct tests for online ingestion helpers.

The CLI already exercises the commands. These tests pin the lower-level helper
contracts without touching the network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.ingestion import ingest_web_topic
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


def _agent(workspace: Path, registry: ToolRegistry):
    return SimpleNamespace(
        registry=registry,
        knowledge_pipeline=KnowledgePipeline(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        knowledge_auto_write=False,
        log=None,
    )


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
