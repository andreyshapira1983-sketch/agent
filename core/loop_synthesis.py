"""Синтез ответа — метод `_synthesize`, вырезанный из ``core/loop.py`` дословно.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Третий кусок раскола `core/loop.py` (после `loop_step_execution` и
`loop_response_deciders`).

Здесь собирается ПРОМПТ синтезатора и делается сам вызов: блоки контекста
(история, долгая память, профиль, допущения, роль), бюджет промпта с
пересборкой урезанной памяти, дешёвый путь и локальная критика, разрешённые
цитаты, `<host_environment>` как справка-не-улика (LPF-001) и лестница
устойчивого синтеза. Это единственная фаза цикла, целиком укладывающаяся в
один метод, — поэтому она переезжает целиком, без протягивания run-локалей
через границу.

Вторым методом сюда уехал ВЫЗОВ синтезатора — лестница устойчивости
(`run_synthesizer_ladder`): выбор дешёвого яруса модели, одноразовый nonce на
КАЖДУЮ попытку, разбор маркера завершения и пауза по исчерпанному бюджету.
Он держится за 14 run-локалей, поэтому уехал под явным состоянием
(`SynthesisState`), как цикл попыток, — подстановка `имя -> st.имя` объявлена
и сверяется с историей.

Nonce именно на попытку, а не на прогон: маркер, скопированный из отброшенной
попытки, не должен пройти проверку у той, что реально записывается (MIR-057).

Тело перенесено символ в символ, что пинится AST-сверкой с историей в
`tests/test_loop_synthesis_split.py`.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from core.answer_format import (
    LOCAL_CRITIQUE_SYSTEM_ADDENDUM,
    SYSTEM_ANSWER,
    file_scope_notice,
    format_allowed_citations_block,
    format_artifact,
    output_contract_requires_headers,
)
from core.completion_marker import (
    marker_instruction as completion_marker_instruction,
)
from core.completion_marker import new_nonce as new_completion_nonce
from core.completion_marker import parse_completion_marker
from core.model_router import ModelRole
from core.model_usage import ModelBudgetExceeded
from core.models import Goal
from core.planner import PlannerOutput
from core.redaction import redact_dlp_text
from core.referent_resolver import (
    ReferentDecision,
    citation_token_for_referent,
    is_show_only_directive,
)
from core.replan import ReplanTrigger
from core.smart_memory import _COMPLETION_DECLARATIONS
from core.synth_resilience import (
    SynthAttempt,
    build_degraded_synthesis_answer,
    run_synthesizer_ladder,
)
from core.user_profile import profile_to_prompt_block


@dataclass
class SynthesisState:
    """То, что вызов синтезатора носит с собой за один прогон.

    Имена полей совпадают с прежними локальными именами `_run_inner` — это
    условие проверяемости переноса: подстановка `имя -> st.имя` механическая,
    и тест сверяет её с историей.
    """

    # ── Вход ─────────────────────────────────────────────────────────────
    goal: Goal
    user_question: str
    file_hint: str | None
    artifacts: dict[str, dict[str, Any]]
    planner_out: PlannerOutput
    plan: Any
    history: str
    persistent_block: str
    failure_history: list[Any]
    replan_exhausted: bool
    cheap_path_active: bool
    local_critique_active: bool
    _task_synth_llm: Any
    _cp: Any

    # ── Выход ────────────────────────────────────────────────────────────
    draft_answer: str = ""
    #: Изменяемая ячейка, а не поле-строка: её пишет ЗАМЫКАНИЕ внутри
    #: лестницы, и на каждой попытке заново. Прогонная, не на экземпляре:
    #: `self._last_*` пережил бы прогон, а ранние выходы (реплей, отказ)
    #: банкуют, сюда не заходя, — вердикт одного хода приписался бы эпизоду
    #: следующего.
    _declared: dict[str, str | None] | None = None

    OUTPUTS: ClassVar[frozenset[str]] = frozenset({"draft_answer", "_declared"})


class AgentLoopSynthesis:
    """Фаза «Ответ»: сборка промпта синтезатора и вызов модели.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        llm: Any
        last_provenance: Any
        last_role_context: Any
        last_user_profile: Any
        memory_record_lines: Any
        _synthesis_expects_contract_headers: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _cycle_findings: Any
        _save_budget_pause_checkpoint: Any
        last_referent_decision: Any
        model_router: Any

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
        llm=None,
        lean_context: bool = False,
        local_critique: ReferentDecision | None = None,
        completion_nonce: str = "",
    ) -> str:
        history_block = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            if history.strip()
            else ""
        )
        # Cheap path: drop the heavy, question-irrelevant injections
        # (long-term memory, user profile, run assumptions). A trivial
        # greeting / config-flag echo is answered from general knowledge;
        # these blocks only inflate the prompt token count.
        # Local critique: keep profile (language/verbosity) but drop LTM,
        # assumptions, role, and conversation_history — target is explicit.
        if lean_context or local_critique is not None:
            long_term_block = ""
            assumptions_block = ""
            role_block = ""
            if local_critique is not None:
                history_block = ""
                profile_block = (
                    profile_to_prompt_block(self.last_user_profile) + "\n\n"
                    if self.last_user_profile is not None
                    else ""
                )
            else:
                profile_block = ""
        else:
            long_term_block = (
                f"{persistent_block}\n\n" if persistent_block.strip() else ""
            )
            role_block = self.last_role_context.to_prompt_block() + "\n\n"
            profile_block = (
                profile_to_prompt_block(self.last_user_profile) + "\n\n"
                if self.last_user_profile is not None
                else ""
            )
            # Layer 5 — inject active assumptions into synthesizer.
            _assumptions_src = getattr(self, "_run_assumptions_current", None)
            assumptions_block = (
                _assumptions_src.to_prompt_block() + "\n\n"
                if _assumptions_src is not None and len(_assumptions_src) > 0
                else ""
            )

        # Kernel-built safety notes — the LLM is told to surface these in
        # the user-facing answer. The notes describe what was redacted
        # (the kernel did it), not what the LLM did.
        safety_block = ""
        if cycle_findings:
            lines = ["<safety_notes>"]
            lines.extend(
                f"- label={f['label']} kinds={f['kinds']} "
                f"count={f['count']} (kernel-redacted)"
                for f in cycle_findings
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
                "Re-planning was exhausted after every attempt failed."
            )
            for trig in failure_history:
                lines.append(
                    f"- attempt={trig.attempt} code={trig.code} "
                    f"tool={trig.tool_name or '(none)'}: {trig.reason}"
                )
            lines.append("</failure_context>")
            failure_block = "\n".join(lines) + "\n\n"

        # The question travels into the LLM prompt; redact any credential
        # or sensitive PII the user pasted in.
        safe_question, _q_findings, _q_pii_findings = redact_dlp_text(question)

        # Read the active synthesis contract from the prompt registry so an
        # env/registry override (e.g. a task-specific table-only contract)
        # actually takes effect here instead of being silently ignored.
        try:
            from core.prompt_registry import get_prompt as _get_prompt
            system_prompt = _get_prompt("synthesizer.system")
        except Exception:  # реестр промптов недоступен — берём встроенный контракт синтеза
            system_prompt = SYSTEM_ANSWER
        if completion_nonce:
            # Appended to the system prompt rather than to one of the three
            # user-prompt branches, so every synthesis shape carries it.
            system_prompt = system_prompt + completion_marker_instruction(
                completion_nonce, _COMPLETION_DECLARATIONS
            )
        if local_critique is not None and not artifacts:
            cite = citation_token_for_referent(local_critique)
            raw_target = (local_critique.analysis_target_excerpt or "").strip()
            # Cap oversized targets; keep the turn bounded.
            _max_target = 12_000
            truncated = False
            if len(raw_target) > _max_target:
                raw_target = raw_target[:_max_target]
                truncated = True
            safe_target, _, _ = redact_dlp_text(raw_target)
            safe_target = (
                safe_target.replace("</analysis_target>", "")
                .replace("<analysis_target", "&lt;analysis_target")
            )
            directive_raw = (local_critique.directive_excerpt or question).strip()
            safe_directive, _, _ = redact_dlp_text(directive_raw)
            safe_directive = safe_directive.replace("</directive>", "")
            show_only = is_show_only_directive(directive_raw)
            trunc_note = (
                "\n[note] analysis_target truncated for length.\n"
                if truncated
                else ""
            )
            show_only_line = (
                "Show-only: do not offer further actions or help.\n"
                if show_only
                else ""
            )
            user_prompt = (
                f"{safety_block}"
                f"{failure_block}"
                f"{profile_block}"
                f"<directive>\n{safe_directive}\n</directive>\n\n"
                f'<analysis_target untrusted="true">\n'
                f"{safe_target}{trunc_note}"
                f"</analysis_target>\n\n"
                f"<allowed_target_citation>{cite}</allowed_target_citation>\n\n"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"{show_only_line}"
                "Answer using the Output Contract. Critique only the "
                "analysis_target. Cite target-descriptive claims with the "
                "allowed_target_citation token. Do not use [general-knowledge] "
                "or [memory:*] for this turn. Do not claim the object is missing."
            )
            system_prompt = system_prompt + "\n" + LOCAL_CRITIQUE_SYSTEM_ADDENDUM
        elif artifacts:
            from core.evidence_budget import (
                MEMORY_BLOCK_LABEL,
                MEMORY_OPEN_TAG,
                apply_total_budget,
                rebuild_trimmed_memory,
                total_trims,
            )
            raw_blocks: list[tuple[str, str]] = []
            for label, art in artifacts.items():
                formatted = format_artifact(
                    art["tool"], art["output"], question=question
                )
                raw_blocks.append((label, formatted))

            # Long-term memory competes for the SAME budget as the evidence
            # collected this cycle. It used to be concatenated into the prompt
            # outside `apply_total_budget` entirely, which made recollection
            # structurally untrimmable while the freshly read file — almost
            # always the largest block — was cut first. Observed consequence:
            # a months-old "Bug fixed …" record survived the trim that removed
            # the code proving it, and the agent reported a fixed bug as
            # current. Memory is demoted (`trim_first`): it is spent before any
            # fresh evidence is touched, whatever the relative sizes.
            memory_label = MEMORY_BLOCK_LABEL
            while memory_label in artifacts:      # never shadow a real artifact
                memory_label += "_"
            memory_payload = long_term_block.strip()
            if memory_payload:
                raw_blocks.append((memory_label, memory_payload))

            # Below one whole record memory rebuilds into nothing, so a stub
            # keeps chars and no citable id (measured live 2026-08-04).
            _lines = self.memory_record_lines(getattr(self, "_last_persistent_records", []))
            trimmed_blocks, was_trimmed = apply_total_budget(
                raw_blocks, trim_first_labels={memory_label},
                min_useful=({memory_label: len(f"{MEMORY_OPEN_TAG}\n{_lines[0]}")}
                            if _lines else None),
            )
            memory_trimmed = False
            # None = memory was not trimmed, so every retrieved record is still
            # in the prompt and citable. A set = only these survived.
            surviving_memory_ids: set[str] | None = None
            evidence_pairs: list[tuple[str, str]] = []
            for lbl, content in trimmed_blocks:
                if lbl != memory_label:
                    evidence_pairs.append((lbl, content))
                    continue
                memory_trimmed = content != memory_payload
                memory_block = content
                if memory_trimmed:
                    _records = getattr(self, "_last_persistent_records", [])
                    memory_block, surviving_memory_ids = rebuild_trimmed_memory(
                        content,
                        memory_payload,
                        list(
                            zip(
                                [rec.id for rec in _records],
                                self.memory_record_lines(_records),
                                strict=True,
                            )
                        ),
                    )
                long_term_block = f"{memory_block}\n\n" if memory_block else ""

            if was_trimmed:
                # Parsed once; feeds both the trim event and the starvation
                # detector below (review round #286).
                _trims = total_trims(trimmed_blocks)
                self.log.log(
                    "evidence_budget_trim",
                    {
                        "labels": [lbl for lbl, _ in trimmed_blocks],
                        # NOTE: since memory joined the budget this total
                        # includes the memory block — not comparable with
                        # totals logged before that change.
                        "total_chars": sum(len(c) for _, c in trimmed_blocks),
                        # Which side paid, and whether memory existed at all —
                        # `memory_trimmed: False` alone cannot say that.
                        "memory_trimmed": memory_trimmed,
                        "memory_chars": len(memory_payload),
                        # What actually reached the model: `persistent_memory_inject`
                        # fires BEFORE the budget and counts records the model
                        # may never have seen.
                        "memory_chars_kept": len(long_term_block.strip()),
                        "memory_ids_kept": sorted(surviving_memory_ids)
                        if surviving_memory_ids is not None
                        else None,
                        # Per-block cut sizes, parsed back from the trim
                        # notices — without them a starved block is invisible
                        # in the trace (MIR-073).
                        "trims": [
                            {"label": lbl, "kept": kept, "original": orig}
                            for lbl, kept, orig in _trims
                        ],
                    },
                )
                # MIR-073: the planner chose these sources; if the budget
                # squeezed one to a sliver, that is two deciders contradicting
                # each other — journal it on the existing disagreement channel
                # instead of continuing as if nothing happened. Logging only,
                # per the operator's sensor policy.
                try:
                    from core.subsystem_disagreement import (
                        detect_budget_starvation,
                    )
                    for _ev in detect_budget_starvation(
                        _trims,
                        planned_labels=set(artifacts.keys()),
                        memory_label=memory_label,
                    ):
                        self.log.log("subsystem_disagreement", _ev)
                except Exception as _sd_exc:
                    # A broken detector must not break the turn — but its
                    # failure must not be invisible either (review round
                    # #286, same rule as verification_explained_failed).
                    try:
                        self.log.log(
                            "subsystem_disagreement_error",
                            {
                                "error_type": type(_sd_exc).__name__,
                                "error": str(_sd_exc)[:300],
                            },
                        )
                    except Exception:
                        pass

            blocks: list[str] = [
                f'<evidence source="{lbl}">\n{content}\n</evidence>'
                for lbl, content in evidence_pairs
            ]
            evidence = "\n\n".join(blocks)
            # A record the trim removed must not stay on the citable list: the
            # synthesizer would cite text it never saw and the verifier would
            # book it as cited-but-unmatched.
            allowed_citations_block = format_allowed_citations_block(
                self.last_provenance, memory_ids=surviving_memory_ids
            )
            scope_notice = file_scope_notice(question, artifacts)
            file_scope_block = (
                "<file_scope_notice>\n"
                f"{scope_notice}\n"
                "Do not claim any unverified path exists or was read.\n"
                "</file_scope_notice>\n\n"
                if scope_notice
                else ""
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
                f"{profile_block}"
                f"{assumptions_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"{allowed_citations_block}"
                f"{file_scope_block}"
                f"{evidence}\n\n"
                f"{warnings_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "Answer using the Output Contract from the system instructions. "
                "Maintain continuity with any prior turns shown in conversation_history."
                # Only offered when the block is actually in the prompt: the
                # budget can drop it entirely, and describing how to cite an
                # absent block is an invitation to cite nothing.
                + (
                    " If long_term_memory contains a relevant record you may "
                    "cite it with source label [memory:<record_id>]."
                    if long_term_block.strip()
                    else ""
                )
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
                f"{profile_block}"
                f"{assumptions_block}"
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

        # Record whether the active contract is the generic Conclusion/Facts
        # prose contract. When a task-specific/structured contract replaced it,
        # the verifier must not flag the answer as malformed for lacking the
        # generic headers (output-contract priority).
        self._synthesis_expects_contract_headers = output_contract_requires_headers(
            system_prompt
        )

        # Defence-in-depth: the prompt is now built from redacted artifacts
        # and a redacted question. One more pass catches anything we missed
        # (e.g. a secret or PII hiding in planner_reasoning).
        safe_user_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        from core.host_tools_context import _build_host_tools_block, host_tools_relevant
        # Local critique must not pull host_tools context. Otherwise inject only
        # when the turn is actually about host tools (name / task word / an
        # effect tool was used) — so .env paths don't ride along on unrelated
        # questions (LPF-001 iteration 1b).
        _tools_used = [a.get("tool") for a in artifacts.values() if isinstance(a, dict)]
        if local_critique is None and host_tools_relevant(
            f"{question}\n{planner_reasoning}", _tools_used
        ):
            host_block = _build_host_tools_block()
            # Reference context ONLY — NOT evidence. Wrapping host tools as an
            # <evidence> block used to flip the synthesizer into "answer STRICTLY
            # from evidence" mode (disabling the general-knowledge path) and
            # produced fabricated [tool:host_tools] citations the verifier could
            # never match (LPF-001). A <host_environment> block is reference-only,
            # must not be cited, and never switches off general-knowledge answering.
            if host_block:
                host_context = (
                    "\n\n<host_environment>\n"
                    + host_block.strip()
                    + "\nThis lists programs installed on the machine. It is reference "
                    "context, NOT a source: never cite it (no [tool:...] or [source] "
                    "label), and its presence does not make an answer 'grounded'. "
                    "When you actually write a script for one of these tools, state the "
                    "EXACT run command from above in the Facts section."
                    + "\n</host_environment>"
                )
                safe_user_prompt = safe_user_prompt + host_context
        _on_token = getattr(self, "_stream_on_token", None)
        if _on_token is not None:
            return _active_llm.stream_complete(
                system=system_prompt, user=safe_user_prompt,
                temperature=0.5, on_token=_on_token,
            )
        return _active_llm.complete(
            system=system_prompt, user=safe_user_prompt, temperature=0.5
        )

    def _run_synthesizer_ladder(self, st: SynthesisState) -> None:
        """Довести черновик ответа, переживая сбои синтезатора.

        Ничего не возвращает: черновик и вердикт о завершении лежат в `st`.
        `ModelBudgetExceeded` проходит наружу — это не сбой синтеза, а конец
        бюджета, и ход обязан встать с сохранённой точкой возврата.
        """
        _synth_llm = st._task_synth_llm
        if st.cheap_path_active:
            try:
                from core.task_complexity import ComplexityTier
                _synth_llm = self.model_router.for_task(
                    ModelRole.SYNTHESIZER,
                    st.user_question,
                    force_tier=ComplexityTier.LIGHT,
                )
                self.log.log(
                    "cheap_path_synth_model",
                    {"model": getattr(_synth_llm, "model", None)},
                )
            except Exception:  # выбор дешёвой модели не удался — работаем на обычной, это не сбой хода
                _synth_llm = st._task_synth_llm
        _saved_on_token = getattr(self, "_stream_on_token", None)

        # Run-local, deliberately NOT an instance attribute. A `self._last_*`
        # field survives the run that set it, and the early-return paths
        # (replay, refusal) bank without ever entering this block — so a
        # declaration from one run would be attributed to the next run's
        # episode. Nothing here outlives the closure.
        st._declared: dict[str, str | None] = {"value": None}

        def _do_synthesize(_attempt: SynthAttempt) -> str:
            # Retries must not double-stream tokens: only the first attempt may
            # stream to the console; adapted/retry attempts render silently and
            # the final answer is returned normally.
            if _attempt.index > 0:
                self._stream_on_token = None
            # Cleared BEFORE the call that can raise: an attempt that dies
            # part-way must not leave the previous attempt's verdict standing.
            st._declared["value"] = None
            # One nonce per ATTEMPT, not per run: a marker copied out of an
            # attempt that was thrown away must not validate against the one
            # that is actually banked (MIR-057).
            _nonce = new_completion_nonce()
            _raw = self._synthesize(
                completion_nonce=_nonce,
                goal=st.goal,
                artifacts=st.artifacts,
                question=st.user_question,
                planner_reasoning=st.planner_out.reasoning,
                history=st.history,
                persistent_block=st.persistent_block,
                cycle_findings=list(self._cycle_findings),
                failure_history=st.failure_history if st.replan_exhausted else None,
                llm=_synth_llm,
                # Shrink the prompt/output on the adapted attempt — this is the
                # recovery for a request the model "could not finish".
                lean_context=st.cheap_path_active or _attempt.adapt_context,
                local_critique=(
                    self.last_referent_decision
                    if st.local_critique_active
                    else None
                ),
            )
            # Strip here, once. Everything downstream — the verifier, the
            # user's answer and the stored `full_answer` — is derived from
            # this return value, so one removal keeps all three identical and
            # the nonce reaches none of them.
            _parsed = parse_completion_marker(
                _raw, nonce=_nonce, valid_tokens=_COMPLETION_DECLARATIONS
            )
            st._declared["value"] = _parsed.declared
            self.log.log(
                "completion_declaration",
                {
                    # The nonce is a secret of the attempt and is never logged:
                    # a log that carries it would hand forgery back to anyone
                    # who can read logs.
                    "attempt": _attempt.index,
                    "parse": _parsed.status,
                    "declared": _parsed.declared,
                    **({"detail": _parsed.detail} if _parsed.detail else {}),
                },
            )
            return _parsed.text

        try:
            _ladder = run_synthesizer_ladder(
                _do_synthesize,
                build_degraded_answer=build_degraded_synthesis_answer,
                on_event=self.log.log,
                fatal_types=(ModelBudgetExceeded,),
            )
            st.draft_answer = _ladder.answer
            self._last_synth_degraded = _ladder.degraded
            if _ladder.degraded:
                # The answer the user gets was assembled by the fallback, not
                # by the attempt that declared. Keeping that declaration would
                # attribute a verdict to text its author never wrote.
                st._declared["value"] = None
        except ModelBudgetExceeded as exc:
            self._save_budget_pause_checkpoint(
                st._cp,
                goal=st.goal,
                question=st.user_question,
                file_hint=st.file_hint,
                current_phase="synthesis",
                plan=st.plan,
                blocked=exc,
            )
            raise
        finally:
            self._stream_on_token = _saved_on_token
