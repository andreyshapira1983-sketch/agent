"""A subagent's evidence crosses the boundary to the parent (work order 1, 2026-09-05).

Pass 2 of the first work order: the parent spawned three FlightResearcher
subagents, they fetched real pages (HTTP 429, an «unsupported» page, dynamic
shells), and the parent's chain held only their prose plus a count
(`external_evidence_count=3`). The verifier booked every claim about them
«subagent_asserted», the low-evidence gate erased the honest negative
report. Delegation existed; trusted delivery of proof across the boundary
did not.

Pinned here: the runner's result carries the child's external evidences
whole (kind, source_id, excerpt, origin `subagent:<name>:<trace>`,
obtained_via `subagent:<name>` — no parent receipt is owed for a carried
observation); the spawn tool stashes them; the run folds them into the
parent's chain after the attempt loop, once; and a parent claim citing the page the child read
is VERIFIED, not subagent_asserted.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.evidence import Evidence, make_evidence
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry
from tools.file_read import FileReadTool

PAGE_URL = "https://www.expedia.com/Flights-Search?trip=roundtrip&leg1=from%3ATLV"
PAGE_TEXT = "HTTP 429 Too Many Requests: access to Expedia flight search is rate-limited for this client."


class _FakeSpawn(Tool):
    """Stands in for tools/spawn_subagent.py: same name, same contract —
    returns the child's prose and stashes the child's external evidences."""

    name = "spawn_subagent"
    description = "fake"
    arguments = "role, objective"
    risk = "read_only"

    def run(self, **kwargs):
        child = make_evidence(
            kind="web_page", source_id=f"web_page:{PAGE_URL}", obtained_via="web_fetch",
            claim="fetched page", excerpt=PAGE_TEXT, confidence=0.75,
        ).to_dict()
        child["origin"] = "subagent:ExpediaCheck:trace_child"
        child["obtained_via"] = "subagent:ExpediaCheck"
        stash = list(getattr(self, "last_child_evidences", None) or [])
        stash.append(child)
        self.last_child_evidences = stash
        return (
            "[subagent-meta external_evidence_count=1 external_kinds=web_page]\n"
            "SubAgent name='ExpediaCheck'\n  answer: Expedia blocked the search with HTTP 429."
        )


PLAN = json.dumps({"reasoning": "delegate", "steps": [
    {"tool": "spawn_subagent", "arguments": {"role": "FlightResearcher", "objective": "check Expedia"}, "rationale": "r"}
]})
SYNTH = (
    f"Conclusion: Expedia blocked the search with HTTP 429 Too Many Requests. [web:{PAGE_URL}]\n"
    f"Facts:\n- access to Expedia flight search is rate-limited for this client [web:{PAGE_URL}]\n"
    f"Sources:\n1. web:{PAGE_URL} - expedia\nConfidence: medium\nUnverified: nothing\n"
)


def _events(log_dir: Path) -> list[dict]:
    out = []
    for p in log_dir.glob("trace_*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def test_the_child_page_is_in_the_parent_chain_and_a_claim_on_it_is_verified(tmp_path: Path):
    llm = FakeLLM(responses=[PLAN, SYNTH])
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    spawn = _FakeSpawn()
    registry.register(spawn)
    logger = TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False)
    agent = AgentLoop(registry=registry, policy=PolicyGate(registry), llm=llm, logger=logger,
                      planner=LLMPlanner(llm=llm, registry=registry), memory=WorkingMemory())

    agent.run(user_question="Проверь Expedia на TLV-BER и скажи, что вернул сайт.")

    kinds = {(e.kind, e.source_id) for e in agent.last_provenance.evidences}
    assert ("web_page", f"web_page:{PAGE_URL}") in kinds, kinds
    carried = next(e for e in agent.last_provenance.evidences if e.kind == "web_page")
    assert carried.origin.startswith("subagent:ExpediaCheck")
    assert carried.obtained_via == "subagent:ExpediaCheck"
    assert spawn.last_child_evidences == [], "the stash is cleared after the fold"
    events = {e["event"]: e for e in _events(tmp_path / "logs") if "event" in e}
    assert events["subagent_evidence_carried"]["payload"]["count"] == 1
    verification = events["verification"]["payload"]
    assert verification["verified_chunks"] >= 1, verification
    assert verification["subagent_asserted_chunks"] == 0, verification


def test_the_runner_result_carries_evidences_and_defaults_to_none():
    from core.subagent_runner import SubAgentRunResult

    fields = SubAgentRunResult.__dataclass_fields__
    assert "external_evidences" in fields
    r = SubAgentRunResult(contract_name="x", role="r", objective="o", answer="a", trace_id="t", status="success")
    assert r.external_evidences == ()
    ev = Evidence.from_dict({**make_evidence(kind="web_page", source_id="web_page:u", obtained_via="web_fetch",
                                              claim="c", excerpt="e", confidence=0.7).to_dict(), "origin": "subagent:x:t"})
    assert ev.origin == "subagent:x:t"
