"""Integration tests for Ranker-to-Output policy inside AgentLoop."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry


class StaticWebFetchTool(Tool):
    name = "web_fetch"
    description = "test-only static web fetch"
    risk = "read_only"

    def __init__(self, text: str) -> None:
        self.text = text

    def run(self, **kwargs: Any) -> dict[str, Any]:
        url = str(kwargs["url"])
        raw = self.text.encode("utf-8")
        return {
            "url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "text": self.text,
            "text_truncated": False,
            "bytes": len(raw),
        }


def _events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_loop_downgrades_realtime_answer_when_ranker_marks_source_insufficient(
    workspace: Path,
) -> None:
    url = "https://coinmarketcap.com/currencies/bitcoin"
    planner_response = json.dumps(
        {
            "reasoning": "Need to fetch the cited market page.",
            "steps": [
                {
                    "tool": "web_fetch",
                    "arguments": {"url": url},
                    "rationale": "fetch page",
                }
            ],
        }
    )
    draft_answer = (
        f"Conclusion: Bitcoin costs 123 USD. [web:{url}]\n"
        "Facts:\n"
        f"- The page says Bitcoin costs 123 USD. [web:{url}]\n"
        "Sources:\n"
        f"1. web:{url}\n"
        "Confidence: high\n"
        "Unverified: nothing\n"
        "Safety: nothing\n"
    )
    llm = FakeLLM(responses=[planner_response, draft_answer])
    registry = ToolRegistry()
    registry.register(StaticWebFetchTool("Bitcoin price is 123 USD"))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
    )

    answer = agent.run(user_question="What is the Bitcoin price right now?")

    assert "Confidence: low" in answer
    assert "[unverified:insufficient_for_realtime]" in answer
    assert "insufficient for confirming a realtime value" in answer

    events = _events(workspace / "logs" / f"{trace_id}.jsonl")
    output_policy = [e for e in events if e["event"] == "output_policy"]
    assert output_policy
    assert output_policy[-1]["payload"]["confidence_ceiling"] == "low"
    respond = [e for e in events if e["event"] == "respond"][-1]
    assert respond["payload"]["replan_exhausted"] is False
