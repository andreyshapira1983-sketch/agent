"""
simulate.py — Brain Simulation & Diagnostic Report

Runs the Brain through a battery of realistic scenarios using mock LLM and Memory.
At the end prints a full diagnostic: what worked, what broke, what is suspicious.

Run:
    python simulate.py
"""

from __future__ import annotations

import asyncio
import textwrap
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from brain.core import Brain, ThinkResult
from brain.brain_loop import BrainLoop, InputMessage, LoopState
from brain.explainer import RiskLevel
from brain.interfaces.llm_interface import LLMInterface
from brain.interfaces.memory_interface import MemoryInterface
from brain.planner import Planner

console = Console()


# ══════════════════════════════════════════════════════════════════════
#  MOCK IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════

class MockMemory(MemoryInterface):
    """In-memory store — no SQLite, no ChromaDB needed for simulation."""

    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = {}
        self._facts: list[dict] = [
            {"text": "Paris is the capital of France"},
            {"text": "The agent should never delete user data without confirmation"},
            {"text": "API rate limit is 60 requests per minute"},
        ]

    def recall_history(self, session_id: str, limit: int = 10) -> list[dict]:
        return self._history.get(session_id, [])[-limit:]

    def recall_facts(self, query: str, top_k: int = 5) -> list[dict]:
        return self._facts[:top_k]

    def store(self, session_id: str, role: str, content: str) -> None:
        if session_id not in self._history:
            self._history[session_id] = []
        self._history[session_id].append({"role": role, "content": content})

    def forget(self, session_id: str) -> None:
        self._history.pop(session_id, None)

    def seed_history(self, session_id: str, messages: list[dict]) -> None:
        """Helper for tests — seed existing conversation."""
        self._history[session_id] = messages


class MockLLM(LLMInterface):
    """
    Controllable mock LLM.
    Each call pops a response from the queue.
    If queue is empty — returns a default respond.
    """

    def __init__(self) -> None:
        self._queue: list[dict] = []
        self._call_count = 0
        self._calls_log: list[dict] = []

    def push(self, response: dict) -> "MockLLM":
        """Chain-able: llm.push({...}).push({...})"""
        self._queue.append(response)
        return self

    def call(self, context: dict) -> dict:
        self._call_count += 1
        response = self._queue.pop(0) if self._queue else {
            "action": "respond",
            "content": "I understand. Let me help you with that.",
            "confidence": 0.85,
            "reasoning": "Default response — no specific instructions",
        }
        self._calls_log.append({
            "call_n": self._call_count,
            "input": context.get("input", "")[:60],
            "response": response,
        })
        return response

    def is_available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock-gpt-sim"

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def calls(self) -> list[dict]:
        return self._calls_log


# ══════════════════════════════════════════════════════════════════════
#  ISSUE TRACKER
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Issue:
    severity: str    # "BUG" | "WARN" | "INFO"
    module: str
    title: str
    detail: str


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    duration_ms: float
    result: ThinkResult | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════
#  SIMULATION
# ══════════════════════════════════════════════════════════════════════

class Simulation:
    def __init__(self) -> None:
        self._issues: list[Issue] = []
        self._scenario_results: list[ScenarioResult] = []

    def bug(self, module: str, title: str, detail: str) -> None:
        self._issues.append(Issue("BUG", module, title, detail))

    def warn(self, module: str, title: str, detail: str) -> None:
        self._issues.append(Issue("WARN", module, title, detail))

    def info(self, module: str, title: str, detail: str) -> None:
        self._issues.append(Issue("INFO", module, title, detail))

    def _brain(self, llm: MockLLM | None = None, memory: MockMemory | None = None) -> Brain:
        return Brain(
            llm=llm or MockLLM(),
            memory=memory or MockMemory(),
        )

    def _run_scenario(self, name: str, fn) -> ScenarioResult:
        console.rule(f"[bold cyan]{name}")
        t0 = datetime.utcnow()
        try:
            result = fn()
            ms = (datetime.utcnow() - t0).total_seconds() * 1000
            sr = ScenarioResult(name=name, passed=True, duration_ms=ms, result=result if isinstance(result, ThinkResult) else None)
            console.print(f"  [green]✓ passed[/] ({ms:.1f} ms)")
            return sr
        except Exception as exc:  # noqa: BLE001
            ms = (datetime.utcnow() - t0).total_seconds() * 1000
            err = traceback.format_exc()
            console.print(f"  [red]✗ EXCEPTION:[/] {exc}")
            self.bug("simulation", f"Exception in {name}", str(exc))
            return ScenarioResult(name=name, passed=False, duration_ms=ms, error=err)

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 1 — Fast path: empty input
    # ─────────────────────────────────────────────────────────────────
    def scenario_empty_input(self) -> ThinkResult:
        brain = self._brain()
        result = brain.think("", session_id="s1")

        assert result.action == "wait", f"Expected wait, got {result.action}"
        assert result.confidence == 1.0

        # DIAGNOSTIC: fast path skips the Explainer
        if result.explanation is None:
            self.bug(
                "brain/core.py",
                "Fast path returns explanation=None",
                "Brain._try_fast_path() returns ThinkResult without calling Explainer. "
                "Every decision should have an explanation for the audit log, "
                "but fast-path results have explanation=None.",
            )
        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 2 — Fast path: explicit stop
    # ─────────────────────────────────────────────────────────────────
    def scenario_fast_path_stop(self) -> ThinkResult:
        brain = self._brain()
        result = brain.think("stop", session_id="s2")

        assert result.action == "stop"

        if result.explanation is None:
            self.bug(
                "brain/core.py",
                "Fast path stop: explanation=None",
                "When user types 'stop', Brain returns immediately via _try_fast_path() "
                "without producing an Explanation. The BrainLoop dispatch and audit log "
                "receive ThinkResult with explanation=None.",
            )
        if result.needs_human_approval:
            self.warn(
                "brain/core.py",
                "Fast path stop does NOT require approval",
                "Fast path sets needs_human_approval=False for stop (correct). "
                "But Interpreter sets needs_human_approval=True for stop action — "
                "these two paths are inconsistent for the same action.",
            )
        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 3 — Normal respond cycle
    # ─────────────────────────────────────────────────────────────────
    def scenario_normal_respond(self) -> ThinkResult:
        llm = MockLLM().push({
            "action": "respond",
            "content": "Paris is the capital of France.",
            "confidence": 0.92,
            "reasoning": "Clear factual question, answer found in facts",
        })
        memory = MockMemory()
        brain = self._brain(llm=llm, memory=memory)
        brain.set_goal("Answer user geography questions", priority=2)

        result = brain.think("What is the capital of France?", session_id="s3")

        assert result.action == "respond"
        assert result.explanation is not None, "explanation must exist on normal path"
        assert result.explanation.risk_level == RiskLevel.LOW

        # DIAGNOSTIC: Brain never writes the interaction to memory
        history_after = memory.recall_history("s3")
        if not history_after:
            self.bug(
                "brain/core.py",
                "Brain.think() never writes to memory",
                "After Brain.think() completes, the user input and Brain's response "
                "are NOT stored in memory. memory.store() is never called inside think(). "
                "Next cycle will have empty history → context quality degrades → "
                "UncertaintyEstimator sees no history → lower calibrated confidence.",
            )
        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 4 — Low confidence → uncertainty gate blocks
    # ─────────────────────────────────────────────────────────────────
    def scenario_low_confidence(self) -> ThinkResult:
        llm = MockLLM().push({
            "action": "respond",
            "content": "I think maybe possibly...",
            "confidence": 0.15,   # Very low — uncertainty gate should block
            "reasoning": "Unsure about this topic",
        })
        brain = self._brain(llm=llm)
        result = brain.think("Explain quantum entanglement in detail", session_id="s4")

        assert result.action == "wait", f"Expected wait after uncertainty block, got {result.action}"
        assert result.needs_human_approval is True

        # DIAGNOSTIC: explanation is also None here (blocked before Step 6)
        if result.explanation is None:
            self.bug(
                "brain/core.py",
                "Uncertainty-blocked result has explanation=None",
                "When uncertainty gate fires (confidence < threshold), Brain returns early "
                "with a wait ThinkResult that has explanation=None. "
                "Human Approval callback receives ThinkResult with no explanation — "
                "the user sees no justification for why Brain is waiting.",
            )
        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 5 — Tool call with high confidence
    # ─────────────────────────────────────────────────────────────────
    def scenario_tool_call(self) -> ThinkResult:
        llm = MockLLM().push({
            "action": "tool_call",
            "content": {"tool": "web_search", "query": "current BTC price"},
            "confidence": 0.88,
            "reasoning": "Need real-time data that is not in memory",
        })
        brain = self._brain(llm=llm)
        brain.set_goal("Get current Bitcoin price", priority=3)

        result = brain.think("What is the current Bitcoin price?", session_id="s5")

        assert result.action == "tool_call"
        assert result.needs_human_approval is True   # Interpreter always flags tool_call
        assert result.explanation is not None
        assert result.explanation.risk_level == RiskLevel.MEDIUM

        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 6 — Malformed LLM output
    # ─────────────────────────────────────────────────────────────────
    def scenario_malformed_llm(self) -> ThinkResult:
        """LLM returns garbage — Interpreter must handle it gracefully."""
        llm = MockLLM().push({
            "garbage": "this is not structured",
            "random_field": 42,
            # no action, no content, no confidence
        })
        brain = self._brain(llm=llm)
        result = brain.think("Do something", session_id="s6")

        # Interpreter defaults: action="respond", confidence=0.8, content=None
        # With confidence=0.8 the uncertainty gate should pass
        # But content=None means respond with None content — that's a silent failure
        if result.action == "respond" and result.content is None:
            self.warn(
                "brain/interpreter.py",
                "Malformed LLM → respond with content=None",
                "When LLM returns garbage, Interpreter defaults action='respond' and "
                "content=None. Brain returns a respond ThinkResult with no content. "
                "BrainLoop._dispatch() checks 'if callable(response_cb) and result.content' "
                "so it silently skips the callback. User receives no response at all.",
            )
        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 7 — Unknown action from LLM
    # ─────────────────────────────────────────────────────────────────
    def scenario_unknown_action(self) -> ThinkResult:
        llm = MockLLM().push({
            "action": "launch_missiles",   # not in EXPECTED_ACTIONS
            "content": "firing...",
            "confidence": 0.99,
            "reasoning": "???",
        })
        brain = self._brain(llm=llm)
        result = brain.think("Do something dangerous", session_id="s7")

        # Interpreter should map unknown → wait
        if result.action != "wait":
            self.bug(
                "brain/interpreter.py",
                "Unknown action not defaulted to wait",
                f"LLM returned unknown action 'launch_missiles'. "
                f"Expected interpreter to return 'wait', got '{result.action}'.",
            )
        else:
            self.info(
                "brain/interpreter.py",
                "Unknown action correctly mapped to wait",
                "Interpreter correctly converts unknown LLM actions to 'wait'. Safe.",
            )
        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 8 — Goal stack accumulation
    # ─────────────────────────────────────────────────────────────────
    def scenario_goal_accumulation(self) -> None:
        llm = MockLLM()
        for _ in range(4):
            llm.push({
                "action": "respond",
                "content": "Done.",
                "confidence": 0.9,
                "reasoning": "Task complete",
            })

        brain = self._brain(llm=llm)

        goals = [
            "Find best flight to Tokyo",
            "Book the flight",
            "Notify the user",
        ]
        for g in goals:
            brain.set_goal(g)

        # Run 3 cycles — each "respond" ideally should complete one goal
        for i, g in enumerate(goals):
            brain.think(f"Task {i+1}", session_id="s8")

        status = brain.status()
        active = status["goal_depth"]

        if active == len(goals):
            self.bug(
                "brain/goal_stack.py",
                "Goals never marked complete after respond/tool_call",
                f"After {len(goals)} respond cycles, all {len(goals)} goals are still active. "
                "GoalStack.update() only marks goals complete on 'stop' action. "
                "Normal 'respond' and 'tool_call' actions never reduce goal depth. "
                "Goals accumulate indefinitely → context bloat → LLM prompt grows unbounded.",
            )
        elif active == 0:
            self.info("brain/goal_stack.py", "All goals auto-completed", "")
        else:
            self.warn("brain/goal_stack.py", f"{active}/{len(goals)} goals still active", "")

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 9 — Calibration feedback loop
    # ─────────────────────────────────────────────────────────────────
    def scenario_calibration(self) -> None:
        llm = MockLLM()
        brain = self._brain(llm=llm)

        initial_threshold = brain.status()["confidence_threshold"]

        # Simulate Brain being consistently overconfident (predicts 0.9, always wrong)
        for _ in range(15):
            brain.feedback(predicted_confidence=0.9, was_correct=False)

        after_threshold = brain.status()["confidence_threshold"]
        stats = brain.status()["uncertainty_stats"]

        if after_threshold > initial_threshold:
            self.info(
                "brain/uncertainty.py",
                "Calibration raises threshold on overconfidence ✓",
                f"Threshold rose from {initial_threshold:.2f} → {after_threshold:.2f} "
                f"after 15 overconfident-wrong predictions. Adaptive calibration works.",
            )
        else:
            self.bug(
                "brain/uncertainty.py",
                "Calibration not raising threshold on consistent overconfidence",
                f"After 15 wrong high-confidence predictions, threshold stayed at "
                f"{after_threshold:.2f}. Adaptive calibration not working.",
            )

        # Now simulate underconfidence (predicts 0.3, always correct)
        for _ in range(15):
            brain.feedback(predicted_confidence=0.3, was_correct=True)

        final_threshold = brain.status()["confidence_threshold"]

        if final_threshold < after_threshold:
            self.info(
                "brain/uncertainty.py",
                "Calibration lowers threshold on underconfidence ✓",
                f"Threshold fell from {after_threshold:.2f} → {final_threshold:.2f} "
                f"after 15 underconfident-correct predictions.",
            )

        console.print(f"  Threshold trajectory: {initial_threshold:.2f} → {after_threshold:.2f} → {final_threshold:.2f}")
        console.print(f"  Calibration stats: {stats}")

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 10 — Multi-turn conversation (history context)
    # ─────────────────────────────────────────────────────────────────
    def scenario_multi_turn(self) -> None:
        llm = MockLLM()
        memory = MockMemory()

        # Seed existing conversation history
        memory.seed_history("s10", [
            {"role": "user",      "content": "I want to book a flight to Tokyo"},
            {"role": "assistant", "content": "Sure! When would you like to fly?"},
            {"role": "user",      "content": "Next Friday"},
            {"role": "assistant", "content": "Searching for flights..."},
            {"role": "user",      "content": "Any business class options?"},
        ])

        llm.push({
            "action": "tool_call",
            "content": {"tool": "flight_search", "params": {"dest": "Tokyo", "class": "business"}},
            "confidence": 0.87,
            "reasoning": "5-message context confirms intent and preferences",
        })

        brain = self._brain(llm=llm, memory=memory)
        result = brain.think("Show me business class prices", session_id="s10")

        assert result.action == "tool_call"

        # Check that history was used (context quality signal)
        if result.explanation:
            chain_text = " ".join(result.explanation.reasoning_chain)
            if "5 history" in chain_text:
                self.info(
                    "brain/context_builder.py",
                    "History context correctly propagated to Explainer ✓",
                    "5-message history visible in explanation reasoning chain.",
                )

        # But history still won't be written back
        new_history = memory.recall_history("s10")
        if len(new_history) == 5:   # unchanged — Brain never wrote anything
            self.bug(
                "brain/core.py",
                "Memory not updated during multi-turn conversation",
                "After Brain responds to turn 6, memory still has only 5 messages. "
                "Brain reads history but never calls memory.store() — "
                "so the conversation never actually grows from Brain's perspective. "
                "Each think() call operates on stale context from before the session started.",
            )

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 11 — BrainLoop async: submit → dispatch → response
    # ─────────────────────────────────────────────────────────────────
    async def scenario_brainloop(self) -> None:
        llm = MockLLM().push({
            "action": "respond",
            "content": "Hello! I am ready to help.",
            "confidence": 0.95,
            "reasoning": "Greeting detected",
        })
        brain = self._brain(llm=llm)
        planner = Planner()
        loop = BrainLoop(brain=brain, planner=planner)

        responses: list[tuple[str, str]] = []
        tool_calls: list[Any] = []
        approval_calls: list[ThinkResult] = []

        async def on_response(session_id: str, content: str, result: ThinkResult) -> None:
            responses.append((session_id, content))

        async def on_tool_call(session_id: str, content: Any, result: ThinkResult) -> None:
            tool_calls.append(content)

        async def on_approval(result: ThinkResult) -> bool:
            approval_calls.append(result)
            return True  # auto-approve in simulation

        loop.on_response = on_response
        loop.on_tool_call = on_tool_call
        loop.on_approval = on_approval

        await loop.start()
        assert loop.state() == LoopState.RUNNING

        submitted = await loop.submit(InputMessage(content="Hello!", session_id="loop1"))
        assert submitted

        # Give the loop time to process
        await asyncio.sleep(0.2)

        await loop.stop()
        assert loop.state() == LoopState.STOPPED

        stats = loop.stats()
        console.print(f"  Loop cycles: {stats['cycles']}  errors: {stats['errors']}")

        if stats["cycles"] == 0:
            self.bug(
                "brain/brain_loop.py",
                "BrainLoop processed 0 cycles despite submitted message",
                "Message was submitted but no cycles ran. Check _run() logic.",
            )
        if stats["errors"] > 0:
            self.bug("brain/brain_loop.py", f"BrainLoop had {stats['errors']} errors", "")

        if not responses:
            self.warn(
                "brain/brain_loop.py",
                "on_response never called after respond action",
                "BrainLoop dispatched 'respond' but on_response callback was never called. "
                "Check _dispatch() logic.",
            )

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 12 — BrainLoop: queue full stress test
    # ─────────────────────────────────────────────────────────────────
    async def scenario_queue_overflow(self) -> None:
        llm = MockLLM()
        brain = self._brain(llm=llm)
        planner = Planner()
        # Use the new max_queue_size parameter
        loop = BrainLoop(brain=brain, planner=planner, max_queue_size=2)

        await loop.start()

        # Submit 5 messages to a queue of size 2 — some should be dropped
        results = []
        for i in range(5):
            ok = await loop.submit(InputMessage(content=f"msg {i}", session_id="qtest"))
            results.append(ok)

        await asyncio.sleep(0.05)
        await loop.stop()

        accepted = sum(1 for r in results if r)
        dropped  = sum(1 for r in results if not r)
        console.print(f"  Submitted 5 to queue(size=2): {accepted} accepted, {dropped} dropped")

        if dropped == 0:
            self.warn(
                "brain/brain_loop.py",
                "Queue overflow not triggered despite small queue",
                "Expected some messages to be dropped with max_queue_size=2, but all were accepted. "
                "The loop may have drained messages faster than we submitted.",
            )
        else:
            self.info(
                "brain/brain_loop.py",
                f"Queue overflow protection works ✓ ({dropped} messages dropped)",
                f"max_queue_size=2 correctly dropped {dropped}/5 messages.",
            )

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 13 — Clarify action
    # ─────────────────────────────────────────────────────────────────
    def scenario_clarify(self) -> ThinkResult:
        llm = MockLLM().push({
            "action": "clarify",
            "content": "Could you please be more specific about what you mean?",
            "confidence": 0.80,
            "reasoning": "Input is ambiguous — need more context",
        })
        memory = MockMemory()
        # Seed history so context quality is high enough to pass uncertainty gate
        memory.seed_history("s13", [
            {"role": "user",      "content": "I need help with my project"},
            {"role": "assistant", "content": "Sure, what kind of project?"},
            {"role": "user",      "content": "It's about automation"},
        ])
        brain = self._brain(llm=llm, memory=memory)
        brain.set_goal("Understand user's project", priority=1)
        result = brain.think("Do the thing", session_id="s13")

        assert result.action == "clarify", (
            f"Expected clarify, got {result.action} "
            f"(confidence={result.confidence:.2f}, reasoning={result.reasoning})"
        )
        assert result.explanation is not None
        assert result.explanation.risk_level == RiskLevel.LOW

        return result

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 14 — Planner not integrated into Brain.think()
    # ─────────────────────────────────────────────────────────────────
    def scenario_planner_integration(self) -> None:
        """
        Brain.think() never interacts with Planner.
        Planner lives in BrainLoop._drive_planner() but Brain itself
        cannot create or advance a plan during think().
        """
        llm = MockLLM().push({
            "action": "respond",
            "content": [
                {"step": "Search flights"},
                {"step": "Compare prices"},
                {"step": "Book best option"},
            ],
            "confidence": 0.9,
            "reasoning": "Multi-step task detected — returning plan",
        })
        brain = self._brain(llm=llm)
        result = brain.think("Plan a trip to Tokyo for me", session_id="s14")

        # Brain has no reference to Planner — it can't create a plan
        self.warn(
            "brain/core.py + brain/planner.py",
            "Brain.think() has no access to Planner",
            "When LLM returns a plan (list of steps), Brain cannot call Planner.create_plan(). "
            "Brain.__init__() doesn't accept a Planner parameter. "
            "BrainLoop._drive_planner() exists but is never triggered automatically. "
            "The Planner and Brain are architecturally disconnected.",
        )

    # ─────────────────────────────────────────────────────────────────
    #  SCENARIO 15 — Confidence boundary: exactly at threshold
    # ─────────────────────────────────────────────────────────────────
    def scenario_confidence_boundary(self) -> None:
        """Test behavior right at the uncertainty threshold (0.60 default)."""
        for conf_raw, label in [(0.60, "AT threshold"), (0.59, "BELOW threshold"), (0.61, "ABOVE threshold")]:
            llm = MockLLM().push({
                "action": "respond",
                "content": "Border test.",
                "confidence": conf_raw,
                "reasoning": "boundary test",
            })
            brain = self._brain(llm=llm)
            result = brain.think(f"Test at {conf_raw}", session_id=f"sb_{conf_raw}")

            # Calibrated score depends on context signals too — not just LLM confidence
            calibrated = result.confidence
            action = result.action
            console.print(f"  LLM conf={conf_raw:.2f} → calibrated={calibrated:.3f} → action={action} [{label}]")

        self.info(
            "brain/uncertainty.py",
            "Confidence boundary behavior",
            "The uncertainty gate uses CALIBRATED confidence, not raw LLM confidence. "
            "Context signals (history, facts, goals) shift the effective threshold. "
            "With empty context, calibrated score ≈ LLM_conf × 0.55 + signals × 0.45 — "
            "so LLM confidence=0.60 may still result in 'wait' if context is poor.",
        )

    # ══════════════════════════════════════════════════════════════════
    #  DISPLAY HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _print_result(self, result: ThinkResult | None) -> None:
        if result is None:
            return
        exp = result.explanation
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column(style="dim", width=22)
        table.add_column()
        table.add_row("action",     f"[bold]{result.action}[/]")
        table.add_row("confidence", f"{result.confidence:.0%}")
        table.add_row("approval?",  str(result.needs_human_approval))
        table.add_row("reasoning",  textwrap.shorten(result.reasoning, 70))
        if exp:
            table.add_row("risk_level", f"{exp.risk_level.emoji()} {exp.risk_level.value}")
            table.add_row("summary",    textwrap.shorten(exp.summary, 70))
        else:
            table.add_row("explanation", "[red]None ← BUG[/]")
        console.print(table)

    # ══════════════════════════════════════════════════════════════════
    #  DIAGNOSTIC REPORT
    # ══════════════════════════════════════════════════════════════════

    def print_report(self) -> None:
        console.rule("[bold white]DIAGNOSTIC REPORT")

        # Scenario summary table
        t = Table(title="Scenario Results", box=box.ROUNDED)
        t.add_column("#",       style="dim",   width=3)
        t.add_column("Scenario",               width=38)
        t.add_column("Status",                 width=8)
        t.add_column("ms",      style="dim",   width=7)

        for i, sr in enumerate(self._scenario_results, 1):
            status = "[green]PASS[/]" if sr.passed else "[red]FAIL[/]"
            t.add_row(str(i), sr.name, status, f"{sr.duration_ms:.0f}")
        console.print(t)

        # Issues table
        if not self._issues:
            console.print("\n[green bold]No issues found![/]")
            return

        bugs  = [x for x in self._issues if x.severity == "BUG"]
        warns = [x for x in self._issues if x.severity == "WARN"]
        infos = [x for x in self._issues if x.severity == "INFO"]

        console.print(
            f"\n[red bold]{len(bugs)} BUG(s)[/]  "
            f"[yellow]{len(warns)} WARNING(s)[/]  "
            f"[blue]{len(infos)} INFO[/]"
        )

        for issue in self._issues:
            color = {"BUG": "red", "WARN": "yellow", "INFO": "blue"}[issue.severity]
            panel = Panel(
                f"[dim]{issue.module}[/]\n\n{issue.detail}",
                title=f"[{color} bold][{issue.severity}] {issue.title}[/]",
                border_style=color,
                padding=(0, 1),
            )
            console.print(panel)

    # ══════════════════════════════════════════════════════════════════
    #  RUN ALL
    # ══════════════════════════════════════════════════════════════════

    def run_all(self) -> None:
        console.print(Panel(
            "[bold]Brain Simulation & Diagnostic[/]\n"
            "Mock LLM + Mock Memory — no real API calls\n"
            "Runs 15 scenarios, collects bugs/warnings, prints report",
            title="[cyan bold]BRAIN SIMULATION[/]",
            border_style="cyan",
        ))

        # ── Sync scenarios ──────────────────────────────────────────
        sync_scenarios = [
            ("Empty input (fast path)",           self.scenario_empty_input),
            ("Fast path stop",                    self.scenario_fast_path_stop),
            ("Normal respond cycle",              self.scenario_normal_respond),
            ("Low confidence → uncertainty gate", self.scenario_low_confidence),
            ("Tool call (high confidence)",       self.scenario_tool_call),
            ("Malformed LLM output",              self.scenario_malformed_llm),
            ("Unknown action from LLM",           self.scenario_unknown_action),
            ("Goal stack accumulation",           self.scenario_goal_accumulation),
            ("Calibration feedback loop",         self.scenario_calibration),
            ("Multi-turn conversation",           self.scenario_multi_turn),
            ("Clarify action",                    self.scenario_clarify),
            ("Planner integration check",         self.scenario_planner_integration),
            ("Confidence boundary tests",         self.scenario_confidence_boundary),
        ]

        for name, fn in sync_scenarios:
            sr = self._run_scenario(name, fn)
            if sr.result:
                self._print_result(sr.result)
            self._scenario_results.append(sr)

        # ── Async scenarios ─────────────────────────────────────────
        async def run_async():
            console.rule("[bold cyan]BrainLoop: submit → dispatch → response")
            t0 = datetime.utcnow()
            try:
                await self.scenario_brainloop()
                ms = (datetime.utcnow() - t0).total_seconds() * 1000
                self._scenario_results.append(ScenarioResult("BrainLoop async dispatch", True, ms))
                console.print(f"  [green]✓ passed[/] ({ms:.1f} ms)")
            except Exception as exc:  # noqa: BLE001
                ms = (datetime.utcnow() - t0).total_seconds() * 1000
                self._scenario_results.append(ScenarioResult("BrainLoop async dispatch", False, ms, error=str(exc)))
                console.print(f"  [red]✗ EXCEPTION:[/] {exc}")
                self.bug("brain/brain_loop.py", f"BrainLoop exception: {exc}", traceback.format_exc())

            console.rule("[bold cyan]BrainLoop: queue overflow check")
            t0 = datetime.utcnow()
            await self.scenario_queue_overflow()
            ms = (datetime.utcnow() - t0).total_seconds() * 1000
            self._scenario_results.append(ScenarioResult("BrainLoop queue overflow", True, ms))
            console.print(f"  [green]✓ passed[/] ({ms:.1f} ms)")

        asyncio.run(run_async())

        # ── Report ──────────────────────────────────────────────────
        self.print_report()


# ══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    sim = Simulation()
    sim.run_all()
