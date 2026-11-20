"""Attempt loop: plan, execute, verdict, replan; the tenth piece of the core/loop.py split, mixed into AgentLoop."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from core.assumption_registry import extract_from_plan
from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.failure_cards import with_past_experience
from core.file_request_intent import force_file_hint_read_when_explicit
from core.model_usage import ModelBudgetExceeded
from core.models import ErrorObject, Goal, Plan, PlanStep
from core.observation_round import (
    charged_attempts,
    continue_after_observation,
    round_failsafe,
    steps_to_run,
)
from core.planner import PlannerOutput
from core.reasoning_action_check import check_by_rationale, check_reasoning_actions
from core.replan import (
    ReplanTrigger,
    count_failures,
    dropped_step_triggers,
    format_replan_context,
)
from core.social_turn import is_social_turn
from core.task_complexity import can_skip_planner


@dataclass
class AttemptState:
    """What the attempt loop carries within one run; field names equal the former `_run_inner` locals so the mechanical substitution `name -> st.name` stays verifiable by tests/test_loop_attempt_split.py."""

    user_question: str
    file_hint: str | None
    goal: Goal
    planner_history: str
    failure_history: list[ReplanTrigger]
    local_critique_active: bool
    forced_sources: list[str] | None
    forced_reasoning: str
    forced_warnings: list[str]
    _task_planner_llm: Any
    _run_assumptions: Any
    _cp: Any

    # Working state between attempts: advice and bans from the previous attempt's policy.decide(); empty on the first.
    advice_for_planner: str = ""
    forbidden_actions: tuple[tuple[str, str], ...] = ()
    attempt: int = 0

    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Typed evidence chain; grows alongside the artifacts.
    chain: ProvenanceChain = field(default_factory=ProvenanceChain)
    planner_out: PlannerOutput | None = None
    plan: Plan | None = None
    replan_exhausted: bool = False
    #: Cheap path: raised only by the planner-skip branch on a trivial no-tool turn; trims synthesizer context, forces the cheap model tier and skips the knowledge pipeline.
    cheap_path_active: bool = False
    #: Shadow S2: set on stagnation, read at run end (what an early stop would have cost); stops nothing.
    _stagnation_shadow: dict[str, Any] | None = None

    #: Fields the caller reads after the loop. Declared so a silent loss of output cannot happen; the test requires `_run_inner` to unpack exactly this set and the loop to write every name. Everything else is input or working state and does not leave.
    OUTPUTS: ClassVar[frozenset[str]] = frozenset({
        "artifacts",
        "chain",
        "planner_out",
        "plan",
        "attempt",
        "replan_exhausted",
        "cheap_path_active",
        "_stagnation_shadow",
    })


class AgentLoopAttempt:
    """Bounded replanning loop around the plan and its execution; the members are host-contract declarations (no assignments, so nothing is created or shadowed at runtime)."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        planner: Any
        replan_policy: Any
        _current_attempt: int
        _termination_guard: Any

        # Declared as CALLABLE attributes: a stub function with an empty body is read by analyzers as a function without return (false E1111).
        _execute_steps_parallel: Any
        _sensor_failed: Any

        # Taken from a sibling mixin via the MRO; the cross-module link must be written down.
        _defect_signals: Any
        _executed_tools: Any
        cheap_path_enabled: Any
        last_referent_decision: Any

    def _run_attempt_loop(self, st: AttemptState) -> None:
        """Run attempts until an answer comes out or the budget ends; returns nothing, everything the loop produced lies in `st`."""
        while True:
            st.attempt += 1
            self._current_attempt = st.attempt

            # Structured `replan_attempt` event for every iteration after the first.
            if st.attempt > 1:
                self.log.log(
                    "replan_attempt",
                    {
                        "attempt": st.attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(st.advice_for_planner),
                        "forbidden_action_count": len(st.forbidden_actions),
                        "failure_counts_so_far": dict(
                            count_failures(st.failure_history)
                        ),
                    },
                )

            failure_context = format_replan_context(
                st.failure_history,
                st.attempt,
                round_failsafe(self),
                advice=st.advice_for_planner,
                forbidden_actions=st.forbidden_actions,
            )

            if st.attempt == 1 and st.forced_sources is not None:
                st.planner_out = PlannerOutput(
                    reasoning=st.forced_reasoning,
                    sources=st.forced_sources,
                    raw_response="",
                    warnings=st.forced_warnings,
                )
            else:
                # Local-critique path: resolved referent + critique/show-only skips planner tools and synthesises from analysis_target.
                if (
                    st.local_critique_active
                    and st.attempt == 1
                    and not failure_context.strip()
                ):
                    st.planner_out = PlannerOutput(
                        reasoning=(
                            "Local critique path: referent resolved — answering "
                            "from analysis_target without tools or memory/GK."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_local_critique"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "referent_local_critique",
                            "fallback": "local_critique",
                        },
                    )
                    self.log.log(
                        "planner_local_critique",
                        {
                            "question_chars": len(st.user_question),
                            "kind": (
                                None
                                if self.last_referent_decision is None
                                or self.last_referent_decision.primary is None
                                else self.last_referent_decision.primary.kind
                            ),
                        },
                    )
                # Cheap-path gate: trivial no-tool input never needs a tool; skip the planner call on the first attempt with no failure/replan context.
                elif (
                    self.cheap_path_enabled
                    and st.attempt == 1
                    and not failure_context.strip()
                    and (can_skip_planner(st.user_question, file_hint=st.file_hint)
                         or is_social_turn(self, st.user_question, file_hint=st.file_hint))
                ):
                    st.planner_out = PlannerOutput(
                        reasoning=(
                            "Cheap path: trivial no-tool input — answering from "
                            "general knowledge and memory without a planner call."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_cheap_path"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "trivial no-tool input",
                            "fallback": "cheap_path",
                        },
                    )
                    self.log.log(
                        "planner_cheap_path",
                        {
                            "question_chars": len(st.user_question),
                            "reason": "trivial no-tool input",
                        },
                    )
                    st.cheap_path_active = True
                else:
                    # The planner is always called: the plan cache was removed (its invalidator made it unreachable, 0 hits); caching would be a new feature with its own contract.
                    try:
                        st.planner_out = self.planner.plan(
                            question=st.user_question,
                            file_hint=st.file_hint,
                            history=st.planner_history,
                            failure_context=failure_context,
                            forbidden_actions=st.forbidden_actions,
                            llm=st._task_planner_llm,
                        )
                    except ModelBudgetExceeded as exc:
                        self._save_budget_pause_checkpoint(
                            st._cp,
                            goal=st.goal,
                            question=st.user_question,
                            file_hint=st.file_hint,
                            current_phase="planning",
                            plan=st.plan,
                            blocked=exc,
                        )
                        # The other exit: settle the run objects before the exception leaves, or `pending` would read as never started.
                        self._settle_run_objects(st, "failed")
                        raise
                st.planner_out = force_file_hint_read_when_explicit(
                    st.planner_out,
                    question=st.user_question,
                    file_hint=st.file_hint,
                )
            self.log.log(
                "planner",
                {
                    "reasoning": st.planner_out.reasoning,
                    "tools_chosen": [s["tool"] for s in st.planner_out.sources],
                    "warnings": st.planner_out.warnings,
                    "raw_chars": len(st.planner_out.raw_response),
                    "attempt": st.attempt,
                    "replan_context_chars": len(failure_context),
                },
            )
            # Surface planner hallucinations as an event: when every step was dropped (plan_empty_after_drop) the loop would synthesise a confident answer from general knowledge.
            if st.planner_out.dropped_tools:
                self.log.log(
                    "plan_tool_drop",
                    {
                        "dropped": st.planner_out.dropped_tools,
                        "plan_empty_after_drop": not st.planner_out.sources,
                        "attempt": st.attempt,
                    },
                )

            # MAST FM-2.6: reasoning-action consistency check.
            try:
                # The rationale belongs to the step; the vocabulary check over general prose fired on 54% of turns, so the planner's per-step `rationale` is used (core/planner.py).
                _sources = list(st.planner_out.sources)
                _with_rationale = sum(1 for s in _sources if s.get("rationale"))
                _ra_mode = "rationale" if _with_rationale else "keywords"
                _ra_report = (
                    check_by_rationale(_sources)
                    if _with_rationale
                    else check_reasoning_actions(
                        st.planner_out.reasoning, [s["tool"] for s in _sources])
                )
                if _ra_report.has_mismatch:
                    self.log.log(
                        "reasoning_action_mismatch",
                        {
                            **_ra_report.to_log_payload(),
                            # What judged and how many steps came with a rationale: distinguishes the structural check from the fallback vocabulary one.
                            "mode": _ra_mode,
                            "steps_with_rationale": _with_rationale,
                            "steps_total": len(_sources),
                            "attempt": st.attempt,
                        },
                    )
                    # Banked with the episode as well as logged; still an observer, but the per-run journal vanishes from the agent's own memory.
                    self._defect_signals.append("reasoning_action_mismatch")
            except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("reasoning_action_check", exc)

            st.plan = self._build_plan(st.goal, st.planner_out.sources)
            self.log.log("plan", st.plan, steps=len(st.plan.steps), attempt=st.attempt)
            st._cp.save_plan(attempt=st.attempt, step_ids=[s.id for s in st.plan.steps])

            # Layer 5: extract plan-level assumptions on the first attempt only.
            if st.attempt == 1:
                try:
                    _plan_assumptions = extract_from_plan(
                        st.planner_out.sources,
                        question=st.user_question,
                        run_id=getattr(self.log, "trace_id", ""),
                    )
                    st._run_assumptions.register_many(_plan_assumptions)
                    if st._run_assumptions.assumptions:
                        self.log.log(
                            "assumptions_registered",
                            {
                                "count": len(st._run_assumptions),
                                "assumptions": st._run_assumptions.to_log_payload(),
                            },
                        )
                except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
                    self._sensor_failed("assumption_extractor", exc)

            attempt_artifacts: dict[str, dict[str, Any]] = {}
            attempt_failures: list[ReplanTrigger] = []
            attempt_chain = ProvenanceChain()

            # Planner JSON parse failure: empty `sources` is a contract break, not an intentional general-knowledge plan; treat it as a failure so replan_policy.decide() retries or trips `replan_exhausted`.
            plan_parse_failed = (
                "plan_parse_failed" in (st.planner_out.warnings or ())
            )
            if plan_parse_failed:
                _parse_diag = dict(getattr(st.planner_out, "diagnostics", {}) or {})
                self.log.log(
                    "plan_parse_failed",
                    {
                        "attempt": st.attempt,
                        "warnings": list(st.planner_out.warnings),
                        "raw_chars": len(st.planner_out.raw_response),
                        # Sanitised, length-capped preview.
                        "raw_preview": _parse_diag.get("raw_preview")
                        or st.planner_out.raw_response[:240],
                        "diagnostics": _parse_diag,
                    },
                )
                attempt_failures.append(
                    ReplanTrigger(
                        code="plan_parse_failed",
                        step_id="planner",
                        tool_name=None,
                        arguments={},
                        reason=(
                            "Planner LLM reply did not parse as JSON "
                            f"(raw_chars={len(st.planner_out.raw_response)})."
                        ),
                        attempt=st.attempt,
                    )
                )

            # A sanitiser-dropped step is a plan failure and is recorded as one; declared in test_loop_attempt_split.py.
            attempt_failures.extend(dropped_step_triggers(st.planner_out.warnings, attempt=st.attempt))

            for step, outcome, trigger in self._execute_steps_parallel(steps_to_run(self, st, attempt_artifacts)):
                if outcome is None:
                    step.status = "failed"
                    if trigger is not None:
                        attempt_failures.append(with_past_experience(self, trigger))
                    continue
                self._executed_tools.append(outcome["tool"])
                attempt_artifacts[outcome["label"]] = {
                    "tool": outcome["tool"],
                    "output": outcome["output"],
                    "issues": outcome["issues"],
                }
                # Typed evidence; the output is already redacted, so the excerpt in the chain is safe.
                ev = evidence_from_tool_result(
                    tool_name=outcome["tool"],
                    arguments=outcome.get("arguments"),
                    output=outcome["output"],
                    status="success",
                )
                if ev is not None:
                    attempt_chain.add(ev)
                step.status = "done"
                st._cp.save_act(
                    label=outcome["label"],
                    tool=outcome["tool"],
                    chars=len(str(outcome["output"])),
                    status="done",
                )

            # Success: a 0-step plan, or at least one artifact; `plan_parse_failed` is not success. Record what failed BEFORE deciding success, so an attempt with one failed and one working step keeps its triggers.
            st.failure_history.extend(attempt_failures)

            if (not st.plan.steps and not plan_parse_failed) or attempt_artifacts:
                # Results accumulate over observation rounds (core/observation_round.py).
                st.artifacts = {**st.artifacts, **attempt_artifacts}
                st.chain = ProvenanceChain([*st.chain.evidences, *attempt_chain.evidences])
                if continue_after_observation(self, st, attempt_artifacts):
                    continue
                break

            # MAST FM-1.5: same failure signature twice in a row means looping; observational only.
            try:
                _stag = self._termination_guard.observe_attempt(
                    attempt=st.attempt,
                    failure_codes=[t.code for t in attempt_failures],
                    artifact_labels=list(attempt_artifacts.keys()),
                )
                if _stag is not None:
                    self.log.log("stagnation_detected", _stag.to_log_payload())
                    # Shadow accounting: record where a stop would have happened; nothing is stopped.
                    st._stagnation_shadow = {
                        "attempt": st.attempt,
                        "artifacts_at_detection": sorted(attempt_artifacts.keys()),
                        "repeat_count": _stag.repeat_count,
                        "failure_codes": list(_stag.failure_codes),
                    }
            except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("stagnation_shadow", exc)

            decision = self.replan_policy.decide(
                failure_history=st.failure_history,
                completed_attempts=charged_attempts(self, st.attempt, st.failure_history),
            )

            if decision.action == "continue":
                # Log `replan` only when another attempt will follow.
                self.log.log(
                    "replan",
                    {
                        "attempt": st.attempt,
                        "next_attempt": st.attempt + 1,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": [t.code for t in attempt_failures],
                        "details": [
                            {
                                "step_id": t.step_id,
                                "tool": t.tool_name,
                                "code": t.code,
                                "reason": t.reason,
                            }
                            for t in attempt_failures
                        ],
                        "decision": decision.to_log_payload(),
                    },
                )
                st.advice_for_planner = decision.advice_for_planner
                st.forbidden_actions = decision.forbidden_actions
                continue

            # Policy said stop: emit an exhaustion event and an `error` event (kept for consumers grepping `code=replan_exhausted`); the synthesizer explains honestly.
            st.replan_exhausted = True
            err = ErrorObject(
                source="loop",
                code="replan_exhausted",
                message=(
                    f"Re-planning stopped after {st.attempt} attempt(s): "
                    f"{decision.reason}. Failure codes: "
                    f"{[t.code for t in st.failure_history]}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "attempts": st.attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "failure_codes": [t.code for t in st.failure_history],
                },
            )
            self.log.log("error", err)
            self.log.log(
                "replan_exhausted",
                {
                    "attempts": st.attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "triggers": [t.code for t in attempt_failures],
                },
            )
            break

        # The in-run Goal/Plan are log objects; they must be settled so a journal reader can tell a finished run from an abandoned one.
        self._settle_run_objects(st, "failed" if st.replan_exhausted else "done")

    def _settle_run_objects(self, st: AttemptState, status: str) -> None:
        """Write the verdict onto the in-run log objects and journal it; called from BOTH exits (a budget interruption re-raises past the normal end and would leave `pending`, meaning never started)."""
        st.goal.status = status  # type: ignore[assignment]
        if st.plan is not None:
            st.plan.status = status  # type: ignore[assignment]
        self.log.log("run_objects_settled", {
            "goal_id": st.goal.id,
            "plan_id": st.plan.id if st.plan is not None else None,
            "status": status,
            "attempts": st.attempt,
        })

    def _build_plan(self, goal: Goal, sources: list[dict[str, Any]]) -> Plan:
        plan = Plan(goal_id=goal.id)
        for i, src in enumerate(sources, start=1):
            plan.steps.append(  # pylint: disable=no-member  # pydantic list field, real list at runtime
                PlanStep(
                    plan_id=plan.id,
                    order=i,
                    action_spec={
                        "type": "tool_call",
                        "tool_name": src["tool"],
                        "arguments": src["arguments"],
                        "source_label": src["label"],
                    },
                    expected_outcome=src["expected_outcome"],
                )
            )
        plan.status = "in_progress"
        return plan

    @staticmethod
    def _checkpoint_step_summaries(plan: Plan | None) -> list[dict[str, Any]]:
        if plan is None:
            return []
        summaries: list[dict[str, Any]] = []
        for step in plan.steps:
            action = step.action_spec or {}
            summaries.append(
                {
                    "id": step.id,
                    "order": step.order,
                    "tool": action.get("tool_name") or action.get("tool"),
                    "source_label": action.get("source_label"),
                    "status": step.status,
                    "expected_outcome": step.expected_outcome,
                }
            )
        return summaries

    def _save_budget_pause_checkpoint(
        self,
        checkpoint: Any,
        *,
        goal: Goal,
        question: str,
        file_hint: str | None,
        current_phase: str,
        plan: Plan | None,
        blocked: ModelBudgetExceeded,
    ) -> None:
        save_paused = getattr(checkpoint, "save_paused", None)
        if not callable(save_paused):
            return
        planned_steps = self._checkpoint_step_summaries(plan)
        completed_steps = [
            step for step in planned_steps if step.get("status") == "done"
        ]
        remaining_steps = [
            step for step in planned_steps if step.get("status") != "done"
        ]
        payload = {
            "active_goal": goal.description,
            "goal_id": goal.id,
            "original_user_question": question,
            "file_hint": file_hint,
            "current_phase": current_phase,
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "remaining_steps": remaining_steps,
            "stop_reason": "budget_exhausted",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "blocked_model": blocked.to_dict(),
        }
        try:
            save_paused(payload)
            self.log.log(
                "resumable_checkpoint_paused",
                {
                    "current_phase": current_phase,
                    "stop_reason": "budget_exhausted",
                    "planned_steps": len(planned_steps),
                    "completed_steps": len(completed_steps),
                    "remaining_steps": len(remaining_steps),
                    "blocked_model": payload["blocked_model"],
                },
            )
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("budget_exhaustion_log", exc)
