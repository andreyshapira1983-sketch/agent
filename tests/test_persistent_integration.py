"""Integration tests for Persistent Memory inside the Control Loop.

MVP-5 acceptance criteria (the seven the user enumerated):

  1. Agent can write a MemoryRecord to disk.
  2. Agent can read a MemoryRecord in a fresh session.
  3. Memory Write Policy decides save / reject (both branches exercised).
  4. Forbidden data is NOT saved (secrets, no-consent).
  5. There is a command to inspect memory (`agent.list_persistent()`).
  6. There is a command to delete memory (`agent.forget()`).
  7. Tests prove save, load, reject, and delete.

These tests exercise everything through the AgentLoop facade so the CLI
behaviour is covered without spinning up the REPL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent(
    workspace: Path,
    llm: FakeLLM,
    persistent_path: Path,
    with_memory: bool = True,
):
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
    memory = WorkingMemory() if with_memory else None
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id,
        log_dir=workspace / "logs",
        verbose=False,
    )
    store = PersistentMemoryStore(persistent_path)
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=memory,
        persistent_store=store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
    )
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    return agent, store, log_path


# ============================================================
# Acceptance #1, #3: agent.remember() saves through the policy
# ============================================================

class TestRememberSaves:
    def test_sensitive_record_is_saved_redacted_with_explicit_consent(
        self, workspace: Path
    ):
        path = workspace / "data" / "mem.jsonl"
        agent, store, log_path = _build_agent(workspace, FakeLLM(), path)

        decision, record = agent.remember(
            content="User email is andre@example.com.",
            tags=["fact", "sensitive-data-consent"],
            source="user-explicit",
        )

        assert decision.decision == "save"
        assert record is not None
        assert "andre@example.com" not in str(record.content)
        assert record.content == "User email is [REDACTED:pii-email]."

        raw_disk = path.read_text(encoding="utf-8")
        assert "andre@example.com" not in raw_disk
        assert "[REDACTED:pii-email]" in raw_disk
        assert store.load()[0].content == "User email is [REDACTED:pii-email]."

        events = _events(log_path)
        assert any(
            e["event"] == "sensitive_detected"
            and e["payload"]["surface"] == "persistent_memory"
            for e in events
        )


# ============================================================
# Acceptance #5, #6: list + forget
# ============================================================

class TestListAndForget:
    def test_forget_one_removes_only_that_record(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, _store, log_path = _build_agent(workspace, FakeLLM(), path)

        _, r1 = agent.remember(content="keep me", tags=["fact"], source="user-explicit")
        _, r2 = agent.remember(content="drop me", tags=["fact"], source="user-explicit")
        _, r3 = agent.remember(content="keep me too", tags=["fact"], source="user-explicit")

        assert agent.forget(record_id=r2.id) == 1
        remaining = {r.id for r in agent.list_persistent()}
        assert remaining == {r1.id, r3.id}

        events = _events(log_path)
        deletes = [e for e in events if e["event"] == "persistent_memory_delete"]
        assert deletes[-1]["payload"] == {
            "scope": "one",
            "record_id": r2.id,
            "deleted": 1,
        }

    def test_forget_all_wipes_store(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, store, log_path = _build_agent(workspace, FakeLLM(), path)

        agent.remember(content="alpha", tags=["fact"], source="user-explicit")
        agent.remember(content="bravo", tags=["fact"], source="user-explicit")

        n = agent.forget(record_id=None)
        assert n == 2
        assert agent.list_persistent() == []
        assert store.count() == 0

        events = _events(log_path)
        deletes = [e for e in events if e["event"] == "persistent_memory_delete"]
        assert any(
            e["payload"]["scope"] == "all" and e["payload"]["deleted"] == 2
            for e in deletes
        )

    def test_forget_unknown_id_emits_zero_deleted(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, _, log_path = _build_agent(workspace, FakeLLM(), path)
        agent.remember(content="keep me", tags=["fact"], source="user-explicit")

        n = agent.forget(record_id="mem_nope")
        assert n == 0

        events = _events(log_path)
        deletes = [e for e in events if e["event"] == "persistent_memory_delete"]
        assert deletes[-1]["payload"]["deleted"] == 0


# ============================================================
# Loop-level injection: persistent_memory_inject fires when records match
# ============================================================

PLAN_EMPTY = json.dumps({"reasoning": "answerable without tools", "steps": []})
SYNTH_OK = (
    "Conclusion: based on memory [memory:demo].\n"
    "Facts:\n- recorded preference [memory:demo]\n"
    "Sources:\n1. memory:demo - long_term_memory\n"
    "Confidence: medium\nUnverified: nothing\n"
)


class TestRetrievalInjection:
    def test_no_overlap_reports_zero_selected(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"

        agent_a, _, _ = _build_agent(workspace, FakeLLM(), path)
        agent_a.remember(
            content="Banana cherry date eggplant fig grape",
            tags=["fact"],
            source="user-explicit",
        )

        llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK])
        agent_b, _, log_path = _build_agent(workspace, llm, path)
        agent_b.run(user_question="Tell me about quantum mechanics.")

        events = _events(log_path)
        injects = [e for e in events if e["event"] == "persistent_memory_inject"]
        assert len(injects) == 1
        assert injects[0]["payload"]["records_selected"] == 0

        # No <long_term_memory> block injected into the synthesizer prompt.
        synth_calls = [c for c in llm.calls if "research analyst" in c["system"]]
        for c in synth_calls:
            assert "<long_term_memory>" not in c["user"]


# ============================================================
# Loop-level: memory competes for the same evidence budget as fresh reads
# ============================================================


class TestMemoryEntersTheEvidenceBudget:
    """ROOT B / S2 — recollection must not outrank the file just read.

    `<long_term_memory>` used to be concatenated into the synthesizer prompt
    *outside* `apply_total_budget`, so it was structurally untrimmable while
    the freshly read file — normally the largest block — was cut first. A
    months-old "Bug fixed …" record then survived a trim that removed the code
    proving it, and the agent reported a fixed bug as current.
    """

    @staticmethod
    def _section(prompt: str, open_tag: str, close_tag: str) -> str:
        """The prompt between the two tags, or "" when the block is absent."""
        if open_tag not in prompt:
            return ""
        start = prompt.index(open_tag)
        return prompt[start : prompt.index(close_tag) + len(close_tag)]

    def _run(
        self,
        workspace: Path,
        monkeypatch,
        fresh_chars: int = 4_000,
        total_chars: int | None = None,
        turns: int = 1,
    ):
        path = workspace / "data" / "mem.jsonl"

        # Fresh evidence: a file about the same topic as the memory records.
        para = (
            "The budget governor limits how many LLM calls the agent may make "
            "per hour and refuses the call once the ceiling is reached.\n\n"
        )
        fresh = (para * (fresh_chars // len(para) + 1))[:fresh_chars]
        (workspace / "doc.txt").write_text(fresh, encoding="utf-8")

        # Long-term memory: three records that overlap the question.
        agent_a, _, _ = _build_agent(workspace, FakeLLM(), path)
        for idx, topic in enumerate(("hourly ceiling", "refusal path", "governor config")):
            agent_a.remember(
                # Short enough that the first record survives the trim whole
                # while the next one is cut — the case where "keep only whole
                # records" and "slice characters" disagree.
                content=(
                    f"Record {idx} about the budget governor and its {topic}: "
                    + f"the governor limits LLM calls per hour ({topic}). " * 6
                )[:150],
                tags=["fact"],
                source="user-explicit",
            )

        # Default budget = fresh block + 400 chars of headroom, so trimming the
        # memory block alone is always enough to fit — the fresh read never has
        # to be touched once memory is spent first.
        monkeypatch.setenv(
            "AGENT_EVIDENCE_TOTAL_CHARS",
            str(fresh_chars + 400 if total_chars is None else total_chars),
        )

        plan = json.dumps(
            {
                "reasoning": "Read the file that documents the governor.",
                "steps": [{"tool": "file_read", "arguments": {"path": "doc.txt"}}],
            }
        )
        answer = (
            "Conclusion: the governor caps LLM calls per hour. [file:doc.txt]\n"
            "Facts:\n- the ceiling is enforced per hour [file:doc.txt]\n"
            "Sources:\n1. file:doc.txt - doc.txt\n"
            "Confidence: high\nUnverified: nothing\n"
        )
        llm = FakeLLM(responses=[plan, answer] * turns)
        agent_b, _, log_path = _build_agent(workspace, llm, path)
        for _ in range(turns):
            agent_b.run(
                user_question="How does the budget governor limit LLM calls per hour?"
            )

        synth = [c for c in llm.calls if "research analyst" in c["system"]]
        assert synth, "no synthesizer call was made"
        return synth[-1]["user"], log_path


    def test_trimmed_memory_block_keeps_its_closing_tag(
        self, workspace: Path, monkeypatch
    ):
        prompt, _log = self._run(workspace, monkeypatch)
        memory_block = self._section(prompt, "<long_term_memory>", "</long_term_memory>")

        assert "TOTAL-BUDGET" in memory_block          # precondition: it was cut
        assert prompt.count("<long_term_memory>") == 1
        assert prompt.count("</long_term_memory>") == 1


    def test_trace_says_no_memory_survived_when_the_block_is_dropped(
        self, workspace: Path, monkeypatch
    ):
        _prompt, log_path = self._run(workspace, monkeypatch, total_chars=480)  # 900 -> 650:
            # memory now keeps one WHOLE record instead of vanishing
            # (2026-08-15), so 900 no longer creates the case this test
            # names. The premise moved, the contract did not.

        events = _events(log_path)
        injects = [e for e in events if e["event"] == "persistent_memory_inject"]
        trims = [e for e in events if e["event"] == "evidence_budget_trim"]
        # Retrieval reports three records...
        assert injects[-1]["payload"]["records_selected"] == 3
        # ...and the trim must say that none of them reached the model.
        assert trims[-1]["payload"]["memory_chars_kept"] == 0
        assert trims[-1]["payload"]["memory_ids_kept"] == []


    def test_memory_cut_to_the_floor_is_dropped_rather_than_left_mangled(
        self, workspace: Path, monkeypatch
    ):
        """A budget that leaves room for no whole record must drop the block.

        A raw character slice ends inside a record and leaves half an id
        (`- [mem_8357646e3d93d6aa`) with no content behind it: prompt weight
        for zero information, and a citation token the verifier can never
        match.
        """
        prompt, _log = self._run(workspace, monkeypatch, total_chars=480)  # 900 -> 650:
            # memory now keeps one WHOLE record instead of vanishing
            # (2026-08-15), so 900 no longer creates the case this test
            # names. The premise moved, the contract did not.
        memory_block = self._section(prompt, "<long_term_memory>", "</long_term_memory>")
        allowed = self._section(prompt, "<allowed_citations>", "</allowed_citations>")

        assert memory_block == "", "a memory block with no whole record survived"
        assert not re.findall(r"mem_[0-9a-f]+", allowed)
        # …and nothing tells the model how to cite a block that is not there.
        assert "cite it with source label [memory:" not in prompt

    def test_trimming_memory_does_not_revoke_working_memory_citations(
        self, workspace: Path, monkeypatch
    ):
        """Prior-turn tool outputs share `kind="memory"` and must survive.

        They are `obtained_via="working_memory"`, live in
        `<conversation_history>` — outside this budget — and were never
        trimmed. Filtering them out because long-term memory was cut leaves the
        synthesizer unable to cite the artifacts of its own previous turn.
        """
        prompt, _log = self._run(workspace, monkeypatch, turns=2)
        memory_block = self._section(prompt, "<long_term_memory>", "</long_term_memory>")
        allowed = self._section(prompt, "<allowed_citations>", "</allowed_citations>")

        assert "TOTAL-BUDGET" in memory_block      # precondition: LTM was cut
        assert "working_turn" in allowed
