"""MVP-14.5 — integration tests for the unresolved-citation replan loop.

When the Verifier produces a draft answer with `[web:URL]` citations
that have NO matching `web_page` evidence in the chain, the loop must:

  1. raise a `FailureType.unresolved_citation` trigger;
  2. consult `ReplanPolicy.decide()` like any other failure;
  3. on `continue`, build a planner advice block that lists the
     unresolved URLs;
  4. re-run the planner, which is expected to add `web_fetch(url=...)`
     steps for the cited URLs;
  5. execute those steps, enrich `chain` with `web_page` evidence;
  6. re-verify the ORIGINAL draft (no second synthesis) — the new
     chain entries should resolve the previously-broken citations.

These tests use a custom planner that returns DIFFERENT plans on each
call (first call: web_search only; second call: web_fetch for the
unresolved URL) and a stubbed `urlopen` for `web_fetch`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.planner import PlannerOutput
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class ScriptedPlanner:
    """Planner that returns plans from a script, one per call.

    The Nth call returns `scripts[N-1]`. When the script is exhausted
    every subsequent call returns an empty plan (the loop interprets
    that as 'I cannot help further').
    """

    def __init__(
        self,
        scripts: list[list[dict[str, Any]]],
        reasoning: str = "scripted",
    ) -> None:
        self.scripts = list(scripts)
        self.reasoning = reasoning
        self.calls: list[dict[str, Any]] = []

    def plan(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> PlannerOutput:
        self.calls.append(
            {
                "question": question,
                "history": history,
                "failure_context": failure_context,
                "forbidden_actions": forbidden_actions,
                "call_index": len(self.calls),
            }
        )
        idx = len(self.calls) - 1
        sources = self.scripts[idx] if idx < len(self.scripts) else []
        for src in sources:
            src.setdefault("expected_outcome", "executes the planned step")
        return PlannerOutput(
            reasoning=self.reasoning,
            sources=sources,
            raw_response="",
            warnings=[],
        )


class _StubHeaders:
    """Case-insensitive headers stand-in (mimics http.client.HTTPMessage)."""

    def __init__(self, items: dict[str, str]) -> None:
        self._items = dict(items)

    def get(self, name: str, default: str | None = None) -> str | None:
        for k, v in self._items.items():
            if k.lower() == name.lower():
                return v
        return default


class _StubResponse:
    """Minimal urlopen response stand-in. WebFetchTool accesses
    `resp.status`, `resp.headers.get(...)`, and `resp.read(n)`."""

    def __init__(
        self,
        body: bytes,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = _StubHeaders(
            headers or {"Content-Type": "text/html; charset=utf-8"}
        )
        self._url = url or "https://stub.example/page"

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class _StubOpener:
    """Implements the opener.open(req, timeout=...) protocol expected
    by WebFetchTool. Maps URL -> bytes; raises URLError otherwise."""

    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = dict(routes)
        self.calls: list[str] = []

    def open(self, req: Any, timeout: float = 0) -> _StubResponse:
        url = getattr(req, "full_url", str(req))
        self.calls.append(url)
        if url not in self.routes:
            raise URLError(f"no stub for {url}")
        return _StubResponse(self.routes[url], url=url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _events(p: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_agent(
    workspace: Path,
    *,
    planner: ScriptedPlanner,
    llm_responses: list[str],
    url_routes: dict[str, bytes] | None = None,
    max_replan_attempts: int = 3,
    verifier_enabled: bool = True,
) -> tuple[AgentLoop, Path, _StubOpener]:
    """Wire registry + agent. web_fetch is given a stub opener.

    `max_replan_attempts` is passed both to the agent constructor and
    reflected by the ReplanPolicy default global cap (3) — the
    verify-driven loop adds extra attempts beyond the tool loop, so
    we keep the budget generous in tests.
    """
    reg = ToolRegistry()
    reg.register(WebSearchTool())

    opener = _StubOpener(url_routes or {})
    reg.register(WebFetchTool(opener=opener))

    llm = FakeLLM(responses=llm_responses)
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=max_replan_attempts,
        verifier_enabled=verifier_enabled,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl", opener


# ---------------------------------------------------------------------------
# WebFetchTool injection point — confirm the patch works
# ---------------------------------------------------------------------------

# We patch `WebFetchTool._opener` rather than `urllib.request.urlopen`
# globally so each test owns its stub. The tool reads `self._opener` if
# present, else falls back to `urllib.request.urlopen`.
# (The bridge is implemented in WebFetchTool — see tools/web_fetch.py.)


# ===========================================================================
# Happy path: web_fetch is added by the planner after unresolved-citation
# ===========================================================================

class TestUnresolvedCitationHappyPath:
    def test_unresolved_url_triggers_replan_and_resolves(
        self, workspace: Path
    ):
        """1st planner call -> web_search only.
        LLM draft cites [web:URL] but URL was never fetched.
        Verifier marks unresolved, loop replans, planner adds web_fetch,
        web_fetch returns the page, Verifier resolves the citation."""
        url = "https://example.com/agent"
        page_html = b"<html><body>An agent perceives and acts.</body></html>"

        planner = ScriptedPlanner(scripts=[
            # 1st call: search only — LLM will cite the URL but not fetch.
            [{
                "tool": "web_search",
                "arguments": {"query": "AI agent definition"},
                "label": "search:ai_agent",
            }],
            # 2nd call (after unresolved_citation trigger): fetch the URL.
            [{
                "tool": "web_fetch",
                "arguments": {"url": url},
                "label": "web:agent_page",
            }],
        ])

        llm_answer = (
            "Conclusion: An agent perceives and acts [web:" + url + "].\n"
            "Facts: definition cited [web:" + url + "].\n"
            "Sources: " + url
        )
        agent, log_path, opener = _build_agent(
            workspace,
            planner=planner,
            llm_responses=[llm_answer],
            url_routes={url: page_html},
        )
        answer = agent.run("define agent")

        # After replan + re-verify the [web:URL] becomes [verified:web:URL].
        assert f"[verified:web:{url}]" in answer
        # And the bare [web:URL] is fully rewritten — no orphan citation.
        assert f"[web:{url}]" not in answer

        # The Verifier ran TWICE: once before replan, once after.
        events = _events(log_path)
        verifications = [e for e in events if e["event"] == "verification"]
        assert len(verifications) == 2
        first, second = verifications
        assert first["payload"]["cited_but_unmatched_chunks"] >= 1
        assert first["payload"]["verified_chunks"] == 0
        assert second["payload"]["cited_but_unmatched_chunks"] == 0
        assert second["payload"]["verified_chunks"] >= 1
        assert second["payload"]["phase"] == "verify"

        # Replan event for the verify phase exists.
        replans = [
            e for e in events
            if e["event"] == "replan"
            and e["payload"].get("phase") == "verify"
        ]
        assert len(replans) == 1
        assert replans[0]["payload"]["triggers"] == ["unresolved_citation"]
        assert url in replans[0]["payload"]["unresolved_urls"]

        # Chain ended up with a web_page evidence entry. source_id has
        # the canonical `web_page:<url>` shape (substring-friendly for
        # match_citation), so we check membership rather than equality.
        chain = agent.last_provenance
        assert any(ev.kind == "web_page" for ev in chain.evidences)
        assert any(url in ev.source_id for ev in chain.evidences)

        # Planner was called TWICE.
        assert len(planner.calls) == 2
        # The 2nd planner call carries the URL in its failure_context.
        assert url in planner.calls[1]["failure_context"]
        assert "unresolved_citation" in planner.calls[1]["failure_context"]


# ===========================================================================
# Hard cap: planner can't help -> loop exits, doesn't infinite-loop
# ===========================================================================

class TestUnresolvedCitationHardCap:
    def test_planner_refuses_to_fetch_loop_exits_cleanly(
        self, workspace: Path
    ):
        """If the planner keeps returning empty plans the loop must
        exit after at most VERIFY_REPLAN_HARD_CAP+1 attempts (2 cap),
        and the unresolved citation must STAY unresolved (no false
        verified marker)."""
        url = "https://example.com/never-fetched"

        # Plan 1 = search; Plans 2, 3, ... = empty (planner gives up).
        planner = ScriptedPlanner(scripts=[
            [{
                "tool": "web_search",
                "arguments": {"query": "foo"},
                "label": "search:foo",
            }],
            [],  # 2nd call: planner returns nothing
            [],  # 3rd call: still nothing
        ])

        llm_answer = f"Conclusion: cited [web:{url}]. Sources: {url}"
        agent, log_path, _ = _build_agent(
            workspace,
            planner=planner,
            llm_responses=[llm_answer],
            url_routes={},  # nothing routed — any fetch would fail
        )
        answer = agent.run("q")

        # The citation MUST stay unresolved — no false verified marker.
        assert f"[verified:web:{url}]" not in answer

        # Loop did NOT infinite-loop: planner called at most 3 times
        # (1 initial + 2 verify-driven replans, bounded by hard cap).
        assert len(planner.calls) <= 3

        # A verify_replan_noop event was logged at least once.
        events = _events(log_path)
        noops = [e for e in events if e["event"] == "verify_replan_noop"]
        assert noops  # at least one — empty plan produced no evidence


# ===========================================================================
# Negative: no web citations at all -> loop NEVER triggers
# ===========================================================================

class TestNoWebCitationsNoReplan:
    def test_file_only_citations_skip_unresolved_loop(self, workspace: Path):
        """A draft with only [file:...] (or no) citations must not
        trigger the unresolved_citation loop — that loop is exclusively
        for [web:URL] mismatches."""
        (workspace / "doc.txt").write_text("hello", encoding="utf-8")
        from tools.file_read import FileReadTool
        reg = ToolRegistry()
        reg.register(FileReadTool(workspace_root=workspace))

        planner = ScriptedPlanner(scripts=[
            [{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        ])

        llm = FakeLLM(responses=["fact [file:doc.txt]. claim with no cite."])
        trace_id = new_trace_id()
        logger = TraceLogger(
            trace_id=trace_id, log_dir=workspace / "logs", verbose=False
        )
        agent = AgentLoop(
            registry=reg,
            policy=PolicyGate(reg),
            llm=llm,
            logger=logger,
            planner=planner,
            approval_provider=AutoApprover(default="approve"),
        )
        answer = agent.run("q", file_hint="doc.txt")

        # The [file:doc.txt] resolves normally and the loop runs once.
        assert "[verified:file:doc.txt]" in answer
        assert len(planner.calls) == 1  # exactly one planner call

        # No verify-phase events at all.
        events = _events(workspace / "logs" / f"{trace_id}.jsonl")
        verify_phase = [
            e for e in events
            if e.get("payload", {}).get("phase") == "verify"
        ]
        assert verify_phase == []


# ===========================================================================
# Negative: verifier_enabled=False -> no replan at all
# ===========================================================================

class TestVerifierDisabledNoReplan:
    def test_verifier_off_skips_loop_entirely(self, workspace: Path):
        """With the Verifier off the loop has no way to know a citation
        is unresolved — the draft must come out untouched and the
        unresolved_citation machinery must not fire."""
        url = "https://example.com/x"
        planner = ScriptedPlanner(scripts=[
            [{
                "tool": "web_search",
                "arguments": {"query": "q"},
                "label": "search:q",
            }],
        ])
        agent, log_path, _ = _build_agent(
            workspace,
            planner=planner,
            llm_responses=[f"cited [web:{url}]."],
            url_routes={},
            verifier_enabled=False,
        )
        answer = agent.run("q")

        # Draft unchanged: no verified marker, no unverified tag.
        assert f"[web:{url}]" in answer
        assert "[verified:" not in answer
        assert "[unverified]" not in answer

        # Planner called exactly once — no verify replan.
        assert len(planner.calls) == 1
        events = _events(log_path)
        assert not [e for e in events if e["event"] == "verification"]


# ===========================================================================
# Helper coverage: extract_unresolved_web_urls is the gate
# ===========================================================================

class TestVerifyAdviceContent:
    def test_advice_lists_each_unresolved_url(self, workspace: Path):
        """Two unresolved URLs -> advice block in the 2nd planner call
        contains BOTH URLs in the order the LLM cited them."""
        url_a = "https://example.com/a"
        url_b = "https://example.com/b"

        planner = ScriptedPlanner(scripts=[
            [{
                "tool": "web_search",
                "arguments": {"query": "thing"},
                "label": "search:thing",
            }],
            [
                {
                    "tool": "web_fetch",
                    "arguments": {"url": url_a},
                    "label": "web:a",
                },
                {
                    "tool": "web_fetch",
                    "arguments": {"url": url_b},
                    "label": "web:b",
                },
            ],
        ])

        llm_answer = (
            f"Conclusion: claim A [web:{url_a}].\n"
            f"Facts: claim B [web:{url_b}].\n"
            f"Sources: {url_a}, {url_b}"
        )
        agent, _, _ = _build_agent(
            workspace,
            planner=planner,
            llm_responses=[llm_answer],
            url_routes={
                url_a: b"<html>page A body</html>",
                url_b: b"<html>page B body</html>",
            },
        )
        agent.run("q")

        # Both URLs resolved.
        assert len(planner.calls) == 2
        ctx = planner.calls[1]["failure_context"]
        assert url_a in ctx
        assert url_b in ctx
        # And the URL ORDER is preserved (a before b).
        assert ctx.index(url_a) < ctx.index(url_b)
