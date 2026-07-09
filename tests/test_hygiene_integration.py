"""End-to-end MVP-10 acceptance: hygiene methods through AgentLoop + JSONL.

Every method on `AgentLoop` (cleanup_backups, expire_persistent,
dedupe_persistent, summarise_persistent) MUST:
  - call the corresponding `core.hygiene` function with the right args
  - emit exactly ONE audit event with the report's `summary()` payload
  - never silently mutate disk when `dry_run=True`

The write-time dedup gate inside `agent.remember()` is also exercised
here because it crosses the AgentLoop / MemoryWritePolicy /
PersistentMemoryStore boundary.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.hygiene import SUMMARY_TAG
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM


def _events(log_path: Path) -> list[dict]:
    out: list[dict] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_agent(
    workspace: Path,
    *,
    canned_llm_responses: list[str] | None = None,
) -> tuple[AgentLoop, Path, PersistentMemoryStore, FakeLLM]:
    """Real AgentLoop, real PersistentMemoryStore, FakeLLM."""
    persistent_path = workspace / "data" / "memory.jsonl"
    store = PersistentMemoryStore(persistent_path)

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(WebSearchTool())
    registry.register(FileWriteTool(workspace_root=workspace))

    llm = FakeLLM(responses=list(canned_llm_responses or []))
    planner = LLMPlanner(llm=llm, registry=registry)

    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=planner,
        memory=WorkingMemory(),
        persistent_store=store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl", store, llm


# ===========================================================
# (a) Write-time dedup blocks `remember()` before disk
# ===========================================================

class TestRememberWriteDedup:
    def test_second_remember_of_same_fact_rejected(self, workspace: Path):
        agent, log_path, store, _ = _build_agent(workspace)

        # First call: saved.
        d1, rec1 = agent.remember(
            content="I prefer concise Russian answers and JSON output",
            tags=["preference", "fact"],
        )
        assert d1.decision == "save"
        assert rec1 is not None
        assert len(store.load()) == 1

        # Second call with the same content: rejected by dedup gate.
        d2, rec2 = agent.remember(
            content="I prefer concise Russian answers and JSON output",
            tags=["preference", "fact"],
        )
        assert d2.decision == "reject"
        assert rec2 is None
        assert any("duplicate of" in r for r in d2.reasons)
        # Still exactly one record on disk.
        assert len(store.load()) == 1

        # Audit trail: one `decision: save`, one `decision: reject`.
        events = _events(log_path)
        writes = [e for e in events if e["event"] == "persistent_memory_write"]
        assert [e["payload"]["decision"] for e in writes] == ["save", "reject"]
        assert any("duplicate of" in r for r in writes[1]["payload"]["reasons"])

    def test_first_party_owner_default_dedup_still_applies(
        self, workspace: Path
    ):
        agent, _, store, _ = _build_agent(workspace)
        a = agent.remember(content="The deploy script lives in ops/deploy.sh", tags=["fact"])
        b = agent.remember(content="The deploy script lives in ops/deploy.sh.", tags=["fact"])
        assert a[0].decision == "save"
        assert b[0].decision == "reject"
        assert len(store.load()) == 1


# ===========================================================
# (b) cleanup_backups emits `backup_cleanup` event
# ===========================================================

class TestBackupCleanupLoop:
    def _ts(self, days_ago: int) -> str:
        dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return dt.strftime("%Y%m%dT%H%M%SZ")

    def test_event_payload_matches_report(self, workspace: Path):
        agent, log_path, _, _ = _build_agent(workspace)

        # Set up 4 backups for one file: 3 recent + 1 old.
        for days in (0, 1, 2, 30):
            (workspace / f"doc.txt.bak.{self._ts(days)}").write_text("x", encoding="utf-8")

        rep = agent.cleanup_backups(workspace)
        assert len(rep.deleted) == 1

        events = _events(log_path)
        bak_events = [e for e in events if e["event"] == "backup_cleanup"]
        assert len(bak_events) == 1
        payload = bak_events[0]["payload"]
        assert payload["deleted_count"] == 1
        assert payload["scanned"] == 4
        assert payload["dry_run"] is False

    def test_dry_run_event_still_fires_no_files_deleted(self, workspace: Path):
        agent, log_path, _, _ = _build_agent(workspace)
        # 4 backups, would-delete 1.
        for days in (0, 1, 2, 30):
            (workspace / f"doc.txt.bak.{self._ts(days)}").write_text("x", encoding="utf-8")

        rep = agent.cleanup_backups(workspace, dry_run=True)
        assert rep.dry_run is True
        # The "old" file is still there.
        assert (workspace / f"doc.txt.bak.{self._ts(30)}").exists()
        events = _events(log_path)
        bak = next(e for e in events if e["event"] == "backup_cleanup")
        assert bak["payload"]["dry_run"] is True


# ===========================================================
# (c) expire_persistent emits `persistent_memory_expire` event
# ===========================================================

class TestExpireLoop:
    def test_expired_record_removed_with_event(self, workspace: Path):
        agent, log_path, store, _ = _build_agent(workspace)
        now = datetime.now(timezone.utc)

        # Direct disk write: simulate an already-expired record.
        stale = MemoryRecord(
            content="this record expires fast",
            tags=["fact"],
            owner="user",
            ttl_seconds=60,
            created_at=now - timedelta(hours=1),
        )
        fresh = MemoryRecord(
            content="this one is forever",
            tags=["fact"],
            owner="user",
            ttl_seconds=None,
        )
        store.save_many([stale, fresh])

        rep = agent.expire_persistent()
        assert rep.expired == [stale.id]

        events = _events(log_path)
        ev = next(e for e in events if e["event"] == "persistent_memory_expire")
        assert ev["payload"]["expired_count"] == 1
        assert ev["payload"]["expired_ids"] == [stale.id]

        # Disk: only the fresh one survives.
        assert [r.id for r in store.load()] == [fresh.id]

    def test_no_records_emits_zero_event(self, workspace: Path):
        agent, log_path, _, _ = _build_agent(workspace)
        rep = agent.expire_persistent()
        assert rep.expired == []
        events = _events(log_path)
        ev = next(e for e in events if e["event"] == "persistent_memory_expire")
        assert ev["payload"]["expired_count"] == 0

    def test_no_store_emits_event_with_zero_count(self, workspace: Path):
        # Build an agent without a persistent store wired.
        registry = ToolRegistry()
        registry.register(FileReadTool(workspace_root=workspace))
        llm = FakeLLM()
        trace_id = new_trace_id()
        logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
        agent = AgentLoop(
            registry=registry,
            policy=PolicyGate(registry),
            llm=llm,
            logger=logger,
            planner=LLMPlanner(llm=llm, registry=registry),
            persistent_store=None,
            max_replan_attempts=1,
        )

        rep = agent.expire_persistent()
        assert rep.expired == []
        events = _events(workspace / "logs" / f"{trace_id}.jsonl")
        assert any(e["event"] == "persistent_memory_expire" for e in events)


# ===========================================================
# (d) dedupe_persistent emits `persistent_memory_dedupe` event
# ===========================================================

class TestDedupeLoop:
    def test_post_hoc_dedupe_via_agent(self, workspace: Path):
        agent, log_path, store, _ = _build_agent(workspace)

        # Push two duplicates directly to disk so we can test the
        # post-hoc cleanup (write-time dedup would have blocked the
        # second one already).
        a = MemoryRecord(
            content="the project ships on Friday",
            tags=["fact"],
            owner="user",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        b = MemoryRecord(
            content="The project ships on Friday.",
            tags=["fact"],
            owner="user",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        store.save_many([a, b])

        rep = agent.dedupe_persistent()
        assert rep.deleted == [b.id]
        assert len(rep.groups) == 1
        assert rep.groups[0].canonical_id == a.id

        events = _events(log_path)
        ev = next(e for e in events if e["event"] == "persistent_memory_dedupe")
        assert ev["payload"]["deleted_count"] == 1
        # Disk now has the canonical only.
        assert [r.id for r in store.load()] == [a.id]


# ===========================================================
# (e) summarise_persistent emits `persistent_memory_summarise` event
# ===========================================================

class TestSummariseLoop:
    def test_two_records_merged_real_path(self, workspace: Path):
        # FakeLLM returns the canned summary on `complete()`. The agent
        # builds the prompt itself; we don't care about its shape here,
        # only that the merged record actually lands on disk.
        agent, log_path, store, llm = _build_agent(
            workspace,
            canned_llm_responses=["merged: ships Fri at noon; on-call rotates"],
        )

        a = MemoryRecord(
            content="The project ships on Friday at noon.",
            tags=["project"],
            owner="user",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        b = MemoryRecord(
            content="On-call rotates monthly; current rotation is Lena.",
            tags=["project"],
            owner="user",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        store.save_many([a, b])

        rep = agent.summarise_persistent(tag="project")
        assert rep.new_record_id is not None
        assert sorted(rep.summarised_ids) == sorted([a.id, b.id])

        # Disk: 1 record, contains the canned summary, carries both tags.
        survivors = store.load()
        assert len(survivors) == 1
        assert survivors[0].id == rep.new_record_id
        assert survivors[0].content == "merged: ships Fri at noon; on-call rotates"
        assert "project" in survivors[0].tags
        assert SUMMARY_TAG in survivors[0].tags

        # Audit event present + carries the canonical fields.
        events = _events(log_path)
        ev = next(e for e in events if e["event"] == "persistent_memory_summarise")
        assert ev["payload"]["tag"] == "project"
        assert ev["payload"]["summarised_count"] == 2
        assert ev["payload"]["new_record_id"] == rep.new_record_id

    def test_summary_is_then_findable_by_retrieval(self, workspace: Path):
        # The merged record must still surface via the retrieval policy
        # when the question mentions a fact from one of the originals.
        agent, _log, store, _ = _build_agent(
            workspace,
            canned_llm_responses=["The project ships on Friday at noon; on-call rotates monthly."],
        )

        a = MemoryRecord(
            content="The project ships on Friday at noon.",
            tags=["project"],
            owner="user",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        b = MemoryRecord(
            content="On-call rotates monthly.",
            tags=["project"],
            owner="user",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        store.save_many([a, b])

        agent.summarise_persistent(tag="project")

        # Retrieval should now find the merged record under the same
        # keywords that previously matched the originals.
        records = store.load()
        selected = agent.retrieval_policy.select(records, "project ships on Friday")
        assert len(selected) == 1
        assert selected[0].id == [r.id for r in records][0]


# ===========================================================
# (f) No-store guards — all four hygiene methods on a loop with
#     persistent_store=None must produce empty reports + audit
#     events without raising.
# ===========================================================

def _build_agent_no_store(workspace: Path) -> tuple[AgentLoop, Path]:
    """Build an AgentLoop with persistent_store=None on purpose."""
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))

    llm = FakeLLM(responses=[])
    planner = LLMPlanner(llm=llm, registry=registry)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=planner,
        memory=WorkingMemory(),
        persistent_store=None,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


class TestNoStoreGuards:
    def test_dedupe_without_store_returns_empty_report_and_logs(
        self, workspace: Path
    ):
        agent, log_path = _build_agent_no_store(workspace)
        rep = agent.dedupe_persistent()
        # Reports zero work.
        assert rep.deleted == []
        assert rep.groups == []
        assert rep.scanned == 0
        # Event recorded with summary payload.
        events = _events(log_path)
        assert any(e["event"] == "persistent_memory_dedupe" for e in events)

    def test_summarise_without_store_skipped_with_reason(self, workspace: Path):
        agent, log_path = _build_agent_no_store(workspace)
        rep = agent.summarise_persistent(tag="anything")
        assert rep.skipped_reason == "no store"
        events = _events(log_path)
        assert any(e["event"] == "persistent_memory_summarise" for e in events)

    def test_expire_without_store_returns_empty_report(self, workspace: Path):
        agent, log_path = _build_agent_no_store(workspace)
        rep = agent.expire_persistent()
        assert rep.expired == []
        assert rep.scanned == 0
        events = _events(log_path)
        assert any(e["event"] == "persistent_memory_expire" for e in events)

    def test_list_persistent_without_store_returns_empty(self, workspace: Path):
        agent, _ = _build_agent_no_store(workspace)
        assert agent.list_persistent() == []

    def test_forget_without_store_returns_zero(self, workspace: Path):
        agent, _ = _build_agent_no_store(workspace)
        assert agent.forget() == 0
        assert agent.forget("any-id") == 0
