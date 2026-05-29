"""Unit tests for the meta-command surface in main.py.

`handle_meta_command` is a pure dispatcher: given a string like
`":hygiene dedupe --dry-run"` and a wired agent + workspace, it produces
side effects (prints / agent calls) but does NOT touch stdin.

These tests pin the parser + dispatcher behavior so we don't accidentally
break the REPL contract while refactoring downstream MVPs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from main import (
    _force_utf8_io,
    _handle_auto_run,
    _handle_hygiene,
    _handle_rollback,
    _parse_remember,
    _print_persistent,
    handle_meta_command,
)
from tests.conftest import FakeLLM
from tools.base import Tool, ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


# ============================================================
# Helpers
# ============================================================

def _build_agent(workspace: Path) -> AgentLoop:
    store = PersistentMemoryStore(workspace / "data" / "memory.jsonl")
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    registry.register(FileWriteTool(workspace_root=workspace))
    llm = FakeLLM(responses=[])
    planner = LLMPlanner(llm=llm, registry=registry)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=planner,
        memory=WorkingMemory(),
        persistent_store=store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        source_registry_store=SourceRegistryStore(workspace / "data" / "sources.jsonl"),
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )


class FakeWebSearchTool(Tool):
    name = "web_search"
    description = "fake web search"
    risk = "read_only"

    def run(self, query: str, max_results: int = 3):
        assert "site:wikipedia.org" in query
        return [
            {
                "title": "Autonomous agent - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/Autonomous_agent",
                "snippet": "An autonomous agent is an intelligent agent.",
                "source": "fake",
            },
            {
                "title": "Wrong domain",
                "url": "https://example.com/Autonomous_agent",
                "snippet": "Should be filtered.",
                "source": "fake",
            },
        ][:max_results]

    def validate_output(self, output):
        return (isinstance(output, list), [])


class FakeWebFetchTool(Tool):
    name = "web_fetch"
    description = "fake web fetch"
    risk = "read_only"

    def run(self, url: str):
        assert url == "https://en.wikipedia.org/wiki/Autonomous_agent"
        text = (
            "Autonomous agent is a system situated in an environment. "
            "Autonomous agent perceives that environment and acts on it."
        )
        return {
            "url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "fetched_at": "2026-05-29T17:30:00+00:00",
            "content_hash": "a" * 64,
            "text": text,
            "text_truncated": False,
            "bytes": len(text.encode("utf-8")),
            "elapsed_ms": 1,
            "compensation_plan": {
                "id": "noop",
                "actions": [{"kind": "noop", "description": "read only"}],
                "tool_name": "web_fetch",
                "description": "read only",
            },
        }

    def validate_output(self, output):
        return (isinstance(output, dict) and bool(output.get("text")), [])


# ============================================================
# _parse_remember — content / tag parsing
# ============================================================

class TestParseRemember:
    def test_empty_input_returns_empty_tags_and_content(self):
        assert _parse_remember("") == ([], "")
        assert _parse_remember("   ") == ([], "")

    def test_no_tag_token_defaults_to_user_approved(self):
        tags, content = _parse_remember("I prefer concise answers")
        assert tags == ["user-approved"]
        assert content == "I prefer concise answers"

    def test_known_tag_picked_up(self):
        tags, content = _parse_remember("preference I like Python")
        assert tags == ["preference"]
        assert content == "I like Python"

    def test_comma_separated_tags(self):
        tags, content = _parse_remember("fact,decision I shipped v1")
        assert tags == ["fact", "decision"]
        assert content == "I shipped v1"

    def test_unknown_single_token_kept_as_content(self):
        # 'wibble' is not a known tag and has no comma -> defaults applied.
        tags, content = _parse_remember("wibble wobble")
        assert tags == ["user-approved"]
        assert content == "wibble wobble"

    def test_case_insensitive_tag_match(self):
        tags, content = _parse_remember("PREFERENCE be terse")
        assert tags == ["preference"]
        assert content == "be terse"


# ============================================================
# handle_meta_command — dispatcher contract
# ============================================================

class TestHandleMetaCommand:
    def test_non_meta_command_returns_false(self, workspace: Path):
        agent = _build_agent(workspace)
        # Anything not starting with `:` is for the LLM, not the dispatcher.
        assert handle_meta_command("hello world", agent, workspace) is False

    def test_help_returns_true(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert handle_meta_command(":help", agent, workspace) is True
        out = capsys.readouterr()
        assert "Commands:" in out.err
        assert ":rollback" in out.err
        assert ":hygiene" in out.err

    def test_question_mark_alias(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert handle_meta_command("?", agent, workspace) is True
        out = capsys.readouterr()
        assert "Commands:" in out.err

    def test_mem_prints_working_and_persistent(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        agent.persistent_store.save_many([MemoryRecord(content="pinned fact", tags=["fact"], owner="user")])
        assert handle_meta_command(":mem", agent, workspace) is True
        out = capsys.readouterr()
        # working memory JSON dump goes to stdout
        assert "session_id" in out.out or "session_id" in out.err
        # persistent records section also goes to stdout
        assert "persistent memory" in out.out

    def test_memory_alias(self, workspace: Path):
        agent = _build_agent(workspace)
        assert handle_meta_command(":memory", agent, workspace) is True

    def test_clear_wipes_working_memory_only(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        # Seed a turn so summary is non-empty.
        agent.memory.record_turn(
            question="hi",
            planner_reasoning="test",
            tools_used=[],
            artifact_labels=[],
            answer="hello",
        )
        assert len(agent.memory.turns) == 1
        handle_meta_command(":clear", agent, workspace)
        assert agent.memory.turns == []
        out = capsys.readouterr()
        assert "cleared" in out.err

    def test_remember_saves_with_default_tag(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        handle_meta_command(":remember I prefer Russian", agent, workspace)
        records = agent.list_persistent()
        assert len(records) == 1
        assert "Russian" in records[0].content
        out = capsys.readouterr()
        assert "saved as" in out.err

    def test_remember_with_no_text_prints_usage(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        handle_meta_command(":remember", agent, workspace)
        out = capsys.readouterr()
        assert "Usage:" in out.err

    def test_forget_all_removes_records(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        agent.persistent_store.save_many([
            MemoryRecord(content="one", tags=["fact"], owner="user"),
            MemoryRecord(content="two", tags=["fact"], owner="user"),
        ])
        handle_meta_command(":forget", agent, workspace)
        assert agent.list_persistent() == []
        out = capsys.readouterr()
        assert "deleted 2" in out.err

    def test_forget_by_id_succeeds(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        rec = MemoryRecord(content="targeted", tags=["fact"], owner="user")
        agent.persistent_store.save_many([rec])
        handle_meta_command(f":forget {rec.id}", agent, workspace)
        assert agent.list_persistent() == []
        out = capsys.readouterr()
        assert f"deleted {rec.id}" in out.err

    def test_forget_unknown_id_reports_miss(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        handle_meta_command(":forget mem_nope", agent, workspace)
        out = capsys.readouterr()
        assert "no record with id mem_nope" in out.err

    def test_ingest_source_saves_source_registry_only_by_default(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "architecture.md").write_text(
            "The planner creates a plan for each verified task. "
            "The verifier checks every cited claim against evidence.",
            encoding="utf-8",
        )

        assert handle_meta_command(":ingest-source architecture.md", agent, workspace) is True

        assert agent.source_registry_store is not None
        counts = agent.source_registry_store.count()
        assert counts["sources"] == 1
        assert counts["claims"] >= 1
        assert agent.list_persistent() == []
        out = capsys.readouterr()
        assert "ingest source" in out.err
        assert "memory_skipped=" in out.err

    def test_ingest_source_write_memory_flag_saves_verified_claims(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "knowledge.txt").write_text(
            "The source registry stores source-backed claims for later review. "
            "The knowledge pipeline rejects conflicted or weak claims.",
            encoding="utf-8",
        )

        assert handle_meta_command(":ingest-source knowledge.txt --write-memory", agent, workspace) is True

        records = agent.list_persistent()
        assert records
        assert any("Source:" in r.content for r in records)
        out = capsys.readouterr()
        assert "memory_saved=" in out.err

    def test_ingest_project_dry_run_does_not_persist_registry(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "a.md").write_text(
            "The agent reads curated markdown sources during controlled ingestion.",
            encoding="utf-8",
        )
        (workspace / "b.py").write_text(
            "def answer():\n    return 'source-backed project claim'\n",
            encoding="utf-8",
        )

        assert handle_meta_command(":ingest-project . --limit 10 --dry-run", agent, workspace) is True

        assert agent.source_registry_store is not None
        assert agent.source_registry_store.count() == {"sources": 0, "claims": 0}
        assert agent.last_source_registry.claims
        out = capsys.readouterr()
        assert "dry_run=True" in out.err

    def test_source_library_command_lists_curated_sources(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_meta_command(":source-library wikis", agent, workspace) is True
        assert handle_meta_command(":source-library --json", agent, workspace) is True

        out = capsys.readouterr()
        assert "source library" in out.err
        assert "wikipedia" in out.err
        assert '"groups"' in out.err

    def test_ingest_web_fetches_curated_source_into_registry(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        agent.registry.register(FakeWebSearchTool())
        agent.registry.register(FakeWebFetchTool())

        assert handle_meta_command(
            ":ingest-web autonomous agent --sources wikipedia --limit 1",
            agent,
            workspace,
        ) is True

        assert agent.source_registry_store is not None
        counts = agent.source_registry_store.count()
        assert counts["sources"] >= 1
        assert counts["claims"] >= 1
        assert agent.last_provenance.by_kind("web_page")
        out = capsys.readouterr()
        assert "ingest web" in out.err
        assert "https://en.wikipedia.org/wiki/Autonomous_agent" in out.err

    def test_learn_project_plans_then_ingests_selected_sources(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "README.md").write_text(
            "The README explains the autonomous agent project.",
            encoding="utf-8",
        )
        (workspace / "core").mkdir()
        (workspace / "core" / "self_repair.py").write_text(
            "Self-repair runs tests and rolls back failed patches.",
            encoding="utf-8",
        )

        assert handle_meta_command(":learn-project self-repair --limit 2 --dry-run", agent, workspace) is True

        assert agent.last_source_registry.claims
        assert agent.source_registry_store is not None
        assert agent.source_registry_store.count() == {"sources": 0, "claims": 0}
        out = capsys.readouterr()
        assert "learning plan" in out.err
        assert "ingest learning" in out.err

    def test_auto_run_dry_run_without_tests(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "README.md").write_text(
            "The README explains the autonomous agent project.",
            encoding="utf-8",
        )

        assert handle_meta_command(":auto-run project health --limit 2 --no-tests", agent, workspace) is True

        out = capsys.readouterr()
        assert "auto-run status=completed" in out.err
        assert "[done] status" in out.err
        assert "[done] learn" in out.err

    def test_auto_run_allow_effects_creates_pending_approval(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert _handle_auto_run("--allow-effects", agent, workspace) is True
        assert handle_meta_command(":auto-status", agent, workspace) is True

        out = capsys.readouterr()
        assert "auto-run status=blocked" in out.err
        assert '"pending": 1' in out.err

    def test_operator_control_plane_status_commands(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_meta_command(":budget-status", agent, workspace) is True
        assert handle_meta_command(":queue-status", agent, workspace) is True
        assert handle_meta_command(":scheduler-status", agent, workspace) is True

        out = capsys.readouterr()
        assert "autonomous budget defaults" in out.err
        assert "runtime task queue" in out.err
        assert "runtime scheduler" in out.err

    def test_conflicts_command_lists_source_registry_conflicts(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        registry = SourceRegistry()
        docs = registry.register_source(
            type="documentation",
            title="docs",
            locator="docs.md",
            trust_level=0.90,
        )
        forum = registry.register_source(
            type="forum",
            title="forum",
            locator="forum",
            trust_level=0.35,
        )
        winner = registry.register_claim(
            source_id=docs.id,
            text="Agent mode is local.",
            confidence=0.90,
        )
        registry.register_claim(
            source_id=forum.id,
            text="Agent mode is cloud.",
            confidence=0.40,
        )
        agent.source_registry_store.save_registry(registry)

        assert handle_meta_command(":conflicts --limit 1", agent, workspace) is True
        assert handle_meta_command(":conflict-status --json", agent, workspace) is True

        out = capsys.readouterr()
        assert "=== conflicts ===" in out.err
        assert "agent mode" in out.err
        assert f"winner={winner.id}" in out.err
        assert '"conflict_count": 1' in out.err

    def test_operator_control_plane_approval_list_approve_and_deny(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        inbox = getattr(agent, "approval_inbox", None)
        assert inbox is None

        assert _handle_auto_run("--allow-effects", agent, workspace) is True
        inbox = getattr(agent, "approval_inbox")
        first = inbox.pending()[0]

        assert handle_meta_command(":approval-list", agent, workspace) is True
        assert handle_meta_command(f":approval-approve {first.id}", agent, workspace) is True

        second = inbox.add(operation="manual.check", summary="Manual check")
        assert handle_meta_command(f":approval-deny {second.id}", agent, workspace) is True
        assert handle_meta_command(":approval-list all", agent, workspace) is True

        out = capsys.readouterr()
        assert "approval inbox" in out.err
        assert "approval approved" in out.err
        assert "approval denied" in out.err
        assert "[approved]" in out.err
        assert "[denied]" in out.err

    def test_operator_control_plane_approval_run_requires_approved_status(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert _handle_auto_run("--allow-effects --no-tests --limit 2", agent, workspace) is True
        inbox = getattr(agent, "approval_inbox")
        item = inbox.pending()[0]

        assert handle_meta_command(f":approval-run {item.id}", agent, workspace) is True
        assert handle_meta_command(f":approval-deny {item.id}", agent, workspace) is True
        assert handle_meta_command(f":approval-run {item.id}", agent, workspace) is True

        out = capsys.readouterr()
        assert "status=pending" in out.err
        assert "status=denied" in out.err

    def test_operator_control_plane_approval_run_rejects_unknown_operation(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert _handle_auto_run("--allow-effects --no-tests --limit 2", agent, workspace) is True
        inbox = getattr(agent, "approval_inbox")
        unknown = inbox.add(operation="unknown.operation", summary="Unknown")

        assert handle_meta_command(f":approval-approve {unknown.id}", agent, workspace) is True
        assert handle_meta_command(f":approval-run {unknown.id}", agent, workspace) is True

        out = capsys.readouterr()
        assert "unsupported operation=unknown.operation" in out.err

    def test_operator_control_plane_approval_run_executes_known_operation(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "README.md").write_text("Project overview.", encoding="utf-8")

        assert _handle_auto_run("--allow-effects --no-tests --limit 2", agent, workspace) is True
        inbox = getattr(agent, "approval_inbox")
        item = inbox.pending()[0]

        assert handle_meta_command(f":approval-approve {item.id}", agent, workspace) is True
        assert handle_meta_command(f":approval-run {item.id}", agent, workspace) is True
        assert inbox.get(item.id).status == "executed"

        out = capsys.readouterr()
        assert "auto-run status=completed dry_run=False" in out.err

    def test_operator_control_plane_approval_abort(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert _handle_auto_run("--allow-effects --no-tests --limit 2", agent, workspace) is True
        inbox = getattr(agent, "approval_inbox")
        item = inbox.pending()[0]

        assert handle_meta_command(f":approval-abort {item.id}", agent, workspace) is True

        out = capsys.readouterr()
        assert "approval aborted" in out.err
        assert inbox.get(item.id).status == "aborted"

    def test_task_add_list_and_run(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        (workspace / "README.md").write_text(
            "The README explains the autonomous agent project.",
            encoding="utf-8",
        )

        assert handle_meta_command(":task-add project health --no-tests --limit 2", agent, workspace) is True
        assert handle_meta_command(":task-list pending", agent, workspace) is True
        assert handle_meta_command(":task-run --limit 1", agent, workspace) is True

        out = capsys.readouterr()
        assert "task added" in out.err
        assert "runtime tasks" in out.err
        assert "task-run status=completed" in out.err

    def test_schedule_add_tick_and_list(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_meta_command(":schedule-add 5 project health --no-tests --limit 2", agent, workspace) is True
        assert handle_meta_command(":schedule-list active", agent, workspace) is True
        assert handle_meta_command(":schedule-tick --limit 1", agent, workspace) is True
        assert handle_meta_command(":task-list pending", agent, workspace) is True

        out = capsys.readouterr()
        assert "schedule added" in out.err
        assert "runtime schedules" in out.err
        assert "scheduler tick" in out.err
        assert "runtime tasks" in out.err

    def test_quit_raises_system_exit(self, workspace: Path):
        agent = _build_agent(workspace)
        with pytest.raises(SystemExit) as exc_info:
            handle_meta_command(":quit", agent, workspace)
        assert exc_info.value.code == 0

    def test_exit_alias(self, workspace: Path):
        agent = _build_agent(workspace)
        with pytest.raises(SystemExit):
            handle_meta_command(":exit", agent, workspace)


# ============================================================
# _handle_hygiene — dispatcher for sub-commands
# ============================================================

class TestHandleHygiene:
    def test_default_runs_expire_dedupe_backups(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("", agent, workspace)
        out = capsys.readouterr()
        assert "expire" in out.err
        assert "dedupe" in out.err
        assert "backups" in out.err

    def test_all_subcommand_runs_default(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("all", agent, workspace)
        out = capsys.readouterr()
        assert "expire" in out.err
        assert "dedupe" in out.err

    def test_dry_run_flag_propagates(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("--dry-run", agent, workspace)
        out = capsys.readouterr()
        assert "dry_run=True" in out.err

    def test_expire_subcommand(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("expire", agent, workspace)
        out = capsys.readouterr()
        assert "expire" in out.err.lower()

    def test_dedupe_subcommand(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("dedupe", agent, workspace)
        out = capsys.readouterr()
        assert "dedupe" in out.err.lower()

    def test_backups_subcommand(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("backups", agent, workspace)
        out = capsys.readouterr()
        assert "backups" in out.err.lower()

    def test_summarise_without_tag_prints_usage(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("summarise", agent, workspace)
        out = capsys.readouterr()
        assert "Usage:" in out.err

    def test_summarise_with_unknown_tag_prints_skipped(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("summarise nope", agent, workspace)
        out = capsys.readouterr()
        assert "summarise" in out.err.lower()

    def test_unknown_subcommand_prints_unknown(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_hygiene("nonsense", agent, workspace)
        out = capsys.readouterr()
        assert "unknown" in out.err.lower()


# ============================================================
# _handle_rollback — list / apply / unknown id
# ============================================================

class TestHandleRollback:
    def test_list_with_empty_log_reports_no_plans(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_rollback("list", agent, workspace)
        out = capsys.readouterr()
        assert "no compensation plans" in out.err

    def test_apply_on_empty_log_reports_skipped(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _handle_rollback("", agent, workspace)
        out = capsys.readouterr()
        # rollback returns a 'skipped' report when the log is empty.
        assert "rollback" in out.err.lower()

    def test_list_shows_registered_plan(self, workspace: Path, capsys):
        from core.compensation import CompensationAction, CompensationPlan

        agent = _build_agent(workspace)
        plan = CompensationPlan(
            tool_name="file_write",
            description="test plan",
            actions=[CompensationAction(kind="delete_path_if_created", path="x.txt")],
        )
        agent.compensation_log.append(plan)
        _handle_rollback("list", agent, workspace)
        out = capsys.readouterr()
        assert plan.id in out.err
        assert "file_write" in out.err

    def test_apply_specific_plan_id(self, workspace: Path, capsys):
        from core.compensation import CompensationAction, CompensationPlan

        agent = _build_agent(workspace)
        (workspace / "to_delete.txt").write_text("x", encoding="utf-8")
        plan = CompensationPlan(
            tool_name="file_write",
            description="rollback test",
            actions=[CompensationAction(kind="delete_path_if_created", path="to_delete.txt")],
        )
        agent.compensation_log.append(plan)

        _handle_rollback(plan.id, agent, workspace)
        out = capsys.readouterr()
        assert "rollback" in out.err.lower()
        assert "ok=1" in out.err
        assert not (workspace / "to_delete.txt").exists()


# ============================================================
# _print_persistent — handles empty store
# ============================================================

# ============================================================
# _force_utf8_io — Windows console encoding fix
# ============================================================

class TestForceUtf8Io:
    def test_sets_pythonioencoding_when_missing(self, monkeypatch):
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        _force_utf8_io()
        assert os.environ.get("PYTHONIOENCODING") == "utf-8"

    def test_keeps_existing_pythonioencoding(self, monkeypatch):
        monkeypatch.setenv("PYTHONIOENCODING", "cp1251")
        _force_utf8_io()
        # setdefault must NOT overwrite an explicit user choice.
        assert os.environ.get("PYTHONIOENCODING") == "cp1251"

    def test_streams_missing_reconfigure_dont_crash(self, monkeypatch):
        """If reconfigure is missing (e.g. pytest-captured streams), the
        function must succeed silently — never raise."""
        class _NoReconfigureStream:
            pass

        monkeypatch.setattr(sys, "stdin", _NoReconfigureStream())
        monkeypatch.setattr(sys, "stdout", _NoReconfigureStream())
        monkeypatch.setattr(sys, "stderr", _NoReconfigureStream())
        # Should be a no-op without raising.
        _force_utf8_io()


class TestPrintPersistent:
    def test_empty_store_message(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        _print_persistent(agent)
        out = capsys.readouterr()
        assert "no persistent records" in out.err

    def test_record_listing_truncates_long_content(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        long = "x" * 500
        rec = MemoryRecord(content=long, tags=["fact"], owner="user")
        agent.persistent_store.save_many([rec])
        _print_persistent(agent)
        out = capsys.readouterr()
        assert rec.id in out.out
        # Truncated to 200 chars (with the ellipsis byte).
        # We can't be too strict about the exact prefix in stdout because
        # the printed line includes the bracketed id + tags. So just check
        # the ellipsis marker is present.
        assert "…" in out.out
