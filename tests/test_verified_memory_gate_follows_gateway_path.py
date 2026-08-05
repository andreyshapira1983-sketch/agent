"""The verified-memory gate follows WHO DRIVES THE RUN, not which file calls it.

Census finding Л8, 2026-08-05. `require_verified` was decided per call site:
`True` where the learning path ran the knowledge pipeline, absent everywhere
else. The unattended runtime drives the ordinary cycle
(`core/autonomous_runtime.py` calls `agent.run`), so it reached the sites that
had no gate — two in the loop layer, one of them inside the citation-fetch loop
and therefore firing once per iteration, plus three in `core/ingestion.py`.

The guard missed it because it parsed the AST of one module. That was not
caution, it was a guess about where the defect could be — the same shape the
census found in review round #294 and in the MIR-077 audit.

The fix asks one question in one place. `gateway_path` is what actually records
who drives the cycle: `repl` is a human typing, `daemon` and `runtime` are
nobody. These tests pin the answer for each value and at every real write site,
so a future call site cannot quietly reintroduce a per-site decision.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from core.loop_memory_write import AgentLoopMemoryWrite


class _Agent(AgentLoopMemoryWrite):
    """Only what the gate reads — the question must not need a whole runtime."""

    def __init__(self, gateway_path: str) -> None:
        self.gateway_path = gateway_path


# ---------------------------------------------------------------------------
# The question, once, for every value the field can hold
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("gateway_path", "unattended"),
    [
        ("repl", False),      # a human is typing and can judge one source
        ("daemon", True),     # nobody is watching
        ("runtime", True),    # nobody is watching
    ],
)
def test_unattended_is_read_from_gateway_path(gateway_path: str, unattended: bool):
    assert _Agent(gateway_path)._unattended_run() is unattended


def test_a_missing_gateway_path_reads_as_attended():
    """The default is the safe direction for the FIELD, not for the gate.

    An agent built without the attribute is a REPL agent — that is what
    `AgentLoop.__init__` defaults to. Reading it as unattended would make an
    ordinary interactive turn demand corroboration it was never meant to need.
    """
    class _Bare(AgentLoopMemoryWrite):
        pass

    assert _Bare()._unattended_run() is False


def test_the_gate_follows_the_agent_not_the_call_site():
    """The whole point of Л8, stated as behaviour rather than as a rule.

    Same code path, same file, two different agents — and the answer differs.
    Before the fix it could not: the value was written into the call.
    """
    assert _Agent("repl")._unattended_run() != _Agent("daemon")._unattended_run()


# ---------------------------------------------------------------------------
# Every real write site asks it
# ---------------------------------------------------------------------------

def _pipeline_calls() -> list[tuple[str, ast.Call]]:
    """Every `…knowledge_pipeline.run(...)` in `core/`, with its file."""
    out: list[tuple[str, ast.Call]] = []
    for path in sorted(pathlib.Path("core").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out.extend(
            (path.name, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and "knowledge_pipeline" in ast.unparse(node.func.value)
        )
    return out


def test_all_five_write_sites_are_accounted_for():
    """A count, so a NEW site cannot arrive unnoticed under a green suite.

    Two in the loop layer (`loop_evidence_chain`, `loop_verify_replan`) and
    three in `core/ingestion.py`. If this number changes, the change is either a
    new write site that needs the gate or a removed one — both want a human
    look, which is why the number is pinned rather than left implicit.
    """
    calls = _pipeline_calls()
    assert len(calls) == 5, [f for f, _ in calls]


def test_no_write_site_decides_the_gate_by_itself():
    """None of them may carry a literal — the answer belongs to the agent."""
    for filename, call in _pipeline_calls():
        kwargs = {kw.arg for kw in call.keywords}
        assert "require_verified" in kwargs, (
            f"{filename}: knowledge pipeline run without the verified gate"
        )
        value = next(kw for kw in call.keywords if kw.arg == "require_verified")
        assert not isinstance(value.value, ast.Constant), (
            f"{filename}: the gate is decided by a literal "
            f"({ast.unparse(value.value)}) instead of by who drives the run"
        )


def test_both_loop_sites_use_the_shared_policy():
    """Named explicitly, because the second one hides inside a loop.

    `core/loop_verify_replan.py`'s call sits inside the citation-fetch loop, so
    it runs once per iteration. A per-site decision there is not one open door
    but one per round.
    """
    seen = {
        filename
        for filename, call in _pipeline_calls()
        if any(
            kw.arg == "require_verified" and "_unattended_run" in ast.unparse(kw.value)
            for kw in call.keywords
        )
    }
    assert {"loop_evidence_chain.py", "loop_verify_replan.py"} <= seen, seen


def test_the_three_ingestion_paths_resolve_it_through_the_agent():
    """`core/ingestion.py` has no `self` — it must still ask the same question."""
    calls = [c for f, c in _pipeline_calls() if f == "ingestion.py"]
    assert len(calls) == 3, len(calls)
    for call in calls:
        value = next(kw for kw in call.keywords if kw.arg == "require_verified")
        assert "_require_verified_for" in ast.unparse(value.value), ast.unparse(value.value)
