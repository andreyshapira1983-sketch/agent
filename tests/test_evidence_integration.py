"""MVP-14.1 — Evidence chain integration through AgentLoop.

Tool-level branches of the factory are already covered by
`tests/test_evidence.py`. Here we pin the wiring inside `AgentLoop`:

  * a successful tool step adds an Evidence to `agent.last_provenance`;
  * a failed step adds none;
  * the `evidence_collected` JSONL event mirrors the chain;
  * memory-injected records become `kind=memory` evidence;
  * a 0-step plan still produces (an empty) chain — no crash;
  * file_write produces no evidence (it's an action, not a source).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.persistent_memory import PersistentMemoryStore
from core.policy import PolicyGate
from core.models import MemoryRecord
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM, FakePlanner


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_agent(
    workspace: Path,
    *,
    canned_sources: list[dict],
    persistent: bool = False,
) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace, timeout_seconds=5.0))
    reg.register(RunTestsTool(workspace_root=workspace, timeout_seconds=10.0))
    reg.register(ReadLogsTool(workspace_root=workspace))
    reg.register(DiffFileTool(workspace_root=workspace))

    for src in canned_sources:
        src.setdefault("expected_outcome", "executes the planned step")

    store = None
    if persistent:
        store_path = workspace / "persistent.jsonl"
        store = PersistentMemoryStore(path=store_path)

    llm = FakeLLM(responses=["[synthesised]"])
    planner = FakePlanner(canned_sources)
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        persistent_store=store,
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# A single tool step adds the expected Evidence
# ============================================================

class TestSingleStepEvidence:
    def test_file_read_adds_file_evidence(self, workspace: Path):
        target = workspace / "doc.txt"
        target.write_text("hello from the file", encoding="utf-8")
        agent, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        agent.run("what does the file say", file_hint="doc.txt")
        chain = agent.last_provenance
        files = chain.by_kind("file")
        assert len(files) == 1
        ev = files[0]
        assert ev.source_id == "file:doc.txt"
        assert ev.obtained_via == "file_read"
        assert "hello from the file" in ev.excerpt

    def test_diff_file_adds_diff_preview_evidence(self, workspace: Path):
        target = workspace / "m.py"
        target.write_text("X = 1\n", encoding="utf-8")
        agent, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "diff_file",
                "arguments": {"path": "m.py", "proposed_content": "X = 2\n"},
                "label": "diff_file:m.py",
            }],
        )
        agent.run("preview the change")
        ev = agent.last_provenance.by_kind("diff_preview")
        assert len(ev) == 1
        assert "m.py" in ev[0].source_id
        assert "+1 -1" in ev[0].claim


# ============================================================
# file_write is an action, not a source
# ============================================================

class TestFileWriteNoEvidence:
    def test_write_produces_no_evidence_in_chain(self, workspace: Path):
        agent, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_write",
                "arguments": {"path": "new.txt", "content": "hello"},
                "label": "file_write:new.txt",
            }],
        )
        agent.run("save a file")
        # Write succeeded (file exists on disk) ...
        assert (workspace / "new.txt").exists()
        # ... but Evidence chain stays empty — a write is not a source.
        assert len(agent.last_provenance) == 0


# ============================================================
# Failed steps add no evidence
# ============================================================

class TestFailedStepNoEvidence:
    def test_missing_file_no_evidence(self, workspace: Path):
        agent, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "does_not_exist.txt"},
                "label": "file:missing",
            }],
        )
        agent.run("read a missing file", file_hint="does_not_exist.txt")
        assert len(agent.last_provenance) == 0


# ============================================================
# evidence_collected JSONL event
# ============================================================

class TestEvidenceCollectedEvent:
    def test_event_emitted_once_per_run(self, workspace: Path):
        target = workspace / "a.txt"
        target.write_text("a", encoding="utf-8")
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "a.txt"},
                "label": "file:a.txt",
            }],
        )
        agent.run("read it", file_hint="a.txt")
        ev = _events(log_path)
        collected = [e for e in ev if e["event"] == "evidence_collected"]
        assert len(collected) == 1
        p = collected[0]["payload"]
        assert p["count"] == 1
        assert "file" in p["kinds"]
        # Chain shape: payload has compact records WITHOUT full excerpt.
        chain = p["chain"]
        assert isinstance(chain, list)
        assert len(chain) == 1
        assert "excerpt" not in chain[0]
        assert chain[0]["excerpt_len"] > 0
        assert chain[0]["kind"] == "file"

    def test_zero_step_plan_emits_empty_chain(self, workspace: Path):
        agent, log_path = _build_agent(workspace, canned_sources=[])
        agent.run("general-knowledge question")
        ev = _events(log_path)
        collected = [e for e in ev if e["event"] == "evidence_collected"]
        assert len(collected) == 1
        assert collected[0]["payload"]["count"] == 0
        assert collected[0]["payload"]["chain"] == []

    def test_source_ranking_event_mirrors_chain(self, workspace: Path):
        target = workspace / "rank.txt"
        target.write_text("rank me", encoding="utf-8")
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "rank.txt"},
                "label": "file:rank.txt",
            }],
        )
        agent.run("what is in the file", file_hint="rank.txt")
        assert agent.last_source_ranking is not None
        assert agent.last_source_ranking.best is not None
        assert agent.last_source_ranking.best.kind == "file"
        ev = _events(log_path)
        ranked = [e for e in ev if e["event"] == "source_ranking"]
        assert len(ranked) == 1
        payload = ranked[0]["payload"]
        assert payload["count"] == 1
        assert payload["best"]["kind"] == "file"
        assert payload["support_counts"]["direct"] == 1

    def test_multi_step_chain_preserves_order(self, workspace: Path):
        (workspace / "a.txt").write_text("first", encoding="utf-8")
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[
                {"tool": "file_read", "arguments": {"path": "a.txt"},
                 "label": "file:a.txt"},
                {"tool": "diff_file",
                 "arguments": {"path": "a.txt", "proposed_content": "second"},
                 "label": "diff_file:a.txt"},
            ],
        )
        agent.run("read then preview", file_hint="a.txt")
        ev = _events(log_path)
        # AgentLoop iterates ALL steps of a successful attempt before
        # break-ing. Evidence is appended in step order — file_read
        # first, diff_file second. Order matters for the Verifier:
        # earlier evidence is treated as primary.
        collected = [e for e in ev if e["event"] == "evidence_collected"]
        chain = collected[0]["payload"]["chain"]
        assert len(chain) == 2
        assert chain[0]["kind"] == "file"
        assert chain[1]["kind"] == "diff_preview"


# ============================================================
# Memory records → Evidence
# ============================================================

class TestMemoryToEvidence:
    def test_persistent_record_becomes_memory_evidence(self, workspace: Path):
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "a.txt"},
                "label": "file:a.txt",
            }],
            persistent=True,
        )
        # Seed the persistent store with a record whose content overlaps
        # with our question.
        rec = MemoryRecord(
            type="working",
            content="The capital of France is Paris.",
            source="user",
            tags=["user-approved"],
            ttl_seconds=None,
        )
        assert agent.persistent_store is not None
        agent.persistent_store.save_many([rec])
        (workspace / "a.txt").write_text("dummy", encoding="utf-8")
        agent.run("What is the capital of France?", file_hint="a.txt")

        mem_evs = agent.last_provenance.by_kind("memory")
        assert len(mem_evs) >= 1
        ev = mem_evs[0]
        assert rec.id in ev.source_id
        assert "Paris" in ev.excerpt


# ============================================================
# Chain is exposed on the agent object
# ============================================================

class TestAgentAttribute:
    def test_last_provenance_initialised_empty(self, workspace: Path):
        agent, _ = _build_agent(workspace, canned_sources=[])
        # Before run() is called, the chain is an empty ProvenanceChain.
        assert len(agent.last_provenance) == 0

    def test_last_provenance_updated_after_run(self, workspace: Path):
        (workspace / "a.txt").write_text("content", encoding="utf-8")
        agent, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "a.txt"},
                "label": "file:a.txt",
            }],
        )
        agent.run("read", file_hint="a.txt")
        # P0 lives only inside verifier; agent.last_provenance still
        # only carries external sources gathered by the planner.
        assert len(agent.last_provenance.by_kind("file")) == 1
        assert len(agent.last_provenance) == 1

    def test_last_provenance_resets_between_runs(self, workspace: Path):
        (workspace / "a.txt").write_text("alpha", encoding="utf-8")
        (workspace / "b.txt").write_text("beta", encoding="utf-8")
        agent, _ = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "a.txt"},
                "label": "file:a.txt",
            }],
        )
        agent.run("first", file_hint="a.txt")
        first_id = agent.last_provenance.evidences[0].id

        # Swap the planner's sources for a second run (FakePlanner has a
        # mutable list).
        agent.planner.sources = [{
            "tool": "file_read",
            "arguments": {"path": "b.txt"},
            "label": "file:b.txt",
            "expected_outcome": "ok",
        }]
        agent.run("second", file_hint="b.txt")
        # New chain — different evidence id.
        assert agent.last_provenance.evidences[0].id != first_id
        assert "b.txt" in agent.last_provenance.evidences[0].source_id


# ============================================================
# Robustness: factory failure must not abort run()
# ============================================================

class TestFactoryRobustness:
    def test_unknown_tool_in_registry_still_produces_chain(self, workspace: Path):
        """A tool the factory doesn't have an explicit branch for falls
        back to `tool_output` kind. The loop continues normally."""
        from tools.base import Risk, Tool

        class _FakeTool(Tool):
            name = "fake_tool"
            description = "test only"
            risk: Risk = "read_only"

            def __init__(self):
                pass

            def risk_for(self, arguments):
                return "read_only"

            def run(self, **kwargs):
                return {"data": "some result"}

            def validate_output(self, output):
                return True, []

        reg = ToolRegistry()
        reg.register(FileReadTool(workspace_root=workspace))
        reg.register(WebSearchTool())
        reg.register(FileWriteTool(workspace_root=workspace))
        reg.register(ShellExecTool(workspace_root=workspace, timeout_seconds=5))
        reg.register(_FakeTool())
        llm = FakeLLM(responses=["[ok]"])
        planner = FakePlanner([{
            "tool": "fake_tool",
            "arguments": {"x": 1},
            "label": "fake:1",
            "expected_outcome": "ok",
        }])
        trace_id = new_trace_id()
        logger = TraceLogger(
            trace_id=trace_id, log_dir=workspace / "logs", verbose=False
        )
        agent = AgentLoop(
            registry=reg,
            policy=PolicyGate(reg),
            llm=llm,
            logger=logger,
            planner=planner,
            approval_provider=AutoApprover(default="approve"),
            max_replan_attempts=1,
        )
        agent.run("use the fake tool")
        # Factory's fallback turned it into a tool_output evidence.
        kinds = {ev.kind for ev in agent.last_provenance.evidences}
        assert "tool_output" in kinds


# ============================================================
# MIR-061 — one malformed record must not drop the rest
# ============================================================

class TestMalformedMemoryRecordDoesNotTruncateChain:
    """`core/loop.py` persistent-record injection wraps the whole `for` loop.

    One record that fails to convert therefore abandons the loop and every
    record after it silently never reaches the provenance chain — while the
    code comment claims "A malformed record stays out of the chain but the
    loop completes normally". The adjacent working-artifact site does it
    correctly (try inside the loop).
    """

    def test_one_bad_record_only_skips_itself(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "a.txt"},
                "label": "file:a.txt",
            }],
            persistent=True,
        )
        assert agent.persistent_store is not None
        recs = [
            MemoryRecord(
                type="working",
                content=f"The capital of France is Paris, note {n}.",
                source="user",
                tags=["user-approved"],
                ttl_seconds=None,
            )
            for n in range(4)
        ]
        agent.persistent_store.save_many(recs)
        (workspace / "a.txt").write_text("dummy", encoding="utf-8")

        # Fail the SECOND record the injection loop hands over, whatever it
        # is. Scoped deliberately to what the loop actually receives
        # (`_last_persistent_records`): retrieval selects a subset by
        # relevance, and a record that was never selected is not evidence of
        # truncation. The defect is only about records that reached the loop.
        import core.loop as loop_mod

        real = loop_mod.evidence_from_memory_record
        seen: list[str] = []
        bad: dict[str, str] = {}

        def flaky(*, record_id: str, content: str, source, created_at):
            seen.append(record_id)
            if len(seen) == 2:
                bad["id"] = record_id
                raise ValueError("malformed record")
            return real(
                record_id=record_id,
                content=content,
                source=source,
                created_at=created_at,
            )

        monkeypatch.setattr(loop_mod, "evidence_from_memory_record", flaky)
        agent.run("What is the capital of France?", file_hint="a.txt")

        handed = [r.id for r in agent._last_persistent_records]
        assert len(handed) >= 3, (
            "test precondition: retrieval must hand the loop at least three "
            f"records for truncation to be observable, got {len(handed)}"
        )
        assert bad.get("id"), "the flaky factory never raised"

        mem_ids = {
            ev.source_id
            for ev in agent.last_provenance.by_kind("memory")
            if ev.obtained_via == "memory"
        }
        skipped = [rid for rid in handed if f"memory:{rid}" not in mem_ids]

        assert skipped == [bad["id"]], (
            "only the malformed record may be dropped, but "
            f"{len(skipped)} of {len(handed)} records handed to the injection "
            "loop never reached the chain — the loop was abandoned"
        )

    def test_skipped_record_is_reported(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A dropped record must leave a trace; a bare `pass` leaves none."""
        agent, log_path = _build_agent(
            workspace,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "a.txt"},
                "label": "file:a.txt",
            }],
            persistent=True,
        )
        assert agent.persistent_store is not None
        rec = MemoryRecord(
            type="working",
            content="The capital of France is Paris.",
            source="user",
            tags=["user-approved"],
            ttl_seconds=None,
        )
        agent.persistent_store.save_many([rec])
        (workspace / "a.txt").write_text("dummy", encoding="utf-8")

        import core.loop as loop_mod

        def always_bad(*, record_id: str, content: str, source, created_at):
            raise ValueError("malformed record")

        monkeypatch.setattr(loop_mod, "evidence_from_memory_record", always_bad)
        agent.run("What is the capital of France?", file_hint="a.txt")

        events = _events(log_path)
        assert any(
            e.get("event") == "memory_evidence_skipped" for e in events
        ), "a record dropped from the chain must be logged, not swallowed"
