"""Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond.

This is the §3 cycle from the architecture, minimal but real:
  - Every phase produces a typed data model.
  - Every phase emits a structured log line.
  - Every action passes through the Policy Gate before execution.
  - A Plan may now contain multiple steps (file_read + web_search + ...).
  - Each artifact is labelled with its source so the Output Contract
    can cite it back to the user.

MVP-8 — Re-planning. The plan→execute→verify pipeline is wrapped in a
bounded retry loop. When every step in a plan fails (and the plan is
non-empty), the agent asks the planner for a NEW plan and shows it
exactly what went wrong via a `<replan_context>` block. Up to
`max_replan_attempts` total attempts; after that the cycle stops with
`error.code=replan_exhausted` and still produces an honest Output
Contract response so the user gets a real answer instead of a stack
trace.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from typing import Literal as _Literal

from core.approval import ApprovalProvider
from core.data_classifier import DataClass, SourceHint, classify
from core.evidence import (
    Evidence,
    ProvenanceChain,
    evidence_from_memory_record,
    evidence_from_tool_result,
    evidence_from_user_directive,
)
from core.ids import new_id
from core.llm import LLM
from core.logger import TraceLogger
from core.memory import WorkingMemory
from core.memory_policy import (
    MemoryRetrievalPolicy,
    MemoryWriteDecision,
    MemoryWritePolicy,
)
from core.models import (
    Action,
    ApprovalRequest,
    ErrorObject,
    Goal,
    MemoryRecord,
    Observation,
    Plan,
    PlanStep,
    PolicyDecision,
    ToolCall,
    ToolResult,
)
from core.output_policy import apply_ranker_output_policy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner, PlannerOutput
from core.policy import PolicyGate
from core.redaction import redact_payload, redact_text, scan
from core.model_router import ModelRole, ModelRouter
from core.knowledge_pipeline import KnowledgePipeline, KnowledgePipelineResult
from core.knowledge_use_policy import KnowledgeUsePolicy
from core.role_router import RoleContext, RoleRouter
from core.source_ranker import SourceRankingReport, rank_chain
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from tools.base import ToolRegistry


# Maps tool names to data_classifier source hints. Drives the per-tool
# default DataClass (file_read -> private, web_search -> public, …).
_TOOL_SOURCE_HINTS: dict[str, SourceHint] = {
    "file_read": "file",
    "web_search": "web",
}


def _to_text(output: Any) -> str:
    """Stringify a tool output for classification + scanning.

    Tools return heterogeneous shapes (file_read → str, web_search → list).
    We need ONE flat text view to feed the classifier.
    """
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        return str(output)


# Default attempt budget for re-planning. Two replans (3 attempts total)
# is the tradeoff: enough room to recover from a typo or a flaky source,
# not enough to mask a fundamentally wrong plan as "just one more try".
DEFAULT_MAX_REPLAN_ATTEMPTS = 3


# Failure codes captured by `_execute_step` and surfaced to the planner
# in the next attempt. Any other failure path defaults to "unknown".
#
# MUST stay in sync with `core.replan.FailureType` — `tests/test_replan_audit.py`
# pins both lists against each other so a new failure type can't be added
# in one place and silently forgotten in the other.
ReplanCode = _Literal[
    "policy_blocked",
    "approval_deny",
    "approval_abort",
    "approval_unavailable",
    "tool_error",
    "verify_failed",
    "web_empty",
    "timeout",
    "unresolved_citation",
    "unknown",
]


@dataclass(frozen=True)
class ReplanTrigger:
    """Structured record of one failed PlanStep.

    Lives in two places:
      - `AgentLoop._last_step_failure` — scratch slot set by
        `_execute_step` at every early-return; the parent loop drains it
        right after the step returns None.
      - `failure_history` inside `run()` — the cumulative list across
        attempts, formatted into a `<replan_context>` block fed to the
        planner.

    `arguments` are stored verbatim from the failed step. They are
    redacted at log time by `TraceLogger` and again before the planner
    prompt is sent, so a credential pasted in by the LLM into a tool
    argument cannot leak via the replan path either.
    """

    code: ReplanCode
    step_id: str
    tool_name: str | None
    arguments: dict[str, Any]
    reason: str
    attempt: int


# Output Contract (§1 Interface & Communication + §8 Verification).
# The LLM MUST emit this structure so the user gets:
#   - a direct answer
#   - explicit citations to source labels embedded in the evidence
#   - explicit confidence and unverified gaps
SYSTEM_ANSWER = """You are a careful research analyst.

If the user message contains <evidence> blocks, answer STRICTLY from them.
Each evidence block carries a `source="..."` label (e.g. file:..., web:...).

If the user message contains NO <evidence> blocks, the planner decided that
no tools were needed. Answer from your general knowledge, mark every fact with
the special source label [general-knowledge], and set Confidence accordingly
(typically medium or low — never high without evidence).

Output Contract — your reply MUST follow this structure exactly, in the user's language:

Conclusion:
  One or two sentences that directly answer the question.
  Every factual sentence in Conclusion MUST end with a source label in
  square brackets, using the same citation grammar as Facts.

Facts:
  - Bullet list of supporting facts.
  - Each bullet MUST end with its source label in square brackets.
    Use the citation grammar the Verifier understands:
      [file:<workspace/path>]    workspace file content
      [web:<url>]                fetched web page (kind=web_page)
      [search:<query>]           weak search-result pointer
      [test:<cmd>]               pytest result
      [log:<trace_id>]           JSONL audit log event
      [shell:<cmd>]              shell_exec stdout
      [diff:<path>]              proposed diff preview
      [memory:<record_id>]       long-term memory record
      [user]                     explicit user directive
      [general-knowledge]        no source consulted (signals
                                 the fact is from training data)
    Pick the citation that matches the <evidence source="..."> label
    closest to the fact you are stating. The Verifier downstream will
    REWRITE matched citations to `[verified:<kind>:<source>]` and tag
    uncited claims with `[unverified]` — so always cite, even when
    using prior knowledge (then cite [general-knowledge]).

Sources:
  1. <source label> - <url or file path verbatim from the evidence header,
     or the literal text "general-knowledge" if no evidence was provided>
  2. ...

Confidence: low | medium | high
  - high   = corroborated by multiple sources OR a direct verbatim quote from a file
  - medium = supported by a single source OR confident general-knowledge answer
  - low    = inferred, partial, or uncertain

Unverified:
  What you could NOT confirm from the evidence, or the single word "nothing".

Safety:
  If a <safety_notes> block is provided, summarise it here in plain language
  (one short paragraph per finding). Tell the user which surface the secret
  was found on (user_input / tool_output / final_answer), how many shapes
  were redacted, and that the kernel — not the model — performed the
  redaction. If no <safety_notes> block is provided, write the single word
  "nothing".

Hard rules:
- NEVER invent facts, URLs, or sources not present in the evidence.
- If the evidence does not answer the question, say so in Conclusion AND list
  the unanswered parts under Unverified.
- Quote URLs and file paths verbatim from the evidence `source` attributes.
- If an <allowed_citations> block is present, cite ONLY those exact bracketed
  citation tokens. Do not cite raw <evidence source="..."> labels unless they
  also appear in <allowed_citations>.
- NEVER reproduce any [REDACTED:*] token's underlying value, even if you
  can guess it; treat the token as the actual content.
"""


class AgentLoop:
    """Runs a single agent cycle.

    MVP-3: the planner is an LLM. The CLI only supplies the question plus an
    optional file hint. The planner picks which tools (if any) to call; the
    Executor runs the plan and the Synthesizer produces the Output Contract.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyGate,
        llm: LLM,
        logger: TraceLogger,
        planner: LLMPlanner | None = None,
        model_router: ModelRouter | None = None,
        memory: WorkingMemory | None = None,
        persistent_store: PersistentMemoryStore | None = None,
        retrieval_policy: MemoryRetrievalPolicy | None = None,
        write_policy: MemoryWritePolicy | None = None,
        role_router: RoleRouter | None = None,
        knowledge_use_policy: KnowledgeUsePolicy | None = None,
        source_registry_store: SourceRegistryStore | None = None,
        knowledge_pipeline: KnowledgePipeline | None = None,
        knowledge_auto_write: bool = False,
        approval_provider: ApprovalProvider | None = None,
        max_replan_attempts: int = DEFAULT_MAX_REPLAN_ATTEMPTS,
        replan_policy: "ReplanPolicy | None" = None,
        verifier_enabled: bool = True,
    ):
        self.registry = registry
        self.policy = policy
        self.model_router = model_router or ModelRouter.single(llm)
        self.llm = self.model_router.for_role(ModelRole.SYNTHESIZER)
        self.log = logger
        self.planner = planner or LLMPlanner(
            llm=self.model_router.for_role(ModelRole.PLANNER),
            registry=registry,
        )
        self.memory = memory  # may be None for stateless one-shot use
        self.persistent_store = persistent_store
        self.retrieval_policy = retrieval_policy or MemoryRetrievalPolicy()
        self.write_policy = write_policy or MemoryWritePolicy()
        self.role_router = role_router or RoleRouter()
        self.knowledge_use_policy = knowledge_use_policy or KnowledgeUsePolicy()
        self.source_registry_store = source_registry_store
        self.knowledge_pipeline = knowledge_pipeline or KnowledgePipeline()
        self.knowledge_auto_write = bool(knowledge_auto_write)
        # When None, escalated actions are blocked outright (safe default).
        self.approval_provider = approval_provider
        # MVP-12: replan policy decides per-FailureType budgets, advises
        # the planner, and tracks forbidden (tool, args) pairs that must
        # not be retried after approval_deny / policy_blocked etc.
        #
        # Backward compatibility: the older `max_replan_attempts` parameter
        # is still honoured. If the caller supplied an explicit policy we
        # use it as-is; otherwise we synthesise the default policy with the
        # caller's global cap. Mismatch is an error — we refuse to silently
        # ignore one of the two knobs.
        if max_replan_attempts < 1:
            raise ValueError(
                f"max_replan_attempts must be >= 1, got {max_replan_attempts}"
            )
        from core.replan import (
            ReplanPolicy as _ReplanPolicyCls,
        )

        if replan_policy is None:
            self.replan_policy = _ReplanPolicyCls(
                max_total_replans=max_replan_attempts
            )
        else:
            if (
                max_replan_attempts != DEFAULT_MAX_REPLAN_ATTEMPTS
                and max_replan_attempts != replan_policy.max_total_replans
            ):
                raise ValueError(
                    "Pass either `max_replan_attempts` OR `replan_policy`, "
                    "not both with conflicting caps "
                    f"(max_replan_attempts={max_replan_attempts}, "
                    f"replan_policy.max_total_replans="
                    f"{replan_policy.max_total_replans})"
                )
            self.replan_policy = replan_policy
        # Keep the legacy attribute for any external code reading it.
        self.max_replan_attempts = self.replan_policy.max_total_replans
        # MVP-11 Compensation registry. Every successful tool call that
        # carries a non-noop CompensationPlan in its structured output
        # gets appended here, LIFO. `rollback()` pops the last plan and
        # applies it via `core.compensation.apply_compensation_plan`.
        # Persistence is intentionally in-memory: rollback must be done
        # WITHIN the session that produced the change (a future MVP can
        # serialise the log if cross-session undo proves useful).
        from core.compensation import CompensationPlan  # avoid top-level cycle

        self._CompensationPlanCls = CompensationPlan
        self.compensation_log: list[CompensationPlan] = []
        # Per-cycle scratch list, populated by `_execute_step` whenever a
        # secret is found in a tool output. `run()` resets it on entry and
        # passes it into the synthesizer so the answer can mention any
        # redaction action it took.
        self._cycle_findings: list[dict[str, Any]] | None = None
        # Per-step scratch slot. `_execute_step` writes a `ReplanTrigger`
        # into this every time it bails out with `None`. The parent loop
        # in `run()` consumes it and clears it. Keeping it as instance
        # state mirrors the `_cycle_findings` pattern and avoids
        # restructuring the return type of `_execute_step`.
        self._last_step_failure: ReplanTrigger | None = None
        # Current attempt number — set by `run()` at the top of each
        # iteration so `_execute_step` can stamp failure triggers with
        # the right attempt index without taking a new parameter.
        self._current_attempt: int = 1
        # MVP-14.1 — provenance scratch state. `_retrieve_persistent`
        # stashes the records it injected so `run()` can fold them into
        # the Evidence chain. `last_provenance` is set at the end of
        # each `run()` and exposed for tests / the upcoming Verifier.
        self._last_persistent_records: list[Any] = []
        self.last_provenance: ProvenanceChain = ProvenanceChain()
        self.last_role_context: RoleContext = self.role_router.route("")
        # MVP-14.4 — Verifier wiring. `verifier_enabled=False` skips the
        # annotation pass; the draft answer is returned verbatim. Useful
        # for legacy tests and for callers who want raw LLM output.
        self.verifier_enabled = bool(verifier_enabled)
        from core.verifier import VerificationReport as _VR

        self.last_verification: _VR | None = None
        # MVP-14.3/14.3x — trust metadata over the Evidence chain.
        # Source ranking is logged/exposed, and Ranker-to-Output Policy uses
        # it to cap confidence for unsuitable realtime sources.
        self.last_source_ranking: SourceRankingReport | None = None
        # Source registry is the catalog view over the same chain:
        # source records plus first-pass claims extracted from Evidence.
        self.last_source_registry: SourceRegistry = SourceRegistry()
        self.last_knowledge_pipeline: KnowledgePipelineResult | None = None

    # ---------- public entry point ----------

    def run(
        self,
        user_question: str,
        file_hint: str | None = None,
    ) -> str:
        # Reset per-cycle safety state. Findings accumulate as _execute_step
        # detects secrets in tool outputs; they're surfaced in the answer
        # via the Output Contract's Safety section.
        self._cycle_findings = []
        self.last_source_ranking = None
        self.last_source_registry = SourceRegistry()
        self.last_knowledge_pipeline = None

        # 1. Observe
        observation = Observation(
            source="cli",
            modality="text",
            content={
                "question": user_question,
                "file_hint": file_hint,
            },
            provenance="user",
        )
        self.log.log("observe", observation)

        # Classify the question itself. A user can paste a secret into the
        # prompt; the kernel must catch it BEFORE the LLM sees it.
        q_cls = classify(user_question, source="cli")
        self.log.log(
            "data_classified",
            {
                "label": "user_question",
                "class": q_cls.cls.value,
                "source": q_cls.source,
                "reasons": q_cls.reasons,
            },
        )
        if q_cls.cls == DataClass.SECRET:
            q_findings = scan(user_question)
            kinds = sorted({f.kind for f in q_findings})
            self.log.log(
                "secret_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )

        self.last_role_context = self.role_router.route(user_question)
        self.log.log("role_route", self.last_role_context.to_log_payload())

        # 2. Interpret -> Goal
        goal = self._interpret(observation)
        self.log.log("interpret", goal)

        # Memory retrieval — read-only injection into prompts
        history = ""
        if self.memory is not None:
            history = self.memory.conversation_context(max_turns=5)
            if history:
                self.log.log(
                    "memory_inject",
                    {
                        "session_id": self.memory.session_id,
                        "turns_visible": len(self.memory.recent_turns(5)),
                        "history_chars": len(history),
                        "artifacts_cached": len(self.memory.artifacts),
                    },
                )

        # Persistent memory retrieval — pick a few long-term records that
        # share keywords with the question, then format them as a
        # <long_term_memory> block injected into planner + synthesizer.
        persistent_block = self._retrieve_persistent(user_question)

        # Planner sees the persistent block prepended to working history so
        # it can opt out of redundant tool calls when the answer is already
        # in long-term memory. Role is logged and injected into synthesis,
        # but kept out of `history` so `<conversation_history>` stays a
        # strict marker for actual prior dialogue.
        planner_history = (
            f"{persistent_block}\n\n{history}".strip() if persistent_block else history
        )

        # 3. Plan + 4. Act + 5. Observe Result + 6. Verify, wrapped in a
        # bounded re-planning loop. On every iteration:
        #   - build a planner prompt (with <replan_context> after the first
        #     attempt)
        #   - run every step and collect artifacts
        #   - if the plan was non-empty AND no artifact survived, this
        #     attempt failed; promote `failure_history` and try again
        #   - stop on success OR when the attempt budget is gone
        failure_history: list[ReplanTrigger] = []
        artifacts: dict[str, dict[str, Any]] = {}
        # MVP-14.1 — typed Evidence chain. Built in parallel with
        # `artifacts`; lives at the same scope so the synthesizer (and,
        # later, the Verifier) can consult it.
        chain: ProvenanceChain = ProvenanceChain()
        planner_out: PlannerOutput | None = None
        plan: Plan | None = None
        replan_exhausted = False
        # MVP-12: advice + forbidden-actions list carried over from the
        # previous attempt's policy.decide() call. Empty on the first
        # attempt; populated on every replan.
        advice_for_planner: str = ""
        forbidden_actions: tuple[tuple[str, str], ...] = ()

        attempt = 0
        while True:
            attempt += 1
            self._current_attempt = attempt

            # Emit a structured `replan_attempt` event for every loop
            # iteration AFTER the first — makes it trivial for tests
            # (and humans) to see how many attempts ran and which advice
            # the planner saw on each.
            if attempt > 1:
                self.log.log(
                    "replan_attempt",
                    {
                        "attempt": attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(advice_for_planner),
                        "forbidden_action_count": len(forbidden_actions),
                        "failure_counts_so_far": dict(
                            self._count_failures(failure_history)
                        ),
                    },
                )

            failure_context = self._format_replan_context(
                failure_history,
                attempt,
                self.replan_policy.max_total_replans,
                advice=advice_for_planner,
                forbidden_actions=forbidden_actions,
            )

            planner_out = self.planner.plan(
                question=user_question,
                file_hint=file_hint,
                history=planner_history,
                failure_context=failure_context,
                forbidden_actions=forbidden_actions,
            )
            self.log.log(
                "planner",
                {
                    "reasoning": planner_out.reasoning,
                    "tools_chosen": [s["tool"] for s in planner_out.sources],
                    "warnings": planner_out.warnings,
                    "raw_chars": len(planner_out.raw_response),
                    "attempt": attempt,
                    "replan_context_chars": len(failure_context),
                },
            )

            plan = self._build_plan(goal, planner_out.sources)
            self.log.log("plan", plan, steps=len(plan.steps), attempt=attempt)

            attempt_artifacts: dict[str, dict[str, Any]] = {}
            attempt_failures: list[ReplanTrigger] = []
            attempt_chain = ProvenanceChain()
            for step in plan.steps:
                outcome = self._execute_step(step)
                if outcome is None:
                    step.status = "failed"
                    if self._last_step_failure is not None:
                        attempt_failures.append(self._last_step_failure)
                        self._last_step_failure = None
                    continue
                attempt_artifacts[outcome["label"]] = {
                    "tool": outcome["tool"],
                    "output": outcome["output"],
                    "issues": outcome["issues"],
                }
                # MVP-14.1 — typed evidence. The output is already
                # redacted (see _execute_step), so the excerpt that
                # ends up on disk in the chain is safe.
                ev = evidence_from_tool_result(
                    tool_name=outcome["tool"],
                    arguments=outcome.get("arguments"),
                    output=outcome["output"],
                    status="success",
                )
                if ev is not None:
                    attempt_chain.add(ev)
                step.status = "done"

            # Success: either a 0-step plan (general-knowledge / history-only
            # answer is intentional) or at least one artifact came through.
            if not plan.steps or attempt_artifacts:
                artifacts = attempt_artifacts
                chain = attempt_chain
                break

            # Failure: this attempt produced nothing usable. Carry the
            # triggers forward and ask the policy what to do next.
            failure_history.extend(attempt_failures)

            decision = self.replan_policy.decide(
                failure_history=failure_history,
                completed_attempts=attempt,
            )

            if decision.action == "continue":
                # Log `replan` ONLY when we are going to try again.
                # Pairs neatly with `replan_attempt` (next iteration).
                self.log.log(
                    "replan",
                    {
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
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
                advice_for_planner = decision.advice_for_planner
                forbidden_actions = decision.forbidden_actions
                continue

            # Policy said stop. Emit a structured exhaustion event AND
            # an `error` event (kept for backward-compat with existing
            # consumers that grep on `code=replan_exhausted`). The
            # synthesizer takes over with an honest explanation.
            replan_exhausted = True
            err = ErrorObject(
                source="loop",
                code="replan_exhausted",
                message=(
                    f"Re-planning stopped after {attempt} attempt(s): "
                    f"{decision.reason}. Failure codes: "
                    f"{[t.code for t in failure_history]}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "attempts": attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "failure_codes": [t.code for t in failure_history],
                },
            )
            self.log.log("error", err)
            self.log.log(
                "replan_exhausted",
                {
                    "attempts": attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "triggers": [t.code for t in attempt_failures],
                },
            )
            break

        # planner_out and plan are guaranteed set here (the for loop ran at
        # least once because max_replan_attempts >= 1 is enforced in __init__).
        assert planner_out is not None and plan is not None

        # MVP-14.1 — fold memory & user-directive evidence into the chain.
        # The tool-level evidence was added per step inside the attempt
        # loop; persistent memory and explicit-consent inputs come from
        # different code paths, so we surface them HERE so the Verifier
        # sees a single uniform chain.
        if persistent_block and self.persistent_store is not None:
            try:
                # `persistent_block` was built from a small set of records
                # in `_retrieve_persistent`; we replay that retrieval cheaply
                # by re-asking the store for the keyword match.
                for rec in self._last_persistent_records:
                    chain.add(
                        evidence_from_memory_record(
                            record_id=rec.id,
                            content=rec.content,
                            source=getattr(rec, "source", None),
                            created_at=getattr(rec, "created_at", None),
                        )
                    )
            except Exception:
                # Defence-in-depth: chain assembly must NEVER abort the
                # run. A malformed record stays out of the chain but the
                # loop completes normally.
                pass

        # Store the chain on the agent so tests / future Verifier code
        # can consult it after `run()` returns.
        self.last_provenance = chain

        self.log.log(
            "evidence_collected",
            {
                "count": len(chain),
                "kinds": sorted({ev.kind for ev in chain.evidences}),
                "chain": chain.to_log_payload(),
            },
        )
        source_ranking = rank_chain(chain, question=user_question)
        self.last_source_ranking = source_ranking
        self.log.log("source_ranking", source_ranking.to_log_payload())
        knowledge_result = self.knowledge_pipeline.run(
            chain,
            ranking=source_ranking,
            source_store=self.source_registry_store,
            remember=self._remember_from_knowledge,
            auto_write_memory=self.knowledge_auto_write,
        )
        source_registry = knowledge_result.registry
        self.last_source_registry = source_registry
        self.log.log("source_registry", source_registry.to_log_payload())
        self.last_knowledge_pipeline = knowledge_result
        self.log.log("knowledge_pipeline", knowledge_result.to_log_payload())

        # 7. Respond. When replan exhausted the synthesizer still produces
        # a structured Output Contract reply — it gets the failure history
        # and is told to explain honestly what was tried and why nothing
        # worked. This is a much better UX than a bare error string.
        draft_answer = self._synthesize(
            goal=goal,
            artifacts=artifacts,
            question=user_question,
            planner_reasoning=planner_out.reasoning,
            history=history,
            persistent_block=persistent_block,
            cycle_findings=list(self._cycle_findings),
            failure_history=failure_history if replan_exhausted else None,
        )

        # 7.5 — MVP-14.4 Verifier. LLM is the DRAFT writer; the Verifier
        # gates what reaches the user. Every claim must be cited (LLM
        # follows the citation grammar in SYSTEM_ANSWER); the Verifier
        # rewrites matched citations to `[verified:<kind>:<src>]` and
        # tags uncited claims with `[unverified]`. A fully-uncited
        # answer earns an explicit disclaimer so the user can never
        # mistake an unsourced answer for a verified one.
        #
        # MVP-14.5 — when the LLM cites [web:URL] but no web_page evidence
        # exists for that URL (Verifier verdict `cited_but_unmatched`),
        # we treat this as a structured failure (`unresolved_citation`)
        # and feed it back through the SAME ReplanPolicy that already
        # governs tool-level failures. The next planner call is told
        # exactly which URLs to fetch; once web_fetch runs, the original
        # draft is re-verified on the enriched chain — no second LLM
        # synthesis is needed because the draft already cites the URLs.
        if self.verifier_enabled:
            from core.verifier import (
                extract_unresolved_web_urls,
                verify as _verify,
            )

            report = _verify(answer=draft_answer, chain=chain)
            self.log.log("verification", report.to_log_payload())
            self.last_verification = report
            self.last_provenance = chain

            verify_replan_attempt = 0
            VERIFY_REPLAN_HARD_CAP = 2  # belt + braces over ReplanPolicy

            while True:
                unresolved_urls = extract_unresolved_web_urls(report)
                if not unresolved_urls:
                    break
                if verify_replan_attempt >= VERIFY_REPLAN_HARD_CAP:
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
                    attempt=attempt + verify_replan_attempt,
                )
                failure_history.append(trigger)

                decision = self.replan_policy.decide(
                    failure_history=failure_history,
                    completed_attempts=attempt + verify_replan_attempt,
                )

                if decision.action != "continue":
                    replan_exhausted = True
                    self.log.log(
                        "replan_exhausted",
                        {
                            "phase": "verify",
                            "attempts": attempt + verify_replan_attempt,
                            "max_total": self.replan_policy.max_total_replans,
                            "decision_action": decision.action,
                            "decision_reason": decision.reason,
                            "failure_counts": dict(decision.failure_counts),
                            "triggers": ["unresolved_citation"],
                        },
                    )
                    break

                # Build the planner advice. We surface `unresolved_citation`
                # advice FIRST (the new failure dominates) and then append
                # an explicit URL list the planner must convert into
                # web_fetch steps. The base `decision.advice_for_planner`
                # is composed over all FailureTypes seen so far, in
                # FailureType-declaration order, so it might bury the
                # critical fetch instruction — we prepend ours explicitly.
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
                        "attempt": attempt + verify_replan_attempt - 1,
                        "next_attempt": attempt + verify_replan_attempt,
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

                # Bump the public attempt counter so `respond.attempts_used`
                # reflects the verify-driven re-plans honestly.
                self._current_attempt = attempt + verify_replan_attempt

                self.log.log(
                    "replan_attempt",
                    {
                        "phase": "verify",
                        "attempt": attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(verify_advice),
                        "forbidden_action_count": len(decision.forbidden_actions),
                        "failure_counts_so_far": dict(
                            self._count_failures(failure_history)
                        ),
                    },
                )

                failure_context = self._format_replan_context(
                    failure_history,
                    attempt + verify_replan_attempt,
                    self.replan_policy.max_total_replans,
                    advice=verify_advice,
                    forbidden_actions=decision.forbidden_actions,
                )

                planner_out = self.planner.plan(
                    question=user_question,
                    file_hint=file_hint,
                    history=planner_history,
                    failure_context=failure_context,
                    forbidden_actions=decision.forbidden_actions,
                )
                self.log.log(
                    "planner",
                    {
                        "phase": "verify",
                        "reasoning": planner_out.reasoning,
                        "tools_chosen": [s["tool"] for s in planner_out.sources],
                        "warnings": planner_out.warnings,
                        "raw_chars": len(planner_out.raw_response),
                        "attempt": attempt + verify_replan_attempt,
                        "replan_context_chars": len(failure_context),
                    },
                )

                verify_plan = self._build_plan(goal, planner_out.sources)
                self.log.log(
                    "plan",
                    verify_plan,
                    steps=len(verify_plan.steps),
                    attempt=attempt + verify_replan_attempt,
                    phase="verify",
                )

                # Execute the new steps. We tolerate sanitiser/policy drops
                # and per-step failures — if NOTHING gets fetched we'll
                # just exit on the next loop iteration when re-verify
                # still finds the same unresolved URLs. No infinite loop
                # because the hard cap + per-type budget both bound us.
                added_evidence = 0
                for step in verify_plan.steps:
                    outcome = self._execute_step(step)
                    if outcome is None:
                        step.status = "failed"
                        # Drain the scratch trigger so it doesn't leak into
                        # the next decide() iteration with a misleading code
                        # (e.g. tool_error for a fetch that was sanitised).
                        if self._last_step_failure is not None:
                            failure_history.append(self._last_step_failure)
                            self._last_step_failure = None
                        continue
                    artifacts[outcome["label"]] = {
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
                        chain.add(ev)
                        added_evidence += 1
                    step.status = "done"

                # Re-verify the ORIGINAL draft against the enriched chain.
                # The draft already cites these URLs (that's why they
                # were unresolved); now the chain has the web_page
                # evidence so `match_citation` will resolve them.
                report = _verify(answer=draft_answer, chain=chain)
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
                self.last_provenance = chain

                # Re-emit the chain snapshot so callers / log consumers
                # see the enriched provenance after each fetch round.
                self.log.log(
                    "evidence_collected",
                    {
                        "phase": "verify",
                        "count": len(chain),
                        "kinds": sorted({ev.kind for ev in chain.evidences}),
                        "chain": chain.to_log_payload(),
                    },
                )
                source_ranking = rank_chain(chain, question=user_question)
                self.last_source_ranking = source_ranking
                self.log.log(
                    "source_ranking",
                    {
                        **source_ranking.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                knowledge_result = self.knowledge_pipeline.run(
                    chain,
                    ranking=source_ranking,
                    source_store=self.source_registry_store,
                    remember=self._remember_from_knowledge,
                    auto_write_memory=self.knowledge_auto_write,
                )
                source_registry = knowledge_result.registry
                self.last_source_registry = source_registry
                self.log.log(
                    "source_registry",
                    {
                        **source_registry.to_log_payload(),
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

                # If the fetch round added zero evidence, re-verify will
                # produce the same unresolved list — exit instead of
                # looping back to a planner call that has nothing new
                # to work with.
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

            answer = report.annotated_answer
        else:
            answer = draft_answer
            self.last_verification = None

        policy_result = apply_ranker_output_policy(
            answer=answer,
            ranking=self.last_source_ranking,
            question=user_question,
            replan_exhausted=replan_exhausted,
        )
        if policy_result.applied:
            answer = policy_result.answer
            self.log.log("output_policy", policy_result.to_log_payload())

        # Defence-in-depth: redact once more on the way out so even an
        # LLM hallucinating a credential cannot bypass the kernel.
        safe_answer, answer_findings = redact_text(answer)
        if answer_findings:
            self.log.log(
                "secret_detected",
                {
                    "label": "final_answer",
                    "kinds": sorted({f.kind for f in answer_findings}),
                    "count": len(answer_findings),
                    "surface": "user_output",
                },
            )
        answer = safe_answer

        self.log.log(
            "respond",
            {
                "chars": len(answer),
                "sources": list(artifacts.keys()) or ["general-knowledge"],
                "redactions": len(self._cycle_findings or []),
                "attempts_used": self._current_attempt,
                "replan_exhausted": replan_exhausted,
            },
        )

        # Memory write — append the completed turn
        if self.memory is not None:
            turn = self.memory.record_turn(
                question=user_question,
                planner_reasoning=planner_out.reasoning,
                tools_used=[s["tool"] for s in planner_out.sources],
                artifact_labels=list(artifacts.keys()),
                answer=answer,
            )
            self.log.log(
                "memory_write",
                {
                    "session_id": self.memory.session_id,
                    "turn_id": turn.id,
                    "turn_index": turn.index,
                    "tools_used": turn.tools_used,
                    "labels": turn.artifact_labels,
                },
            )

        return answer

    # ---------- persistent memory facade ----------

    def _remember_from_knowledge(
        self,
        content: str,
        tags: list[str],
        source: str,
        record_type: str,
        owner: str,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        return self.remember(
            content=content,
            tags=tags,
            source=source,
            record_type=record_type,
            owner=owner,
        )

    def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        source: str = "user-explicit",
        record_type: str = "semantic",
        owner: str = "user",
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        """Run a `:remember`-style write through the Write Policy.

        Returns the decision plus the saved record (or None on reject).
        The store must be wired; otherwise a reject decision is returned.

        `owner` flows into the Write Policy. Anything outside the first-
        party whitelist needs a `cross-owner-consent` tag.
        """
        if self.persistent_store is None:
            decision = MemoryWriteDecision(
                "reject", ["persistent store not configured"]
            )
            self.log.log("persistent_memory_write", {"decision": decision.decision, "reasons": decision.reasons})
            return decision, None

        tags = tags or []
        # Dedup gate (MVP-10) needs the existing records — load them once
        # and pass into the policy so the policy stays a pure function.
        existing = self.persistent_store.load()
        decision = self.write_policy.decide(
            content=content,
            tags=tags,
            source=source,
            owner=owner,
            existing=existing,
        )
        if decision.decision == "reject":
            self.log.log(
                "persistent_memory_write",
                {
                    "decision": "reject",
                    "reasons": decision.reasons,
                    "policy_id": decision.policy_id,
                    "source": source,
                    "tag_count": len(tags),
                    "content_chars": len(content or ""),
                },
            )
            return decision, None

        record = MemoryRecord(
            type=record_type,  # type: ignore[arg-type]
            content=content.strip(),
            tags=tags,
            owner=owner,
        )
        self.persistent_store.save(record)
        self.log.log(
            "persistent_memory_write",
            {
                "decision": "save",
                "reasons": decision.reasons,
                "policy_id": decision.policy_id,
                "record_id": record.id,
                "tags": record.tags,
                "type": record.type,
                "chars": len(record.content) if isinstance(record.content, str) else 0,
            },
        )
        return decision, record

    def forget(self, record_id: str | None = None) -> int:
        """Delete one record (by id) or all records. Returns deletion count."""
        if self.persistent_store is None:
            return 0
        if record_id is None:
            n = self.persistent_store.delete_all()
            self.log.log("persistent_memory_delete", {"scope": "all", "deleted": n})
            return n
        ok = self.persistent_store.delete(record_id)
        self.log.log(
            "persistent_memory_delete",
            {"scope": "one", "record_id": record_id, "deleted": int(ok)},
        )
        return 1 if ok else 0

    def list_persistent(self) -> list[MemoryRecord]:
        if self.persistent_store is None:
            return []
        return self.persistent_store.load()

    # ------------------------------------------------------------------
    # MVP-11 Compensation surface (rollback / undo)
    # ------------------------------------------------------------------

    def propose_repair(
        self,
        *,
        target_path: str,
        workspace_root: "Path",
        test_paths: tuple[str, ...] = ("tests",),
        test_pattern: str | None = None,
        trace_id: str | None = None,
        extra_context: str = "",
    ):
        """Generate a guarded RepairProposal without applying it."""
        from core.repair_proposal import RepairProposalGenerator

        return RepairProposalGenerator(
            workspace_root=workspace_root,
            llm=self.model_router.for_role(ModelRole.REPAIR_PROPOSAL),
            logger=self.log,
        ).generate(
            target_path=target_path,
            test_paths=test_paths,
            test_pattern=test_pattern,
            trace_id=trace_id,
            extra_context=extra_context,
        )

    def repair(
        self,
        proposal: "Any",
        *,
        workspace_root: "Path",
    ):
        """Run one self-repair proposal through the MVP-13.2 controller."""
        from core.self_repair import SelfRepairController

        return SelfRepairController(
            self,
            workspace_root=workspace_root,
        ).run(proposal)

    def rollback(
        self,
        plan_id: str | None = None,
        *,
        workspace_root: "Path | None" = None,
    ):
        """Apply the most recent compensation plan (or one by id).

        Returns the `CompensationReport`. When the log is empty, the
        report carries zero outcomes and the audit event records the
        no-op. When `plan_id` is provided, the matching plan is
        removed from the log; otherwise the LAST plan is popped.

        `workspace_root` is required because `AgentLoop` is workspace-
        agnostic by construction. The CLI passes it from the main()
        --workspace argument; tests pass the same root the producing
        tool used.
        """
        from pathlib import Path as _Path

        from core.compensation import CompensationReport, apply_compensation_plan

        if workspace_root is None:
            report = CompensationReport(plan_id=plan_id or "", workspace_root="")
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no workspace_root supplied"},
            )
            return report

        if not self.compensation_log:
            report = CompensationReport(
                plan_id=plan_id or "", workspace_root=str(_Path(workspace_root).resolve())
            )
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no plans registered"},
            )
            return report

        if plan_id is None:
            plan = self.compensation_log.pop()
        else:
            for i, p in enumerate(self.compensation_log):
                if p.id == plan_id:
                    plan = self.compensation_log.pop(i)
                    break
            else:
                report = CompensationReport(
                    plan_id=plan_id, workspace_root=str(_Path(workspace_root).resolve())
                )
                self.log.log(
                    "compensation_apply",
                    {**report.summary(), "skipped_reason": f"plan_id '{plan_id}' not found"},
                )
                return report

        report = apply_compensation_plan(plan, _Path(workspace_root))
        self.log.log("compensation_apply", report.summary())
        return report

    # ------------------------------------------------------------------
    # MVP-10 Memory Hygiene surface
    # ------------------------------------------------------------------
    # Every hygiene operation is a discrete, deliberate call. The CLI
    # exposes them via `:hygiene <subcmd>`. Each method logs ONE event
    # carrying the typed report's `summary()` so audits show exactly
    # what was removed and why.

    def cleanup_backups(
        self,
        workspace_root,
        *,
        keep_last: int | None = None,
        max_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Delete old `.bak.<ts>` files under the workspace root."""
        from core.hygiene import (
            DEFAULT_KEEP_LAST,
            DEFAULT_MAX_AGE_DAYS,
            cleanup_backups,
        )

        report = cleanup_backups(
            workspace_root,
            keep_last=DEFAULT_KEEP_LAST if keep_last is None else keep_last,
            max_age_days=DEFAULT_MAX_AGE_DAYS if max_age_days is None else max_age_days,
            dry_run=dry_run,
        )
        self.log.log("backup_cleanup", report.summary())
        return report

    def expire_persistent(self, *, dry_run: bool = False):
        """Remove persistent records whose TTL has elapsed."""
        from core.hygiene import expire_memory

        if self.persistent_store is None:
            from core.hygiene import ExpiryReport

            empty = ExpiryReport(dry_run=dry_run)
            self.log.log("persistent_memory_expire", empty.summary())
            return empty
        report = expire_memory(self.persistent_store, dry_run=dry_run)
        self.log.log("persistent_memory_expire", report.summary())
        return report

    def dedupe_persistent(self, *, threshold: float | None = None, dry_run: bool = False):
        """Collapse near-duplicate persistent records (oldest kept)."""
        from core.hygiene import DEFAULT_DEDUP_THRESHOLD, deduplicate_memory

        if self.persistent_store is None:
            from core.hygiene import DedupReport

            empty = DedupReport(
                threshold=DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold,
                dry_run=dry_run,
            )
            self.log.log("persistent_memory_dedupe", empty.summary())
            return empty
        report = deduplicate_memory(
            self.persistent_store,
            threshold=DEFAULT_DEDUP_THRESHOLD if threshold is None else threshold,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_dedupe", report.summary())
        return report

    def summarise_persistent(
        self,
        tag: str,
        *,
        max_records: int | None = None,
        dry_run: bool = False,
    ):
        """Merge records sharing `tag` into a single summary via the LLM."""
        from core.hygiene import DEFAULT_SUMMARY_MAX_RECORDS, summarise_memory

        if self.persistent_store is None:
            from core.hygiene import SummaryReport

            empty = SummaryReport(tag=tag, skipped_reason="no store", dry_run=dry_run)
            self.log.log("persistent_memory_summarise", empty.summary())
            return empty
        report = summarise_memory(
            self.persistent_store,
            self.model_router.for_role(ModelRole.MEMORY_SUMMARY),
            tag=tag,
            max_records=DEFAULT_SUMMARY_MAX_RECORDS if max_records is None else max_records,
            dry_run=dry_run,
        )
        self.log.log("persistent_memory_summarise", report.summary())
        return report

    def _retrieve_persistent(self, question: str) -> str:
        """Pick + format relevant persistent records for prompt injection.

        Returns a `<long_term_memory>` XML block, or empty string when no
        store is wired, no records exist, or none score above threshold.
        """
        # MVP-14.1: stash the selected records so the evidence chain
        # can include them after run() finishes. Reset every cycle.
        self._last_persistent_records = []
        if self.persistent_store is None:
            return ""
        records = self.persistent_store.load()
        if not records:
            return ""
        use_report = self.knowledge_use_policy.filter(
            records,
            role_context=self.last_role_context,
            question=question,
        )
        self.log.log("knowledge_use_policy", use_report.to_log_payload())
        if not use_report.allowed:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no records applicable to current role",
                    "role": self.last_role_context.role,
                },
            )
            return ""
        selected = self.retrieval_policy.select(use_report.allowed, question)
        if not selected:
            self.log.log(
                "persistent_memory_inject",
                {
                    "records_total": len(records),
                    "records_selected": 0,
                    "reason": "no keyword overlap above threshold",
                    "role": self.last_role_context.role,
                    "records_applicable": len(use_report.allowed),
                },
            )
            return ""
        formatted = self.retrieval_policy.format_for_prompt(selected)
        self.log.log(
            "persistent_memory_inject",
            {
                "records_total": len(records),
                "records_selected": len(selected),
                "records_applicable": len(use_report.allowed),
                "ids": [r.id for r in selected],
                "chars": len(formatted),
                "role": self.last_role_context.role,
            },
        )
        self._last_persistent_records = list(selected)
        return f"<long_term_memory>\n{formatted}\n</long_term_memory>"

    # ---------- replan helpers ----------

    @staticmethod
    def _count_failures(history: list[ReplanTrigger]) -> dict[str, int]:
        """Return {failure_code: count} over the cumulative history.

        Used both by the audit log payload (`replan_attempt`) and the
        synthesizer prompt, so it's worth having one place that knows
        how to summarise the list.
        """
        counts: dict[str, int] = {}
        for t in history:
            counts[t.code] = counts.get(t.code, 0) + 1
        return counts

    @staticmethod
    def _format_replan_context(
        failure_history: list[ReplanTrigger],
        attempt: int,
        max_attempts: int,
        advice: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """Build the <replan_context> block fed to the planner.

        Empty string when there's nothing to replan against (first attempt
        and no prior failures). Otherwise a compact XML block listing each
        failed step with its code, tool, arguments, and reason — exactly
        the shape the planner's system prompt has been told to consume.

        MVP-12 additions:
          - `advice` is the `ReplanDecision.advice_for_planner` string
            composed from per-FailureType budgets. It tells the planner
            what KIND of correction to make.
          - `forbidden_actions` lists (tool, args_json) pairs the planner
            must NOT propose again. Surfaced inline so the LLM can read
            and respect it; the sanitiser also enforces it as defence in
            depth.

        We do NOT redact secrets here because:
          - tool arguments came from the planner's previous output, which
            was itself redacted before going to the LLM, so they cannot
            contain raw secrets that the planner hadn't already seen;
          - `LLMPlanner.plan` runs `redact_text` over the assembled user
            prompt one more time as a safety net.
        """
        if not failure_history and not advice and not forbidden_actions:
            return ""
        lines = [
            f"<replan_context attempt=\"{attempt}\" "
            f"max_attempts=\"{max_attempts}\">"
        ]
        if failure_history:
            lines.append(
                f"  Previous attempts produced {len(failure_history)} failed "
                f"step(s). Pick a different approach."
            )
            for trig in failure_history:
                args_compact = json.dumps(trig.arguments, ensure_ascii=False)
                lines.append(
                    f"  - attempt={trig.attempt} "
                    f"code={trig.code} "
                    f"tool={trig.tool_name or '(none)'} "
                    f"arguments={args_compact}"
                )
                lines.append(f"    reason: {trig.reason}")
        if advice:
            lines.append("  <advice>")
            for advice_line in advice.splitlines():
                if advice_line.strip():
                    lines.append(f"    {advice_line}")
            lines.append("  </advice>")
        if forbidden_actions:
            lines.append(
                "  <forbidden>  # do NOT propose these exact (tool, arguments) pairs"
            )
            for tool, args_json in forbidden_actions:
                lines.append(f"    - tool={tool} arguments={args_json}")
            lines.append("  </forbidden>")
        lines.append("</replan_context>")
        return "\n".join(lines)

    # ---------- phase implementations ----------

    def _interpret(self, observation: Observation) -> Goal:
        question = observation.content["question"]
        return Goal(
            description=f"Answer the question: {question}",
            success_criteria="A grounded answer citing every claim back to a provided source.",
        )

    def _build_plan(self, goal: Goal, sources: list[dict[str, Any]]) -> Plan:
        plan = Plan(goal_id=goal.id)
        for i, src in enumerate(sources, start=1):
            plan.steps.append(
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

    def _execute_step(self, step: PlanStep) -> dict[str, Any] | None:
        """Run a single PlanStep through Act -> Policy -> Tool -> Verify.

        Working Memory short-circuit: if (tool, arguments) is already in the
        artifact cache, we skip Policy + Tool + Verify and reuse the cached
        output. Read-only tools are deterministic enough that this is safe
        within a single session.

        Returns artifact dict on success, None on hard failure. On failure
        the method also writes a `ReplanTrigger` to `self._last_step_failure`
        so the parent loop can decide whether to re-plan.
        """
        # Clear the scratch slot so the caller never sees a stale trigger
        # from a previous step within the same attempt.
        self._last_step_failure = None

        tool_name = step.action_spec.get("tool_name")
        source_label = step.action_spec.get("source_label") or f"step:{step.id}"
        arguments = step.action_spec.get("arguments", {})

        # Memory cache short-circuit
        if self.memory is not None and tool_name:
            cached = self.memory.cache_lookup(tool_name, arguments)
            if cached is not None:
                self.log.log(
                    "memory_cache_hit",
                    {
                        "tool": tool_name,
                        "arguments": arguments,
                        "label": cached["label"],
                        "stored_at_turn": cached["turn_index"],
                    },
                )
                return {
                    "label": cached["label"],
                    "tool": tool_name,
                    "arguments": arguments,
                    "output": cached["output"],
                    "issues": ["served from working-memory cache"],
                }

        action = Action(
            step_id=step.id,
            type=step.action_spec["type"],
            tool_name=tool_name,
            parameters=arguments,
            side_effects="read" if tool_name else "none",
        )
        self.log.log("act", action, source_label=source_label)

        # Policy Gate — pre-execution checkpoint
        decision = self.policy.check(action)
        self.log.log("policy", decision)

        if decision.decision == "deny":
            err = ErrorObject(
                source="policy",
                code="policy_blocked",
                message=f"Action blocked: {', '.join(decision.reasons)}",
                severity="error",
                recoverable=False,
                context={
                    "action_id": action.id,
                    "decision": "deny",
                    "source_label": source_label,
                },
            )
            self.log.log("error", err)
            self._last_step_failure = ReplanTrigger(
                code="policy_blocked",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Policy gate denied: {', '.join(decision.reasons) or 'unknown'}",
                attempt=self._current_attempt,
            )
            return None

        if decision.decision == "escalate":
            # PolicyGate flagged a risky action. Bring in the human via the
            # ApprovalProvider; if no provider is wired, the safe default
            # is to refuse — exactly the pre-MVP-6 behaviour.
            verdict = self._request_approval(
                action=action,
                step=step,
                source_label=source_label,
                policy_decision=decision,
                arguments=arguments,
            )
            if verdict != "approve":
                # Map the approval-side verdict back to a replan code
                # so the planner can pick a safer alternative (e.g.
                # swap an irreversible action for a read-only one).
                code: ReplanCode = (
                    "approval_deny" if verdict == "deny"
                    else "approval_abort" if verdict == "abort"
                    else "approval_unavailable"
                )
                # PolicyGate would have denied an unknown tool before we
                # got here, so the registry lookup is safe.
                risk = (
                    self.registry.get(tool_name).risk if tool_name else "unknown"
                )
                self._last_step_failure = ReplanTrigger(
                    code=code,
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=arguments,
                    reason=f"Approval gate returned '{verdict}' for risk={risk}",
                    attempt=self._current_attempt,
                )
                return None

        if action.type != "tool_call":
            self._last_step_failure = ReplanTrigger(
                code="unknown",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Unsupported action type: {action.type}",
                attempt=self._current_attempt,
            )
            return None  # not used in MVP-2

        # Tool execution
        result = self._call_tool(action)
        if result.status != "success":
            err = ErrorObject(
                source=action.tool_name or "tool",
                code="tool_error",
                message=result.error or "tool failed",
                severity="error",
                recoverable=False,
                context={"tool_call_id": result.tool_call_id, "source_label": source_label},
            )
            self.log.log("error", err)
            self._last_step_failure = ReplanTrigger(
                code="tool_error",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=result.error or "tool execution failed",
                attempt=self._current_attempt,
            )
            return None

        # Tool Result Validation (delegates to the tool itself)
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        is_ok, issues = tool.validate_output(result.output)
        self.log.log(
            "verify",
            {
                "step_id": step.id,
                "ok": is_ok,
                "issues": issues,
                "source_label": source_label,
            },
        )
        if not is_ok:
            err = ErrorObject(
                source="verifier",
                code="verify_failed",
                message=f"Tool Result Validation failed: {', '.join(issues) or 'unspecified'}",
                severity="error",
                recoverable=True,
                context={"step_id": step.id, "source_label": source_label},
            )
            self.log.log("error", err)
            self._last_step_failure = ReplanTrigger(
                code="verify_failed",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason=f"Validation rejected output: {'; '.join(issues) or 'no detail'}",
                attempt=self._current_attempt,
            )
            return None

        # --- MVP-12: structured-failure reclassification ----------------------
        # Two outcomes pass validate_output but still need a re-plan with a
        # specific advice: empty web_search results and shell_exec timeouts.
        # Detecting them HERE (not inside the tools) keeps the tool contracts
        # stable while letting the ReplanPolicy advise the planner per case.
        if action.tool_name == "web_search" and isinstance(result.output, list) and len(result.output) == 0:
            self.log.log(
                "error",
                ErrorObject(
                    source="web_search",
                    code="web_empty",
                    message="web_search returned zero hits — query needs reformulation",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            self._last_step_failure = ReplanTrigger(
                code="web_empty",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="web_search returned 0 results; reformulate the query",
                attempt=self._current_attempt,
            )
            return None
        if action.tool_name == "shell_exec" and isinstance(result.output, dict) and result.output.get("timed_out"):
            self.log.log(
                "error",
                ErrorObject(
                    source="shell_exec",
                    code="timeout",
                    message="shell_exec hit its timeout — output truncated, exit_code unknown",
                    severity="error",
                    recoverable=True,
                    context={"step_id": step.id, "source_label": source_label},
                ),
            )
            self._last_step_failure = ReplanTrigger(
                code="timeout",
                step_id=step.id,
                tool_name=tool_name,
                arguments=arguments,
                reason="shell_exec timed out; reduce scope or pick a faster path",
                attempt=self._current_attempt,
            )
            return None

        # --- Safety pipeline (§7 / MVP-7) ---------------------------------
        # 1. Classify the tool output.
        # 2. Emit secret_detected when the classifier returns SECRET.
        # 3. Deep-redact the output BEFORE it reaches artifacts, memory
        #    cache, or the synthesizer prompt. From this point on the loop
        #    only handles redacted text.
        source_hint: SourceHint = _TOOL_SOURCE_HINTS.get(
            action.tool_name or "", "tool_output"
        )
        flat_output = _to_text(result.output)
        cls_result = classify(flat_output, source=source_hint)
        self.log.log(
            "data_classified",
            {
                "label": source_label,
                "tool": action.tool_name,
                "class": cls_result.cls.value,
                "source": cls_result.source,
                "reasons": cls_result.reasons,
            },
        )
        if cls_result.cls == DataClass.SECRET:
            findings = scan(flat_output)
            kinds = sorted({f.kind for f in findings})
            self.log.log(
                "secret_detected",
                {
                    "label": source_label,
                    "tool": action.tool_name,
                    "kinds": kinds,
                    "count": len(findings),
                    "surface": "tool_output",
                },
            )
            if self._cycle_findings is not None:
                self._cycle_findings.append(
                    {"label": source_label, "kinds": kinds, "count": len(findings)}
                )

        safe_output = redact_payload(result.output)

        # Memory write — cache the REDACTED artifact for future turns
        if self.memory is not None and action.tool_name:
            self.memory.cache_store(
                tool_name=action.tool_name,
                arguments=arguments,
                output=safe_output,
                label=source_label,
            )

        return {
            "label": source_label,
            "tool": action.tool_name,
            # MVP-14.1: surfaced so the evidence factory can build a
            # typed `Evidence` (source_id often depends on an argument
            # like `path` or `url`).
            "arguments": action.parameters,
            "output": safe_output,
            "issues": issues,
            "data_class": cls_result.cls.value,
        }

    def _call_tool(self, action: Action) -> ToolResult:
        assert action.tool_name is not None
        tool = self.registry.get(action.tool_name)
        call = ToolCall(
            action_id=action.id,
            tool_name=action.tool_name,
            arguments=action.parameters,
        )
        self.log.log("tool_call", call)
        result = tool.invoke(call)
        self.log.log("tool_result", result, status=result.status, latency_ms=result.latency_ms)
        # MVP-11 Compensation capture: a successful tool call may carry a
        # `compensation_plan` block in its structured output. The agent
        # owns the plan registry from here on — `rollback()` reads from
        # this list, in LIFO order, and applies the plan inside the
        # workspace root sandbox.
        self._capture_compensation_plan(result)
        return result

    def _capture_compensation_plan(self, result: ToolResult) -> None:
        """Extract + register a `compensation_plan` from tool output, if any.

        Only success results are inspected. The plan is logged as a
        `compensation_registered` event so an audit can match every
        applied rollback to the tool call that produced it.
        """
        if result.status != "success" or not isinstance(result.output, dict):
            return
        raw = result.output.get("compensation_plan")
        if not isinstance(raw, dict):
            return
        # Skip noop plans — they pollute :rollback semantics and the
        # audit log. The plan is still IN the tool_result output, so
        # an analyst can see read-only commands also produced one.
        actions = raw.get("actions") or []
        if all(a.get("kind") == "noop" for a in actions):
            return
        from core.compensation import CompensationPlan  # avoid import cycle

        plan = CompensationPlan.from_dict(raw)
        self.compensation_log.append(plan)
        self.log.log(
            "compensation_registered",
            {
                "plan_id": plan.id,
                "tool_name": plan.tool_name,
                "description": plan.description,
                "action_count": len(plan.actions),
                "tool_call_id": result.tool_call_id,
            },
        )

    # ---------- approval ----------

    def _request_approval(
        self,
        action: Action,
        step: PlanStep,
        source_label: str,
        policy_decision: PolicyDecision,
        arguments: dict[str, Any],
    ) -> _Literal["approve", "deny", "abort", "unavailable"]:
        """Bridge between the PolicyGate's `escalate` verdict and the human.

        Emits two trace events — `approval_request` then `approval_decision`
        — and turns the decision into a control-flow signal for the loop:
          - "approve" -> caller continues with the tool call
          - anything else -> caller bails out, no tool_call is logged
        """
        if self.approval_provider is None:
            err = ErrorObject(
                source="approval",
                code="approval_unavailable",
                message=(
                    "Action escalated but no approval provider is configured. "
                    f"Reasons: {', '.join(policy_decision.reasons)}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "action_id": action.id,
                    "policy_decision_id": policy_decision.id,
                    "source_label": source_label,
                },
            )
            self.log.log("error", err)
            return "unavailable"

        # Tool must exist — PolicyGate would have returned deny otherwise.
        tool = self.registry.get(action.tool_name)  # type: ignore[arg-type]
        # Mirror the PolicyGate: ask the tool for its argument-aware
        # risk so the approval prompt reports what the gate actually
        # decided on, not the static class fallback.
        effective_risk = tool.risk_for(arguments or {})
        request = ApprovalRequest(
            action_id=action.id,
            step_id=step.id,
            tool_name=action.tool_name,
            arguments=arguments,
            risk=effective_risk,
            reasons=list(policy_decision.reasons),
            summary=(
                f"Tool '{action.tool_name}' wants to execute "
                f"with risk={effective_risk}."
            ),
            policy_decision_id=policy_decision.id,
        )
        self.log.log("approval_request", request)

        decision = self.approval_provider.request(request)
        self.log.log("approval_decision", decision)

        if decision.decision == "approve":
            return "approve"

        err = ErrorObject(
            source="approval",
            code=f"approval_{decision.decision}",  # approval_deny | approval_abort
            message=(
                f"Approval not granted ({decision.decision}): "
                f"{'; '.join(decision.reasons) or 'no reason given'}"
            ),
            severity="error",
            recoverable=False,
            context={
                "action_id": action.id,
                "approval_request_id": request.id,
                "approval_decision_id": decision.id,
                "responder": decision.responder,
                "source_label": source_label,
            },
        )
        self.log.log("error", err)
        return decision.decision

    # ---------- response synthesis ----------

    def _synthesize(
        self,
        goal: Goal,
        artifacts: dict[str, dict[str, Any]],
        question: str,
        planner_reasoning: str,
        history: str = "",
        persistent_block: str = "",
        cycle_findings: list[dict[str, Any]] | None = None,
        failure_history: list[ReplanTrigger] | None = None,
    ) -> str:
        history_block = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            if history.strip()
            else ""
        )
        long_term_block = (
            f"{persistent_block}\n\n" if persistent_block.strip() else ""
        )
        role_block = self.last_role_context.to_prompt_block() + "\n\n"

        # Kernel-built safety notes — the LLM is told to surface these in
        # the user-facing answer. The notes describe what was redacted
        # (the kernel did it), not what the LLM did.
        safety_block = ""
        if cycle_findings:
            lines = ["<safety_notes>"]
            for f in cycle_findings:
                lines.append(
                    f"- label={f['label']} kinds={f['kinds']} "
                    f"count={f['count']} (kernel-redacted)"
                )
            lines.append("</safety_notes>")
            safety_block = "\n".join(lines) + "\n\n"

        # Failure context (MVP-8). Only injected when re-planning was
        # exhausted; carries the cumulative trigger list so the
        # synthesizer can write an honest Conclusion ("I tried X, Y, Z;
        # here is why none worked") instead of an empty/fake answer.
        failure_block = ""
        if failure_history:
            lines = ["<failure_context>"]
            lines.append(
                f"Re-planning was exhausted after every attempt failed."
            )
            for trig in failure_history:
                lines.append(
                    f"- attempt={trig.attempt} code={trig.code} "
                    f"tool={trig.tool_name or '(none)'}: {trig.reason}"
                )
            lines.append("</failure_context>")
            failure_block = "\n".join(lines) + "\n\n"

        # The question travels into the LLM prompt verbatim; redact any
        # credential the user pasted in.
        safe_question, _q_findings = redact_text(question)

        if artifacts:
            blocks: list[str] = []
            for label, art in artifacts.items():
                formatted = self._format_artifact(art["tool"], art["output"])
                blocks.append(f'<evidence source="{label}">\n{formatted}\n</evidence>')
            evidence = "\n\n".join(blocks)
            allowed_citations_block = self._format_allowed_citations_block(
                self.last_provenance
            )

            warnings = [
                f"{label}: {'; '.join(art['issues'])}"
                for label, art in artifacts.items()
                if art.get("issues")
            ]
            warnings_block = (
                "<validator_notes>\n" + "\n".join(warnings) + "\n</validator_notes>\n\n"
                if warnings
                else ""
            )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{role_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"{allowed_citations_block}"
                f"{evidence}\n\n"
                f"{warnings_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "Answer using the Output Contract from the system instructions. "
                "Maintain continuity with any prior turns shown in conversation_history. "
                "If long_term_memory contains a relevant record you may cite it "
                "with source label [memory:<record_id>]."
            )
        else:
            # No tools were called — either the planner judged this a
            # general-knowledge question, OR a follow-up answerable from
            # conversation_history / long_term_memory alone, OR re-planning
            # exhausted (failure_history present). The Output Contract
            # still applies in every case.
            extra_guidance = ""
            if failure_history:
                extra_guidance = (
                    "Re-planning was exhausted. Use the <failure_context> "
                    "block to write an honest Conclusion: state plainly that "
                    "the agent could not collect evidence, list what was "
                    "tried (one bullet per attempt), and put the unmet "
                    "information need under Unverified. Cite each fact as "
                    "[general-knowledge] when relying on prior knowledge."
                )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{role_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "No <evidence> blocks are provided. If conversation_history or "
                "long_term_memory covers the answer, cite those sources verbatim "
                "(use [memory:<record_id>] for long_term_memory entries); "
                "otherwise answer from general knowledge with [general-knowledge] "
                "as the source label. Follow the Output Contract from the system instructions."
                + (f" {extra_guidance}" if extra_guidance else "")
            )

        # Defence-in-depth: the prompt is now built from redacted artifacts
        # and a redacted question. One more pass catches anything we missed
        # (e.g. a secret hiding in planner_reasoning).
        safe_user_prompt, _ = redact_text(user_prompt)
        return self.llm.complete(system=SYSTEM_ANSWER, user=safe_user_prompt, temperature=0.5)

    @staticmethod
    def _citation_for_evidence(ev: Evidence) -> str | None:
        source_id = ev.source_id
        if ev.kind == "file" and source_id.startswith("file:"):
            body = source_id[len("file:"):]
            return f"[file:{body}]"
        if ev.kind == "web_page" and source_id.startswith("web_page:"):
            body = source_id[len("web_page:"):]
            return f"[web:{body}]"
        if ev.kind == "web_search_hit" and source_id.startswith("web_search:"):
            body = source_id[len("web_search:"):]
            return f"[search:{body}]"
        if ev.kind == "test_result" and source_id.startswith("test_result:"):
            body = source_id[len("test_result:"):]
            return f"[test:{body}]"
        if ev.kind == "log_event" and source_id.startswith("log_event:"):
            body = source_id[len("log_event:"):]
            return f"[log:{body}]"
        if ev.kind == "shell_output" and source_id.startswith("shell_output:"):
            body = source_id[len("shell_output:"):]
            return f"[shell:{body}]"
        if ev.kind == "diff_preview" and source_id.startswith("diff_preview:"):
            body = source_id[len("diff_preview:"):]
            return f"[diff:{body}]"
        if ev.kind == "memory":
            return f"[memory:{source_id}]"
        if ev.kind == "user_explicit":
            return "[user]"
        return None

    @classmethod
    def _format_allowed_citations_block(cls, chain: ProvenanceChain) -> str:
        if not chain.evidences:
            return ""
        lines = ["<allowed_citations>"]
        seen: set[str] = set()
        for ev in chain.evidences:
            token = cls._citation_for_evidence(ev)
            if token is None or token in seen:
                continue
            seen.add(token)
            lines.append(
                f"- {token} kind={ev.kind} source_id={ev.source_id}"
            )
        lines.append("</allowed_citations>")
        return "\n".join(lines) + "\n\n" if len(lines) > 2 else ""

    @staticmethod
    def _format_artifact(tool_name: str | None, output: Any) -> str:
        """Render a tool output into a stable string the LLM can ground on."""
        if tool_name == "web_search" and isinstance(output, list):
            if not output:
                return "(no results)"
            lines: list[str] = []
            for r in output:
                title = r.get("title") or "(no title)"
                url = r.get("url") or ""
                snippet = r.get("snippet") or ""
                source = r.get("source") or "duckduckgo"
                lines.append(f"- {title}")
                lines.append(f"  url: {url}")
                if snippet:
                    lines.append(f"  snippet: {snippet}")
                lines.append(f"  provider: {source}")
            return "\n".join(lines)
        if tool_name == "file_read" and isinstance(output, str):
            return output
        # Fallback: stringify whatever came back.
        return str(output)


def new_trace_id() -> str:
    return new_id("run")
