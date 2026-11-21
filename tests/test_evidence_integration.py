"""MVP-14.1 — Evidence chain integration through AgentLoop.

Tool-level branches of the factory are already covered by
`tests/test_evidence.py`. Here we pin the wiring inside `AgentLoop`:

  * a successful tool step adds an Evidence to `agent.last_provenance`;
  * a failed step adds none;
  * the `evidence_collected` JSONL event mirrors the chain;
  * memory-injected records become `kind=memory` evidence;
  * a 0-step plan still produces (an empty) chain — no crash;
  * file_write leaves a receipt of the call (tool_output), not a `file` evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool


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
        agent, _log_path = _build_agent(
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

        # Fail the SECOND record the injection loop hands over, whatever it is.
        # Scoped to what the loop actually receives (`_last_persistent_records`):
        # retrieval selects a subset by relevance, and a record that was never
        # selected is not evidence of truncation. The defect is only about
        # records that reached the loop.
        # Цель подмены — модуль, в чьих globals имя РАЗРЕШАЕТСЯ. Досборка
        # цепочки уехала из `core/loop.py` в свой модуль (раскол, кусок 4),
        # и фабрика улик уехала вместе с ней.
        import core.loop_evidence_chain as loop_mod

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

        # Тот же переезд, что и выше: имя живёт в модуле досборки цепочки.
        import core.loop_evidence_chain as loop_mod

        def always_bad(*, record_id: str, content: str, source, created_at):
            raise ValueError("malformed record")

        monkeypatch.setattr(loop_mod, "evidence_from_memory_record", always_bad)
        agent.run("What is the capital of France?", file_hint="a.txt")

        events = _events(log_path)
        assert any(
            e.get("event") == "memory_evidence_skipped" for e in events
        ), "a record dropped from the chain must be logged, not swallowed"
