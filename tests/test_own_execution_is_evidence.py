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

from core.evidence import (
    ProvenanceChain,
)
from core.verifier_utils import match_citation, parse_citations

# ── the permission half: the contract must name what the model may ground on ──


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
