"""Smart memory tests: episodes, procedures and consolidation."""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.planner import LLMPlanner
from core.policy import PolicyGate
from core.smart_memory import (
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    consolidate_memory,
    episode_from_agent_cycle,
    format_experience_context,
    procedure_from_episode,
)
from main import handle_meta_command
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


PLAN_FILE_READ = json.dumps(
    {
        "reasoning": "Read the hinted file.",
        "steps": [
            {"tool": "file_read", "arguments": {"path": "doc.txt"}, "rationale": "..."}
        ],
    }
)

SYNTH_FILE = (
    "Conclusion: The file lists alpha. [file:doc.txt]\n"
    "Facts:\n"
    "- alpha is present [file:doc.txt]\n"
    "Sources:\n"
    "1. file:doc.txt - doc.txt\n"
    "Confidence: high\n"
    "Unverified: nothing\n"
    "Safety: nothing\n"
)


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent(workspace: Path, llm: FakeLLM) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        episodic_store=EpisodicMemoryStore(workspace / "data" / "episodes.jsonl"),
        procedural_store=ProceduralMemoryStore(workspace / "data" / "procedures.jsonl"),
        consolidation_store=MemoryConsolidationStore(workspace / "data" / "consolidation.jsonl"),
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def test_episode_from_agent_cycle_redacts_and_tags() -> None:
    episode = episode_from_agent_cycle(
        goal="read a file",
        question="What is in the file?",
        answer="The key is sk-abc1234567890123456789012345678901234567890123",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=2,
        unverified_chunks=0,
    )

    assert episode.outcome == "success"
    assert "[REDACTED:openai-key]" in episode.summary
    assert "sk-abc" not in episode.summary
    assert episode.tags == ("episode", "success", "file_read", "file")


def test_failed_or_partial_episode_does_not_become_procedure() -> None:
    failed = episode_from_agent_cycle(
        goal="read file",
        question="read missing file",
        answer="could not read",
        tools_used=["file_read"],
        source_labels=[],
        replan_exhausted=True,
    )
    partial = episode_from_agent_cycle(
        goal="answer from no source",
        question="unknown",
        answer="unknown",
        tools_used=[],
        source_labels=["general-knowledge"],
        verified_chunks=0,
        unverified_chunks=3,
    )

    assert failed.outcome == "failed"
    assert partial.outcome == "partial"
    assert procedure_from_episode(failed) is None
    assert procedure_from_episode(partial) is None


def test_procedural_store_upserts_successful_tool_workflow(workspace: Path) -> None:
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    episode1 = episode_from_agent_cycle(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    episode2 = episode_from_agent_cycle(
        goal="read file again",
        question="read doc again",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )

    proc1, created1 = store.upsert_from_episode(episode1)
    proc2, created2 = store.upsert_from_episode(episode2)

    assert proc1 is not None
    assert proc2 is not None
    assert created1 is True
    assert created2 is False
    assert proc2.success_count == 2
    assert proc2.confidence == 1.0
    assert set(proc2.source_episode_ids) == {episode1.id, episode2.id}
    assert store.count() == 1


def test_consolidation_links_episodes_and_procedures(workspace: Path) -> None:
    episodic = EpisodicMemoryStore(workspace / "episodes.jsonl")
    procedural = ProceduralMemoryStore(workspace / "procedures.jsonl")
    consolidation = MemoryConsolidationStore(workspace / "consolidation.jsonl")
    episode = episode_from_agent_cycle(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    episodic.save(episode)
    procedure, _created = procedural.upsert_from_episode(episode)

    report = consolidate_memory(episodes=episodic.load(), procedures=procedural.load())
    consolidation.save(report)

    assert procedure is not None
    assert report.episode_count == 1
    assert report.procedure_count == 1
    assert episode.id in report.linked_episode_ids
    assert procedure.id in report.active_procedure_ids
    assert consolidation.count() == 1


def test_format_experience_context_contains_procedures_and_episodes() -> None:
    episode = episode_from_agent_cycle(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    procedure = procedure_from_episode(episode)
    assert procedure is not None
    text = format_experience_context(episodes=[episode], procedures=[procedure])

    assert "<agent_experience_memory>" in text
    assert "Workflow using file_read" in text
    assert f"[{episode.id}]" in text


def test_agent_loop_records_smart_memory_after_successful_cycle(workspace: Path) -> None:
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")
    agent, log_path = _build_agent(workspace, FakeLLM([PLAN_FILE_READ, SYNTH_FILE]))

    answer = agent.run("Read doc.txt", file_hint="doc.txt")

    assert "alpha" in answer
    summary = agent.smart_memory_summary()
    assert summary["episodic"]["episodes"] == 1
    assert summary["episodic"]["outcomes"]["success"] == 1
    assert summary["procedural"]["procedures"] == 1
    assert summary["consolidation"]["reports"] == 1
    events = _events(log_path)
    assert [e["event"] for e in events].count("episodic_memory_write") == 1
    assert [e["event"] for e in events].count("procedural_memory_update") == 1
    assert [e["event"] for e in events].count("memory_consolidation") == 1


def test_experience_memory_is_injected_into_next_planner_call(workspace: Path) -> None:
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")
    llm = FakeLLM([PLAN_FILE_READ, SYNTH_FILE, PLAN_FILE_READ, SYNTH_FILE])
    agent, log_path = _build_agent(workspace, llm)

    agent.run("Read doc.txt", file_hint="doc.txt")
    agent.run("Read doc.txt again", file_hint="doc.txt")

    planner_calls = [call for call in llm.calls if "PLANNER_MODE" in call["system"]]
    assert len(planner_calls) == 2
    assert "<agent_experience_memory>" in planner_calls[1]["user"]
    events = _events(log_path)
    inject_events = [e for e in events if e["event"] == "experience_memory_inject"]
    assert inject_events[-1]["payload"]["procedures_selected"] == 1


def test_smart_memory_cli_commands(workspace: Path, capsys) -> None:
    agent, _log_path = _build_agent(workspace, FakeLLM([]))
    episode = episode_from_agent_cycle(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    agent.episodic_store.save(episode)
    agent.procedural_store.upsert_from_episode(episode)

    assert handle_meta_command(":smart-memory", agent, workspace) is True
    out = capsys.readouterr()
    assert "episodic: episodes=1" in out.err

    assert handle_meta_command(":memory-consolidate --json", agent, workspace) is True
    out = capsys.readouterr()
    assert '"episode_count": 1' in out.err
    assert agent.consolidation_store.count() == 1


def test_episodic_store_evicts_oldest_when_over_limit(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=3)
    for i in range(5):
        ep = episode_from_agent_cycle(
            goal="g", question=f"q{i}", answer="a",
            tools_used=[], source_labels=[], verified_chunks=1,
        )
        store.save(ep)
    episodes = store.load()
    assert len(episodes) == 3
    # Oldest two (q0, q1) should have been evicted
    questions = {e.question for e in episodes}
    assert "q0" not in questions
    assert "q1" not in questions
    assert "q4" in questions


def test_episodic_store_protects_lesson_tags(tmp_path: Path) -> None:
    from core.smart_memory import EpisodeRecord
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=2)
    # Save 2 normal episodes first
    for i in range(2):
        ep = episode_from_agent_cycle(
            goal="g", question=f"normal{i}", answer="a",
            tools_used=[], source_labels=[], verified_chunks=1,
        )
        store.save(ep)
    # Save a lesson episode (should never be evicted)
    lesson = EpisodeRecord(
        goal="repair", question="how to fix", outcome="success",
        summary="fixed bug", tags=("lesson", "bug-fix"),
    )
    store.save(lesson)
    # Now we have 3 episodes with max=2 → one normal evicted, lesson survives
    episodes = store.load()
    assert len(episodes) == 2
    ids_with_lesson_tag = [e for e in episodes if "lesson" in e.tags]
    assert len(ids_with_lesson_tag) == 1


def test_episodic_store_no_eviction_under_limit(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=10)
    for i in range(5):
        ep = episode_from_agent_cycle(
            goal="g", question=f"q{i}", answer="a",
            tools_used=[], source_labels=[], verified_chunks=1,
        )
        store.save(ep)
    assert store.count() == 5
