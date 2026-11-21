"""C04 — the admission gates, dissected as detector -> verdict -> caller -> early
return -> cleanup, not lumped under the word "gate".

Four things the operator required, each its own test:

1. Each gate has both poles (str and None) THROUGH A REAL CALLER: a clean
   question proceeds; a domain-only question refuses; a clarification-only
   question asks.
2. An early str actually STOPS downstream (the planner LLM is never called);
   None passes into planning.
3. `_stream_on_token` cleanup is a SEPARATE side wire — bitten on its own.
4. The gate ORDER is pinned by an EXTERNAL INVARIANT, not by "it is currently
   odd-then-clarification": when both apply, the domain refusal must not be
   hidden behind a clarification question. The map already measured that
   swapping the two gave full green — this is the behavioral order bite that
   was missing.

Deliberately NOT here: the Russian ODD-regex defect (declined stems slip the
\\b). That is a DETECTOR bug (core/operational_domain.py), not a statement
about the gate WIRING, and the map keeps the two apart.
"""
from __future__ import annotations

from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

# Fires BOTH: a bare destructive verb AND an out-of-domain clause.
BOTH = "delete everything and hack into the account"

_ODD_SIGNATURE = "вне моей операционной области"
_CLARIFY_SIGNATURE = "Уточни"


def _loop(tmp_path: Path, llm: FakeLLM) -> AgentLoop:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False
        ),
        # Both gates on; verifier off keeps the answer text clean to read.
        odd_enabled=True,
        clarification_enabled=True,
        verifier_enabled=False,
    )


# ── 4. External order invariant, NOT the current order for its own sake ───────

def test_domain_refusal_is_not_hidden_behind_a_clarification(tmp_path: Path):
    """The invariant the map prescribed instead of pinning odd-then-clarification.

    When a request trips BOTH gates, the operator must receive the domain
    refusal, never only the clarification question — a safety stop may not be
    masked by an ambiguity prompt. This reddens if the two gates are swapped,
    which is the behavioral order bite the earlier walk could not produce
    (swapping left the whole suite green).
    """
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    loop = _loop(tmp_path, llm)

    answer = loop.run(BOTH)

    assert _ODD_SIGNATURE in answer, (
        "a request that is both out-of-domain and ambiguous returned the "
        "clarification instead of the refusal — the domain stop is hidden"
    )
    assert _CLARIFY_SIGNATURE not in answer, (
        "the clarification question surfaced ahead of the domain refusal"
    )
    assert llm.calls == [], "neither gate may fall through to planning here"
