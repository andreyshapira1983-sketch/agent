"""The in-run Goal and Plan end the run with a verdict — MIR-026.

The ephemeral Goal/Plan are LOG objects: their whole purpose is to let a
journal reader reconstruct the run. Born `pending` / `in_progress` and never
finalised, they made a finished run and an abandoned one indistinguishable by
their own objects (LPF-016). Settled at the attempt loop's single exit:
`done` when the loop broke with its work, `failed` on replan exhaustion — the
one failure the loop itself can declare. Cosmetic severity, honest journal.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import FakeLLM

PLAN_EMPTY = json.dumps({"reasoning": "no tools needed", "sources": []})
SYNTH = (
    "Conclusion: done. [general-knowledge]\n"
    "Facts:\n- fine. [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _events(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in
            log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build(workspace: Path, llm: FakeLLM):
    from core.logger import TraceLogger
    from core.loop import AgentLoop, new_trace_id
    from core.memory import WorkingMemory
    from core.planner import LLMPlanner
    from core.policy import PolicyGate
    from tools.base import ToolRegistry

    registry = ToolRegistry()
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def test_a_completed_run_settles_done(workspace: Path) -> None:
    agent, log_path = _build(workspace, FakeLLM([PLAN_EMPTY, SYNTH]))

    agent.run("скажи одно слово")

    settled = [e for e in _events(log_path) if e.get("event") == "run_objects_settled"]
    assert settled, (
        "the run finished and its Goal/Plan died pending — the journal cannot "
        "tell a finished run from an abandoned one"
    )
    payload = settled[-1].get("payload", settled[-1])
    assert payload.get("status") == "done"
    assert payload.get("goal_id")


def test_a_budget_interruption_settles_too(workspace: Path) -> None:
    """Audit of this closure (docs/audit/archive/CLOSURE_AUDIT_2026-08-22.md).

    The field's rule for settle-on-exit is that a status written at ONE exit
    lies about the others. Probed by parsing the attempt loop: it has no
    `return`, but it does re-raise `ModelBudgetExceeded` after saving a pause
    checkpoint — an exit that skipped the settle, leaving the objects
    `pending`, which in this vocabulary means «never started» rather than
    «interrupted». Both exits settle now.
    """
    import ast
    import inspect

    import core.loop_attempt as mod

    tree = ast.parse(inspect.getsource(mod))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_attempt_loop")
    settle_calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "_settle_run_objects"
    ]
    raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]

    assert len(settle_calls) >= 1 + len(raises), (
        f"{len(raises)} escaping raise(s) but only {len(settle_calls)} settle "
        "call(s) — an exit leaves the log objects reading «never started»"
    )
