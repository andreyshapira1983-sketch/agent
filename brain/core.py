"""
brain/core.py — The Brain (Cognitive Core)

The Brain is in full control of the reasoning cycle.
LLM is a tool. The Brain decides WHEN and HOW to use it.

Cycle:
    1. Receive input
    2. Build context (memory + goals + state)
    3. Decide: can Brain answer without LLM? (fast path)
    4. If not — call LLM via interface (slow path)
    5. Interpret the output
    6. Update goal stack
    7. Return action
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .skills.job import Job, JobStore
    from .skills.registry import SkillRegistry
    from .skills.workflow_runner import JobOutcome, ToolRunner

from .context_builder import ContextBuilder
from .explainer import Explainer, Explanation
from .goal_stack import GoalStack
from .interpreter import Interpreter
from .interfaces.llm_interface import LLMInterface
from .interfaces.memory_interface import MemoryInterface
from .policy import ActionRequest, PolicyDecision, PolicyEngine, PolicyVerdict
from .privacy import PIIRedactor
from .uncertainty import UncertaintyEstimator

# `skills` is a sibling package — import lazily inside methods to keep
# Brain creation cheap when no profession features are used.

logger = logging.getLogger(__name__)


_FEEDBACK_HISTORY = 128   # how many recent ThinkResults to remember for feedback correlation


def _new_action_id() -> str:
    return uuid.uuid4().hex[:16]


class _LLMSimilarityAdapter:
    """Adapter exposing LLMInterface as `Verifier.LLMJudge`.

    Builds a tiny one-shot prompt that asks the LLM for a numeric
    similarity score. Robust to malformed replies — always returns a
    float in [0, 1].
    """

    def __init__(self, llm: LLMInterface) -> None:
        self._llm = llm

    def similarity(self, original: str, candidate: str) -> float:
        """Ask the LLM for a similarity score.

        Returns NaN (not 1.0) when the judge cannot deliver a verdict —
        either the call raised, the reply did not contain a parseable
        number, or the parsed value is non-finite. Verifier interprets NaN
        as ``inconclusive`` and fails-closed for blocking checks, instead
        of silently treating a broken judge as a 100 % match.
        """
        prompt = (
            "You are a faithfulness judge. Compare ORIGINAL and CANDIDATE. "
            "Return a single number in [0, 1]: 1.0 means CANDIDATE preserves "
            "ALL facts from ORIGINAL without adding new ones; 0.0 means it "
            "contradicts or invents facts. Reply with ONLY the number."
            f"\n\nORIGINAL:\n{original}\n\nCANDIDATE:\n{candidate}"
        )
        try:
            reply = self._llm.call({
                "input":      prompt,
                "goals":      [],
                "history":    [],
                "facts":      [],
                "session_id": "verifier",
            })
        except Exception:  # noqa: BLE001 — verifier records inconclusive
            return float("nan")
        text = ""
        if isinstance(reply, dict):
            text = str(reply.get("content") or reply.get("text") or "")
        import re as _re
        m = _re.search(r"[01](?:\.\d+)?", text)
        if not m:
            return float("nan")
        try:
            score = float(m.group(0))
        except ValueError:
            return float("nan")
        if score != score:  # NaN guard
            return float("nan")
        return max(0.0, min(1.0, score))


@dataclass
class ThinkResult:
    """What the Brain returns after one reasoning cycle.

    `id` is a stable correlation handle. Pass it back to
    `Brain.feedback(action_id, was_correct)` once the action's outcome is
    known — that closes the loop and lets UncertaintyEstimator calibrate.
    """
    action: str                      # "respond" | "tool_call" | "wait" | "stop"
    content: Any                     # The actual payload
    confidence: float                # 0.0 – 1.0
    reasoning: str                   # Why this decision was made
    needs_human_approval: bool = False
    explanation: Explanation | None = None  # Set by Brain after explain()
    policy_verdict: PolicyVerdict | None = None  # Set by Brain after policy gate
    id: str = field(default_factory=_new_action_id)


class Brain:
    """
    The Cognitive Core.

    The Brain controls the LLM — not the other way around.
    It decides:
        - Whether to use the LLM at all
        - What context to give it
        - How to interpret its output
        - What action to take
    """

    def __init__(
        self,
        llm: LLMInterface,
        memory: MemoryInterface,
        redactor: PIIRedactor | None = None,
        policy: PolicyEngine | None = None,
        autonomy_level: int = 2,
        skill_registry: "SkillRegistry | None" = None,
        tool_runner: "ToolRunner | None" = None,
        job_store: "JobStore | None" = None,
    ) -> None:
        """
        Args:
            llm:      LLM adapter the Brain may call.
            memory:   Memory backend.
            redactor: Optional PIIRedactor. When provided:
                        - context sent to LLM is scrubbed of PII
                        - tokens like [EMAIL_1] in LLM output are restored
                          before returning to the caller and before being
                          stored in memory (so memory always holds real text
                          and is recoverable across process restarts).
            policy:   Optional PolicyEngine. When provided, every tool_call
                        ThinkResult is run through the engine and its verdict
                        attached to `result.policy_verdict`. DENY converts
                        the result to `action="wait"`. REQUIRE_APPROVAL sets
                        `needs_human_approval=True`. ALLOW clears the flag.
            autonomy_level: 0..5, see policy.py for semantics. Default 2 —
                        every tool_call needs human approval, which is the
                        safe starting point for a freelance agent.
            skill_registry: Optional SkillRegistry. Enables Brain.intake_job
                        to route Jobs to Professions.
            tool_runner: Optional ToolRunner for WorkflowRunner. Required if
                        you want intake_job to actually execute workflows.
            job_store: Optional JobStore. When provided, intake_job persists
                        status transitions; otherwise the Job is processed
                        in-memory only.
        """
        if not (0 <= autonomy_level <= 5):
            raise ValueError(f"autonomy_level must be 0..5, got {autonomy_level}")

        self._llm = llm
        self._memory = memory
        self._redactor = redactor
        self._policy = policy
        self._autonomy_level = int(autonomy_level)
        self._skill_registry = skill_registry
        self._tool_runner = tool_runner
        self._job_store = job_store
        self._goals = GoalStack()
        self._context_builder = ContextBuilder(memory=memory, redactor=redactor)
        self._interpreter = Interpreter()
        self._uncertainty = UncertaintyEstimator()
        self._explainer = Explainer()

        # Correlation buffer for Brain.feedback(action_id, was_correct).
        # We remember (action_id, calibrated_confidence) for the most recent
        # N cycles so feedback can find the prediction it's grading.
        self._recent: deque[tuple[str, float]] = deque(maxlen=_FEEDBACK_HISTORY)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def think(
        self,
        raw_input: str,
        session_id: str,
        *,
        system_prompt: str | None = None,
    ) -> ThinkResult:
        """Main reasoning cycle. Called once per user message or scheduled event.

        Args:
            raw_input:     What the user (or upstream channel) just said.
            session_id:    Stable handle for memory continuity.
            system_prompt: Optional persona override forwarded to the LLM
                           adapter. The adapter still appends its structured
                           output rules, so the JSON envelope stays intact.
                           ChatHandler uses this to give the agent a
                           conversational voice without surgery on the
                           default reasoning prompt.
        """
        logger.info("[Brain] Starting reasoning cycle | session=%s", session_id)

        # Step 1: Build full context.
        # Note we deliberately DO NOT store `raw_input` in memory yet —
        # otherwise ContextBuilder would put the just-stored user message
        # into `history`, while the LLM adapter ALSO renders it as the
        # explicit `input`, sending the same sentence twice. The user
        # input is committed to memory at the very end of this cycle so
        # the *next* cycle sees it as legitimate history.
        context = self._context_builder.build(
            raw_input=raw_input,
            goals=self._goals.current(),
            session_id=session_id,
            system_prompt=system_prompt,
        )

        # Step 2: Fast path — can the Brain answer without LLM?
        fast_result = self._try_fast_path(raw_input, context)
        if fast_result:
            fast_result.explanation = self._explainer.explain(
                result=fast_result,
                context=context,
                uncertainty=None,
            )
            self._record_for_feedback(fast_result, fast_result.confidence)
            self._commit_turn_to_memory(session_id, raw_input, fast_result)
            logger.info(
                "[Brain] Fast path | action=%s risk=%s",
                fast_result.action,
                fast_result.explanation.risk_level.value,
            )
            return fast_result

        # Step 3: Slow path — call LLM as a tool
        llm_output = self._llm.call(context)

        # Step 4: Interpret LLM output → structured action
        result = self._interpreter.interpret(llm_output, context)

        # Step 5: Uncertainty gate — Brain calibrates confidence adaptively
        uncertainty = self._uncertainty.estimate(
            llm_confidence=result.confidence,
            context=context,
        )
        if not uncertainty.should_act:
            logger.warning(
                "[Brain] Uncertainty gate blocked | calibrated=%.2f threshold=%.2f reasoning=%s",
                uncertainty.calibrated_confidence,
                uncertainty.threshold_used,
                uncertainty.reasoning,
            )
            blocked = ThinkResult(
                action="wait",
                content="Confidence too low. Requesting clarification.",
                confidence=uncertainty.calibrated_confidence,
                reasoning=uncertainty.reasoning,
                needs_human_approval=True,
            )
            blocked.explanation = self._explainer.explain(
                result=blocked,
                context=context,
                uncertainty=uncertainty,
            )
            self._record_for_feedback(blocked, uncertainty.calibrated_confidence)
            self._commit_turn_to_memory(session_id, raw_input, blocked)
            return blocked

        # Step 6: De-redact PII tokens.
        # LLM saw `[EMAIL_1]` etc.; tools, memory, and the user must see the
        # real values. Restoration walks `respond`/`clarify` strings AND
        # `tool_call` parameter dicts so we never invoke a tool with a
        # placeholder address (which would either fail at the protocol
        # layer or — worse — leak the placeholder into a downstream system).
        self._restore_pii(result, session_id)

        # Step 7: Policy gate — the single safety predicate for tool_call.
        # MUST run before the explanation step so the audit trail records
        # the final, post-policy action.
        if self._policy is not None and result.action == "tool_call":
            self._apply_policy(result, context)

        # Step 8: Explain the decision (audit & human approval).
        # Runs after Policy so a DENY/APPROVAL verdict shows up in the
        # explanation rather than the original LLM intent.
        result.explanation = self._explainer.explain(
            result=result,
            context=context,
            uncertainty=uncertainty,
        )

        # Step 9: Update goals based on result
        self._goals.update(result)

        # Step 10: Record this cycle for feedback correlation.
        self._record_for_feedback(result, uncertainty.calibrated_confidence)

        # Step 11: Commit the entire turn to memory (user input + reply)
        # so the next cycle sees it once, in the correct order.
        self._commit_turn_to_memory(session_id, raw_input, result)

        logger.info(
            "[Brain] Cycle complete | action=%s confidence=%.2f risk=%s policy=%s",
            result.action,
            result.confidence,
            result.explanation.risk_level.value,
            result.policy_verdict.decision.value if result.policy_verdict else "none",
        )
        return result

    def set_goal(self, goal: str, priority: int = 1) -> None:
        """Push a new goal onto the stack."""
        self._goals.push(goal, priority=priority)
        logger.info("[Brain] New goal set: %s (priority=%d)", goal, priority)

    def clear_goals(self) -> None:
        """Reset all goals."""
        self._goals.clear()
        logger.info("[Brain] Goals cleared")

    def status(self) -> dict:
        """Return current Brain state — for monitoring."""
        return {
            "active_goals":         self._goals.current(),
            "goal_depth":           self._goals.depth(),
            "uncertainty_stats":    self._uncertainty.calibration_stats(),
            "confidence_threshold": self._uncertainty.threshold(),
            "autonomy_level":       self._autonomy_level,
            "policy_rules":         (
                [r.id for r in self._policy.rules()] if self._policy else []
            ),
            "recent_actions":       len(self._recent),
        }

    @property
    def autonomy_level(self) -> int:
        return self._autonomy_level

    def feedback(
        self,
        was_correct: bool,
        *,
        action_id: str | None = None,
        predicted_confidence: float | None = None,
    ) -> bool:
        """Close the calibration loop: tell Brain whether an action's outcome
        matched what it predicted.

        Two forms are supported, in order of preference:

        1. **By action_id** (preferred):
                brain.feedback(was_correct=True, action_id=result.id)
            Brain looks up the calibrated_confidence it recorded for that
            ThinkResult and calibrates against it. This is the form an
            external orchestrator should use after a tool ran.

        2. **By explicit confidence** (legacy):
                brain.feedback(was_correct=True, predicted_confidence=0.7)
            Direct calibration without lookup. Useful in tests.

        If `action_id` is given but unknown (rotated out of the buffer), the
        call is a no-op and returns False — losing one feedback signal is
        much better than calibrating against a wrong confidence value.

        Returns:
            True if a calibration sample was actually recorded.
        """
        confidence: float | None = predicted_confidence
        if action_id is not None:
            for stored_id, stored_conf in reversed(self._recent):
                if stored_id == action_id:
                    confidence = stored_conf
                    break
            else:
                logger.warning(
                    "[Brain] feedback for unknown action_id=%s — ignored", action_id,
                )
                return False

        if confidence is None:
            raise ValueError(
                "feedback() needs either action_id (preferred) or predicted_confidence"
            )

        self._uncertainty.calibrate(predicted=confidence, was_correct=was_correct)
        return True

    # ------------------------------------------------------------------
    # Job intake — Profession routing entry point
    # ------------------------------------------------------------------

    def intake_job(
        self,
        job: "Job",
        *,
        profession_id: str | None = None,
    ) -> "JobOutcome":
        """Route an incoming Job to a matched Profession and execute it.

        Lifecycle (the Brain owns these state transitions):

            NEW  → MATCHED  → IN_PROGRESS → DELIVERED   (happy path)
                            ↘ FAILED                   (verifier rejected / step failed)
                 ↘ DECLINED                            (no Profession matched the brief)

        Args:
            job:           Validated Job from JobStore. Brain assumes the
                           store already persisted it with status=NEW.
            profession_id: Optional explicit profession to force-route to,
                           bypassing matching. Useful for the IMAP channel
                           when the user pre-tags the subject line.

        Returns:
            JobOutcome describing what happened — caller uses this to write
            the Verifier report into JobStore, audit it, and send the reply.

        Raises:
            RuntimeError if the Brain wasn't constructed with the required
            wiring (skill_registry + tool_runner).
        """
        from .skills.job import JobStatus  # local import — soft dependency
        from .skills.workflow_runner import (
            JobOutcome,
            WorkflowRunner,
        )

        if self._skill_registry is None or self._tool_runner is None:
            raise RuntimeError(
                "Brain.intake_job requires both skill_registry and tool_runner "
                "wired in __init__"
            )

        logger.info(
            "[Brain] intake_job | id=%s source=%s client=%s",
            job.id, job.source, job.client_id,
        )

        # ── 1) Match profession ─────────────────────────────────
        profession = None
        if profession_id:
            try:
                profession = self._skill_registry.get(profession_id)
            except KeyError:
                profession = None
        else:
            matches = self._skill_registry.match(job.brief)
            for m in matches:
                if m.can_serve:
                    profession = m.profession
                    break

        if profession is None:
            if self._job_store is not None:
                try:
                    self._job_store.update_status(
                        job.id, JobStatus.DECLINED,
                        note="no matching profession",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("[Brain] failed to mark job declined")
            outcome = JobOutcome(
                job_id=job.id,
                profession_id="",
                final_status=JobStatus.DECLINED,
            )
            outcome.add_note("no matching profession")
            return outcome

        # ── 2) Pre-flight policy check on the profession ───────
        if self._policy is not None:
            pre_request = ActionRequest(
                kind="job_intake",
                target=profession.id,
                risk_class=profession.risk_class,
                autonomy_level=self._autonomy_level,
                profession_id=profession.id,
            )
            verdict = self._policy.evaluate(pre_request)

            if verdict.denied:
                outcome = JobOutcome(
                    job_id=job.id,
                    profession_id=profession.id,
                    final_status=JobStatus.DECLINED,
                )
                outcome.add_note(f"policy DENY: {verdict.reason}")
                if self._job_store is not None:
                    try:
                        self._job_store.update_status(
                            job.id, JobStatus.DECLINED,
                            note=f"policy DENY: {verdict.reason}",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("[Brain] failed to mark job declined by policy")
                return outcome

            if verdict.requires_approval:
                # Park the job in MATCHED — not a terminal state. An
                # operator can re-trigger workflow execution after they
                # grant approval. We do NOT advance to IN_PROGRESS so a
                # follow-up `intake_job` call can resume cleanly.
                if self._job_store is not None:
                    try:
                        self._job_store.set_profession(job.id, profession.id)
                        self._job_store.update_status(
                            job.id, JobStatus.MATCHED,
                            note=f"policy APPROVAL needed: {verdict.reason}",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("[Brain] failed to park job pending approval")
                logger.warning(
                    "[Brain] Job %s parked: policy requires approval (rule=%s)",
                    job.id, verdict.rule_id,
                )
                outcome = JobOutcome(
                    job_id=job.id,
                    profession_id=profession.id,
                    final_status=JobStatus.MATCHED,
                    requires_approval=True,
                )
                outcome.add_note(f"policy APPROVAL: {verdict.reason}")
                return outcome

        # ── 3) Status transition RECEIVED → MATCHED → IN_PROGRESS ──
        if self._job_store is not None:
            try:
                self._job_store.set_profession(job.id, profession.id)
                self._job_store.update_status(
                    job.id, JobStatus.MATCHED,
                    note=f"matched profession={profession.id}",
                )
                self._job_store.update_status(
                    job.id, JobStatus.IN_PROGRESS,
                    note="workflow started",
                )
            except Exception:  # noqa: BLE001
                logger.exception("[Brain] failed to advance job state")

        # ── 4) Build Verifier with optional LLM judge ───────────
        from .skills.verifier import Verifier
        verifier = Verifier(llm_judge=_LLMSimilarityAdapter(self._llm))

        runner = WorkflowRunner(
            llm=self._llm,
            tools=self._tool_runner,
            verifier=verifier,
        )

        # ── 5) Execute workflow with a hard failure boundary ───
        # If the runner explodes (tool crash, LLM bork, anything),
        # we MUST land the job in FAILED — otherwise it stays stuck
        # in IN_PROGRESS forever and the warmup check will flag it
        # at every restart.
        try:
            outcome = runner.execute(profession, job)
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.exception("[Brain] workflow execution crashed for job %s", job.id)
            outcome = JobOutcome(
                job_id=job.id,
                profession_id=profession.id,
                final_status=JobStatus.FAILED,
            )
            outcome.add_note(f"workflow crashed: {exc!r}")

        # ── 6) Persist final status ─────────────────────────────
        if self._job_store is not None:
            try:
                self._job_store.update_status(
                    job.id, outcome.final_status,
                    note=outcome.notes[-1] if outcome.notes else None,
                )
            except Exception:  # noqa: BLE001
                logger.exception("[Brain] failed to persist final job status")

        return outcome

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _restore_pii(self, result: ThinkResult, session_id: str) -> None:
        """De-redact `[EMAIL_1]`-style placeholders in the outbound result.

        Covers two surfaces:
            * `respond` / `clarify` — `result.content` is a string we hand to
              the user. Restore in place.
            * `tool_call` — `result.content` is `{"tool_name": ..., "params": ...}`
              and `params` may carry PII the LLM masked while reasoning
              (e.g. `{"to": "[EMAIL_1]"}`). Walk the dict recursively so we
              never invoke a tool with a placeholder.
        """
        if self._redactor is None:
            return

        if result.action in {"respond", "clarify"} and isinstance(result.content, str):
            result.content = self._redactor.restore(result.content, session_id=session_id)
            return

        if result.action == "tool_call" and isinstance(result.content, dict):
            result.content = self._restore_walk(result.content, session_id)

    def _restore_walk(self, value: Any, session_id: str) -> Any:
        """Recursive de-redact for arbitrary nested dict/list payloads."""
        if self._redactor is None:
            return value
        if isinstance(value, str):
            return self._redactor.restore(value, session_id=session_id)
        if isinstance(value, dict):
            return {k: self._restore_walk(v, session_id) for k, v in value.items()}
        if isinstance(value, list):
            return [self._restore_walk(v, session_id) for v in value]
        if isinstance(value, tuple):
            return tuple(self._restore_walk(v, session_id) for v in value)
        return value

    def _commit_turn_to_memory(
        self,
        session_id: str,
        raw_input: str,
        result: ThinkResult,
    ) -> None:
        """Persist (user, assistant) for the just-finished turn.

        Stored at end-of-cycle so that ContextBuilder, called at the start
        of THIS cycle, did not see the current input as part of history
        (which would have duplicated it alongside `context["input"]`).
        The next cycle gets a clean, chronologically correct history.
        """
        if raw_input.strip():
            self._memory.store(session_id, "user", raw_input)
        if result.action in {"respond", "clarify"} and result.content:
            self._memory.store(session_id, "assistant", str(result.content))

    def _record_for_feedback(self, result: ThinkResult, calibrated_confidence: float) -> None:
        """Store (action_id, calibrated_confidence) for later feedback lookup.

        We store the *calibrated* confidence — that's what UncertaintyEstimator
        should learn from, not the raw LLM number. Idempotent: appending the
        same id twice is harmless because the lookup walks newest→oldest.
        """
        self._recent.append((result.id, float(calibrated_confidence)))

    def _apply_policy(self, result: ThinkResult, context: dict) -> None:
        """Run the PolicyEngine over a tool_call result and apply the verdict."""
        content = result.content if isinstance(result.content, dict) else {}
        tool_name = ""
        if isinstance(content, dict):
            # The LLM prompt asks for `tool_name`, but older personas /
            # adapters might emit `tool` or `name`. Accept all three so a
            # well-behaved LLM cannot bypass a per-tool policy rule by
            # returning the documented key.
            tool_name = str(
                content.get("tool_name", "")
                or content.get("tool", "")
                or content.get("name", "")
            )

        # `risk_class`, `profession_id`, and any "is_destructive" hint are
        # piped through the context dict so the orchestrator can populate
        # them once a Job is being executed under a Profession.
        request = ActionRequest(
            kind="tool_call",
            target=tool_name,
            is_destructive=bool(
                content.get("is_destructive", False)
                or context.get("tool_is_destructive", False)
            ) if isinstance(content, dict) else False,
            requires_approval_hint=result.needs_human_approval,
            risk_class=str(context.get("profession_risk_class", "low")),
            autonomy_level=int(context.get("autonomy_level", self._autonomy_level)),
            profession_id=context.get("profession_id"),
        )

        verdict = self._policy.evaluate(request)
        result.policy_verdict = verdict

        if verdict.denied:
            logger.warning(
                "[Brain] Policy DENIED tool_call '%s' (rule=%s)",
                tool_name, verdict.rule_id,
            )
            result.action = "wait"
            result.content = {
                "blocked_by_policy": True,
                "rule_id":           verdict.rule_id,
                "reason":            verdict.reason,
                "original_tool":     tool_name,
            }
            result.needs_human_approval = False
            result.reasoning = (
                f"{result.reasoning} [Policy={verdict.rule_id}: DENY — {verdict.reason}]"
            )
        elif verdict.requires_approval:
            result.needs_human_approval = True
            result.reasoning = (
                f"{result.reasoning} [Policy={verdict.rule_id}: APPROVAL — {verdict.reason}]"
            )
        else:  # ALLOW
            result.needs_human_approval = False
            result.reasoning = (
                f"{result.reasoning} [Policy={verdict.rule_id}: ALLOW]"
            )

    def _try_fast_path(self, raw_input: str, _context: dict) -> ThinkResult | None:
        """
        Handle trivial cases without calling LLM.
        Examples: empty input, explicit stop command.
        """
        stripped = raw_input.strip().lower()

        if not stripped:
            return ThinkResult(
                action="wait",
                content=None,
                confidence=1.0,
                reasoning="Empty input — nothing to process",
            )

        if stripped in {"stop", "quit", "exit", "halt"}:
            return ThinkResult(
                action="stop",
                content=None,
                confidence=1.0,
                reasoning="Explicit stop command received",
            )

        return None
