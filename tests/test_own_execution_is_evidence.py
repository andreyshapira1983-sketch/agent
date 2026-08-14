"""The process running the agent is a source about the agent.

Measured 2026-08-14. Asked «Ты видишь Python?» the agent planned
`shell_exec where python`, searched the disk, found the interpreter — while
executing on it — and honestly added that it had not verified the interpreter
works. Its own existence is that proof and it had no way to offer it: evidence
in this system is what a tool call returned, and the process never called a
tool to exist.

Consequence beyond the joke: every claim the agent made about its own body was
`llm_claim`, so it could not be verified, so the honest answer was always
"cannot be determined" — the same wall as the deleted README and the trimmed
memory layer, one floor down.

Two halves, deliberately separate:

* the `<runtime_self>` BLOCK is what the synthesiser reads;
* the citation POOL is what `[runtime:…]` resolves against.

The pool is not folded into the provenance chain. The first attempt did fold
it and 21 tests said why that is wrong — the chain is counted and ordered, and
its contracts are real: a failed step yields no evidence, a `file_write` yields
none, a zero-step plan yields an EMPTY chain, and `chain_was_empty` separates
"looked and found nothing" from "did not look". Five always-present entries
break all of them.

`kind="runtime"` sits in the `trace` class, never `external_world`: it says
what the agent IS running on and can never confirm a claim about the world.
"""
from __future__ import annotations

import sys

from core.evidence import (
    ALL_EVIDENCE_KINDS,
    DEFAULT_CONFIDENCE,
    ProvenanceChain,
    make_evidence,
)
from core.evidence_classes import classify_evidence
from core.runtime_self import process_facts, runtime_self_block
from core.verifier_patterns import CITATION_PREFIXES
from core.verifier_utils import match_citation, parse_citations

_ANSWER = (
    "Analysis: p.\n"
    "Findings: p [file:present.txt]\n"
    "Conclusion: p\n"
    "Sources:\n1. file:present.txt - present.txt\n"
    "Confidence: medium\nUnverified: nothing\n"
)


def test_the_process_can_name_what_it_runs_on():
    """Measured from the interpreter, not inferred from a prompt."""
    facts = process_facts()
    assert facts["interpreter"] == sys.executable
    assert facts["python_version"].count(".") >= 1
    assert facts["pid"].isdigit()
    # cwd and platform are present rather than pinned: their VALUES are
    # environment, their ABSENCE is the defect this closes.
    assert facts["cwd"] and facts["platform"]


def test_the_facts_reach_the_block_the_synthesiser_reads():
    block = runtime_self_block(
        trace_id="t1", run_id="r1", session_id="s1",
        stores={"persistent_store": object()}, durable_writes=("episode",),
    )
    assert "interpreter:" in block
    assert "python_version:" in block
    assert sys.executable in block


def test_a_runtime_claim_can_be_cited_and_resolves():
    """Resolves against an EMPTY chain — the pool is separate by design."""
    assert CITATION_PREFIXES["runtime"] == "runtime"

    cits = parse_citations("Я исполняюсь на Python [runtime:python_version].")
    assert len(cits) == 1, cits
    hit = match_citation(cits[0], ProvenanceChain())
    assert hit is not None, "a runtime citation must resolve without a chain"
    assert hit.excerpt == process_facts()["python_version"]


def test_resolving_a_runtime_citation_does_not_touch_the_chain():
    """Containment. The chain's contracts are not negotiable for this feature."""
    chain = ProvenanceChain()
    match_citation(parse_citations("[runtime:pid]")[0], chain)
    assert chain.evidences == [], (
        "resolving a runtime citation must not add anything to the chain"
    )


def test_runtime_evidence_is_trace_not_world():
    """Scope guard. It must never be able to confirm a claim about the world."""
    ev = make_evidence(
        kind="runtime", source_id="runtime:python_version",
        obtained_via="process_self_measurement",
        claim="This run's python_version", excerpt="3.11.9",
    )
    assert classify_evidence(ev) == "trace"


def test_the_kind_is_declared_everywhere_the_guards_look():
    """One kind, three tables — omission in any of them is the usual defect."""
    assert "runtime" in ALL_EVIDENCE_KINDS
    assert "runtime" in DEFAULT_CONFIDENCE
    assert DEFAULT_CONFIDENCE["runtime"] > DEFAULT_CONFIDENCE["llm_claim"]


def test_a_real_run_shows_the_process_facts_to_the_synthesiser(tmp_path):
    """Wired, not merely written — built-but-not-connected is the house failure.

    A fact the synthesiser never sees cannot be stated however citable it is.
    """
    from core.ids import new_trace_id
    from core.logger import TraceLogger
    from core.loop import AgentLoop
    from core.policy import PolicyGate
    from tests.conftest import FakeLLM, FakePlanner
    from tools.base import ToolRegistry
    from tools.file_read import FileReadTool

    (tmp_path / "present.txt").write_text("alpha=1", encoding="utf-8")
    llm = FakeLLM(responses=[_ANSWER] * 4)
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    agent = AgentLoop(
        planner=FakePlanner(sources=[{
            "tool": "file_read", "arguments": {"path": "present.txt"},
            "label": "s:1", "expected_outcome": "content",
        }]),
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False,
        ),
        memory=None,
        max_replan_attempts=2,
    )
    agent.run("что в файле")

    prompts = " || ".join(call["user"] for call in llm.calls)
    assert "<runtime_self>" in prompts, "the self block never reached synthesis"
    assert "interpreter:" in prompts and "python_version:" in prompts, (
        "the agent was shown its organs and not what it runs on"
    )
    assert sys.executable in prompts

    # And the chain keeps its contracts: exactly the file it read, nothing added.
    assert [e.kind for e in agent.last_provenance.evidences] == ["file"], (
        "the runtime pool leaked into the provenance chain"
    )
