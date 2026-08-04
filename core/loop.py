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

from asyncio import CancelledError
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.evidence import (
    ProvenanceChain,
)
from core.file_request_intent import (
    prepare_multi_file_review,
)
from core.ids import new_id
from core.replan import ReplanTrigger
from core.run_context import run_scope

if TYPE_CHECKING:
    from core.approval_inbox import ApprovalInbox
# Ре-экспорт сохранён целиком: десятки тестов и соседей импортируют эти имена
# как `from core.loop import …`, и роспуск `loop_helpers` не имеет права рвать
# их пути. Сами имена теперь живут там, где их предмет.
from core.answer_format import (  # noqa: F401 -- re-exported
    _ANSWER_CITATION_RE,
    _VERIF_MARKER_RE,
    LOCAL_CRITIQUE_SYSTEM_ADDENDUM,
    SYSTEM_ANSWER,
    _strip_verification_markers,
    citation_for_evidence,
    file_scope_notice,
    format_allowed_citations_block,
    format_artifact,
    format_human_response,
    output_contract_requires_headers,
)
from core.completion_contract import derive_completion_contract
from core.ids import new_trace_id  # noqa: F401 -- re-exported
from core.injection_guard import _to_text, untrusted_scan_view  # noqa: F401 -- re-exported
from core.loop_attempt import AgentLoopAttempt, AttemptState
from core.loop_context import AgentLoopContext
from core.loop_evidence_chain import AgentLoopEvidenceChain
from core.loop_gates import AgentLoopGates
from core.loop_hygiene import AgentLoopHygiene
from core.loop_init import AgentLoopInit
from core.loop_knowledge import AgentLoopKnowledge
from core.loop_memory_commands import AgentLoopMemoryCommands
from core.loop_memory_read import AgentLoopMemoryRead
from core.loop_memory_write import AgentLoopMemoryWrite
from core.loop_observe import AgentLoopObserve
from core.loop_repair import AgentLoopRepair
from core.loop_response_deciders import AgentLoopResponseDeciders
from core.loop_run_tail import AgentLoopRunTail
from core.loop_sensor import AgentLoopSensor
# Швы импорта: имена принадлежат `core/loop_step_execution`, но соседи и тесты
# берут их отсюда. `# noqa: F401` тут не косметика, а ЕДИНСТВЕННОЕ, что их
# защищает: пояснение обычным комментарием ruff не читает и снимает импорт как
# неиспользуемый (проверено — так и случилось, см. блокнот §24).
from core.loop_step_execution import (  # noqa: F401 -- шов импорта
    _TOOL_SOURCE_HINTS,
    _TRUSTED_INTERNAL_TOOLS,
    _step_trigger_tls,
)
from core.loop_step_execution import (
    AgentLoopStepExecution,
)
from core.loop_synthesis import AgentLoopSynthesis, SynthesisState
from core.loop_verification import AgentLoopVerification
from core.loop_verify_replan import AgentLoopVerifyReplan, VerifyState
from core.models import (
    Plan,
)
from core.planner import PlannerOutput
from core.replan import DEFAULT_MAX_REPLAN_ATTEMPTS  # noqa: F401 -- re-exported

# Шаг плана уехал в свой модуль (правило «компактные модули»); имена
# ре-экспортируются, чтобы существующие импорт-пути не порвались.
# `ReplanCode` (алиас FailureType) жил здесь и импортируется снаружи
# (tests/test_replan_audit.py). Исполнение шага уехало и унесло его
# использование — сохраняем шов явным ре-экспортом.
from core.replan import FailureType as ReplanCode  # noqa: F401 — шов импорта
from core.smart_memory import (
    effective_completion,
)
from core.source_registry import SourceRegistry
from core.step_repetition import StepRepetitionTracker
from core.termination_guard import TerminationGuard

# Default attempt budget for re-planning. Two replans (3 attempts total)
# is the tradeoff: enough room to recover from a typo or a flaky source,
# not enough to mask a fundamentally wrong plan as "just one more try".


# ReplanCode is an alias for FailureType (core/replan.py) — single source of truth.
# Imported above. No local definition needed. `ReplanTrigger` and the two
# helpers that summarise/format the failure history moved there too
# (2026-08-02): the whole replan vocabulary now lives in one module.


# Output Contract (§1 Interface & Communication + §8 Verification).
# The LLM MUST emit this structure so the user gets:
#   - a direct answer
#   - explicit citations to source labels embedded in the evidence
#   - explicit confidence and unverified gaps

# §3.x — register this prompt with the global Prompt Registry
try:
    from core.prompt_registry import register_prompt as _rp
    _rp("synthesizer.system", SYSTEM_ANSWER, module="core.loop",
        description="Output contract for the LLM synthesizer (§3 Cognitive Core)")
except ImportError:  # pragma: no cover
    pass

# Regex that matches the internal verification markers the Verifier inlines
# into the answer text.  These are audit annotations, not user content.
# Stripped before the answer leaves the kernel so users never see them.


# Regex that strips source citation tokens from individual sentences/bullets.
# Matches: [general-knowledge] [web:url] [file:path] [search:q] [test:cmd]
# [log:id] [shell:cmd] [diff:p] [memory:id] [user] [declared:...] etc.


class AgentLoop(
    AgentLoopStepExecution,
    AgentLoopAttempt,
    AgentLoopContext,
    AgentLoopEvidenceChain,
    AgentLoopGates,
    AgentLoopObserve,
    AgentLoopResponseDeciders,
    AgentLoopRunTail,
    AgentLoopSynthesis,
    AgentLoopVerification,
    AgentLoopVerifyReplan,
    AgentLoopInit,
    AgentLoopMemoryRead,
    AgentLoopMemoryWrite,
    AgentLoopHygiene,
    AgentLoopKnowledge,
    AgentLoopMemoryCommands,
    AgentLoopRepair,
    AgentLoopSensor,
):
    """Runs a single agent cycle.

    MVP-3: the planner is an LLM. The CLI only supplies the question plus an
    optional file hint. The planner picks which tools (if any) to call; the
    Executor runs the plan and the Synthesizer produces the Output Contract.
    """

    #: Навешивается снаружи и лениво: `cli/commands_approval.py` создаёт ящик
    #: при первом обращении. Контракт держался на `getattr` со строкой и был
    #: невидим; объявляем явно, значение по умолчанию прежнее.
    approval_inbox: ApprovalInbox | None = None

    # Сборка уехала в `core/loop_init.py` (кусок 12): 34 параметра и 65
    # присваиваний описывают, ИЗ ЧЕГО агент состоит, а не как он ведёт ход.
    # `__init__` разрешается по MRO — для вызывающего ничего не изменилось.

    # ---------- audit / read-only execution brake ----------


    # ---------- public entry point ----------

    def run(
        self,
        user_question: str,
        file_hint: str | None = None,
        on_token: Any = None,
        deep_escalation: Any = None,
        task_id: str | None = None,
    ) -> str:
        """Run one observe→plan→act→verify→respond cycle.

        Args:
            user_question: The user's natural-language input.
            file_hint: Optional workspace file path to pre-load.
            on_token: Optional ``(str) -> None`` callback invoked for each
                      synthesis token as it streams from the LLM.  Pass
                      ``lambda t: print(t, end="", flush=True)`` for live
                      CLI display.  ``None`` (default) disables streaming.
            deep_escalation: Optional operator-supplied
                      :class:`~core.deep_escalation.OperatorEscalation`. Only an
                      explicit, valid operator reason lets planner/synthesizer
                      escalate to the deep (Opus) tier; the default ``None``
                      keeps every autonomous run on the standard tier.
            task_id: Optional id of the *logical task* this run serves. It
                      survives a retry; the run id minted below does not.

        This is a thin wrapper: it owns run identity and nothing else, so the
        identity is bound before any cycle work and released even if the cycle
        raises. The body lives in `_run_inner`.
        """
        with run_scope(new_id("run"), task_id):
            try:
                return self._run_inner(
                    user_question=user_question,
                    file_hint=file_hint,
                    on_token=on_token,
                    deep_escalation=deep_escalation,
                )
            except (KeyboardInterrupt, CancelledError):
                # Cancellation is a control signal, not a failure to absorb.
                # Record the outcome honestly, then let it keep propagating —
                # swallowing it would strand the caller that asked to stop.
                # Caught explicitly rather than via `except BaseException` so
                # unrelated exits (SystemExit, MemoryError) are not reinterpreted.
                self._record_aborted_episode(user_question, reason="cancelled")
                raise
            except Exception as exc:
                self._record_aborted_episode(
                    user_question, reason=type(exc).__name__
                )
                raise

    # Minimum measured quality an episode needs before its answer may be
    # served verbatim instead of running a real cycle.
    _REPLAY_MIN_QUALITY = 0.70
    # Jaccard overlap with the stored question below which a replay is not
    # even considered the same ask.
    _REPLAY_MIN_SIMILARITY = 0.85

    @staticmethod
    def _fast_path_allows_replay(episode: Any, similarity: float) -> bool:
        """The episode-shaped half of the fast-path gate.

        Named so the three episodic readers agree on how the completion axis
        is read: through the frozen state and the shared accessor, never the
        declaration and never a re-derivation. Replay serves a stored answer
        INSTEAD of running a cycle, so a `lesson` gets no exception here —
        being retrievable as a warning is not being reusable as an answer.
        """
        return bool(
            episode is not None
            and similarity >= AgentLoop._REPLAY_MIN_SIMILARITY
            and effective_completion(episode) == "achieved"
            and AgentLoop._quality_allows_replay(episode)
            and getattr(episode, "full_answer", "")
            and not getattr(episode, "tools_used", ())
        )

    @staticmethod
    def _quality_allows_replay(episode: Any) -> bool:
        """May this episode's answer be replayed, on quality grounds alone?

        An unmeasured score (None — the episode carried no evidence chunks)
        is refused. Absence of measurement is not evidence of quality, and
        the previous encoding of "unmeasured" as 1.0 cleared this gate by the
        widest possible margin (MIR-002).
        """
        score = getattr(episode, "answer_quality_score", None)
        if score is None:
            return False
        return score >= AgentLoop._REPLAY_MIN_QUALITY

    def _run_inner(
        self,
        user_question: str,
        file_hint: str | None = None,
        on_token: Any = None,
        deep_escalation: Any = None,
    ) -> str:
        """The cycle body. Always entered through `run`, which owns run identity."""
        # Store streaming callback so _synthesize() can pick it up without
        # changing its signature (which is called from multiple paths).
        self._stream_on_token = on_token
        self._cycle_findings = []
        # Tools that ACTUALLY executed this run, in order. Procedure
        # attribution (MIR-049) is judged from this rather than from the plan,
        # so a run cancelled before reaching a procedure's steps never debits
        # it. Accumulated as execution happens so an exception cannot discard
        # attribution already earned.
        self._executed_tools = []
        # Sensor verdicts this run raised about ITSELF, accumulated as they
        # fire. Each of these sensors used to log and drop its finding, so a
        # run's own faults never reached the episode and the same mistake could
        # be repeated indefinitely without a trace. Reset per cycle for the same
        # reason `_executed_tools` is: instance state outlives a run, and an
        # inherited fault would be banked against the wrong episode.
        self._defect_signals = []
        self.last_replan_exhausted = False
        self.last_source_ranking = None
        self.last_source_registry = SourceRegistry()
        self.last_knowledge_pipeline = None
        # Per-sink permissions for this cycle. Experience-memory sinks
        # (episode/procedure/consolidation) are resolved inside
        # `_record_experience_memory`, which owns those three writes.
        may_knowledge = not self._durable_learning_suppressed("knowledge")
        may_source_registry = not self._durable_learning_suppressed("source_registry")
        may_profile = not self._durable_learning_suppressed("profile")
        may_assumptions = not self._durable_learning_suppressed("assumptions")

        # Кусок 16 разбора `_run_inner`: открывающая часть прогона (профиль,
        # реестр допущений, писатель контрольных точек) живёт в
        # `core/loop_context.py` — всё, что заводится один раз и до фаз.
        _run_assumptions, _cp = self._open_run(user_question)

        # 1. Observe
        # Куски 6 разбора `_run_inner`: наблюдение, классификация вопроса,
        # маршрут роли и выбор модели живут в `core/loop_observe.py`.
        goal, _task_planner_llm, _task_synth_llm = self._observe_and_route(
            user_question,
            file_hint=file_hint,
            deep_escalation=deep_escalation,
            _cp=_cp,
        )

        # 2a. Completion contract (MIR-067) — derived from the REQUEST, here,
        # before a single tool runs. The ordering is the proof: this event
        # precedes every `act`/`tool_call` in the journal, so the criterion
        # cannot have been shaped by the work it judges. Recorded even when
        # empty, because "this request owed nothing verifiable" is itself the
        # fact a later reader needs.
        # Deliberately a LOCAL, never an attribute: a contract that outlived
        # its run would judge the NEXT request by this one's criterion.
        # `tests/test_completion_marker.py` pins that invariant for the whole
        # completion family, and it caught this exact mistake in review.
        completion_contract = derive_completion_contract(
            user_question, file_hint=file_hint
        )
        self.log.log(
            "completion_contract", completion_contract.to_log_payload()
        )

        _decided = self._odd_gate(user_question)
        if _decided is not None:
            return _decided

        _decided = self._clarification_gate(user_question)
        if _decided is not None:
            return _decided

        # Memory retrieval — read-only injection into prompts
        # Кусок 9 разбора `_run_inner`: чтение контекста хода живёт в
        # `core/loop_context.py`. Только чтение — ничего, кроме журнала и
        # полей на цикле, эти шаги не меняют.
        (
            history,
            local_critique_active,
            persistent_block,
            experience_block,
        ) = self._retrieve_turn_context(user_question, file_hint=file_hint)

        _decided = self._episodic_fast_path(
            user_question,
            file_hint=file_hint,
            goal=goal,
            local_critique_active=local_critique_active,
        )
        if _decided is not None:
            return _decided

        # Planner sees the persistent block prepended to working history so
        # it can opt out of redundant tool calls when the answer is already
        # in long-term memory. Role is logged and injected into synthesis,
        # but kept out of `history` so `<conversation_history>` stays a
        # strict marker for actual prior dialogue.
        planner_history = "\n\n".join(
            part for part in (persistent_block, experience_block, history) if part.strip()
        )
        multi_file = prepare_multi_file_review(
            user_question,
            file_hint=file_hint,
            workspace_root=self._file_read_workspace_root(),
            log=self.log.log,
        )
        # Кусок 15 разбора `_run_inner`: ворота живут в `core/loop_gates.py`.
        # Возвращают `str | None` — «ход решён, вот ответ» или «я не при делах»:
        # `return` из помощника не есть выход из цикла, поэтому решает вызывающий.
        _decided = self._multi_file_refusal(
            multi_file, user_question=user_question, file_hint=file_hint, goal=goal,
        )
        if _decided is not None:
            return _decided
        forced_sources = (
            list(multi_file["sources"])
            if multi_file["kind"] == "forced"
            else None
        )
        forced_reasoning = str(multi_file.get("reasoning") or "")
        forced_warnings = list(multi_file.get("warnings") or [])

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
        # Per-run step repetition tracker (MAST FM-1.3). Counts (tool, args)
        # executions across all attempts so the loop can surface looping
        # planners. Reset every `run()` call.
        self._step_repetition = StepRepetitionTracker()
        # Per-run termination guard (MAST FM-1.5, FM-3.1).
        self._termination_guard = TerminationGuard()
        # MVP-14.1 — typed Evidence chain. Built in parallel with
        # `artifacts`; lives at the same scope so the synthesizer (and,
        # later, the Verifier) can consult it.
        chain: ProvenanceChain = ProvenanceChain()
        planner_out: PlannerOutput | None = None
        plan: Plan | None = None
        replan_exhausted = False
        # S2 shadow: set when stagnation is detected, read at the end of the run
        # to report what an early stop would have cost. Never stops anything.
        _stagnation_shadow: dict[str, Any] | None = None
        # S5 shadow: every disagreement seen this run, for the same purpose.
        _disagreement_shadow: list[dict[str, Any]] = []
        # Cheap-path cost gate: set True only when the planner-skip branch
        # below fires for a trivial no-tool turn. Downstream this trims the
        # synthesizer context, forces the LIGHT (cheap) model tier and skips
        # the per-turn knowledge pipeline + memory consolidation — none of
        # which add value for a one-line greeting / config-flag echo.
        cheap_path_active = False
        # MVP-12: advice + forbidden-actions list carried over from the
        # previous attempt's policy.decide() call. Empty on the first
        # attempt; populated on every replan.
        advice_for_planner: str = ""
        forbidden_actions: tuple[tuple[str, str], ...] = ()

        attempt = 0
        # Кусок 10 разбора `_run_inner`: цикл попыток живёт в
        # `core/loop_attempt.py`. Он держится за 22 run-локали, поэтому уехал
        # не списком параметров, а с явно названным состоянием прогона —
        # `AttemptState` перечисляет то, что раньше существовало только
        # россыпью локальных имён. Подстановка `имя -> st.имя` механическая и
        # сверяется с историей в `tests/test_loop_attempt_split.py`.
        _attempt_state = AttemptState(
            user_question=user_question,
            file_hint=file_hint,
            goal=goal,
            planner_history=planner_history,
            failure_history=failure_history,
            local_critique_active=local_critique_active,
            forced_sources=forced_sources,
            forced_reasoning=forced_reasoning,
            forced_warnings=forced_warnings,
            _task_planner_llm=_task_planner_llm,
            _run_assumptions=_run_assumptions,
            _cp=_cp,
            advice_for_planner=advice_for_planner,
            forbidden_actions=forbidden_actions,
            attempt=attempt,
            artifacts=artifacts,
            chain=chain,
            planner_out=planner_out,
            plan=plan,
            replan_exhausted=replan_exhausted,
            cheap_path_active=cheap_path_active,
            _stagnation_shadow=_stagnation_shadow,
        )
        self._run_attempt_loop(_attempt_state)
        # Распаковываем обратно в локали: остальная часть `_run_inner` (и
        # восемь уже вынесенных кусков) работает с ними по именам, и трогать
        # её ради этого переноса нечего.
        artifacts = _attempt_state.artifacts
        chain = _attempt_state.chain
        planner_out = _attempt_state.planner_out
        plan = _attempt_state.plan
        attempt = _attempt_state.attempt
        replan_exhausted = _attempt_state.replan_exhausted
        cheap_path_active = _attempt_state.cheap_path_active
        _stagnation_shadow = _attempt_state._stagnation_shadow

        # planner_out and plan are guaranteed set here (the for loop ran at
        # least once because max_replan_attempts >= 1 is enforced in __init__).
        assert planner_out is not None and plan is not None

        # MVP-14.1 — fold memory & user-directive evidence into the chain.
        # The tool-level evidence was added per step inside the attempt
        # loop; persistent memory and explicit-consent inputs come from
        # different code paths, so we surface them HERE so the Verifier
        # sees a single uniform chain.
        # Кусок 4 разбора `_run_inner`: сама досборка живёт в
        # `core/loop_evidence_chain.py`; цепочка меняется на месте.
        self._fold_evidence_chain(chain, persistent_block=persistent_block)

        # Кусок 8 разбора `_run_inner`: сенсор, ранжирование и каталогизация
        # живут в `core/loop_evidence_chain.py`. Первое значение — теневой
        # вердикт, он едет в событие ниже, а не решает что-либо здесь.
        (
            _premature_keyword_fired,
            source_ranking,
            source_registry,
        ) = self._rank_and_catalog_evidence(
            chain,
            user_question=user_question,
            artifacts=artifacts,
            cheap_path_active=cheap_path_active,
            may_knowledge=may_knowledge,
            may_source_registry=may_source_registry,
        )

        # 7. Respond. When replan exhausted the synthesizer still produces
        # a structured Output Contract reply — it gets the failure history
        # and is told to explain honestly what was tried and why nothing
        # worked. This is a much better UX than a bare error string.
        # Layer 5 — expose current-run assumptions to _synthesize via instance.
        self._run_assumptions_current = _run_assumptions
        # Cheap path: force the LIGHT (cheap/fast) synthesizer tier even when
        # the complexity heuristic would return STANDARD (e.g. a config-flag
        # echo carries no LIGHT signal), and trim the prompt to just the
        # essentials — a one-line greeting/flag never needs long-term memory.
        # Кусок 13 разбора `_run_inner`: лестница синтеза живёт в
        # `core/loop_synthesis.py`, рядом с самим синтезатором. 14 run-локалей,
        # поэтому — явное состояние, как у цикла попыток.
        _synth_state = SynthesisState(
            goal=goal,
            user_question=user_question,
            file_hint=file_hint,
            artifacts=artifacts,
            planner_out=planner_out,
            plan=plan,
            history=history,
            persistent_block=persistent_block,
            failure_history=failure_history,
            replan_exhausted=replan_exhausted,
            cheap_path_active=cheap_path_active,
            local_critique_active=local_critique_active,
            _task_synth_llm=_task_synth_llm,
            _cp=_cp,
        )
        self._run_synthesizer_ladder(_synth_state)
        draft_answer = _synth_state.draft_answer
        _declared = _synth_state._declared

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
        # Кусок 11 разбора `_run_inner`: проверка и перепланирование по
        # неразрешённым цитатам живут в `core/loop_verify_replan.py`. Участок
        # держится за 21 run-локаль (перепланирование перезапускает попытку
        # целиком), поэтому уехал под явным состоянием, как цикл попыток.
        _verify_state = VerifyState(
            draft_answer=draft_answer,
            user_question=user_question,
            file_hint=file_hint,
            goal=goal,
            chain=chain,
            artifacts=artifacts,
            attempt=attempt,
            plan=plan,
            planner_history=planner_history,
            failure_history=failure_history,
            source_ranking=source_ranking,
            source_registry=source_registry,
            may_knowledge=may_knowledge,
            may_source_registry=may_source_registry,
            _task_planner_llm=_task_planner_llm,
            _disagreement_shadow=_disagreement_shadow,
            _cp=_cp,
            planner_out=planner_out,
            replan_exhausted=replan_exhausted,
        )
        self._verify_and_settle_answer(_verify_state)
        answer = _verify_state.answer
        planner_out = _verify_state.planner_out
        replan_exhausted = _verify_state.replan_exhausted
        verifier_failure = _verify_state.verifier_failure

        # From here the response is a DRAFT, not a string. The deciders either
        # rewrite the claims (`set_body`) or attach something about them
        # (`add_notice`); composition happens once, at `render()` below. Before
        # this, everything wrote to one variable and the last writer won — which
        # is how a truncation could delete the clarifying questions the loop had
        # just decided to ask (measured; see core/response_draft.py).
        # Кусок 1 разбора `_run_inner`: сами решатели живут в
        # `core/loop_response_deciders.py`, точка арбитража осталась здесь.
        draft = self._build_response_draft(
            answer,
            user_question=user_question,
            artifacts=artifacts,
            replan_exhausted=replan_exhausted,
            local_critique_active=local_critique_active,
            verifier_failure=verifier_failure,
        )

        # ── Compose ─────────────────────────────────────────────────────────
        # The single arbitration point: claims and notices are joined here and
        # nowhere else, so no decider can silently outrank another by running
        # later. The journal carries the ledger, including anything that failed
        # to survive — a contribution that goes missing is now visible instead
        # of having to be found by reading the code.
        answer = draft.render()
        self.log.log("response_composed", draft.to_log_payload(answer))

        # Strip internal verification markers before user-facing output.
        # Must happen AFTER output_policy which needs [verified:...] markers.
        answer = _strip_verification_markers(answer)

        # Кусок 16 разбора `_run_inner`: обязательства завершения живут в
        # `core/loop_run_tail.py`. Наблюдательно — вердикт в журнал, ход не меняется.
        self._check_completion_obligations(
            answer,
            user_question=user_question,
            file_hint=file_hint,
            artifacts=artifacts,
            chain=chain,
            plan=plan,
            failure_history=failure_history,
            completion_contract=completion_contract,
            _premature_keyword_fired=_premature_keyword_fired,
        )

        # Defence-in-depth: redact once more on the way out so even an
        # LLM hallucinating a credential or PII cannot bypass the kernel.
        # Кусок 7 разбора `_run_inner`: всё между готовым ответом и записью
        # эпизода живёт в `core/loop_run_tail.py`. Ответ возвращается, а не
        # меняется на месте: в эпизод обязан уехать тот же текст, что и
        # пользователю, — то есть уже отредактированный.
        answer, verification, weak_chunks = self._finalize_run_tail(
            answer,
            user_question=user_question,
            artifacts=artifacts,
            planner_out=planner_out,
            replan_exhausted=replan_exhausted,
            may_profile=may_profile,
            may_assumptions=may_assumptions,
            _run_assumptions=_run_assumptions,
            _stagnation_shadow=_stagnation_shadow,
            _disagreement_shadow=_disagreement_shadow,
            _cp=_cp,
        )

        # ── Bank the episode LAST ────────────────────────────────────────────
        # A `success` outcome may only be recorded once the run has actually
        # finished. Writing it earlier leaves a window where a later failure
        # would abort the run with a success already banked — and idempotency
        # (keyed on run_id) would then refuse to correct it.
        #
        # No outer permission gate here: episode, procedure and consolidation
        # are three separate sinks, and `_record_experience_memory` resolves
        # each one. Gating the whole call would make "bank an episode but
        # promote no procedure" unreachable.
        self._record_experience_memory(
            goal_description=goal.description,
            question=user_question,
            answer=answer,
            tools_used=[s["tool"] for s in planner_out.sources],
            source_labels=list(artifacts.keys()) or ["general-knowledge"],
            verified_chunks=verification.verified_chunks if verification else 0,
            unverified_chunks=verification.unverified_chunks if verification else 0,
            weak_chunks=weak_chunks,
            replan_exhausted=replan_exhausted,
            skip_consolidation=cheap_path_active,
            # Set by either soft-fail site (`:1625` initial, `:1929` replan).
            # Both write this same local, which is why one flag covers them.
            verifier_failure=verifier_failure,
            # Run-local: the verdict of the synthesis attempt that produced
            # THIS answer, or None when the ladder degraded.
            declared_completion=_declared["value"],
        )

        # Clear streaming callback so it cannot leak into the next turn.
        self._stream_on_token = None
        self.last_replan_exhausted = bool(replan_exhausted)
        return answer

    # ---------- persistent memory facade ----------


    # ------------------------------------------------------------------
    # MVP-11 Compensation surface (rollback / undo)
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # MVP-10 Memory Hygiene surface
    # ------------------------------------------------------------------
    # Every hygiene operation is a discrete, deliberate call. The CLI
    # exposes them via `:hygiene <subcmd>`. Each method logs ONE event
    # carrying the typed report's `summary()` so audits show exactly
    # what was removed and why.


    # ---------- replan helpers ----------

    # ---------- phase implementations ----------




    def _file_read_workspace_root(self) -> Path | None:
        try:
            tool = self.registry.get("file_read")
        except KeyError:
            return None
        root = getattr(tool, "workspace_root", None)
        if root is None:
            return None
        return Path(root).resolve()









