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


def test_it_writes_nothing_to_the_journal_or_to_the_agent():
    """The whole reason it can be shared.

    The two callers log different payloads and store into different places. A
    core that did either would have to be told which caller it is, and that is
    the mode flag this extraction exists to avoid.
    """
    agent = _Agent()
    agent.log = object()  # any use would raise AttributeError

    agent._catalogue_chain(
        _Chain(), question="q", may_knowledge=True, may_source_registry=True,
    )

    assert not hasattr(agent, "last_source_ranking")
    assert not hasattr(agent, "last_source_registry")
    assert not hasattr(agent, "last_knowledge_pipeline")


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
