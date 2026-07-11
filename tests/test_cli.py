"""Unit tests for the meta-command surface in main.py.

`handle_meta_command` is a pure dispatcher: given a string like
`":hygiene dedupe --dry-run"` and a wired agent + workspace, it produces
side effects (prints / agent calls) but does NOT touch stdin.

These tests pin the parser + dispatcher behavior so we don't accidentally
break the REPL contract while refactoring downstream MVPs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import main as main_module
from core.approval import AutoApprover
from core.budget_ledger import BudgetLedger, BudgetWindow
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger
from core.models import MemoryRecord
from core.operator_intent import route_operator_intent
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.scheduler import SchedulerStore
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from main import (
    _autonomy_readiness_payload,
    _budget_enforcement_status,
    _collect_instruction_buffer,
    _force_utf8_io,
    _format_next_actions,
    _format_operator_budget_digest,
    _handle_auto_run,
    _handle_hygiene,
    _handle_local_operator_reply,
    _handle_rollback,
    _local_operator_reply,
    _parse_remember,
    _preflight_file_hint,
    _print_persistent,
    _run_agent_with_budget_guard,
    handle_conversational_operator_input,
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


def _zero_limit_budget_snapshot() -> dict:
    return {
        "path": "data/budget_ledger.jsonl",
        "windows": [
            {
                "name": "hour",
                "seconds": 3600,
                "counters": {
                    "llm_calls": {"used": 2, "limit": 0},
                    "model_tokens": {"used": 1200, "limit": 0},
                    "model_cost_units": {"used": 4, "limit": 0},
                },
            },
            {
                "name": "day",
                "seconds": 86400,
                "counters": {
                    "llm_calls": {"used": 3, "limit": 0},
                    "model_tokens": {"used": 1800, "limit": 0},
                    "model_cost_units": {"used": 6, "limit": 0},
                },
            },
        ],
        "totals": {"llm_calls": 3, "model_tokens": 1800, "model_cost_units": 6},
        "recent": [],
    }


def test_run_agent_with_budget_guard_returns_clean_budget_message(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    agent = _build_agent(workspace)

    def raise_budget_error(*args, **kwargs):
        raise ModelBudgetExceeded(
            "model call budget exhausted: 1/1 before synthesizer:fake/fake-1"
        )

    monkeypatch.setattr(agent, "run", raise_budget_error)

    answer = _run_agent_with_budget_guard(agent, user_question="hello")

    assert answer == (
        "Model budget exceeded: model call budget exhausted: "
        "1/1 before synthesizer:fake/fake-1"
    )
    assert "model_budget_blocked" in agent.log.path.read_text(encoding="utf-8")


def test_local_operator_reply_bypasses_agent_run(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    agent = _build_agent(workspace)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("agent.run must not be called for local reply")

    monkeypatch.setattr(agent, "run", fail_if_called)

    handled = _handle_local_operator_reply(
        "\n".join(
            [
                "TD-001 stop condition.",
                "Do not call Planner or Synthesizer for this instruction.",
                "Do not use Claude.",
                'Reply only with: "TD-001 evidence recorded; waiting for explicit patch approval."',
            ]
        ),
        agent,
    )

    assert handled is True
    out = capsys.readouterr()
    assert "TD-001 evidence recorded; waiting for explicit patch approval." in out.out
    assert "local_operator_reply" in agent.log.path.read_text(encoding="utf-8")


def test_local_operator_reply_requires_explicit_llm_stop_marker():
    assert _local_operator_reply('Reply only with: "ok"') is None


@pytest.mark.parametrize(
    "cmd",
    [
        ":model-usage",
        ":budget-window-status",
        ":models",
        ":help",
        # TD-002 — deterministic local-only bypass for read-only status commands.
        ":auto-status",
        ":approval-list",
        ":approval-list all",
        ":schedule-list",
        ":schedule-list active",
        ":source-registry",
        ":source-registry --claims",
        # TD-011 — local, no-network model discovery audit.
        ":model-discovery-audit",
        ":model-discovery-audit --json",
        # self-build supervisor — read-only, no LLM/provider calls.
        ":self-build-supervisor",
        ":self-build-supervisor --json",
        # TD-022 — read-only budget kill-switch status.
        ":budget-kill-switch",
        ":budget-kill-switch --json",
        # TD-037 — local read-only autonomous health panel.
        ":dry-health-pass",
        ":dry-health-pass --json",
    ],
)
def test_local_meta_commands_do_not_start_model_calls(
    workspace: Path,
    capsys,
    cmd: str,
):
    agent = _build_agent(workspace)

    assert handle_meta_command(cmd, agent, workspace) is True

    capsys.readouterr()
    assert len(agent.llm.calls) == 0
    assert "model_call_start" not in agent.log.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "cmd",
    [
        ":auto-status",
        ":approval-list",
        ":schedule-list",
        ":source-registry",
        ":model-discovery-audit",
        ":self-build-supervisor",
        ":budget-kill-switch",
        ":dry-health-pass",
    ],
)
def test_local_meta_commands_never_invoke_planner_or_synthesizer(
    workspace: Path,
    capsys,
    cmd: str,
):
    """TD-002/TD-011: these read-only status/audit commands must resolve
    deterministically without Planner, Synthesizer or any LLM/provider call."""
    agent = _build_agent(workspace)

    planner_calls = 0
    original_plan = agent.planner.plan

    def _spy_plan(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return original_plan(*args, **kwargs)

    agent.planner.plan = _spy_plan  # type: ignore[method-assign]

    assert handle_meta_command(cmd, agent, workspace) is True

    capsys.readouterr()
    # 1. handled successfully (asserted above)
    # 2. no LLM/provider calls at all (Synthesizer route + Planner share this LLM)
    assert len(agent.llm.calls) == 0
    # 3. no routed model call was ever started
    assert "model_call_start" not in agent.log.path.read_text(encoding="utf-8")
    # 4. Planner was never invoked
    assert planner_calls == 0


def test_quit_meta_command_does_not_start_model_call(workspace: Path):
    agent = _build_agent(workspace)

    with pytest.raises(SystemExit):
        handle_meta_command(":quit", agent, workspace)

    assert len(agent.llm.calls) == 0
    assert "model_call_start" not in agent.log.path.read_text(encoding="utf-8")


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


class FakeRssFetchTool(Tool):
    name = "rss_fetch"
    description = "fake rss fetch"
    risk = "read_only"

    def run(self, url: str, max_entries: int = 10):
        assert url == "https://example.com/feed.xml"
        return {
            "url": url,
            "status_code": 200,
            "content_type": "application/rss+xml",
            "fetched_at": "2026-05-29T17:40:00+00:00",
            "content_hash": "b" * 64,
            "title": "Example Feed",
            "feed_type": "rss",
            "entries": [
                {
                    "title": "First RSS item",
                    "url": "https://example.com/item-1",
                    "summary": "RSS item summary.",
                    "published_at": "Fri, 29 May 2026 10:00:00 GMT",
                    "id": "item-1",
                }
            ][:max_entries],
            "text_truncated": False,
            "bytes": 120,
            "elapsed_ms": 1,
            "compensation_plan": {
                "id": "noop",
                "actions": [{"kind": "noop", "description": "read only"}],
                "tool_name": "rss_fetch",
                "description": "read only",
            },
        }

    def validate_output(self, output):
        return (isinstance(output, dict) and isinstance(output.get("entries"), list), [])


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
# _collect_instruction_buffer — :task-begin/:task-end/:task-abort
# ============================================================

class TestCollectInstructionBuffer:
    @staticmethod
    def _reader(lines: list[str]):
        """Return a callable that yields the given lines one per call."""
        it = iter(lines)
        return lambda: next(it)

    def test_commits_multiline_buffer_on_task_end(self):
        reader = self._reader([
            "Не маршрутизировать в implementation_plan,",
            "если пользователь просит создать заявку.",
            ":task-end",
        ])
        text, cancelled = _collect_instruction_buffer(reader)
        assert cancelled is False
        assert text == (
            "Не маршрутизировать в implementation_plan,\n"
            "если пользователь просит создать заявку."
        )

    def test_abort_discards_buffer(self):
        reader = self._reader([
            "первая строка",
            "вторая строка",
            ":task-abort",
        ])
        text, cancelled = _collect_instruction_buffer(reader)
        assert cancelled is True
        assert text == ""

    def test_terminator_is_case_insensitive_and_trimmed(self):
        reader = self._reader(["payload", "  :TASK-END  "])
        text, cancelled = _collect_instruction_buffer(reader)
        assert cancelled is False
        assert text == "payload"

    def test_empty_buffer_returns_empty_string(self):
        reader = self._reader([":task-end"])
        text, cancelled = _collect_instruction_buffer(reader)
        assert cancelled is False
        assert text == ""

    def test_eof_propagates_to_caller(self):
        def reader() -> str:
            raise EOFError

        with pytest.raises(EOFError):
            _collect_instruction_buffer(reader)


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

    def test_campaign_start_command_reaches_handler_not_budget(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Regression: ':campaign-start --max-cost-units 0' contains budget
        # wording, but that flag belongs to the campaign command, not a budget
        # status question.
        cmd = ":campaign-start --dry-run --max-cycles 3 --max-cost-units 0"
        trapped = route_operator_intent(cmd)
        assert trapped is None or trapped.kind != "budget_status"

        # Explicit ':' meta-commands must win: handle_meta_command must
        # dispatch to the campaign handler (which calls run_campaign), never to
        # the budget digest. This is why main() routes ':'-prefixed --ask input
        # through handle_meta_command BEFORE the conversational classifier.
        called = {}

        class _FakeReport:
            def user_summary(self) -> str:
                return "status=completed cycles=3"

        def _fake_run_campaign(config, **kwargs):
            called["goal"] = config.goal
            called["dry_run"] = config.dry_run
            called["max_cost_units"] = config.max_cost_units
            return _FakeReport()

        monkeypatch.setattr("app.runtime_cli.run_campaign", _fake_run_campaign)
        agent = _build_agent(workspace)
        assert handle_meta_command(cmd, agent, workspace) is True
        assert called == {"goal": "project health", "dry_run": True, "max_cost_units": 0}

    def test_self_apply_run_command_registered(self, workspace: Path, capsys):
        # Dispatcher recognizes :self-apply-run. An unknown id is refused by the
        # bridge (needs_validated_proposal) without touching git or tests.
        agent = _build_agent(workspace)
        assert handle_meta_command(":self-apply-run ain-nope", agent, workspace) is True
        out = capsys.readouterr()
        assert "self-apply run" in out.err
        assert "needs_validated_proposal" in out.err

    def test_self_apply_run_rejects_free_text(self, workspace: Path, capsys):
        # More than one token (a patch body / extra args) is rejected outright.
        agent = _build_agent(workspace)
        assert (
            handle_meta_command(":self-apply-run ain-1 extra words", agent, workspace)
            is True
        )
        out = capsys.readouterr()
        assert "Usage: :self-apply-run" in out.err

    def test_self_apply_run_rejects_empty(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert handle_meta_command(":self-apply-run", agent, workspace) is True
        out = capsys.readouterr()
        assert "Usage: :self-apply-run" in out.err

    def test_self_build_produce_registered(self, workspace: Path, capsys):
        # Dispatcher recognizes :self-build-produce and runs the producer end to
        # end (a safety gate may short-circuit in the tmp workspace). The
        # grounded-default wiring itself is asserted in
        # test_self_build_produce_uses_grounded_default below.
        import subprocess

        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=workspace, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@e",
             "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=workspace, check=True,
        )
        agent = _build_agent(workspace)
        assert handle_meta_command(":self-build-produce", agent, workspace) is True
        out = capsys.readouterr()
        assert "=== self-build produce ===" in out.err
        assert "status:" in out.err

    def test_self_build_produce_uses_grounded_default(self, workspace: Path, capsys):
        # TD-036 follow-up: the CLI must drive the producer on its grounded
        # DEFAULT path — it never forces the legacy LLM manager and never pins a
        # selector, so the producer's workspace-backed grounded selector runs.
        import cli.commands_self_build as mod

        captured: dict = {}

        def _fake_produce(**kwargs):
            captured.update(kwargs)
            from core.self_build_producer import ProducerReport

            return ProducerReport(status="no_patch", reason="stub")

        mod_produce = mod.produce_self_apply_proposal
        mod.produce_self_apply_proposal = _fake_produce
        try:
            agent = _build_agent(workspace)
            assert (
                mod._handle_self_build_produce("", agent, workspace) is True
            )
        finally:
            mod.produce_self_apply_proposal = mod_produce

        # No legacy opt-in and no forced selector => producer default (grounded).
        assert captured.get("legacy_llm_manager") in (None, False)
        assert "grounded_selector" not in captured

    def test_self_build_produce_rejects_args(self, workspace: Path, capsys):
        # Narrow trigger: it takes no arguments and no free-text patch.
        agent = _build_agent(workspace)
        assert (
            handle_meta_command(":self-build-produce some patch text", agent, workspace)
            is True
        )
        out = capsys.readouterr()
        assert "Usage: :self-build-produce" in out.err

    def test_self_task_propose_registered(self, workspace: Path, capsys):
        # Dispatcher recognizes :self-task-propose and runs the Stage-A producer
        # end to end (a gate short-circuits to no_task in the tmp workspace, which
        # has no code TODO/FIXME backlog).
        import subprocess

        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=workspace, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@e",
             "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=workspace, check=True,
        )
        agent = _build_agent(workspace)
        assert handle_meta_command(":self-task-propose", agent, workspace) is True
        out = capsys.readouterr()
        assert "=== self-task propose ===" in out.err
        assert "status:" in out.err

    def test_self_task_propose_rejects_args(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert (
            handle_meta_command(":self-task-propose extra words", agent, workspace)
            is True
        )
        out = capsys.readouterr()
        assert "Usage: :self-task-propose" in out.err

    def test_self_task_build_registered(self, workspace: Path, capsys):
        # Dispatcher recognizes :self-task-build and routes to the Stage-B builder
        # (a deterministic gate short-circuits before any LLM call in a bare tmp
        # workspace), proving the command is wired end to end.
        import subprocess

        for args in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=workspace, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@e",
             "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=workspace, check=True,
        )
        agent = _build_agent(workspace)
        assert (
            handle_meta_command(":self-task-build ain_missing", agent, workspace)
            is True
        )
        out = capsys.readouterr()
        assert "=== self-task build ===" in out.err
        assert "status:" in out.err

    def test_self_task_build_requires_id(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        assert handle_meta_command(":self-task-build", agent, workspace) is True
        out = capsys.readouterr()
        assert "Usage: :self-task-build" in out.err


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

    def test_source_registry_command_lists_ingested_sources(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        registry = SourceRegistry()
        source = registry.register_source(
            type="file",
            title="operator_task_layer_request.md",
            locator="operator_task_layer_request.md",
            trust_level=0.9,
        )
        registry.register_claim(
            source_id=source.id,
            text="Operator Task Layer needs safer routing.",
            confidence=0.8,
            status="extracted",
        )
        assert agent.source_registry_store is not None
        agent.source_registry_store.save_registry(registry)

        assert handle_meta_command(":source-registry --claims", agent, workspace) is True
        assert handle_meta_command(":source-status --json", agent, workspace) is True

        out = capsys.readouterr()
        assert "source registry" in out.err
        assert "operator_task_layer_request.md" in out.err
        assert "claims=1" in out.err
        assert "Operator Task Layer needs safer routing." in out.err
        assert '"sources": 1' in out.err
        assert '"claims": 1' in out.err

    def test_conversational_source_review_plan_uses_registry_without_llm(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        registry = SourceRegistry()
        task_source = registry.register_source(
            type="file",
            title="operator_task_layer_request.md",
            locator="operator_task_layer_request.md",
            trust_level=0.9,
        )
        registry.register_claim(
            source_id=task_source.id,
            text="Operator Task Layer needs cheap routing and budget-waste fixes.",
            confidence=0.85,
        )
        intent_source = registry.register_source(
            type="file",
            title="operator_intent.py",
            locator="core/operator_intent.py",
            trust_level=0.9,
        )
        registry.register_claim(
            source_id=intent_source.id,
            text="Operator intent routing is deterministic and local.",
            confidence=0.8,
        )
        assert agent.source_registry_store is not None
        agent.source_registry_store.save_registry(registry)

        handled = handle_conversational_operator_input(
            "Сравни загруженные источники operator_task_layer_request.md, "
            "main.py и core/operator_intent.py. Составь точный план реализации "
            "Operator Task Layer.",
            agent,
            workspace,
        )

        out = capsys.readouterr()
        assert handled is True
        assert "operator intent: source_review_plan" in out.err
        assert "source review plan" in out.err
        assert "operator_task_layer_request.md" in out.err
        assert "core/operator_intent.py" in out.err
        assert "main.py" in out.err
        assert "not verified from registry" in out.err
        assert "tests to add" in out.err
        assert agent.llm.calls == []

    def test_conversational_implementation_plan_has_distinct_local_report(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        registry = SourceRegistry()
        source = registry.register_source(
            type="file",
            title="operator_intent.py",
            locator="core/operator_intent.py",
            trust_level=0.9,
        )
        registry.register_claim(
            source_id=source.id,
            text="Operator intent routing should split source review from implementation planning.",
            confidence=0.86,
        )
        assert agent.source_registry_store is not None
        agent.source_registry_store.save_registry(registry)

        handled = handle_conversational_operator_input(
            "Составь точный план реализации для core/operator_intent.py и main.py",
            agent,
            workspace,
        )

        out = capsys.readouterr()
        assert handled is True
        assert "operator intent: implementation_plan" in out.err
        assert "implementation plan" in out.err
        assert "kind: implementation_plan" in out.err
        assert "files/functions to inspect or change:" in out.err
        assert "implementation steps:" in out.err
        assert "approval boundary:" in out.err
        assert "source review plan" not in out.err
        assert "operator digest" not in out.err
        assert agent.llm.calls == []

    def test_explicit_read_first_request_does_not_emit_grounded_plan(
        self,
        workspace: Path,
        capsys,
    ):
        # Registry is empty: the named files are NOT grounded (sources=0).
        agent = _build_agent(workspace)

        handled = handle_conversational_operator_input(
            "Сначала прочитай только эти файлы: core/loop.py и main.py. "
            "Затем составь точный план реализации.",
            agent,
            workspace,
        )

        out = capsys.readouterr()
        assert handled is True
        assert "operator intent: implementation_plan" in out.err
        # Blocked instead of a fake-grounded implementation plan.
        assert "insufficient source evidence (blocked)" in out.err
        assert "status: blocked" in out.err
        # A normal grounded plan must NOT be produced.
        assert "implementation steps:" not in out.err
        assert "=== implementation plan ===" not in out.err
        assert agent.llm.calls == []

    def test_explicit_read_first_request_lists_missing_files(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        handled = handle_conversational_operator_input(
            "Read only these files first: core/loop.py and main.py, "
            "then produce an implementation plan.",
            agent,
            workspace,
        )

        out = capsys.readouterr()
        assert handled is True
        assert "requested files NOT read/verified:" in out.err
        assert "core/loop.py" in out.err
        assert "main.py" in out.err

    def test_ordinary_implementation_plan_without_read_requirement_stays_deterministic(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        handled = handle_conversational_operator_input(
            "Составь точный план реализации для core/loop.py и main.py",
            agent,
            workspace,
        )

        out = capsys.readouterr()
        assert handled is True
        assert "operator intent: implementation_plan" in out.err
        assert "kind: implementation_plan" in out.err
        assert "implementation steps:" in out.err
        assert "insufficient source evidence" not in out.err
        assert agent.llm.calls == []

    def test_explicit_read_first_block_is_side_effect_free(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        before = {p.name for p in workspace.iterdir()}

        assert handle_meta_command(
            ":implementation-plan Read these files first: core/loop.py "
            "and main.py --json",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        payload = json.loads(out.err)
        assert payload["kind"] == "insufficient_source_evidence"
        assert payload["status"] == "blocked"
        assert {"core/loop.py", "main.py"} <= set(payload["files_not_read"])
        # Read-only guard: no web, no tests, no file writes promised and honored.
        assert "no web search" in payload["constraints"]
        assert "no tests" in payload["constraints"]
        assert "no file writes" in payload["constraints"]
        assert agent.llm.calls == []
        # No new files were written into the workspace by the blocked plan.
        after = {p.name for p in workspace.iterdir()}
        assert after == before

    def test_implementation_and_patch_plan_commands_support_json(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        assert handle_meta_command(
            ":implementation-plan core/operator_intent.py --json",
            agent,
            workspace,
        ) is True
        assert handle_meta_command(
            ":patch-proposal-plan fix operator routing --json",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert '"kind": "implementation_plan"' in out.err
        assert '"kind": "patch_proposal"' in out.err
        assert '"approval_boundary"' in out.err
        assert agent.llm.calls == []

    def test_patch_proposal_plan_uses_matched_targets_and_behavior_tests(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        registry = SourceRegistry()
        routing_source = registry.register_source(
            type="file",
            title="operator_intent.py",
            locator="core/operator_intent.py",
            trust_level=0.9,
        )
        registry.register_claim(
            source_id=routing_source.id,
            text="BUG notes mentioning patch proposal must stay normal chat.",
            confidence=0.87,
        )
        main_source = registry.register_source(
            type="file",
            title="main.py",
            locator="main.py",
            trust_level=0.9,
        )
        registry.register_claim(
            source_id=main_source.id,
            text="Patch proposal plans are dispatched locally and read-only.",
            confidence=0.82,
        )
        assert agent.source_registry_store is not None
        agent.source_registry_store.save_registry(registry)

        assert handle_meta_command(
            ":patch-proposal-plan fix BUG note routing in core/operator_intent.py and main.py --json",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        payload = json.loads(out.err)
        matched_locators = {
            item["locator"]
            for item in payload["source_evidence"]["matched_sources"]
        }
        targets = payload["files_functions_to_inspect_or_change"]
        tests_to_add = "\n".join(payload["tests_to_add"]).casefold()

        assert payload["kind"] == "patch_proposal"
        assert {"core/operator_intent.py", "main.py"} <= matched_locators
        assert any(item.startswith("core/operator_intent.py -") for item in targets)
        assert any(item.startswith("main.py -") for item in targets)
        assert "bug note routing" in tests_to_add
        assert "source-review requests route to source_review_plan" not in tests_to_add
        assert "implementation-plan requests route to implementation_plan" not in tests_to_add
        assert agent.llm.calls == []

    def test_self_build_propose_command_returns_unified_diff_without_writes(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        agent = SimpleNamespace(llm=FakeLLM(responses=[]))
        (workspace / "core").mkdir()
        target = workspace / "core" / "operator_intent.py"
        original = (
            "def route_operator_intent(text: str):\n"
            "    normalized = _normalize(text)\n"
            "    if _looks_like_meta_instruction(normalized):\n"
            "        return None\n"
            "    if _matches_inbox_task_request(normalized):\n"
            "        return None\n"
            "    return None\n"
            "\n"
            "\n"
            "def _looks_like_meta_instruction(text: str) -> bool:\n"
            "    return False\n"
            "\n"
            "\n"
            "def _matches_inbox_task_request(text: str) -> bool:\n"
            "    return False\n"
        )
        target.write_text(original, encoding="utf-8")
        original_read_text = Path.read_text

        def guarded_read_text(path: Path, *args, **kwargs):
            normalized = str(path).replace("\\", "/").casefold()
            assert not normalized.endswith("config/model_registry.json")
            assert not normalized.endswith("config/credentials.json")
            assert "/data/" not in normalized
            assert "/logs/" not in normalized
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded_read_text)

        assert handle_meta_command(":self-build-propose", agent, workspace) is True

        out = capsys.readouterr()
        assert "=== self-build proposal ===" in out.err
        assert "--- core/operator_intent.py" in out.err
        assert "+++ core/operator_intent.py" in out.err
        assert "if _looks_like_self_build_request(normalized):" in out.err
        assert "def _looks_like_self_build_request" in out.err
        assert target.read_text(encoding="utf-8") == original
        assert agent.llm.calls == []

    def test_self_build_propose_command_returns_no_patch_when_guard_exists(
        self,
        workspace: Path,
        capsys,
    ):
        agent = SimpleNamespace(llm=FakeLLM(responses=[]))
        (workspace / "core").mkdir()
        (workspace / "core" / "operator_intent.py").write_text(
            "def route_operator_intent(text: str):\n"
            "    normalized = _normalize(text)\n"
            "    if _looks_like_meta_instruction(normalized):\n"
            "        return None\n"
            "    if _looks_like_self_build_request(normalized):\n"
            "        return None\n"
            "    return None\n"
            "\n"
            "\n"
            "def _looks_like_self_build_request(text: str) -> bool:\n"
            "    return True\n",
            encoding="utf-8",
        )

        assert handle_meta_command(":self-build-propose", agent, workspace) is True

        out = capsys.readouterr()
        assert out.err.strip() == "NO_PATCH"
        assert agent.llm.calls == []

    def test_self_build_large_files_reports_python_files_only(
        self,
        workspace: Path,
        capsys,
    ):
        agent = SimpleNamespace(llm=FakeLLM(responses=[]))
        (workspace / "core").mkdir()
        (workspace / "core" / "large.py").write_text("x = 1\n" * 3001, encoding="utf-8")
        (workspace / "core" / "edge.py").write_text("x = 1\n" * 3000, encoding="utf-8")

        assert handle_meta_command(":self-build-propose --large-files", agent, workspace) is True

        out = capsys.readouterr()
        assert out.err.splitlines()[0] == "LARGE_FILE_REPORT"
        assert "threshold_lines=3000" in out.err
        assert "core/large.py lines=3001" in out.err
        assert "core/edge.py" not in out.err
        assert agent.llm.calls == []

    def test_self_build_large_files_returns_no_large_files_at_threshold(
        self,
        workspace: Path,
        capsys,
    ):
        agent = SimpleNamespace(llm=FakeLLM(responses=[]))
        (workspace / "core").mkdir()
        (workspace / "core" / "edge.py").write_text("x = 1\n" * 3000, encoding="utf-8")

        assert handle_meta_command(":self-build-propose --large-files", agent, workspace) is True

        out = capsys.readouterr()
        assert out.err.strip() == "NO_LARGE_FILES"
        assert agent.llm.calls == []

    def test_self_build_large_files_one_shot_skips_forbidden_roots(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        (workspace / "core").mkdir()
        (workspace / "core" / "large.py").write_text("x = 1\n" * 3001, encoding="utf-8")
        for name in ("config", "data", "logs", ".venv", ".git"):
            root = workspace / name
            root.mkdir()
            (root / "ignored.py").write_text("x = 1\n" * 4000, encoding="utf-8")

        def fail_if_called(*args, **kwargs):
            raise AssertionError("large-file report must not build agent or load dotenv")

        original_open = Path.open

        def guarded_open(path: Path, *args, **kwargs):
            normalized = str(path).replace("\\", "/").casefold()
            assert "/config/" not in normalized
            assert "/data/" not in normalized
            assert "/logs/" not in normalized
            assert "/.venv/" not in normalized
            assert "/.git/" not in normalized
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guarded_open)
        monkeypatch.setattr(main_module, "build_agent", fail_if_called)
        monkeypatch.setattr(main_module, "load_dotenv", fail_if_called)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "main.py",
                "--workspace",
                str(workspace),
                "--ask",
                ":self-build-propose --large-files",
            ],
        )

        assert main_module.main() == 0

        out = capsys.readouterr()
        assert out.err.splitlines()[0] == "LARGE_FILE_REPORT"
        assert "core/large.py lines=3001" in out.err
        assert "ignored.py" not in out.err

    def test_self_build_propose_one_shot_short_circuits_before_agent_build(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        (workspace / "core").mkdir()
        (workspace / "core" / "operator_intent.py").write_text(
            "def route_operator_intent(text: str):\n"
            "    normalized = _normalize(text)\n"
            "    if _looks_like_meta_instruction(normalized):\n"
            "        return None\n"
            "    if _looks_like_self_build_request(normalized):\n"
            "        return None\n"
            "    return None\n"
            "\n"
            "\n"
            "def _looks_like_self_build_request(text: str) -> bool:\n"
            "    return True\n",
            encoding="utf-8",
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("self-build propose must not build agent or load dotenv")

        monkeypatch.setattr(main_module, "build_agent", fail_if_called)
        monkeypatch.setattr(main_module, "load_dotenv", fail_if_called)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "main.py",
                "--workspace",
                str(workspace),
                "--ask",
                ":self-build-propose",
            ],
        )

        assert main_module.main() == 0

        out = capsys.readouterr()
        assert out.err.strip() == "NO_PATCH"
        assert not (workspace / "logs").exists()
        assert not (workspace / "data").exists()

    def test_operator_task_block_reports_once_without_effects(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        before_memory = (
            agent.persistent_store.count()
            if agent.persistent_store is not None
            else 0
        )
        block = """
Проверь свою систему в безопасном режиме.
Ничего не меняй и ничего не записывай без approval.
Проверь архитектуру, runtime, budget, model usage, approvals, source registry, scheduler, task queue и тесты.
В конце скажи человеческим языком:
1. что работает нормально,
2. что требует внимания,
3. что опасно запускать сейчас,
4. какой один следующий шаг самый правильный.
""".strip()

        assert handle_meta_command(f":operator-task {block}", agent, workspace) is True

        out = capsys.readouterr()
        assert "operator task report" in out.err
        assert "goal: safe_system_check" in out.err
        assert "constraints:" in out.err
        assert "no file/code changes" in out.err
        assert "require approval before any effect" in out.err
        assert "checks:" in out.err
        assert "architecture" in out.err
        assert "runtime" in out.err
        assert "budget" in out.err
        assert "model usage" in out.err
        assert "approvals" in out.err
        assert "source registry" in out.err
        assert "scheduler" in out.err
        assert "task queue" in out.err
        assert "tests" in out.err
        assert "1. what works normally:" in out.err
        assert "2. what requires attention:" in out.err
        assert "3. what is dangerous to run now:" in out.err
        assert "4. one safest next step:" in out.err
        assert agent.llm.calls == []
        assert agent.persistent_store is not None
        assert agent.persistent_store.count() == before_memory
        log_text = agent.log.path.read_text(encoding="utf-8")
        assert "operator_task_report" in log_text
        assert "autonomous_runtime_start" not in log_text
        assert "team execution" not in log_text
        assert "repair_start" not in log_text

    def test_operator_task_patch_proposal_uses_multi_file_evidence(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        (workspace / "core").mkdir()
        (workspace / "tests").mkdir()
        (workspace / "core" / "operator_intent.py").write_text(
            "def route_operator_intent(text):\n    return None\n",
            encoding="utf-8",
        )
        (workspace / "main.py").write_text(
            "def _operator_task_payload(block, agent, workspace):\n    return {}\n",
            encoding="utf-8",
        )
        (workspace / "tests" / "test_operator_intent.py").write_text(
            "def test_routes():\n    assert True\n",
            encoding="utf-8",
        )
        block = r"""
Use explicit multi-file review mode.
Read core/operator_intent.py, main.py and tests/test_operator_intent.py.
Do not change anything.
Produce a patch proposal for the routing bug.
""".strip()

        assert handle_meta_command(f":operator-task {block}", agent, workspace) is True

        out = capsys.readouterr()
        assert "operator task report" in out.err
        assert "goal: patch_proposal" in out.err
        assert "goal: safe_system_check" not in out.err
        assert "mode: explicit_multi_file_review" in out.err
        assert "file:core/operator_intent.py" in out.err
        assert "file:main.py" in out.err
        assert "file:tests/test_operator_intent.py" in out.err
        assert "1. functions/files to inspect/change:" in out.err
        assert "2. tests to add:" in out.err
        assert "3. minimal diff-plan:" in out.err
        assert "4. risks:" in out.err
        assert "5. actions forbidden without approval:" in out.err
        assert "file_write" in out.err
        assert "shell_exec" in out.err
        assert agent.llm.calls == []

        log_text = agent.log.path.read_text(encoding="utf-8")
        assert "operator_task_report" in log_text
        assert "\"goal\": \"patch_proposal\"" in log_text
        assert "\"source_id\": \"file:core/operator_intent.py\"" in log_text
        assert "\"source_id\": \"file:main.py\"" in log_text
        assert "\"source_id\": \"file:tests/test_operator_intent.py\"" in log_text
        assert "\"event\": \"tool_call\"" not in log_text
        assert "autonomous_runtime_start" not in log_text
        assert "repair_start" not in log_text

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

    def test_ingest_rss_fetches_entries_into_registry(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        agent.registry.register(FakeRssFetchTool())

        assert handle_meta_command(
            ":ingest-rss https://example.com/feed.xml --limit 1",
            agent,
            workspace,
        ) is True

        assert agent.source_registry_store is not None
        counts = agent.source_registry_store.count()
        assert counts["sources"] == 1
        assert counts["claims"] >= 1
        assert agent.last_provenance.by_kind("web_page")
        out = capsys.readouterr()
        assert "ingest rss" in out.err
        assert "https://example.com/item-1" in out.err

    def test_source_connector_registry_commands(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_meta_command(":connectors wired", agent, workspace) is True
        assert handle_meta_command(":connectors --json", agent, workspace) is True
        assert handle_meta_command(
            ":connector-plan monitor Python releases --limit 2",
            agent,
            workspace,
        ) is True
        assert handle_meta_command(
            ":connector-plan research papers about agents --json",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert "source connectors" in out.err
        assert "rss" in out.err
        assert '"connectors"' in out.err
        assert "connector plan" in out.err
        assert "openalex" in out.err

    def test_model_routes_command(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_meta_command(":models", agent, workspace) is True
        assert handle_meta_command(":models --json", agent, workspace) is True
        assert handle_meta_command(":model-registry-audit", agent, workspace) is True
        assert handle_meta_command(":architecture-audit --json", agent, workspace) is True
        assert handle_meta_command(":model-usage", agent, workspace) is True
        assert handle_meta_command(":team-plan news business architecture --json", agent, workspace) is True
        assert handle_meta_command(
            ":team-run news business architecture --max-model-calls 20 --max-cost-units 30",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert "model routes" in out.err
        assert "model selection policy" in out.err
        assert "model registry audit" in out.err
        assert "planner" in out.err
        assert '"routes"' in out.err
        assert '"selection_policy"' in out.err
        assert '"multi_agent_state"' in out.err
        assert "model usage ledger is not enabled" in out.err
        assert '"contracts"' in out.err
        assert "NewsSignalAgent" in out.err
        assert "team execution dry-run" in out.err
        assert "verifier handoffs" in out.err

    def test_operator_check_command_prints_digest(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_meta_command(":operator-check", agent, workspace) is True
        assert handle_meta_command(":project-status --json", agent, workspace) is True
        assert handle_meta_command(":operator-budget", agent, workspace) is True
        assert handle_meta_command(":urgent-status", agent, workspace) is True
        assert handle_meta_command(":next-actions", agent, workspace) is True
        assert handle_meta_command(":autonomy-readiness", agent, workspace) is True

        out = capsys.readouterr()
        assert "operator digest" in out.err
        assert "source registry" in out.err
        assert "recommended actions" in out.err
        assert '"architecture"' in out.err
        assert "operator budget" in out.err
        assert "urgent status" in out.err
        assert "next actions" in out.err
        assert "autonomy readiness" in out.err

    def test_capability_request_command_prints_json_and_submits(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        assert handle_meta_command(
            ":capability-request Хочу чтобы ты сам сообщал мне когда нужно решение --json",
            agent,
            workspace,
        ) is True
        assert handle_meta_command(
            ":capability-request Следи за важными письмами --submit",
            agent,
            workspace,
        ) is True

        inbox = getattr(agent, "approval_inbox")
        pending = inbox.pending()
        out = capsys.readouterr()
        assert '"capability_type": "telegram"' in out.err
        assert "=== capability request ===" in out.err
        assert "capability-request submitted to approval inbox" in out.err
        assert len(pending) == 1
        assert pending[0].operation == "capability_request"
        assert pending[0].payload["capability_type"] == "email"

    def test_conversational_capability_request_routes_without_llm(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        assert handle_conversational_operator_input(
            "Хочу, чтобы ты сам сообщал мне, когда нужно решение",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert "operator intent: capability_request" in out.err
        assert "=== capability request ===" in out.err
        assert "type: telegram" in out.err
        assert "approval_required: true" in out.err

    def test_budget_enforcement_status_warns_when_limits_are_zero(self):
        status = _budget_enforcement_status(_zero_limit_budget_snapshot())

        assert status["tracking_enabled"] is True
        assert status["enforcement_enabled"] is False
        assert status["usage_recorded"] is True
        assert status["all_limits_zero"] is True
        assert status["over_limit"] is False
        assert "cross-run enforcement is disabled" in status["warning"]

    def test_budget_enforcement_status_warns_when_usage_exceeds_limit(self):
        status = _budget_enforcement_status(
            {
                "windows": [
                    {
                        "name": "day",
                        "seconds": 86400,
                        "counters": {"model_tokens": {"used": 1200, "limit": 1000}},
                    }
                ],
                "totals": {"model_tokens": 1200},
            }
        )

        assert status["tracking_enabled"] is True
        assert status["enforcement_enabled"] is True
        assert status["over_limit"] is True
        assert status["limit_breaches"] == [{"used": 1200, "limit": 1000}]
        assert "next matching expensive action will be blocked" in status["warning"]

    def test_operator_budget_digest_surfaces_zero_limit_warning(self):
        text = _format_operator_budget_digest(
            {
                "model_usage": {
                    "limits": {},
                    "totals": {"calls": 3, "total_tokens": 1800, "cost_units": 6},
                    "session_totals": {
                        "calls": 1,
                        "total_tokens": 500,
                        "cost_units": 2,
                    },
                },
                "persistent_budget_windows": _zero_limit_budget_snapshot(),
            }
        )

        assert "persistent enforcement: tracking=True limits_configured=False" in text
        assert "session caps: max_calls=unlimited max_tokens=unlimited max_cost_units=unlimited" in text
        assert "llm_calls=2/unlimited" in text
        assert "model_tokens=1200/unlimited" in text
        assert "budget warning:" in text
        assert "before long unattended sessions" in text

    def test_budget_config_command_shows_effective_limits(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        budget_path = workspace / "data" / "budget.jsonl"
        config_path = workspace / "config" / "budget_limits.json"
        budget = BudgetLedger(
            path=budget_path,
            config_path=config_path,
            windows=(
                BudgetWindow("hour", 3600, {"llm_calls": 2}),
                BudgetWindow("day", 86400, {"model_tokens": 1000}),
            ),
        )
        agent.model_router.usage_ledger = ModelUsageLedger(
            path=workspace / "data" / "model_usage.jsonl",
            budget_ledger=budget,
        )

        assert handle_meta_command(":budget-config", agent, workspace) is True
        assert handle_meta_command(":budget-config --json", agent, workspace) is True

        out = capsys.readouterr()
        assert "budget config" in out.err
        assert "config_path:" in out.err
        assert "limits_configured=True" in out.err
        assert "hour: llm_calls=0/2" in out.err
        assert "day: model_tokens=0/1000" in out.err
        assert '"budget_policy"' in out.err
        assert '"enforcement_enabled": true' in out.err

    def test_autonomy_readiness_blocks_when_budget_enforcement_is_disabled(self):
        readiness = _autonomy_readiness_payload(
            {
                "runtime": {"approval_inbox": {"pending": 0}},
                "architecture": {"ready_for_multi_agent_execution": True},
                "persistent_budget_windows": _zero_limit_budget_snapshot(),
                "recommendations": [],
            }
        )

        assert readiness["state"] == "limited"
        assert readiness["persistent_budget_limits_configured"] is False
        assert (
            "persistent budget tracking is active but enforcement is disabled"
            in readiness["blockers"]
        )

    def test_autonomy_readiness_allows_configured_budget_when_architecture_ready(self):
        readiness = _autonomy_readiness_payload(
            {
                "runtime": {"approval_inbox": {"pending": 0}},
                "architecture": {"ready_for_multi_agent_execution": True},
                "persistent_budget_windows": {
                    "windows": [
                        {
                            "name": "hour",
                            "seconds": 3600,
                            "counters": {"llm_calls": {"used": 0, "limit": 2}},
                        }
                    ],
                    "totals": {},
                },
                "recommendations": [],
            }
        )

        assert readiness["state"] == "ready"
        assert readiness["persistent_budget_limits_configured"] is True
        assert readiness["blockers"] == []

    def test_autonomy_readiness_blocks_when_configured_budget_is_exhausted(self):
        readiness = _autonomy_readiness_payload(
            {
                "runtime": {"approval_inbox": {"pending": 0}},
                "architecture": {"ready_for_multi_agent_execution": True},
                "persistent_budget_windows": {
                    "windows": [
                        {
                            "name": "day",
                            "seconds": 86400,
                            "counters": {"model_tokens": {"used": 1200, "limit": 1000}},
                        }
                    ],
                    "totals": {"model_tokens": 1200},
                },
                "recommendations": [],
            }
        )

        assert readiness["state"] == "limited"
        assert readiness["persistent_budget_limits_configured"] is True
        assert (
            "persistent budget usage is already at or above a configured limit"
            in readiness["blockers"]
        )

    def test_next_actions_puts_budget_prerequisites_before_long_session(self):
        text = _format_next_actions(
            {
                "prerequisites": [
                    "Persistent budget tracking is active, but all hour/day limits are 0.",
                    "Run a live state-store recovery drill before enabling Long Work Session Mode.",
                ],
                "priority_gaps": [
                    {
                        "title": "Long Work Session Mode",
                        "next_step": "Enable unattended work sessions.",
                    }
                ],
                "recommendations": [],
            }
        )

        budget_index = text.index("prerequisites before long work sessions")
        long_session_index = text.index("Long Work Session Mode")
        assert budget_index < long_session_index

    def test_conversational_operator_phrase_bypasses_llm(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        assert handle_conversational_operator_input(
            "Проверь проект и скажи что требует внимания",
            agent,
            workspace,
        ) is True
        assert handle_conversational_operator_input("Есть ли что-то срочное", agent, workspace) is True
        assert handle_conversational_operator_input("Что делать дальше", agent, workspace) is True
        assert handle_conversational_operator_input(
            "Можно ли запускать автономность",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert "operator intent: project_health" in out.err
        assert "operator intent: urgent_status" in out.err
        assert "operator intent: next_actions" in out.err
        assert "operator intent: autonomy_readiness" in out.err
        assert "operator digest" in out.err
        assert "attention:" in out.err

    def test_conversational_self_build_request_calls_producer_without_llm(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        # The full natural-language command must reach the deterministic
        # self-build producer directly — never the general planner (agent.run)
        # and never the self-apply lane (no code is applied). The command even
        # contains an apply-consent clause ("ничего не применяй без моего
        # согласия") that must NOT block routing.
        agent = _build_agent(workspace)

        produce_calls: list[str] = []

        def _spy_produce(rest, ag, ws) -> bool:
            produce_calls.append(rest)
            return True

        def _run_must_not_be_called(*args, **kwargs):
            raise AssertionError("agent.run (planner) must not run for self_build_request")

        def _apply_must_not_be_called(*args, **kwargs):
            raise AssertionError("_handle_self_apply_run must not run — no code may be applied")

        monkeypatch.setattr(main_module, "_handle_self_build_produce", _spy_produce)
        monkeypatch.setattr(main_module, "_handle_self_apply_run", _apply_must_not_be_called)
        monkeypatch.setattr(agent, "run", _run_must_not_be_called)

        assert handle_conversational_operator_input(
            "Начни безопасно программировать себя. Найди одну реальную небольшую "
            "проблему в своём коде, подготовь одно исправление и передай его мне "
            "на проверку. Ничего не применяй без моего согласия",
            agent,
            workspace,
        ) is True

        assert produce_calls == [""]  # producer invoked exactly once, no residual args
        assert "operator intent: self_build_request" in capsys.readouterr().err

    def test_conversational_self_build_question_is_not_routed_to_producer(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        # A question / description about self-programming must NOT trigger the
        # producer; it falls through to the normal path (handled=False here).
        agent = _build_agent(workspace)

        def _produce_must_not_run(*args, **kwargs):
            raise AssertionError("producer must not run for a self-build question")

        monkeypatch.setattr(main_module, "_handle_self_build_produce", _produce_must_not_run)

        assert handle_conversational_operator_input(
            "Может ли агент программировать себя?",
            agent,
            workspace,
        ) is False

    def test_deliberation_strategy_router_handles_live_operator_phrases_locally(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        (workspace / "README.md").write_text(
            "README should not be read for live operator strategy checks.",
            encoding="utf-8",
        )

        samples = [
            "Начни безопасную проверку себя",
            "Проверь свои возможности",
            "Посмотри, что у тебя сейчас не готово",
            "Найди слабое место в своей системе",
            "Скажи, какой безопасный тест сделать следующим",
        ]
        for sample in samples:
            assert handle_conversational_operator_input(sample, agent, workspace) is True

        out = capsys.readouterr()
        assert "operator intent: safe_self_check" in out.err
        assert "operator intent: capability_check" in out.err
        assert "operator intent: current_gaps_check" in out.err
        assert "operator intent: weakness_finder" in out.err
        assert "operator intent: next_safe_test" in out.err
        assert "operator digest" in out.err
        assert "operator capabilities" in out.err
        assert "current gaps" in out.err
        assert "live weakness digest" in out.err
        assert "next safe test" in out.err
        assert agent.llm.calls == []
        log_text = agent.log.path.read_text(encoding="utf-8")
        assert '"event": "tool_call"' not in log_text
        assert '"tool_name": "file_read"' not in log_text

    def test_deliberation_strategy_router_allows_explicit_readme_docs_path(
        self,
        workspace: Path,
    ):
        agent = _build_agent(workspace)

        assert handle_conversational_operator_input(
            "Расскажи по README, что ты умеешь",
            agent,
            workspace,
        ) is False
        assert agent.llm.calls == []

    def test_programming_readiness_phrase_returns_coding_report_not_health_report(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)
        (workspace / "core").mkdir()
        (workspace / "tests").mkdir()
        (workspace / "core" / "loop.py").write_text("# loop\n", encoding="utf-8")
        (workspace / "core" / "repair_proposal.py").write_text("# repair\n", encoding="utf-8")
        (workspace / "core" / "operator_intent.py").write_text("# routing\n", encoding="utf-8")
        (workspace / "main.py").write_text("# cli\n", encoding="utf-8")
        (workspace / "tests" / "test_operator_intent.py").write_text(
            "def test_placeholder():\n    assert True\n",
            encoding="utf-8",
        )
        (workspace / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

        assert handle_conversational_operator_input(
            "Проверь, насколько ты готов к безопасной программной задаче",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert "operator intent: programming_readiness" in out.err
        assert "programming readiness" in out.err
        assert "can_inspect_files_read_only" in out.err
        assert "can_propose_patch_read_only" in out.err
        assert "can_name_targeted_tests" in out.err
        assert "can_explain_rollback_boundary" in out.err
        assert "safe small task:" in out.err
        assert "files to read first:" in out.err
        assert "tests to run:" in out.err
        assert "risk estimation:" in out.err
        assert "requires approval:" in out.err
        assert "operator digest" not in out.err
        assert agent.llm.calls == []
        log_text = agent.log.path.read_text(encoding="utf-8")
        assert "operator_programming_readiness" in log_text
        assert '"event": "tool_call"' not in log_text

    def test_programming_readiness_command_supports_json(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        assert handle_meta_command(":coding-readiness --json", agent, workspace) is True

        out = capsys.readouterr()
        assert '"status"' in out.err
        assert '"capabilities"' in out.err
        assert '"requires_approval"' in out.err
        assert agent.llm.calls == []

    def test_conversational_patch_proposal_routes_to_local_patch_plan(
        self,
        workspace: Path,
        capsys,
    ):
        agent = _build_agent(workspace)

        assert handle_conversational_operator_input(
            "Составь patch proposal для исправления operator routing",
            agent,
            workspace,
        ) is True

        out = capsys.readouterr()
        assert "operator intent: patch_proposal" in out.err
        assert "patch proposal plan" in out.err
        assert "kind: patch_proposal" in out.err
        assert "matched=0" in out.err
        assert "implementation steps:" in out.err
        assert "approval boundary:" in out.err
        assert "goal: Составь patch proposal" in out.err
        assert "source review plan" not in out.err
        assert "operator task report" not in out.err
        assert agent.llm.calls == []

    def test_shell_like_repl_input_bypasses_llm_with_hint(self, workspace: Path, capsys):
        agent = _build_agent(workspace)

        handled = handle_conversational_operator_input(
            r"py -3 .\main.py --auto-approve deny --file .\x.md",
            agent,
            workspace,
        )

        out = capsys.readouterr()
        assert handled is True
        assert "Run it in PowerShell, not inside the agent REPL" in out.err
        assert agent.llm.calls == []

    def test_file_hint_preflight_fails_before_agent_run(self, workspace: Path):
        ok, message = _preflight_file_hint(r".\docs\missing.md", workspace)

        assert ok is False
        assert message is not None
        assert "ERROR: file hint does not exist:" in message
        assert str(workspace / "docs" / "missing.md") in message
        assert "No model calls were made." in message

    def test_file_hint_preflight_accepts_existing_relative_file(self, workspace: Path):
        doc = workspace / "task.md"
        doc.write_text("task", encoding="utf-8")

        ok, message = _preflight_file_hint(r".\task.md", workspace)

        assert ok is True
        assert message is None

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
        assert handle_meta_command(":budget-window-status", agent, workspace) is True
        assert handle_meta_command(":budget-window-status --json", agent, workspace) is True
        assert handle_meta_command(":state-store-drill", agent, workspace) is True
        assert handle_meta_command(":state-store-drill --json", agent, workspace) is True
        assert handle_meta_command(":release-audit", agent, workspace) is True
        assert handle_meta_command(":supply-chain-audit", agent, workspace) is True
        assert handle_meta_command(":queue-status", agent, workspace) is True
        assert handle_meta_command(":scheduler-status", agent, workspace) is True

        out = capsys.readouterr()
        assert "autonomous budget defaults" in out.err
        assert "persistent budget ledger is not enabled" in out.err
        assert "state-store recovery drill" in out.err
        assert "status=passed" in out.err
        assert "release hygiene" in out.err
        assert "supply-chain audit" in out.err
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

    def test_schedule_disable_marks_schedule_and_handles_missing(self, workspace: Path, capsys):
        agent = _build_agent(workspace)
        store = SchedulerStore(workspace / "data" / "runtime_schedules.jsonl")
        schedule = store.add(name="health", goal="project health", every_minutes=60)
        before_log_size = agent.log.path.stat().st_size

        assert handle_meta_command(f":schedule-disable {schedule.id}", agent, workspace) is True
        assert handle_meta_command(":schedule-list all", agent, workspace) is True
        assert handle_meta_command(":schedule-disable sched_missing", agent, workspace) is True

        out = capsys.readouterr()
        assert f"SCHEDULE_DISABLED {schedule.id}" in out.err
        assert "SCHEDULE_NOT_FOUND" in out.err
        assert f"{schedule.id} [disabled]" in out.err
        assert store.load()[0].status == "disabled"
        assert agent.log.path.stat().st_size == before_log_size
        assert not (workspace / "data" / "budget_ledger.jsonl").exists()
        assert not (workspace / "data" / "model_usage.jsonl").exists()
        assert not (workspace / "data" / "source_registry.jsonl").exists()

    def test_schedule_disable_one_shot_short_circuits_before_agent_build(
        self,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ):
        store = SchedulerStore(workspace / "data" / "runtime_schedules.jsonl")
        schedule = store.add(name="health", goal="project health", every_minutes=60)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("schedule-disable must not build agent or load dotenv")

        monkeypatch.setattr(main_module, "build_agent", fail_if_called)
        monkeypatch.setattr(main_module, "load_dotenv", fail_if_called)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "main.py",
                "--workspace",
                str(workspace),
                "--ask",
                f":schedule-disable {schedule.id}",
            ],
        )

        assert main_module.main() == 0

        out = capsys.readouterr()
        assert out.err.strip() == f"SCHEDULE_DISABLED {schedule.id}"
        assert store.load()[0].status == "disabled"
        assert not (workspace / "logs").exists()
        assert not (workspace / "config").exists()
        assert not (workspace / "data" / "budget_ledger.jsonl").exists()
        assert not (workspace / "data" / "model_usage.jsonl").exists()
        assert not (workspace / "data" / "source_registry.jsonl").exists()

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
