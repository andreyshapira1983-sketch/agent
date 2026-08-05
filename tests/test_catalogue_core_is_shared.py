"""Cataloguing a chain is written once, and the callers keep their differences.

Census item B3. `rank_chain -> knowledge_pipeline.run -> source_registry` existed
twice: in `core/loop_evidence_chain.py` and, copied rather than called, in
`core/loop_verify_replan.py`. That is the layer's only `duplicated`
classification, and it is the shape that makes a fix land at one site while the
class stays open at the other — the same thing Л10 found in the memory store and
review #294 found before that.

What is shared is the COMPUTATION. What is not, and deliberately stays with each
caller:

    evidence chain  skips the pipeline entirely on the cheap path
                    stores into `self.last_*`
                    logs a plain payload

    verify replan   runs inside the citation-fetch loop
                    stores into the run state as well as `self.last_*`
                    stamps every event with `phase` and `iteration`
                    quarantines conflicted records afterwards

Those are real differences, so `_catalogue_chain` does not log and does not
store. Folding them in would need a mode flag, and a function that behaves two
ways by argument is two functions wearing one name.

The extraction proved itself immediately: the wiring guard went red because five
host attributes — `knowledge_pipeline`, `knowledge_auto_write`,
`source_registry_store`, `_knowledge_remember_batch`, `_unattended_run` — became
unused in `loop_verify_replan`. It had removed a coupling, not relocated a call.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

from core.loop_evidence_chain import AgentLoopEvidenceChain, CatalogueResult


class _Pipeline:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, chain, **kw):
        self.calls.append(kw)

        class _Result:
            registry = "REGISTRY"

            def to_log_payload(self):
                return {}
        return _Result()


class _Agent(AgentLoopEvidenceChain):
    def __init__(self, *, gateway_path: str = "repl") -> None:
        self.knowledge_pipeline = _Pipeline()
        self.knowledge_auto_write = True
        self.source_registry_store = "STORE"
        self.gateway_path = gateway_path

    def _knowledge_remember_batch(self):
        return "REMEMBER"

    def _unattended_run(self) -> bool:
        return self.gateway_path != "repl"


class _Chain:
    """Only what `rank_chain` touches."""

    evidences: tuple = ()

    def __len__(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# The core computes, and nothing else
# ---------------------------------------------------------------------------

def test_it_returns_both_halves():
    agent = _Agent()

    result = agent._catalogue_chain(
        _Chain(), question="q", may_knowledge=True, may_source_registry=True,
    )

    assert isinstance(result, CatalogueResult)
    assert result.ranking is not None
    assert result.knowledge.registry == "REGISTRY"


def test_it_writes_no_journal_event_and_assigns_no_field():
    """Why it CAN be shared — not a claim that it has no effects.

    It has one: `knowledge_pipeline.run` may write to long-term memory. What it
    must not do is the caller's part, because that is what differs. A core doing
    either would have to be told which caller it is, and that is the mode flag
    this extraction exists to avoid.

    This is NOT a test of the callers' consequences. Those have their own tests
    below, one per consequence.
    """
    agent = _Agent()
    agent.log = object()  # any use would raise AttributeError

    agent._catalogue_chain(
        _Chain(), question="q", may_knowledge=True, may_source_registry=True,
    )

    assert not hasattr(agent, "last_source_ranking")
    assert not hasattr(agent, "last_source_registry")
    assert not hasattr(agent, "last_knowledge_pipeline")


def test_the_core_can_reach_memory_and_says_so():
    """The contract, pinned: this is not a pure computation.

    `auto_write_memory` and `require_verified` travel INTO the pipeline from
    here, so a write can begin inside this call. Describing the core as "only
    computes and returns" would be the comfortable version, and a reader
    trusting it would look for the write somewhere else.
    """
    agent = _Agent(gateway_path="daemon")

    agent._catalogue_chain(
        _Chain(), question="q", may_knowledge=True, may_source_registry=True,
    )

    sent = agent.knowledge_pipeline.calls[0]
    assert sent["auto_write_memory"] is True, "a write can start in here"
    assert sent["remember"] == "REMEMBER"
    assert sent["require_verified"] is True

    import inspect

    doc = inspect.getdoc(AgentLoopEvidenceChain._catalogue_chain) or ""
    assert "not a pure computation" in doc


def test_permissions_reach_the_pipeline():
    agent = _Agent()

    agent._catalogue_chain(
        _Chain(), question="q", may_knowledge=False, may_source_registry=False,
    )

    sent = agent.knowledge_pipeline.calls[0]
    assert sent["remember"] is None
    assert sent["auto_write_memory"] is False
    assert sent["source_store"] is None


def test_the_verified_gate_still_follows_who_drives_the_run():
    """A1 must survive the extraction — one place to get it wrong now, not two."""
    assert _Agent(gateway_path="repl").knowledge_pipeline is not None

    for path, expected in (("repl", False), ("daemon", True), ("runtime", True)):
        agent = _Agent(gateway_path=path)
        agent._catalogue_chain(
            _Chain(), question="q", may_knowledge=True, may_source_registry=True,
        )
        assert agent.knowledge_pipeline.calls[0]["require_verified"] is expected, path


# ---------------------------------------------------------------------------
# The duplication is gone and must not return
# ---------------------------------------------------------------------------

def test_only_one_module_runs_the_knowledge_pipeline_for_a_turn():
    """The class, not the instance.

    Two copies became one; a third would be the same defect again. `ingestion`
    is excluded because it catalogues a document set on an operator command, not
    a turn's evidence chain — a different caller with a different lifetime.
    """
    callers: list[str] = []
    for path in sorted(pathlib.Path("core").glob("loop*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run"
                    and "knowledge_pipeline" in ast.unparse(node.func.value)):
                callers.append(f"{path.name}:{node.lineno}")

    assert callers == ["loop_evidence_chain.py:" + str(
        _catalogue_line()
    )], callers


def _catalogue_line() -> int:
    source, start = inspect.getsourcelines(
        AgentLoopEvidenceChain._catalogue_chain
    )
    for offset, line in enumerate(source):
        if "knowledge_pipeline.run(" in line:
            return start + offset
    raise AssertionError("the core no longer runs the pipeline")


def test_the_verify_path_calls_the_core_rather_than_repeating_it():
    src = inspect.getsource(
        __import__("core.loop_verify_replan", fromlist=["x"])
    )

    assert "_catalogue_chain(" in src
    assert "rank_chain(" not in src, "the ranking call came back"

# ---------------------------------------------------------------------------
# What each caller kept. One test per consequence, because "the core writes
# nothing" says nothing about whether the callers still do their own part.
# ---------------------------------------------------------------------------

def _evidence_chain_source() -> str:
    import inspect

    return inspect.getsource(
        AgentLoopEvidenceChain._rank_and_catalog_evidence
    )


def _verify_replan_source() -> str:
    import inspect

    from core.loop_verify_replan import AgentLoopVerifyReplan

    return inspect.getsource(AgentLoopVerifyReplan._verify_and_settle_answer)


def test_the_first_caller_still_skips_the_pipeline_on_the_cheap_path():
    """The cheap path must not have been folded into the core.

    It is the one consequence that would be invisible in a passing suite: the
    core would simply run, the answer would be right, and every turn flagged
    trivial would quietly pay for a knowledge-pipeline pass the operator
    complained about.
    """
    body = _evidence_chain_source()

    assert "if cheap_path_active:" in body
    cheap_branch = body[body.index("if cheap_path_active:"):body.index("else:")]
    assert "_catalogue_chain(" not in cheap_branch, (
        "the cheap path now runs the pipeline it exists to skip"
    )
    assert "knowledge_pipeline_skipped" in cheap_branch
    assert "rank_chain(" in cheap_branch, (
        "ranking must still happen — the event is what tells a reader the chain "
        "was empty rather than unexamined"
    )


def test_the_second_caller_still_stamps_phase_and_iteration():
    """Three events, each carrying which round of the fetch loop it came from.

    Without the stamps a reader cannot tell one iteration's registry from
    another's, and the fetch loop can run up to VERIFY_REPLAN_HARD_CAP times.
    """
    body = _verify_replan_source()
    after_core = body[body.index("_catalogue_chain("):]

    for event in ("source_ranking", "source_registry", "knowledge_pipeline"):
        assert f'"{event}"' in after_core, event
    assert after_core.count('"phase": "verify"') >= 3, after_core.count('"phase"')
    assert after_core.count('"iteration": verify_replan_attempt') >= 3


def test_the_second_caller_still_updates_the_run_state():
    """It writes into `st` as well as onto the agent — the core does neither."""
    body = _verify_replan_source()
    after_core = body[body.index("_catalogue_chain("):]

    assert "st.source_ranking = catalogued.ranking" in after_core
    assert "st.source_registry = knowledge_result.registry" in after_core
    assert "self.last_source_ranking = st.source_ranking" in after_core
    assert "self.last_source_registry = st.source_registry" in after_core


def test_the_quarantine_stayed_on_the_path_that_had_it():
    """Only the verify path quarantines, and it must still do so after the core.

    Moving it into the shared core would have made the evidence-chain caller
    quarantine too — a behaviour change smuggled in as deduplication, and the
    kind that shows up as memory disappearing for reasons nobody chose.
    """
    verify = _verify_replan_source()
    assert "_quarantine_conflicted_memory(knowledge_result)" in verify

    import inspect

    core_src = inspect.getsource(AgentLoopEvidenceChain._catalogue_chain)
    assert "_quarantine" not in core_src, "the quarantine leaked into the core"
    assert "_quarantine" not in _evidence_chain_source(), (
        "the first caller gained a quarantine it never had"
    )
