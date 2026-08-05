"""Где живёт состояние прогона и что переживает ход.

Census item B2, step one. The operator's instruction was explicit: «Только по
шагам. Сначала нужен сквозной characterization-тест для _run_inner и
классификация полей по времени жизни. Не ставь целью заранее слить
AttemptState, SynthesisState и VerifyState в один изменяемый мешок.»

So this file changes nothing and judges nothing. It writes down what is true
today, mechanically, so that the next step has a baseline that cannot be
argued with — and so that any move of a field between homes shows up as a red
test rather than as a thing someone has to notice.

The census counted five homes for run state: `AttemptState` (21 in / 8 out),
`SynthesisState` (13/2), `VerifyState` (18/4), fields on the agent instance,
and plain locals in `_run_inner`. This file measures the fourth of those,
because it is the only one with no declared boundary at all.

Two questions, and the second is the one that matters for B2:

    which fields does a run RESET?     -> they cost nothing to move
    which fields SURVIVE a run?        -> each is either a deliberate output
                                          or a leak into the next turn, and
                                          the difference has to be decided
                                          one field at a time

Counting by hand was tried and produced wrong numbers twice during this
census, so every list below is derived by walking the AST at test time. A new
field appears in the inventory the moment it is written, whether or not anyone
remembered to update a document.

**The answer, measured, and it corrects the census rather than confirming it.**
28 fields are mutable run state. 12 are reset by `_run_inner`. Of the 16 that
survive it, planting a marker between two live runs shows that 25 of the 28 are
replaced by the next run outright, and exactly THREE carry turn 1's value into
turn 2: two audit flags that are session state by design, and one profile field
that only stands still because this configuration has no profile store.

The census recorded "~25 fields on the agent instance" as a fifth home for run
state and implied the risk was leakage between turns. Per-turn isolation
actually holds. What is left is that the same run's state is spread across five
places with no declared boundary, which costs a reader — a different and
smaller problem than the one written down, and B2 should be scoped to it
instead.

The first version of this measurement was wrong and the mistake is recorded at
`_Marker`: comparing `id()` across runs reports interned `True`/`False`/small
ints as "untouched" regardless of what happened, and five of the six fields it
flagged were artefacts of the method.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

_LAYER = Path(__file__).resolve().parents[1] / "core"


def _self_attributes() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Every `self.X` written and read across the loop layer, by module."""
    writes: dict[str, set[str]] = defaultdict(set)
    reads: dict[str, set[str]] = defaultdict(set)
    for path in sorted(_LAYER.glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                bucket = writes if isinstance(node.ctx, ast.Store) else reads
                bucket[node.attr].add(path.name)
    return writes, reads


def _run_state_fields() -> set[str]:
    """Fields the layer ASSIGNS somewhere other than the constructor.

    A field written only in `loop_init.py` is wiring — a store, a policy, a
    router — handed in once and never reassigned. What is left is state that
    changes while the agent works, and that is what B2 is about.
    """
    writes, _ = _self_attributes()
    return {attr for attr, mods in writes.items() if mods - {"loop_init.py"}}


def _reset_by_a_run() -> set[str]:
    """Fields `_run_inner` assigns itself, anywhere in its body.

    Position does not matter, only that the run gives the field a fresh value
    before the phases that read it. `_step_repetition` and `_termination_guard`
    are assigned two hundred lines in, not in the prologue, and they are just
    as fresh for it.
    """
    tree = ast.parse((_LAYER / "loop.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_run_inner"
    )
    return {
        n.attr for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
        and isinstance(n.ctx, ast.Store)
    }


#: Measured 2026-08-05. A run gives each of these a fresh value, so none of
#: them can carry anything from the previous turn.
RESET_BY_A_RUN = frozenset({
    "_cycle_findings",
    "_defect_signals",
    "_executed_tools",
    "_run_assumptions_current",
    "_step_repetition",
    "_stream_on_token",
    "_synthesis_expects_contract_headers",
    "_termination_guard",
    "last_knowledge_pipeline",
    "last_replan_exhausted",
    "last_source_ranking",
    "last_source_registry",
})

#: Measured 2026-08-05. These survive a run. NOT a defect list — `last_*` is a
#: deliberate reading surface for the CLI and the tests, and several are
#: written by a phase that always runs before every reader. What the list says
#: is narrower and is the whole point of recording it: for each of these, "is
#: the value from THIS turn?" is a question the code does not answer on its
#: own, and B2 has to answer it one field at a time.
SURVIVES_A_RUN = frozenset({
    "_audit_froze_agent_auto",
    "_current_attempt",
    "_last_best_similar_episode",
    "_last_best_similar_score",
    "_last_episode_records",
    "_last_persistent_records",
    "_last_procedure_records",
    "_last_synth_degraded",
    "audit_read_only",
    "last_assumptions",
    "last_provenance",
    "last_referent_decision",
    "last_role_context",
    "last_self_analysis",
    "last_user_profile",
    "last_verification",
})


def test_the_inventory_is_complete():
    """Every mutable field is in exactly one of the two lists.

    Red when a new one appears — which is the point. A field added without a
    decision about its lifetime is how the fifth home got its twenty-fifth
    resident.
    """
    found = _run_state_fields()
    catalogued = RESET_BY_A_RUN | SURVIVES_A_RUN

    assert found == catalogued, (
        "the run-state inventory drifted.\n"
        f"  new, not catalogued: {sorted(found - catalogued)}\n"
        f"  catalogued, now gone: {sorted(catalogued - found)}\n"
        "Decide the lifetime and add it to RESET_BY_A_RUN or SURVIVES_A_RUN."
    )


def test_the_reset_list_matches_what_the_run_actually_assigns():
    """Derived from `_run_inner`, not from the constant above.

    The constant is what a reader sees; this is what the code does. They are
    checked against each other so the documentation cannot quietly go stale.
    """
    assert _run_state_fields() & _reset_by_a_run() == RESET_BY_A_RUN


def test_nothing_is_in_both_lists():
    assert not (RESET_BY_A_RUN & SURVIVES_A_RUN)


def test_the_surviving_fields_are_the_ones_a_run_never_reassigns():
    assert _run_state_fields() - _reset_by_a_run() == SURVIVES_A_RUN


#: Of the survivors, these are READ somewhere in the layer — so a stale value
#: is not merely stored, it is consulted. Measured, not assumed.
SURVIVORS_THAT_ARE_READ = frozenset({
    "_audit_froze_agent_auto",
    "_current_attempt",
    "_last_best_similar_episode",
    "_last_best_similar_score",
    "_last_persistent_records",
    "audit_read_only",
    "last_provenance",
    "last_referent_decision",
    "last_role_context",
    "last_self_analysis",
    "last_user_profile",
    "last_verification",
})


def test_which_survivors_are_actually_consulted():
    """The distinction that decides how much each field costs.

    A survivor nobody reads is a reporting surface and can stay where it is. A
    survivor the layer reads DURING a run is the one that has to be shown to
    come from this turn — that is the work B2 is scoped to, and this is the
    list of it.
    """
    _, reads = _self_attributes()
    consulted = {a for a in SURVIVES_A_RUN if reads.get(a)}

    assert consulted == SURVIVORS_THAT_ARE_READ, (
        f"  newly consulted: {sorted(consulted - SURVIVORS_THAT_ARE_READ)}\n"
        f"  no longer read : {sorted(SURVIVORS_THAT_ARE_READ - consulted)}"
    )


def test_the_write_only_survivors_are_named():
    """The other four, stated so the split is exhaustive rather than implied."""
    write_only = frozenset({
        "_last_episode_records",
        "_last_procedure_records",
        "_last_synth_degraded",
        "last_assumptions",
    })
    never_read = SURVIVES_A_RUN - SURVIVORS_THAT_ARE_READ
    assert never_read == write_only


# ---------------------------------------------------------------------------
# The same question asked of a running agent, not of the syntax tree
# ---------------------------------------------------------------------------

def _agent(workspace: Path) -> AgentLoop:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    return AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=FakeLLM(responses=[
            "Conclusion: первый ответ [general-knowledge].",
            "Conclusion: второй ответ [general-knowledge].",
        ]),
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
        ),
        planner=FakePlanner([]),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=True,
        memory=WorkingMemory(),
    )


class _Marker:
    """A value that tolerates being read, so a run measures instead of dying.

    The first attempt at this measurement compared `id()` before and after a
    second run. That is worthless for the fields that matter most: `True`,
    `False` and small ints are interned, so `_current_attempt` and
    `audit_read_only` looked "untouched" no matter what the run did. Five of
    the six fields that method reported were artefacts of the method.

    Planting a marker asks the question directly — is the object still the one
    from turn 1? — and works for every type.
    """

    def __getattr__(self, name):
        return _Marker()

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def __iter__(self):
        return iter(())

    def __repr__(self) -> str:
        return "<MARKER>"


def _survives_a_second_run(field: str, tmp: Path) -> bool:
    agent = _agent(tmp)
    agent.run("первый вопрос")
    marker = _Marker()
    setattr(agent, field, marker)
    agent.run("второй вопрос")
    return getattr(agent, field, None) is marker


#: Measured 2026-08-05 by planting a marker between two runs, one field at a
#: time. Everything NOT here — 25 of 28 — is replaced by the second run.
#:
#: This corrected the census's picture rather than confirming it. "Twenty-five
#: fields on the agent instance" was recorded as a leak risk; it is mostly not
#: one. Per-turn isolation holds for every field the loop actually recomputes.
#: What is left is a readability problem — state spread over five homes — which
#: is a real cost, but a different one, and B2 should be scoped to it.
SURVIVES_A_SECOND_RUN = frozenset({
    # Session state by design: the audit brake is set once for a session and
    # must outlive every turn in it. Resetting these per run would be the bug.
    "_audit_froze_agent_auto",
    "audit_read_only",
    # NOT by design — an artefact of configuration, and worth its own line.
    # The tail only writes the profile when `user_profile_store` is not None,
    # and this agent has none. With a store configured the second run replaces
    # it. Kept in the list because the test measures THIS configuration, and
    # pretending otherwise would be reasoning past the measurement.
    "last_user_profile",
})


def test_the_run_hands_back_a_clean_streaming_callback(workspace: Path):
    """The callback is cleared at the end of the run so it cannot fire for the
    next turn's tokens. Checked by value, because `None` is the point.
    """
    agent = _agent(workspace)
    agent.run("вопрос")

    assert agent._stream_on_token is None


def test_every_reset_field_is_genuinely_rebuilt(workspace: Path):
    """RESET_BY_A_RUN checked against a live agent, not the syntax tree.

    The static test proves an assignment EXISTS in `_run_inner`. This proves it
    is reached: an assignment behind a branch that never fires satisfies the
    AST and leaks anyway.
    """
    leaked = [
        f for f in sorted(RESET_BY_A_RUN)
        if _survives_a_second_run(f, workspace / f"r_{f}")
    ]

    assert not leaked, (
        f"a run claims to reset these and does not: {leaked}"
    )


def test_only_three_fields_survive_a_second_run(workspace: Path):
    """The measurement B2 acts on, taken rather than argued.

    Red when the set changes in either direction — a new survivor is a new
    cross-turn dependency, and a departed one is a win worth banking.
    """
    survivors = {
        f for f in sorted(SURVIVES_A_RUN)
        if _survives_a_second_run(f, workspace / f"s_{f}")
    }

    assert survivors == SURVIVES_A_SECOND_RUN, (
        "the set of fields carrying turn 1's value into turn 2 changed.\n"
        f"  now : {sorted(survivors)}\n"
        f"  was : {sorted(SURVIVES_A_SECOND_RUN)}"
    )


def test_the_verifier_off_path_clears_its_own_field():
    """One survivor checked in the code rather than left as a worry.

    `last_verification` looked like the worst candidate — it is read by
    `_finalize_run_tail` and its numbers go straight into the episode, so a
    stale report would bank the previous turn's verification against this
    turn's answer. It does not: the `verifier_enabled=False` branch assigns
    `None` explicitly.

    Pinned because the guarantee lives in an `else` two hundred lines from the
    reader, and nothing else states it.
    """
    src = (_LAYER / "loop_verify_replan.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_verify_and_settle_answer"
    )
    gate = next(
        n for n in ast.walk(fn)
        if isinstance(n, ast.If) and "verifier_enabled" in ast.unparse(n.test)
    )
    cleared = [
        n for n in ast.walk(ast.Module(body=gate.orelse, type_ignores=[]))
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "last_verification"
            for t in n.targets
        )
        and isinstance(n.value, ast.Constant)
        and n.value.value is None
    ]
    assert cleared, (
        "the verifier-off branch stopped clearing `last_verification`; the tail "
        "would bank the previous turn's report against this turn's answer"
    )
