"""Safety integration — MVP-7 hard invariants.

The promises this file proves end-to-end:

  S1. A secret in a file_read output NEVER reaches the JSONL trace.
  S2. A secret in a file_read output NEVER reaches the LLM.
  S3. A secret echoed by the LLM is still scrubbed in the final answer.
  S4. The working-memory artifact cache stores the REDACTED text.
  S5. `secret_detected` and `data_classified` events fire on the right surfaces.
  S6. A secret pasted INTO the user's question is also redacted.
  S7. MemoryWritePolicy rejects records whose `owner` is third-party
      unless the `cross-owner-consent` tag is present.

Three surfaces, one invariant: no raw secret ever leaves the kernel.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryWritePolicy
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tests.conftest import FakeLLM


# A single fake-OpenAI key that we will look for everywhere afterwards.
# Length and shape match the scanner's `openai-key` regex.
SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123"


PLAN_FILE_READ = json.dumps(
    {
        "reasoning": "Read the hinted file.",
        "steps": [
            {"tool": "file_read", "arguments": {"path": "doc.txt"}, "rationale": "..."}
        ],
    }
)
SYNTH_OK = (
    "Conclusion: file read. [file:doc.txt]\n"
    "Facts:\n- something was found [file:doc.txt]\n"
    "Sources:\n1. file:doc.txt - doc.txt\n"
    "Confidence: medium\n"
    "Unverified: nothing\n"
    "Safety: a credential was kernel-redacted.\n"
)
PLAN_EMPTY = json.dumps({"reasoning": "no tools needed", "steps": []})
SYNTH_PLAIN = (
    "Conclusion: ok. [general-knowledge]\n"
    "Facts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: medium\nUnverified: nothing\nSafety: nothing\n"
)


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent(workspace: Path, llm: FakeLLM, with_memory: bool = False):
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
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=memory,
    )
    return agent, memory, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# S1 + S2 + S4 + S5 — secret in a file
# ============================================================

class TestSecretInFileDoesNotLeak:
    def test_secret_never_appears_in_log_llm_or_answer(self, workspace: Path):
        # Write a file with the secret embedded.
        file_path = workspace / "doc.txt"
        file_path.write_text(
            f"Project notes.\nDeploy key: {SECRET}\nEnd of file.\n",
            encoding="utf-8",
        )

        llm = FakeLLM(responses=[PLAN_FILE_READ, SYNTH_OK])
        agent, memory, log_path = _build_agent(workspace, llm, with_memory=True)

        answer = agent.run(user_question="summarise the file", file_hint="doc.txt")

        # ---- S3 — final answer does not contain the secret ----
        assert SECRET not in answer

        # ---- S1 — JSONL trace does not contain the secret anywhere ----
        log_text = log_path.read_text(encoding="utf-8")
        assert SECRET not in log_text, (
            "raw secret leaked into the JSONL trace; "
            "TraceLogger must have redacted it"
        )

        # ---- S2 — neither LLM call (planner + synthesizer) saw the secret ----
        for call in llm.calls:
            assert SECRET not in call["user"], (
                "secret leaked into the LLM user prompt — "
                "prompt redaction failed"
            )
            assert SECRET not in call["system"]

        # ---- S4 — working memory cached only the redacted artifact ----
        assert memory is not None
        cached = memory.cache_lookup("file_read", {"path": "doc.txt"})
        assert cached is not None
        cached_text = str(cached["output"])
        assert SECRET not in cached_text
        assert "[REDACTED:openai-key]" in cached_text

        # ---- S5 — events fired with the right surface ----
        events = _events(log_path)
        names = [e["event"] for e in events]
        assert "data_classified" in names
        assert "secret_detected" in names

        classified = [e for e in events if e["event"] == "data_classified"]
        # First classification is the user question (clean).
        # The file output classification follows.
        tool_classifications = [
            e for e in classified if e["payload"].get("tool") == "file_read"
        ]
        assert tool_classifications, "file_read output must be classified"
        assert tool_classifications[0]["payload"]["class"] == "secret"

        secret_events = [e for e in events if e["event"] == "secret_detected"]
        # At minimum one with surface=tool_output for the file output.
        assert any(
            e["payload"]["surface"] == "tool_output" for e in secret_events
        )
        assert any(
            "openai-key" in (e["payload"].get("kinds") or []) for e in secret_events
        )


# ============================================================
# S6 — secret in the user's question
# ============================================================

class TestSecretInQuestion:
    def test_question_with_secret_is_classified_and_never_sent_to_llm(
        self, workspace: Path
    ):
        llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_PLAIN])
        agent, _memory, log_path = _build_agent(workspace, llm)

        question = f"is this key real: {SECRET}?"
        answer = agent.run(user_question=question, file_hint=None)

        # Answer is clean
        assert SECRET not in answer

        # Neither LLM prompt saw it
        for call in llm.calls:
            assert SECRET not in call["user"]

        # JSONL is clean
        assert SECRET not in log_path.read_text(encoding="utf-8")

        # And the event trail names the user_input surface
        events = _events(log_path)
        question_class = next(
            e for e in events
            if e["event"] == "data_classified"
            and e["payload"].get("label") == "user_question"
        )
        assert question_class["payload"]["class"] == "secret"

        secret_events = [e for e in events if e["event"] == "secret_detected"]
        assert any(
            e["payload"]["surface"] == "user_input" for e in secret_events
        )


# ============================================================
# Defence in depth — LLM-side leak is also caught
# ============================================================

class TestLLMHallucinatedLeakIsCaught:
    def test_llm_echoing_a_secret_is_still_scrubbed_in_answer(
        self, workspace: Path
    ):
        # The "answer" from the LLM tries to leak a secret directly.
        # Even though it could only happen if the model invented the
        # key (the kernel redacted everything on the way in), defence
        # in depth still scrubs it on the way out.
        leaking_answer = (
            "Conclusion: trust me.\n"
            f"Facts:\n- here is the key: {SECRET} [general-knowledge]\n"
            "Sources:\n1. general-knowledge - general-knowledge\n"
            "Confidence: medium\nUnverified: nothing\nSafety: nothing\n"
        )

        llm = FakeLLM(responses=[PLAN_EMPTY, leaking_answer])
        agent, _memory, log_path = _build_agent(workspace, llm)

        answer = agent.run(user_question="please be careful", file_hint=None)

        assert SECRET not in answer
        assert "[REDACTED:openai-key]" in answer

        # And the kernel logged the leak attempt on the user_output surface.
        events = _events(log_path)
        secret_events = [e for e in events if e["event"] == "secret_detected"]
        assert any(
            e["payload"]["surface"] == "user_output" for e in secret_events
        )


# ============================================================
# Clean inputs do not trigger any safety events
# ============================================================

class TestCleanInputsAreSilent:
    def test_no_secret_no_secret_detected_event(self, workspace: Path):
        (workspace / "doc.txt").write_text("alpha beta gamma\n", encoding="utf-8")

        llm = FakeLLM(responses=[PLAN_FILE_READ, SYNTH_PLAIN])
        agent, _memory, log_path = _build_agent(workspace, llm)

        agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")

        events = _events(log_path)
        secret_events = [e for e in events if e["event"] == "secret_detected"]
        assert secret_events == []

        # data_classified events still fire (for audit), with safe classes.
        classified = [e for e in events if e["event"] == "data_classified"]
        assert classified, "classifier must always emit, even for clean input"
        for e in classified:
            assert e["payload"]["class"] in {"public", "private", "sensitive"}


# ============================================================
# S7 — third-party data gate on MemoryWritePolicy
# ============================================================

class TestThirdPartyDataGate:
    def test_first_party_owners_pass(self):
        policy = MemoryWritePolicy()
        for owner in ("self", "user", "session", "User", "  USER  "):
            d = policy.decide(
                content="A perfectly normal fact about the project.",
                tags=["fact"],
                source="user-explicit",
                owner=owner,
            )
            assert d.decision == "save", f"owner={owner!r} should be first-party"

    def test_third_party_without_consent_rejected(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="My client said something interesting last meeting.",
            tags=["fact"],
            source="user-explicit",
            owner="client",
        )
        assert d.decision == "reject"
        assert any("third-party" in r for r in d.reasons)

    def test_third_party_with_cross_owner_consent_passes(self):
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="My client gave permission to record this preference.",
            tags=["fact", "cross-owner-consent"],
            source="user-explicit",
            owner="client",
        )
        assert d.decision == "save"

    def test_default_owner_is_first_party(self):
        # Existing callers don't pass `owner` — they should keep working.
        policy = MemoryWritePolicy()
        d = policy.decide(
            content="A normal fact.",
            tags=["fact"],
            source="user-explicit",
        )
        assert d.decision == "save"
