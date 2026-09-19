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


# ── the permission half: the contract must name what the model may ground on ──

def test_the_output_contract_grants_and_bounds_the_runtime_prefix():
    """Built and reachable is not the same as permitted.

    Measured 2026-08-14, after the pool was wired: asked what it runs on, the
    agent answered correctly from `<runtime_self>` and cited
    `[general-knowledge]` — `sources=['general-knowledge']`, three claims with
    no external source. The mechanism resolved; nothing told the model it was
    allowed to use it. `<runtime_self>` appeared nowhere in SYSTEM_ANSWER, the
    same gap `<host_environment>` had before MIR-013 gave it a named rule.

    The two limits are asserted, not just the permission. Granting without them
    is how MIR-013 happened in reverse: `host_tools` injected AS evidence forced
    strict-evidence mode on every turn and broke general-knowledge answers.
    """
    from core.answer_format import SYSTEM_ANSWER

    text = SYSTEM_ANSWER
    assert "<runtime_self>" in text, "the contract never names the block"
    assert "[runtime:" in text, "the citation grammar omits the prefix"

    lowered = text.casefold()
    assert "never about the world" in lowered or "never for a world fact" in lowered, (
        "a runtime fact must be barred from supporting a claim about the world"
    )
    assert "does not mean evidence was gathered" in lowered, (
        "without this, a turn carrying only <runtime_self> reads as an "
        "evidence turn and general-knowledge answers break — MIR-013 in reverse"
    )


def test_the_contract_still_forbids_citing_the_two_context_blocks():
    """The neighbours keep their rule; the new permission is narrow."""
    from core.answer_format import SYSTEM_ANSWER

    lowered = SYSTEM_ANSWER.casefold()
    assert "<host_environment>" in SYSTEM_ANSWER
    assert "never cite it" in lowered
    assert "<failure_context>" in SYSTEM_ANSWER, (
        "the failure block reaches the prompt since 2026-08-14 and the contract "
        "must say it is context, not a source"
    )


def test_the_probe_rule_knows_the_process_is_already_measured():
    """The planner must not shell out for a fact it was handed.

    Measured 2026-08-14: asked for its pid and working directory, the planner
    ran `whoami` and `hostname` — both returned `andre` — while `<runtime_self>`
    already carried both facts. The rule is not at fault for existing: it dates
    from the initial commit, a month before any self-measurement existed, when
    asking the host was the only honest route. The overlap was created the day
    `process_facts()` landed, and hunting that echo is the same discipline the
    rest of this repo applies to a renamed module.

    The exception is narrow on purpose, and the test pins the narrowness: five
    external programs keep their mandatory probe, and `where python` survives
    for the case it actually answers — which interpreter a CHILD process gets,
    a different question from `sys.executable` and one that has returned a
    different path on this very host.
    """
    from core.planner_prompt import PLANNER_SYSTEM

    text = PLANNER_SYSTEM
    assert "<runtime_self>" in text, (
        "the probe rule never mentions the block that already holds the answer"
    )
    lowered = text.casefold()
    assert "plan no probe" in lowered, "the exception is stated but not actionable"
    assert "mandatory" in lowered, "the rule lost its force for the other cases"
    for external in ("soffice", "pandoc", "magick", "ffmpeg", "pip"):
        assert external in lowered, (
            f"{external} lost its probe — it has no measurement to fall back on"
        )


def test_every_field_the_block_shows_can_be_cited_in_that_run():
    """Замер 2026-09-19: блок показывал run_id, хранилища и durable_writes, а пул
    знал пять полей процесса; «(run_id=…) [runtime:run_id]» вырезалось как
    выдуманная ссылка, и верный эпизод терял допуск в опыт."""
    from core.run_context import run_scope
    from core.runtime_self import runtime_self_block

    with run_scope("run_abc123"):
        runtime_self_block(trace_id="t1", run_id="run_abc123", session_id=None,
                           stores={"episodic_store": object(), "persistent_store": None},
                           durable_writes=())
        for field, value in (("run_id", "run_abc123"), ("episodic_store", "подключён"),
                             ("persistent_store", "нет"), ("durable_writes", "ничего не разрешено")):
            hit = match_citation(parse_citations(f"x [runtime:{field}]")[0], ProvenanceChain())
            assert hit is not None and hit.excerpt == value, field


def test_another_runs_block_cannot_be_cited():
    """Факты прошлого прогона — не улика этого."""
    from core.run_context import run_scope
    from core.runtime_self import runtime_self_block

    with run_scope("run_old"):
        runtime_self_block(trace_id="t1", run_id="run_old", session_id=None,
                           stores={}, durable_writes=())
    with run_scope("run_new"):
        assert match_citation(parse_citations("x [runtime:run_id]")[0], ProvenanceChain()) is None
