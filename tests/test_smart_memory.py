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
    EpisodeRecord,
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


def test_weak_verdicts_block_success_and_procedure() -> None:
    """Regression: an answer resting on sub-agent-asserted / cited-but-unmatched
    claims must not be banked as a perfect success or minted into a skill.

    Mirrors the real trace where a sub-agent hallucinated a code bug: the
    verifier reported verified=4 alongside weak chunks (cited_but_unmatched=4,
    subagent_asserted=1). Before this fix the turn scored quality 1.0,
    outcome=success and was consolidated into procedural memory.
    """
    episode = episode_from_agent_cycle(
        goal="introspect the repo",
        question="what changed and are there bugs?",
        answer="Confirmed defect: EpisodeRecord.full_answer is dropped.",
        tools_used=["spawn_subagent"],
        source_labels=["subagent:bughunter"],
        verified_chunks=4,
        unverified_chunks=0,
        weak_chunks=5,
    )

    assert episode.outcome == "partial"
    assert episode.answer_quality_score < 0.5
    assert episode.weak_chunks == 5
    assert procedure_from_episode(episode) is None


def test_weak_verdicts_dent_quality_but_keep_success_when_dominated() -> None:
    """A single stray unmatched citation among mostly verified evidence lowers
    quality without flipping a clean turn to partial."""
    episode = episode_from_agent_cycle(
        goal="read a file",
        question="what is in the file?",
        answer="the file lists the modules",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=5,
        unverified_chunks=0,
        weak_chunks=1,
    )

    assert episode.outcome == "success"
    assert episode.answer_quality_score == round(5 / 6, 3)
    assert procedure_from_episode(episode) is not None


def test_weak_chunks_survive_serialization_round_trip() -> None:
    episode = episode_from_agent_cycle(
        goal="g",
        question="q",
        answer="a",
        tools_used=["spawn_subagent"],
        source_labels=["subagent:x"],
        verified_chunks=1,
        unverified_chunks=0,
        weak_chunks=3,
    )
    restored = EpisodeRecord.from_dict(episode.to_dict())

    assert restored.weak_chunks == 3
    assert restored.answer_quality_score == episode.answer_quality_score
    assert restored.outcome == episode.outcome


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
    # Beta(1,1)-smoothed: (2+1)/(2+2) = 0.75. Two successes are good evidence
    # but not a certainty — confidence must not read 1.0.
    assert proc2.confidence == 0.75
    assert set(proc2.source_episode_ids) == {episode1.id, episode2.id}
    assert store.count() == 1


def test_single_success_procedure_is_not_certain(workspace: Path) -> None:
    """A procedure minted from ONE successful episode must not report
    confidence 1.0 — one observation is weak evidence. Regression for the
    trace where a low-relevance turn minted a proc at confidence=1.0.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    episode = episode_from_agent_cycle(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    proc, created = store.upsert_from_episode(episode)
    assert created is True
    assert proc is not None
    assert proc.success_count == 1
    # Beta(1,1): (1+1)/(1+2) = 0.667 — active but modest, never 1.0.
    assert proc.confidence == 0.667
    assert proc.confidence < 1.0
    assert proc.status == "active"


def test_procedure_confidence_grows_but_never_reaches_one(workspace: Path) -> None:
    """Repeated successes push confidence up asymptotically toward — but never
    to — 1.0, so a workflow earns trust with evidence instead of by fiat.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    last = 0.0
    for i in range(6):
        episode = episode_from_agent_cycle(
            goal="read file",
            question=f"read doc {i}",
            answer="done",
            tools_used=["file_read"],
            source_labels=["file:doc.txt"],
            verified_chunks=1,
        )
        proc, _created = store.upsert_from_episode(episode)
        assert proc is not None
        assert proc.confidence > last  # monotonically increasing
        assert proc.confidence < 1.0   # never certain
        last = proc.confidence


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


def test_cheap_path_skips_consolidation_but_still_records_episode(
    workspace: Path,
) -> None:
    """A trivial cheap-path turn must NOT run the per-turn full memory
    consolidation (which re-reads every episode + procedure), yet the episode
    itself is still recorded so learning is not lost."""
    gk_answer = (
        "Conclusion: The flag disables effects. [general-knowledge]\n"
        "Facts:\n- A false flag turns a feature off. [general-knowledge]\n"
        "Sources:\n1. general-knowledge - general-knowledge\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    # Only the synthesizer response is queued — the planner is skipped.
    agent, log_path = _build_agent(workspace, FakeLLM([gk_answer]))

    answer = agent.run("effects=disabled")

    assert answer.strip()
    names = [e["event"] for e in _events(log_path)]
    # Cheap path fired.
    assert "planner_cheap_path" in names
    # Episode is still written (learning preserved).
    assert names.count("episodic_memory_write") == 1
    # Consolidation was skipped for this turn.
    assert "memory_consolidation" not in names
    assert "memory_consolidation_skipped" in names
    # No consolidation report was produced.
    assert agent.smart_memory_summary()["consolidation"]["reports"] == 0


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


def test_search_boosts_lesson_episodes(tmp_path: Path) -> None:
    """Lesson episodes should appear above ordinary ones on same token overlap."""
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=100)
    ordinary = episode_from_agent_cycle(
        goal="repair", question="fix bug in core", answer="patched",
        tools_used=[], source_labels=[], verified_chunks=1,
    )
    lesson = EpisodeRecord(
        goal="repair", question="how to fix", outcome="success",
        summary="Bug fixed in 'core/loop.py': bad import. Tests used: tests/test_loop.py.",
        tags=("lesson", "bug-fix", "regression-guard"),
    )
    store.save(ordinary)
    store.save(lesson)

    results = store.search("fix bug in core", limit=2)
    # Lesson must appear (either first or second)
    assert lesson in results
    # Lesson must be ranked above or equal to ordinary
    assert results.index(lesson) <= results.index(ordinary)


def test_search_by_tags_returns_matching_episodes(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=100)
    lesson = EpisodeRecord(
        goal="repair", question="q", outcome="success",
        summary="fixed core/foo.py", tags=("lesson", "bug-fix"),
    )
    other = episode_from_agent_cycle(
        goal="g", question="q2", answer="a",
        tools_used=[], source_labels=[], verified_chunks=1,
    )
    store.save(lesson)
    store.save(other)

    results = store.search_by_tags(["lesson"])
    assert lesson in results
    assert other not in results


# ── Episodic fast-path environment-safety guard ──────────────────────────────
# The fast path replays a stored answer verbatim, skipping planner + synthesizer.
# It must NOT do so when the cached episode used tools: such answers depend on
# the state of the environment (files, installed packages, command output) which
# may have changed since. Only purely reasoned (no-tool) answers are replayable.

PLAN_GENERAL_KNOWLEDGE = json.dumps({"reasoning": "general knowledge", "steps": []})

SYNTH_GENERAL_KNOWLEDGE = (
    "Conclusion: A fresh general-knowledge answer. [general-knowledge]\n"
    "Facts:\n"
    "- a fact [general-knowledge]\n"
    "Sources:\n"
    "1. general-knowledge - general-knowledge\n"
    "Confidence: high\n"
    "Unverified: nothing\n"
    "Safety: nothing\n"
)


def test_fast_path_skipped_when_cached_episode_used_tools(workspace: Path) -> None:
    """A high-quality cached answer produced WITH tools must not be replayed
    verbatim — the environment may have changed, so the agent must re-plan."""
    llm = FakeLLM([PLAN_GENERAL_KNOWLEDGE, SYNTH_GENERAL_KNOWLEDGE])
    agent, log_path = _build_agent(workspace, llm)
    question = "is pytest cov installed in this project"
    agent.episodic_store.save(
        EpisodeRecord(
            goal="check tooling",
            question=question,
            outcome="success",
            summary="cached tool-based answer",
            tools_used=("shell_exec",),
            verified_chunks=2,
            unverified_chunks=0,
            full_answer="STALE: pytest-cov is NOT installed.",
        )
    )

    answer = agent.run(question)

    events = _events(log_path)
    assert not [e for e in events if e["event"] == "episodic_fast_path"]
    planner_calls = [c for c in llm.calls if "PLANNER_MODE" in c["system"]]
    assert planner_calls, "fast path was taken — planner should have run"
    assert "STALE" not in answer


def test_fast_path_used_when_cached_episode_had_no_tools(workspace: Path) -> None:
    """A high-quality cached answer produced WITHOUT tools is environment-
    independent and may be replayed verbatim via the fast path."""
    llm = FakeLLM([])  # fast path returns before any LLM call
    agent, log_path = _build_agent(workspace, llm)
    question = "what is the capital of france"
    agent.episodic_store.save(
        EpisodeRecord(
            goal="trivia",
            question=question,
            outcome="success",
            summary="cached reasoned answer",
            tools_used=(),
            verified_chunks=2,
            unverified_chunks=0,
            full_answer="Paris is the capital of France.",
            # This test covers the fast-path MECHANISM; eligibility (2c) and
            # completion (MIR-057) fail-close on unclassified episodes and
            # have their own suites.
            usage_eligible=True,
            completion_state="achieved",
        )
    )

    answer = agent.run(question)

    events = _events(log_path)
    assert [e for e in events if e["event"] == "episodic_fast_path"]
    assert answer == "Paris is the capital of France."
    assert not [c for c in llm.calls if "PLANNER_MODE" in c["system"]]
