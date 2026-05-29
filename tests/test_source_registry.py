"""Source Registry tests.

This pins the catalog layer above Evidence: source records and extracted
claims are separate objects, so books/PDFs/docs/logs/tests/user notes can all
feed the same knowledge pipeline later.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.evidence import ProvenanceChain, make_evidence
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.source_ranker import rank_chain
from core.source_registry import SourceRegistry, source_type_from_evidence
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _ev(kind: str, source: str, *, claim: str = "claim"):
    return make_evidence(
        kind=kind,  # type: ignore[arg-type]
        source_id=source,
        obtained_via="test",
        claim=claim,
        excerpt="excerpt",
        confidence=0.8,
    )


def test_manual_book_source_can_store_page_level_claim():
    registry = SourceRegistry()
    source = registry.register_source(
        type="book",
        title="Artificial Intelligence: A Modern Approach",
        locator="chapter=2 page=34",
        author="Russell and Norvig",
        trust_level=0.86,
    )

    claim = registry.register_claim(
        source_id=source.id,
        text="An agent perceives through sensors and acts through actuators.",
        locator="chapter=2 page=34",
        confidence=0.82,
        status="verified",
    )

    assert source.type == "book"
    assert claim.source_id == source.id
    assert registry.claims_for_source(source.id) == (claim,)
    payload = registry.to_log_payload()
    assert payload["source_count"] == 1
    assert payload["claim_count"] == 1
    assert payload["source_types"]["book"] == 1
    assert payload["claim_statuses"]["verified"] == 1


def test_source_type_from_evidence_maps_core_evidence_kinds():
    assert source_type_from_evidence(_ev("file", "file:README.md")) == "file"
    assert source_type_from_evidence(_ev("test_result", "test:pytest tests")) == "test_result"
    assert source_type_from_evidence(_ev("log_event", "log:trace")) == "log"
    assert source_type_from_evidence(_ev("memory", "memory:mem_123")) == "memory"
    assert source_type_from_evidence(_ev("user_explicit", "user:req_123")) == "user"
    docs = _ev("web_page", "web_page:https://docs.python.org/3/library/json.html")
    assert source_type_from_evidence(docs) == "documentation"
    forum = _ev("web_page", "web_page:https://reddit.com/r/python/comments/1")
    assert source_type_from_evidence(forum) == "forum"
    rss = make_evidence(
        kind="web_page",
        source_id="web_page:https://example.com/feed.xml",
        obtained_via="rss_fetch",
        claim="RSS feed",
        excerpt="entry",
        confidence=0.68,
    )
    assert source_type_from_evidence(rss) == "article"


def test_registry_from_provenance_uses_ranking_metadata():
    chain = ProvenanceChain()
    chain.add(_ev("web_search_hit", "web_search:bitcoin price", claim="search pointer"))
    chain.add(_ev("file", "file:README.md", claim="README says x"))
    ranking = rank_chain(chain, question="Какая цена Bitcoin прямо сейчас?")

    registry = SourceRegistry.from_provenance(chain, ranking=ranking)

    assert len(registry.sources) == 2
    assert len(registry.claims) == 2
    search_source = registry.get_source("web_search:bitcoin price")
    assert search_source is not None
    assert search_source.metadata["rank_tier"] == "search_pointer"
    assert search_source.trust_level <= 0.35
    search_claim = registry.claims_for_source("web_search:bitcoin price")[0]
    assert search_claim.status == "unverified"
    file_source = registry.get_source("file:README.md")
    assert file_source is not None
    assert file_source.type == "file"


def test_agent_loop_logs_source_registry_for_evidence(tmp_path: Path):
    (tmp_path / "doc.txt").write_text(
        "Agent stores source registry claims.",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["[synthesised]"]),
        logger=logger,
        planner=FakePlanner([{
            "tool": "file_read",
            "arguments": {"path": "doc.txt"},
            "label": "file:doc.txt",
            "expected_outcome": "read doc",
        }]),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )

    agent.run("read the doc", file_hint="doc.txt")

    assert agent.last_source_registry.get_source("file:doc.txt") is not None
    events = _events(tmp_path / "logs" / f"{trace_id}.jsonl")
    source_events = [e for e in events if e["event"] == "source_registry"]
    assert source_events
    payload = source_events[-1]["payload"]
    assert payload["source_count"] == 1
    assert payload["claim_count"] == 1
    assert payload["source_types"]["file"] == 1
