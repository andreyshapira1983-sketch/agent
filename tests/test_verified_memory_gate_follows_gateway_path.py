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
import inspect
import pathlib

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


def test_all_four_write_sites_are_accounted_for():
    """A count, so a NEW site cannot arrive unnoticed under a green suite.

    Four now, not the five this test first pinned. B3 merged the two loop-layer
    sites into one shared core (`_catalogue_chain` in `loop_evidence_chain.py`),
    so `loop_verify_replan` calls it instead of running the pipeline itself.
    That is the gate getting HARDER to lose, not easier: one place to get it
    wrong in the loop layer rather than two.

    Three remain in `core/ingestion.py`, which catalogues a document set on an
    operator command — a different caller with a different lifetime, and no
    business sharing the turn's core.

    If this number changes, the change is either a new write site that needs the
    gate or a removed one. Both want a human look, which is why it is pinned.
    """
    calls = _pipeline_calls()
    assert len(calls) == 4, [f for f, _ in calls]


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


def test_the_loop_layer_asks_the_question_in_exactly_one_place():
    """Was "both loop sites use the shared policy" — there is one site now.

    The verify path used to run the pipeline itself, inside the citation-fetch
    loop, so a per-site decision there was not one open door but one per round.
    B3 gave both callers a shared core, and the question is asked there.

    Checked as "exactly one", not "at least one": a second site reappearing is
    how this defect returns.
    """
    asking = [
        f"{filename}:{call.lineno}"
        for filename, call in _pipeline_calls()
        if filename.startswith("loop")
        and any(
            kw.arg == "require_verified" and "_unattended_run" in ast.unparse(kw.value)
            for kw in call.keywords
        )
    ]
    # Length first: the next line indexes `asking[0]`, and on an empty list
    # that is an IndexError rather than a readable failure — the guard would
    # crash instead of saying "no call site asks for the gate any more".
    assert len(asking) == 1, asking
    assert asking == ["loop_evidence_chain.py:" + asking[0].split(":")[1]], asking


def test_the_verify_path_still_gets_the_gate_through_the_core():
    """It no longer runs the pipeline, so the gate must arrive another way.

    Losing the gate by delegating would be a silent regression of A1 dressed as
    a tidy-up, which is exactly the trade B3 must not make.
    """
    src = inspect.getsource(
        __import__("core.loop_verify_replan", fromlist=["x"])
    )
    assert "_catalogue_chain(" in src
    assert "knowledge_pipeline.run(" not in src


def test_the_three_ingestion_paths_resolve_it_through_the_agent():
    """`core/ingestion.py` has no `self` — it must still ask the same question."""
    calls = [c for f, c in _pipeline_calls() if f == "ingestion.py"]
    assert len(calls) == 3, len(calls)
    for call in calls:
        value = next(kw for kw in call.keywords if kw.arg == "require_verified")
        assert "_require_verified_for" in ast.unparse(value.value), ast.unparse(value.value)
