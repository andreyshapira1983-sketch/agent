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

# Fires the ODD gate only (out-of-domain, no bare destructive verb).
DOMAIN_ONLY = "hack into the account"
# Fires the clarification gate only (destructive verb, no target; in-domain).
CLARIFY_ONLY = "delete"
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


# ── 1. Both poles of each gate, through run() ─────────────────────────────────

def test_a_clean_question_passes_both_gates_and_reaches_planning(tmp_path: Path):
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}', "Ответ."])
    loop = _loop(tmp_path, llm)

    answer = loop.run("сколько будет два плюс два")

    assert _ODD_SIGNATURE not in answer and _CLARIFY_SIGNATURE not in answer
    assert llm.calls, "a clean question must reach the planner (None from both gates)"


def test_a_domain_only_question_returns_the_refusal(tmp_path: Path):
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    loop = _loop(tmp_path, llm)

    answer = loop.run(DOMAIN_ONLY)

    assert _ODD_SIGNATURE in answer, "an out-of-domain request must be refused"


def test_a_clarification_only_question_returns_the_question(tmp_path: Path):
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    loop = _loop(tmp_path, llm)

    answer = loop.run(CLARIFY_ONLY)

    assert _CLARIFY_SIGNATURE in answer, "an ambiguous destructive verb must ask"


# ── 2. An early str stops downstream ──────────────────────────────────────────

def test_a_gate_refusal_stops_before_the_planner_runs(tmp_path: Path):
    """The early return is the point of a gate: no planning on a refused turn."""
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    loop = _loop(tmp_path, llm)

    loop.run(DOMAIN_ONLY)

    assert llm.calls == [], (
        "the planner LLM ran after a domain refusal — the gate did not stop "
        "the cycle, it only prepended a message"
    )


# ── 3. The _stream_on_token cleanup, a separate side wire ─────────────────────

def test_a_gate_clears_the_stream_callback(tmp_path: Path):
    """A refused turn must not leave a streaming callback armed for reuse.

    Distinct wire from the return value: the gate returns its message AND
    clears _stream_on_token (loop_gates.py:86,110). Bitten alone so removing
    the cleanup line reddens something.
    """
    llm = FakeLLM(responses=['{"reasoning":"no tools","sources":[]}'])
    loop = _loop(tmp_path, llm)
    seen: list[str] = []

    loop.run(DOMAIN_ONLY, on_token=seen.append)

    assert loop._stream_on_token is None, (
        "the streaming callback survived a gated turn — it can leak into the next"
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
