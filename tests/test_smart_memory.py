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
    PROCEDURE_STATUSES,
    ConsolidationReport,
    EpisodeRecord,
    EpisodicMemoryStore,
    MemoryConsolidationStore,
    ProceduralMemoryStore,
    ProcedureRecord,
    consolidate_memory,
    effective_completion,
    episode_from_agent_cycle,
    format_experience_context,
    procedure_credit_allowed,
    procedure_from_episode,
)
from main import handle_meta_command
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _declare_completion(monkeypatch, token: str = "achieved") -> None:
    """Append a valid completion marker to whatever synthesis produces.

    An end-to-end cycle here is driven by a canned `FakeLLM` answer, which
    cannot carry the per-attempt nonce. Without a declaration the cycle freezes
    as `unknown` and distils no procedure — true, and not what these tests are
    measuring.
    """
    original = AgentLoop._synthesize

    def _synth(self, *args, completion_nonce: str = "", **kwargs) -> str:
        text = original(self, *args, **kwargs)
        return f"{text}\n[[agent.completion:{completion_nonce}:{token}]]"

    monkeypatch.setattr("core.loop.AgentLoop._synthesize", _synth)


def _make_achieved_episode(**kwargs):
    """An episode whose cycle DECLARED the task done, for cases that need it.

    Named rather than shadowing the factory, so the premise is visible at the
    call site: credit needs both axes since MIR-057, and an undeclared cycle
    freezes as `unknown`. Cases that are about non-completion — a failed run,
    a weak-evidence run, or the factory itself — call the real factory
    directly and say so by doing it.
    """
    kwargs.setdefault("declared_completion", "achieved")
    return episode_from_agent_cycle(**kwargs)


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


def test_grounded_but_incomplete_success_mints_no_procedure() -> None:
    """MIR-003 / MIR-057 — the completion axis on procedure birth.

    The two axes answer different questions: ``outcome`` says *were the claims
    supported*, ``completion_state`` says *was the goal reached*. A blocked or
    refused answer can be impeccably supported (``outcome == "success"``) and
    still not have finished the task — the exact live case that motivated the
    completion axis: a blocked answer that cited every claim it made and
    credited ``tools:file_read`` to 0.857 anyway (MIR-057).

    Such an episode must mint no procedure. The evidence axis alone would call
    it a success; the completion gate (`procedure_credit_allowed`) is what
    refuses it. This guarantee was reproduced but never regression-locked.
    """
    for token in ("blocked", "refused"):
        episode = episode_from_agent_cycle(
            goal="count the lines of a large file",
            question="how many lines?",
            answer="Cannot determine: the provided content was truncated. [file:big.txt]",
            tools_used=["file_read"],
            source_labels=["file:big.txt"],
            verified_chunks=3,      # well-grounded — every claim held up ...
            unverified_chunks=0,
            weak_chunks=0,
            declared_completion=token,   # ... but the goal was NOT reached
        )
        # The evidence axis on its own would admit this as a success.
        assert episode.outcome == "success", token
        assert effective_completion(episode) == token
        # Both axes must agree before a procedure may be credited (MIR-057).
        assert procedure_credit_allowed(episode) is False, token
        assert procedure_from_episode(episode) is None, token


def test_incomplete_success_mints_no_procedure_through_the_store(
    workspace: Path,
) -> None:
    """The same guarantee at the real production entry point.

    ``_record_experience_memory`` reaches procedures only through
    ``ProceduralMemoryStore.upsert_from_episode``; a grounded-but-blocked
    episode must yield no procedure, report ``created == False``, and leave the
    store empty — nothing is written that a later planner could reuse.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    blocked = episode_from_agent_cycle(
        goal="count the lines of a large file",
        question="how many lines?",
        answer="Cannot determine: content truncated. [file:big.txt]",
        tools_used=["file_read"],
        source_labels=["file:big.txt"],
        verified_chunks=3,
        unverified_chunks=0,
        weak_chunks=0,
        declared_completion="blocked",
    )

    proc, created = store.upsert_from_episode(blocked)

    assert proc is None
    assert created is False
    assert store.load() == []


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
    episode = _make_achieved_episode(
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
    episode1 = _make_achieved_episode(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    episode2 = _make_achieved_episode(
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
    episode = _make_achieved_episode(
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
    # Beta(1,1): (1+1)/(1+2) = 0.667 — modest, never 1.0.
    assert proc.confidence == 0.667
    assert proc.confidence < 1.0
    # One success is unproven: born `candidate`, not `active` (MIR-003 A4
    # maturity gate, owner decision 2026-07-22). Confidence is high enough
    # (0.667 ≥ 0.6) that the OLD rule would have said active; the maturity
    # gate holds it at candidate until a second independent success.
    assert proc.status == "candidate"


def test_new_procedure_is_born_candidate_not_active(workspace: Path) -> None:
    """MIR-003 A4 maturity gate (owner decision 2026-07-22).

    A procedure distilled from ONE genuine completed+verified success is
    unproven. It must be born `candidate`, never `active` — a single success
    is not enough standing to be treated as a reusable workflow.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    proc, created = store.upsert_from_episode(
        _make_achieved_episode(
            goal="read a file",
            question="read doc",
            answer="done",
            tools_used=["file_read"],
            source_labels=["file:doc.txt"],
            verified_chunks=3,
        )
    )
    assert created is True
    assert proc is not None
    assert proc.success_count == 1
    assert proc.status == "candidate"


def test_candidate_procedure_is_not_offered_to_planning(workspace: Path) -> None:
    """A `candidate` must not participate in ordinary planning retrieval.

    ``ProceduralMemoryStore.search`` is the only path that injects procedures
    into the planner (`core/loop_methods2.py:354`). An unproven candidate must
    not surface there, or a one-off success would steer later plans before it
    earned the right to.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    store.upsert_from_episode(
        _make_achieved_episode(
            goal="read a file",
            question="read the doc",
            answer="done",
            tools_used=["file_read"],
            source_labels=["file:doc.txt"],
            verified_chunks=3,
        )
    )
    # It IS stored (auditable) ...
    assert store.count() == 1
    # ... but it is NOT offered to planning while it is a candidate.
    assert store.search("read the doc file_read") == []


def test_second_independent_success_promotes_candidate_to_active(
    workspace: Path,
) -> None:
    """Promotion requires a SECOND, independent, completed+verified success.

    One success → `candidate` (not planned with). A second distinct success on
    the same workflow → `active`, and only then does it surface to planning.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    first = _make_achieved_episode(
        goal="read a file",
        question="read the doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=3,
        run_id="run-first",
    )
    second = _make_achieved_episode(
        goal="read a file again",
        question="read the doc again",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=3,
        run_id="run-second",
    )

    p1, created1 = store.upsert_from_episode(first)
    assert created1 is True
    assert p1.success_count == 1
    assert p1.status == "candidate"
    assert store.search("read the doc file_read") == []  # not yet planned with

    p2, created2 = store.upsert_from_episode(second)
    assert created2 is False  # same workflow, updated not re-created
    assert p2.success_count == 2  # two independent episodes credited
    assert p2.status == "active"  # promoted
    assert len(store.search("read the doc file_read")) == 1  # now planned with


def test_candidate_status_survives_serialization_round_trip(workspace: Path) -> None:
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    proc, _ = store.upsert_from_episode(
        _make_achieved_episode(
            goal="read a file",
            question="read doc",
            answer="done",
            tools_used=["file_read"],
            source_labels=["file:doc.txt"],
            verified_chunks=3,
        )
    )
    assert proc.status == "candidate"
    restored = ProcedureRecord.from_dict(proc.to_dict())
    assert restored.status == "candidate"


def test_procedure_confidence_grows_but_never_reaches_one(workspace: Path) -> None:
    """Repeated successes push confidence up asymptotically toward — but never
    to — 1.0, so a workflow earns trust with evidence instead of by fiat.
    """
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    last = 0.0
    for i in range(6):
        episode = _make_achieved_episode(
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
    episode = _make_achieved_episode(
        goal="read file",
        question="read doc",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
    )
    # Two independent successes so the procedure is promoted past the A4
    # maturity gate to `active` — a single success now yields a `candidate`,
    # which the consolidation `active` bucket (correctly) does not list.
    second = _make_achieved_episode(
        goal="read file again",
        question="read doc again",
        answer="done",
        tools_used=["file_read"],
        source_labels=["file:doc.txt"],
        verified_chunks=1,
        run_id="run-consolidation-2",
    )
    episodic.save(episode)
    episodic.save(second)
    procedural.upsert_from_episode(episode)
    procedure, _created = procedural.upsert_from_episode(second)

    report = consolidate_memory(episodes=episodic.load(), procedures=procedural.load())
    consolidation.save(report)

    assert procedure is not None
    assert procedure.status == "active"
    assert report.episode_count == 2
    assert report.procedure_count == 1
    assert episode.id in report.linked_episode_ids
    assert procedure.id in report.active_procedure_ids
    assert consolidation.count() == 1


def test_format_experience_context_contains_procedures_and_episodes() -> None:
    episode = _make_achieved_episode(
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


def test_agent_loop_records_smart_memory_after_successful_cycle(workspace: Path, monkeypatch) -> None:
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")
    agent, log_path = _build_agent(workspace, FakeLLM([PLAN_FILE_READ, SYNTH_FILE]))
    _declare_completion(monkeypatch)

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


def test_experience_memory_is_injected_into_next_planner_call(workspace: Path, monkeypatch) -> None:
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")
    # Three cycles: the MIR-003 A4 maturity gate makes a procedure reusable only
    # after a SECOND independent success promotes it. Retrieval happens at the
    # START of a cycle and promotion at its END, so the timeline is:
    #   cycle 1 → banks a `candidate` (1 success);
    #   cycle 2 → retrieval still sees a candidate (withheld); banking promotes
    #             it to `active` (2 successes);
    #   cycle 3 → retrieval now sees an `active` procedure and injects it.
    llm = FakeLLM([PLAN_FILE_READ, SYNTH_FILE] * 3)
    agent, log_path = _build_agent(workspace, llm)
    _declare_completion(monkeypatch)

    agent.run("Read doc.txt", file_hint="doc.txt")
    agent.run("Read doc.txt again", file_hint="doc.txt")
    agent.run("Read doc.txt once more", file_hint="doc.txt")

    planner_calls = [call for call in llm.calls if "PLANNER_MODE" in call["system"]]
    assert len(planner_calls) == 3
    # Episodes are injected throughout — the experience block appears regardless.
    assert "<agent_experience_memory>" in planner_calls[1]["user"]
    events = _events(log_path)
    inject = [e for e in events if e["event"] == "experience_memory_inject"]
    # Maturity gate, both boundaries:
    assert inject[1]["payload"]["procedures_selected"] == 0   # still a candidate
    assert inject[2]["payload"]["procedures_selected"] == 1   # promoted → planned with


def test_smart_memory_cli_commands(workspace: Path, capsys) -> None:
    agent, _log_path = _build_agent(workspace, FakeLLM([]))
    episode = _make_achieved_episode(
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
        ep = _make_achieved_episode(
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
        ep = _make_achieved_episode(
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
        ep = _make_achieved_episode(
            goal="g", question=f"q{i}", answer="a",
            tools_used=[], source_labels=[], verified_chunks=1,
        )
        store.save(ep)
    assert store.count() == 5


def test_search_boosts_lesson_episodes(tmp_path: Path) -> None:
    """Lesson episodes should appear above ordinary ones on same token overlap."""
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=100)
    ordinary = _make_achieved_episode(
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
    other = _make_achieved_episode(
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


# ---------- MIR-003 follow-up: `candidate` must be visible to the operator ----------


def test_smart_memory_summary_counts_candidate_procedures(workspace: Path) -> None:
    """A one-success procedure is a `candidate`; the operator summary must say so.

    Live run (2026-07-22) surfaced this: `:smart-memory` reported
    `procedures=1 statuses={'active': 0, 'needs_review': 0, 'obsolete': 0}` —
    one procedure that appears in no status at all, which reads as a fault.
    """
    agent, _log = _build_agent(workspace, FakeLLM([]))
    agent.procedural_store.upsert_from_episode(
        _make_achieved_episode(
            goal="read a file",
            question="read doc",
            answer="done",
            tools_used=["file_read"],
            source_labels=["file:doc.txt"],
            verified_chunks=3,
        )
    )
    summary = agent.smart_memory_summary()
    statuses = summary["procedural"]["statuses"]

    assert summary["procedural"]["procedures"] == 1
    assert statuses.get("candidate") == 1
    # every procedure is accounted for in exactly one bucket
    assert sum(statuses.values()) == summary["procedural"]["procedures"]


def test_smart_memory_summary_covers_every_procedure_status(workspace: Path) -> None:
    """Drift guard: the summary must enumerate the real status vocabulary.

    The tally used to hardcode three statuses, so adding `candidate` silently
    made a procedure invisible. Deriving the keys from `PROCEDURE_STATUSES`
    means a future status can never go missing the same way.
    """
    agent, _log = _build_agent(workspace, FakeLLM([]))
    statuses = agent.smart_memory_summary()["procedural"]["statuses"]
    assert set(statuses) == set(PROCEDURE_STATUSES)


def test_consolidation_report_lists_candidate_procedures(workspace: Path) -> None:
    """A candidate belongs in its own bucket, not silently in none of them."""
    procedural = ProceduralMemoryStore(workspace / "procedures.jsonl")
    procedure, _created = procedural.upsert_from_episode(
        _make_achieved_episode(
            goal="read a file",
            question="read doc",
            answer="done",
            tools_used=["file_read"],
            source_labels=["file:doc.txt"],
            verified_chunks=3,
        )
    )
    assert procedure.status == "candidate"

    report = consolidate_memory(episodes=[], procedures=procedural.load())

    assert procedure.id in report.candidate_procedure_ids
    assert procedure.id not in report.active_procedure_ids
    # survives the round trip the store performs
    assert procedure.id in ConsolidationReport.from_dict(
        report.to_dict()
    ).candidate_procedure_ids
