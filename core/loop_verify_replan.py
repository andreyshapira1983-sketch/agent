"""Answer verification and re-planning on unresolved citations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.file_request_intent import force_file_hint_read_when_explicit
from core.model_usage import ModelBudgetExceeded
from core.models import Goal, Plan
from core.observation_round import charged_attempts
from core.planner import PlannerOutput
from core.replan import ReplanTrigger, count_failures, format_replan_context
from core.source_ranker import SourceRankingReport


@dataclass
class VerifyState:
    """What verification and citation re-planning carry along.

    Field names equal the old `_run_inner` locals, so the `name -> st.name` substitution stays mechanical and a test checks it against history.
    """

    # ── Input: does not change during the section ──
    draft_answer: str
    user_question: str
    file_hint: str | None
    goal: Goal
    chain: ProvenanceChain
    artifacts: dict[str, dict[str, Any]]
    attempt: int
    plan: Plan | None
    planner_history: str
    failure_history: list[ReplanTrigger]
    source_ranking: SourceRankingReport
    source_registry: Any
    may_knowledge: bool
    may_source_registry: bool
    _task_planner_llm: Any
    _disagreement_shadow: list[dict[str, Any]]
    _cp: Any

    # ── Output: read AFTER the section ──
    # Text that reached the draft deciders: the draft as is when the verifier is off, otherwise
    # marked by the report.
    answer: str = ""
    planner_out: PlannerOutput | None = None
    replan_exhausted: bool = False
    # NOT "did not confirm" but "verification broke": the decider chain below must tell them apart.
    verifier_failure: bool = False

    # What the caller reads from the state after the section. Declared, not left as a comment: a
    # silent loss of an output is the quietest way to break this move, and a test holds the
    # unpacking to this list.
    OUTPUTS: ClassVar[frozenset[str]] = frozenset({
        "answer", "planner_out", "replan_exhausted", "verifier_failure",
    })


class AgentLoopVerifyReplan:
    """Answer verification and re-planning on unresolved citations.

    Members below are declarations of the host contract (``AgentLoop`` creates them in ``__init__``); no assignments, so nothing is created or shadowed at run time.
    """

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        planner: Any
        replan_policy: Any
        verifier_enabled: Any
        last_verification: Any
        last_provenance: Any
        last_source_registry: Any
        last_knowledge_pipeline: Any
        _current_attempt: int

        # Declared as CALLABLE attributes: a stub function with an empty body is read by analysers
        # as "function without return" and every call is falsely flagged E1111.
        _verify_draft: Any
        _build_plan: Any
        _quarantine_conflicted_memory: Any

        # Taken from a neighbouring mixin through the MRO; the link between modules must be written
        # down.
        _execute_steps_parallel: Any
        _executed_tools: Any
        _save_budget_pause_checkpoint: Any
        _verification_receipt_kwargs: Any
        # Set by `core/loop_synthesis.py`, reset per turn by `core/loop.py`; reading through
        # `getattr` with a default turned a missing link into a silent verdict by someone else's
        # rule.
        _synthesis_expects_contract_headers: Any
        # Five declarations left with the extraction of the cataloguing core: `knowledge_pipeline`,
        # `knowledge_auto_write`, `source_registry_store`, `_knowledge_remember_batch` and
        # `_unattended_run` are now needed only by it.
        _catalogue_chain: Any

    def _replan_on_refuted_claims(self, report: Any, st: VerifyState) -> None:
        """Rung 4 of the ladder: carry the WHY of a refuted claim into the next attempt.

        Travels the road `unresolved_citation` uses: a `ReplanTrigger` whose `reason` is copied verbatim into `<replan_context>`, with `requires_different_action` so the sanitiser REFUSES a repeat. One trigger for the whole draft.
        """
        refuted = [c for c in report.chunks if getattr(c, "reason", None)]
        if not refuted:
            return
        corrections = "; ".join(
            f"{c.reason.explanation} (from {c.reason.computed_from})"
            for c in refuted[:5]
        )
        st.failure_history.append(ReplanTrigger(
            code="claim_refuted",
            step_id=f"verify-claims-{st.attempt}",
            tool_name=None,
            arguments={"codes": sorted({c.reason.code for c in refuted})},
            reason=(
                f"{len(refuted)} claim(s) were checked against the source they "
                f"cited and do not follow from it: {corrections}"
            ),
            attempt=st.attempt,
        ))
        # The name is shared: four gates give a `ClaimReason`, told apart by the `code` field
        # (tests/test_refutation_event_names_its_gate).
        self.log.log("claims_refuted_by_content", {
            "count": len(refuted),
            "reasons": [c.reason.to_log_payload() for c in refuted[:5]],
        })

    @staticmethod
    def _terminal_verify_fallback_source(
        unresolved_urls: list[str],
        iteration: int,
    ) -> dict[str, Any]:
        """One alternative step after a terminal fetch/no-op.

        The main verify-replan requires ``web_fetch`` of every URL. If the plan is exhausted without evidence, one ``web_search`` (a different kind of action) gets a chance to find an available source before synthesis gives up.
        """
        return {
            "tool": "web_search",
            "arguments": {"query": " OR ".join(unresolved_urls)},
            "label": f"verify-fallback-search-{iteration}",
            "expected_outcome": (
                "Find an accessible source for the unresolved web citations."
            ),
        }

    def _verify_and_settle_answer(self, st: VerifyState) -> None:
        """Bring the draft to the text the deciders will see.

        Returns nothing: the result lies in `st` (the text, the planner's last output, the exhausted-replan flag and the verifier-broke flag).
        """
        if self.verifier_enabled:
            from core.verifier import (
                extract_unresolved_web_urls,
            )
            from core.verifier import (
                verify as _verify,
            )

            # Verification and its sensors live in `core/loop_verification.py`.
            # The second element is "verification broke", not "did not confirm"; they are never
            # mixed below.
            report, st.verifier_failure = self._verify_draft(
                st.draft_answer,
                chain=st.chain,
                user_question=st.user_question,
                attempt=st.attempt,
                plan=st.plan,
                artifacts=st.artifacts,
                failure_history=st.failure_history,
                _disagreement_shadow=st._disagreement_shadow,
            )

            self._replan_on_refuted_claims(report, st)

            verify_replan_attempt = 0
            VERIFY_REPLAN_HARD_CAP = 2  # belt + braces over ReplanPolicy
            terminal_fallback_used = False

            while True:
                unresolved_urls = extract_unresolved_web_urls(report)
                if not unresolved_urls:
                    break
                if verify_replan_attempt >= VERIFY_REPLAN_HARD_CAP:
                    # The hard cap is replan exhaustion, not a successful citation resolution; same
                    # output signal as the ReplanPolicy branch below.
                    st.replan_exhausted = True
                    self.log.log(
                        "verify_replan_capped",
                        {
                            "attempts": verify_replan_attempt,
                            "hard_cap": VERIFY_REPLAN_HARD_CAP,
                            "unresolved_count": len(unresolved_urls),
                            "unresolved_sample": unresolved_urls[:3],
                        },
                    )
                    break

                verify_replan_attempt += 1
                trigger = ReplanTrigger(
                    code="unresolved_citation",
                    step_id=f"verify-{verify_replan_attempt}",
                    tool_name=None,
                    arguments={"urls": list(unresolved_urls)},
                    reason=(
                        f"Verifier found {len(unresolved_urls)} [web:...] "
                        f"citation(s) the chain cannot resolve. "
                        f"Planner must add web_fetch for: {unresolved_urls}"
                    ),
                    attempt=st.attempt + verify_replan_attempt,
                )
                st.failure_history.append(trigger)

                decision = self.replan_policy.decide(
                    failure_history=st.failure_history,
                    completed_attempts=charged_attempts(self, st.attempt, st.failure_history) + verify_replan_attempt,
                )

                if decision.action != "continue":
                    st.replan_exhausted = True
                    self.log.log(
                        "replan_exhausted",
                        {
                            "phase": "verify",
                            "attempts": st.attempt + verify_replan_attempt,
                            "max_total": self.replan_policy.max_total_replans,
                            "decision_action": decision.action,
                            "decision_reason": decision.reason,
                            "failure_counts": dict(decision.failure_counts),
                            "triggers": ["unresolved_citation"],
                        },
                    )
                    break

                # Planner advice: `unresolved_citation` advice goes FIRST, then an explicit URL list
                # the planner must convert into web_fetch steps; the base advice follows FailureType
                # order and might bury the fetch instruction.
                base_advice = (
                    self.replan_policy.budgets["unresolved_citation"].advice
                )
                urls_block = "\n".join(f"  - {u}" for u in unresolved_urls)
                verify_advice = (
                    f"{base_advice}\n\n"
                    f"URLs that MUST be opened via web_fetch (one step each):"
                    f"\n{urls_block}"
                )

                self.log.log(
                    "replan",
                    {
                        "phase": "verify",
                        "attempt": st.attempt + verify_replan_attempt - 1,
                        "next_attempt": st.attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": ["unresolved_citation"],
                        "details": [
                            {
                                "step_id": trigger.step_id,
                                "tool": None,
                                "code": "unresolved_citation",
                                "reason": trigger.reason,
                            },
                        ],
                        "decision": decision.to_log_payload(),
                        "unresolved_urls": list(unresolved_urls),
                    },
                )

                # Bump the public attempt counter so `respond.attempts_used` reflects verify-driven
                # re-plans.
                self._current_attempt = st.attempt + verify_replan_attempt

                self.log.log(
                    "replan_attempt",
                    {
                        "phase": "verify",
                        "attempt": st.attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(verify_advice),
                        "forbidden_action_count": len(decision.forbidden_actions),
                        "failure_counts_so_far": dict(
                            count_failures(st.failure_history)
                        ),
                    },
                )

                failure_context = format_replan_context(
                    st.failure_history,
                    st.attempt + verify_replan_attempt,
                    self.replan_policy.max_total_replans,
                    advice=verify_advice,
                    forbidden_actions=decision.forbidden_actions,
                )

                try:
                    st.planner_out = self.planner.plan(
                        question=st.user_question,
                        file_hint=st.file_hint,
                        history=st.planner_history,
                        failure_context=failure_context,
                        forbidden_actions=decision.forbidden_actions,
                        # Keep the complexity-escalated planner model on verify re-plans; otherwise
                        # the re-plan dropped to the default tier.
                        llm=st._task_planner_llm,
                    )
                except ModelBudgetExceeded as exc:
                    self._save_budget_pause_checkpoint(
                        st._cp,
                        goal=st.goal,
                        question=st.user_question,
                        file_hint=st.file_hint,
                        current_phase="verification_replan",
                        plan=st.plan,
                        blocked=exc,
                    )
                    raise
                st.planner_out = force_file_hint_read_when_explicit(
                    st.planner_out,
                    question=st.user_question,
                    file_hint=st.file_hint,
                )
                self.log.log(
                    "planner",
                    {
                        "phase": "verify",
                        "reasoning": st.planner_out.reasoning,
                        "tools_chosen": [s["tool"] for s in st.planner_out.sources],
                        "warnings": st.planner_out.warnings,
                        "raw_chars": len(st.planner_out.raw_response),
                        "attempt": st.attempt + verify_replan_attempt,
                        "replan_context_chars": len(failure_context),
                    },
                )

                verify_plan = self._build_plan(st.goal, st.planner_out.sources)
                self.log.log(
                    "plan",
                    verify_plan,
                    steps=len(verify_plan.steps),
                    attempt=st.attempt + verify_replan_attempt,
                    phase="verify",
                )

                # Execute the new steps, tolerating sanitiser/policy drops and per-step failures; if
                # nothing is fetched we exit on the next iteration (the hard cap and per-type budget
                # bound the loop).
                def execute_verify_steps(steps: list[Any]) -> int:
                    added = 0
                    for step, outcome, trigger in self._execute_steps_parallel(steps):
                        if outcome is None:
                            step.status = "failed"
                            # Drain the scratch trigger so it does not leak into a later policy
                            # decision with a misleading code.
                            if trigger is not None:
                                st.failure_history.append(trigger)
                            continue
                        self._executed_tools.append(outcome["tool"])
                        st.artifacts[outcome["label"]] = {
                            "tool": outcome["tool"],
                            "output": outcome["output"],
                            "issues": outcome["issues"],
                        }
                        ev = evidence_from_tool_result(
                            tool_name=outcome["tool"],
                            arguments=outcome.get("arguments"),
                            output=outcome["output"],
                            status="success",
                        )
                        if ev is not None:
                            st.chain.add(ev)
                            added += 1
                        step.status = "done"
                    return added

                added_evidence = execute_verify_steps(verify_plan.steps)
                fallback_executed = False
                if added_evidence == 0 and not terminal_fallback_used:
                    terminal_fallback_used = True
                    fallback_executed = True
                    fallback_source = self._terminal_verify_fallback_source(
                        unresolved_urls,
                        verify_replan_attempt,
                    )
                    fallback_plan = self._build_plan(st.goal, [fallback_source])
                    self.log.log(
                        "verify_replan_terminal_fallback",
                        {
                            "iteration": verify_replan_attempt,
                            "failed_step_count": len(verify_plan.steps),
                            "fallback_tool": fallback_source["tool"],
                            "unresolved_urls": list(unresolved_urls),
                        },
                    )
                    added_evidence += execute_verify_steps(fallback_plan.steps)


                # Re-verify the ORIGINAL draft against the enriched chain: it already cites these
                # URLs, and now the chain has the web_page evidence.
                try:
                    report = _verify(
                        answer=st.draft_answer,
                        chain=st.chain,
                        user_question=st.user_question,
                        expects_contract_headers=self._synthesis_expects_contract_headers,
                        **self._verification_receipt_kwargs(),
                    )
                except Exception as _ver_exc:  # noqa: BLE001 — reason stated above
                    st.verifier_failure = True
                    self.log.log(
                        "verifier_failure",
                        {
                            "error_type": type(_ver_exc).__name__,
                            "error": str(_ver_exc)[:300],
                            "draft_chars": len(st.draft_answer),
                            "phase": "verify_replan",
                            "iteration": verify_replan_attempt,
                        },
                    )
                    break
                self.log.log(
                    "verification",
                    {
                        **report.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                        "evidence_added": added_evidence,
                    },
                )
                self.last_verification = report
                self.last_provenance = st.chain

                # Re-emit the chain snapshot so log consumers see the enriched provenance after each
                # fetch round.
                self.log.log(
                    "evidence_collected",
                    {
                        "phase": "verify",
                        "count": len(st.chain),
                        "kinds": sorted({ev.kind for ev in st.chain.evidences}),
                        "chain": st.chain.to_log_payload(),
                    },
                )
                # The shared core. Everything below is this caller's own: the run state, the `phase`
                # and `iteration` stamps, and the quarantine.
                catalogued = self._catalogue_chain(
                    st.chain,
                    question=st.user_question,
                    may_knowledge=st.may_knowledge,
                    may_source_registry=st.may_source_registry,
                )
                st.source_ranking = catalogued.ranking
                self.last_source_ranking = st.source_ranking
                self.log.log(
                    "source_ranking",
                    {
                        **st.source_ranking.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                knowledge_result = catalogued.knowledge
                st.source_registry = knowledge_result.registry
                self._quarantine_conflicted_memory(knowledge_result)
                self.last_source_registry = st.source_registry
                self.log.log(
                    "source_registry",
                    {
                        **st.source_registry.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                self.last_knowledge_pipeline = knowledge_result
                self.log.log(
                    "knowledge_pipeline",
                    {
                        **knowledge_result.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )

                # Exactly one alternative step was done: regardless of its result the next outcome
                # is the final marking of the current draft, not another fallback step.
                if fallback_executed:
                    self.log.log(
                        "verify_replan_terminal_fallback_settled",
                        {
                            "iteration": verify_replan_attempt,
                            "evidence_added": added_evidence,
                        },
                    )
                    break

                # If the fetch round added no evidence, re-verify gives the same unresolved list:
                # exit instead of calling the planner with nothing new.
                if added_evidence == 0:
                    self.log.log(
                        "verify_replan_noop",
                        {
                            "iteration": verify_replan_attempt,
                            "unresolved_count_before": len(unresolved_urls),
                            "unresolved_count_after": len(
                                extract_unresolved_web_urls(report)
                            ),
                        },
                    )
                    break

            st.answer = report.annotated_answer
        else:
            st.answer = st.draft_answer
            self.last_verification = None
