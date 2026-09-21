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

from typing import TYPE_CHECKING, Any

# Re-export moved helpers/state to keep the public API exactly as before.
from .loop_synthesis_helpers import _artifact_blocks, _log_budget_trim, _organ_map
from .loop_synthesis_state import SynthesisState

if TYPE_CHECKING:  # pragma: no cover

    from core.models import Goal
    from core.referent_resolver import ReferentDecision
    from core.synth_resilience import SynthAttempt


from core.answer_format import (
    LOCAL_CRITIQUE_SYSTEM_ADDENDUM,
    SYSTEM_ANSWER,
    file_scope_notice,
    format_allowed_citations_block,
    output_contract_requires_headers,
)
from core.completion_marker import marker_instruction as completion_marker_instruction
from core.completion_marker import new_nonce as new_completion_nonce
from core.completion_marker import parse_completion_marker
from core.model_router import ModelRole
from core.model_usage import ModelBudgetExceeded
from core.models import Goal
from core.redaction import redact_dlp_text
from core.referent_resolver import (
    ReferentDecision,
    citation_token_for_referent,
    is_show_only_directive,
)
from core.replan import ReplanTrigger, world_facing_failures
from core.runtime_self import runtime_self_block
from core.smart_memory import _COMPLETION_DECLARATIONS
from core.synth_resilience import (
    SynthAttempt,
    build_degraded_synthesis_answer,
    run_synthesizer_ladder,
)
from core.user_profile import profile_to_prompt_block

#: Ниже этого совпадения с вопросом черновик считается «не про то». Живые
#: разговоры 2026-09-20: 0.20 и 0.22 у двух ответов, каждый из которых отвечал
#: на свой вопрос, а не на заданный.
_OFF_TOPIC_RELEVANCE = 0.35


class AgentLoopSynthesis:
    """Фаза «Ответ»: сборка промпта синтезатора и вызов модели.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        # Список инструментов для блока «о себе» (рабочий экзамен 2026-09-19):
        # зарегистрированные минус закрытые политикой.
        registry: Any
        policy: Any
        llm: Any
        last_provenance: Any
        last_role_context: Any
        last_user_profile: Any
        # Читается только ради `session_id` в блоке фактов о рантайме: агент
        # обязан уметь назвать свой сеанс, а не выводить его из подсказки.
        memory: Any
        memory_record_lines: Any
        _synthesis_expects_contract_headers: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _sensor_failed: Any
        _cycle_findings: Any
        _save_budget_pause_checkpoint: Any
        last_referent_decision: Any
        model_router: Any
        # Свои открытые записи о промахах: заводит чтение контекста хода
        # (`core/loop_context.py`), синтез ставит их рядом с <failure_context>.
        _self_defects_block: Any

    def _resolve_synthesis_contract(self) -> str:
        """The active output contract, and a fallback that admits itself.

        Read from the prompt registry so an env/registry override actually takes
        effect instead of being silently ignored — which is exactly what the old
        handler here did. Measured (census A4): the built-in `SYSTEM_ANSWER`
        requires section headers and a task-specific contract need not, so on a
        registry failure `_synthesis_expects_contract_headers` reads True where
        it should read False, and the verifier then marks the answer
        `malformed_output` for headers that contract never asked for. A wrong
        verdict about the ANSWER, caused by a swallowed error about the PROMPT.

        The fallback stays: a missing registry is no reason to fail a turn. Its
        silence does not.
        """
        try:
            from core.prompt_registry import get_prompt as _get_prompt
            return _get_prompt("synthesizer.system")
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # Reported through `_sensor_failed`, which journals it — the audit
            # in `scripts/except_audit.py` looks for a literal `.log(` call and
            # does not recognise the layer's own reporting helper, so the
            # comment carries the justification it asks for. Teaching the audit
            # about `_sensor_failed` belongs to queue item A7, not here.
            self._sensor_failed("synthesis_contract_registry", exc)
            return SYSTEM_ANSWER

    def _runtime_self_prompt_block(self) -> str:
        """Блок фактов о себе, или пусто — сенсор не имеет права ронять ход."""
        try:
            from core.run_context import current_run
            ctx = current_run()
            block = runtime_self_block(
                trace_id=str(getattr(self.log, "trace_id", "") or ""),
                run_id=str(getattr(ctx, "run_id", "") or ""),
                session_id=getattr(self.memory, "session_id", None),
                stores=_organ_map(self),
                # `None` едет как есть: это «все приёмники», а не «ничего».
                durable_writes=getattr(self, "durable_writes", ()),
                tools=[t.name for t in self.registry.list()
                       if t.name not in (getattr(self.policy, "blocked_tools", None) or ())],
            )
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется
            # Молча вернуть пустоту здесь — ровно тот порок, который храповик
            # молчания и сторожит: оператор, читающий журнал, не узнал бы, что
            # агент не смог назвать собственный состав.
            self._sensor_failed("runtime_self", exc)
            return ""
        return f"{block}\n\n" if block else ""

    def _self_defects_prompt(self) -> str:
        """Свои открытые записи о промахах — у того, кто пишет «не могу».

        2026-09-21: агент записал «объяснил стену вместо проверки» и повторил
        это через десять часов. Блок заводит чтение контекста хода; синтез
        зовут и мимо него, поэтому пустота — законный ответ.
        """
        block = getattr(self, "_self_defects_block", "") or ""
        return f"{block}\n\n" if block else ""

    def _synthesize(  # noqa: PLR0913, PLR0917 — the roster rides beside the memory block (exam 2026-09-04)
        self,
        goal: Goal,
        artifacts: dict[str, dict[str, Any]],
        question: str,
        planner_reasoning: str,
        history: str = "",
        persistent_block: str = "",
        spend_block: str = "",
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
            # Facts about his own models and spend travel with long-term memory:
            # the answerer must see what the planner saw (exam 2026-09-04, turn 2:
            # «the model_roster block is not in the context passed to me» — true).
            if spend_block.strip():
                long_term_block += f"{spend_block}\n\n"
            role_block = self.last_role_context.to_prompt_block() + "\n\n"
            profile_block = (
                profile_to_prompt_block(self.last_user_profile) + "\n\n"
                if self.last_user_profile is not None
                else ""
            )
            # Рядом с профилем ОПЕРАТОРА — проверяемые факты о СЕБЕ. До этого
            # синтез знал, кто спрашивает, и не знал, что подключено у него
            # самого; «кто ты» приходилось брать из строки подсказки
            # (`SYSTEM_ANSWER`), а не из измерения. Персону блок не объявляет.
            profile_block += self._runtime_self_prompt_block()
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
            # No claim about replan exhaustion: since 2026-08-14 this block also
            # carries the failures of an attempt that SUCCEEDED on another step,
            # where nothing was exhausted. Each entry states its own `attempt=N`,
            # which is the fact; the old sentence asserted a status the list no
            # longer implies.
            lines.append(
                "Steps that failed this turn. This is a FACT about the turn: "
                "say what did not work and why. It is context, NOT evidence — "
                "do not cite it as a source."
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
        system_prompt = self._resolve_synthesis_contract()
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
                f"{self._self_defects_prompt()}{failure_block}"
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
            )
            raw_blocks = _artifact_blocks(artifacts, question=question)

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

            # The one truth two consumers need: did any WHOLE record reach the
            # model. `None` means memory was never trimmed, so all of them did.
            memory_has_records = bool(long_term_block.strip()) and (
                surviving_memory_ids is None or bool(surviving_memory_ids)
            )

            _log_budget_trim(
                self.log,
                trimmed_blocks=trimmed_blocks,
                was_trimmed=was_trimmed,
                memory_trimmed=memory_trimmed,
                memory_payload=memory_payload,
                memory_label=memory_label,
                long_term_block=long_term_block,
                memory_has_records=memory_has_records,
                surviving_memory_ids=surviving_memory_ids,
                artifacts=artifacts,
            )

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
                f"{self._self_defects_prompt()}{failure_block}"
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
                # Records, not characters. Since a dropped block leaves a notice
                # saying so (MIR-092), a non-empty block no longer implies a
                # citable record — and offering the label over a drop notice is
                # the same invitation to cite nothing, one step subtler.
                + (
                    " If long_term_memory contains a relevant record you may "
                    "cite it with source label [memory:<record_id>]."
                    if memory_has_records
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
                    "Use the <failure_context> block to write an honest "
                    "Conclusion: state plainly what could not be collected, "
                    "list what was tried (one bullet per attempt), and put the "
                    "unmet information need under Unverified. Cite each fact as "
                    "[general-knowledge] when relying on prior knowledge."
                )
            # Exam 2026-09-04, turn 3: on a no-tool turn the sensor blocks
            # (model roster, spend mirror) were in the prompt but not citable —
            # fifteen facts read off them were booked «user asserted» and the
            # whole answer was suppressed. The chain already carries them as
            # `sensor:` evidence; offer the tokens here as on a tool turn.
            sensor_citations_block = format_allowed_citations_block(self.last_provenance)
            user_prompt = (
                f"{safety_block}"
                f"{self._self_defects_prompt()}{failure_block}"
                f"{role_block}"
                f"{profile_block}"
                f"{assumptions_block}"
                f"{long_term_block}"
                f"{history_block}"
                f"{sensor_citations_block}"
                f"planner_reasoning: {planner_reasoning}\n\n"
                f"Question: {safe_question}\n\n"
                "No <evidence> blocks are provided. If conversation_history or "
                "long_term_memory covers the answer, cite those sources verbatim "
                "(use [memory:<record_id>] for long_term_memory entries); a fact read "
                "from a <model_roster> or <spend_mirror> block is a measured fact about "
                "this agent — cite it [sensor:model_roster] / [sensor:spend_mirror]; "
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

    def _rewrite_if_off_topic(self, st: SynthesisState, do_synthesize: Any) -> None:
        """Один переписанный черновик, когда ответ не про заданный вопрос.

        Соответствие вопросу СЧИТАЛОСЬ (`core/confidence_vector.relevance_score`),
        печаталось человеку и клалось в эпизод — и ничем не распоряжалось.
        Живой разговор 2026-09-20: на вопрос «какое условие не выполнялось»
        агент перечислил четыре условия, нашёл ответ среди СВОИХ ЖЕ фактов
        («цель драйва идёт действием propose_engineering_task, а не
        improve_failure_to_idea_pipeline»), в выводе написал «данных нет», а
        собственная проверка отметила 0.22. Мерить и не различать — то же
        самое, что не мерить. Вторая сборка идёт по ТЕМ ЖЕ уликам, без новых
        шагов и без сети: меняется только требование отвечать на вопрос.
        Лучший из двух черновиков по тому же измерению и остаётся.
        """
        import os

        from core.confidence_vector import relevance_applicable, relevance_score
        from core.replan import ReplanTrigger
        from core.synth_resilience import SynthAttempt

        # Переключатель, по умолчанию выключённый — как `AGENT_OBSERVE_BEFORE_ANSWER`
        # у наблюдения перед ответом. Мера соответствия груба (совпадение слов), а
        # цена срабатывания — лишний вызов модели: включать это всем и всегда
        # означало бы менять поведение каждого хода ради части случаев.
        if (os.getenv("AGENT_REWRITE_OFF_TOPIC", "") or "").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        draft = st.draft_answer or ""
        question = st.user_question or ""
        if self._last_synth_degraded or not relevance_applicable(question, draft):
            return
        before = relevance_score(question, draft)
        if before >= _OFF_TOPIC_RELEVANCE:
            return
        st.failure_history.append(ReplanTrigger(
            code="answer_off_topic",
            step_id="synthesis-relevance",
            tool_name=None,
            arguments={"relevance": round(before, 3)},
            reason=(
                f"the draft answered something else: its overlap with the question is "
                f"{before:.2f}. The question was: {question.strip()[:300]}. Answer THAT "
                "question with the facts already gathered this turn — and if they do not "
                "settle it, say which fact is missing and where it would be."
            ),
            attempt=0,
        ))
        try:
            second = do_synthesize(SynthAttempt(index=1, adapt_context=False, is_final=True))
        except Exception as exc:  # noqa: BLE001 — переписывание не вправе ронять ответ
            self._sensor_failed("off_topic_rewrite", exc)
            return
        after = relevance_score(question, second or "")
        self.log.log("answer_off_topic", {
            "relevance_before": round(before, 3),
            "relevance_after": round(after, 3),
            "threshold": _OFF_TOPIC_RELEVANCE,
            "rewritten": bool(second) and after > before,
            "question_head": question.strip()[:120],
        })
        if second and after > before:
            st.draft_answer = second

    def _read_what_it_left_unverified(self, st: SynthesisState, do_synthesize: Any) -> None:
        """Файл своей папки, вынесенный в «Не подтверждено», — прочитать и ответить.

        Замер 2026-09-21: 44 из 74 блоков «Не подтверждено» были о его же
        файлах («не проверял, требует ли file_write подтверждения», когда
        tools/file_write.py рядом), а просьба «открой и проверь» в самом
        вопросе не помогла. Это дверь, не стена: файлы читаются тем же
        инструментом (только чтение, не больше трёх), ложатся в улики, и
        черновик собирается один раз заново с требованием решить эти пункты.
        """
        from core.answer_format import unverified_own_paths
        from core.evidence import evidence_from_tool_result

        try:
            tool = self.registry.get("file_read")
        except KeyError:
            self.log.log("unverified_own_files_skipped", {"reason": "no file_read tool"})
            return
        paths = unverified_own_paths(
            st.draft_answer or "", root=getattr(tool, "workspace_root", None),
            already_read=st.artifacts)
        if not paths or self._last_synth_degraded:
            return
        read: list[str] = []
        for rel in paths:
            try:
                output = tool.run(path=rel)
            except Exception as exc:  # noqa: BLE001 — непрочитанный файл остаётся непроверенным
                self._sensor_failed("unverified_self_read", exc)
                continue
            st.artifacts[f"file:{rel}"] = {"tool": "file_read", "output": output, "issues": []}
            ev = evidence_from_tool_result(tool_name="file_read", arguments={"path": rel},
                                           output=output)
            if ev is not None and getattr(self, "last_provenance", None) is not None:
                self.last_provenance.add(ev)
            read.append(rel)
        if not read:
            return
        st.failure_history.append(ReplanTrigger(
            code="unverified_own_file", step_id="synthesis-unverified", tool_name="file_read",
            arguments={"paths": read},
            reason=(
                "Your draft put under Unverified what lives in your OWN workspace: "
                + ", ".join(read) + ". Those files have now been read in full and are in "
                "your evidence. Settle those points from them and cite them; keep under "
                "Unverified only what the files really do not settle."
            ),
            attempt=0,
        ))
        try:
            second = do_synthesize(SynthAttempt(index=1, adapt_context=False, is_final=True))
        except Exception as exc:  # noqa: BLE001 — дочитывание не вправе ронять ответ
            self._sensor_failed("unverified_self_read", exc)
            return
        self.log.log("unverified_own_files_read", {"paths": read, "rewritten": bool(second)})
        if second:
            st.draft_answer = second

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
            except Exception as _tier_exc:  # noqa: BLE001 — reason stated above
                # Falling back to the normal model is correct — a tier that
                # cannot be selected is not a reason to fail the turn. Being
                # quiet about it is not: the cheap path exists to cut cost, and
                # its whole point disappears while `cheap_path_active` stays
                # True and the journal shows a cheap turn. The absent
                # `cheap_path_synth_model` event was the only difference, and
                # absence reads the same as "no cheap path was taken".
                _synth_llm = st._task_synth_llm
                self._sensor_failed("cheap_path_model_tier", _tier_exc)
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
                spend_block=st.spend_block,
                cycle_findings=list(self._cycle_findings),
                # Every failed step, not only the ones that survived to replan
                # exhaustion. Until 2026-08-14 this read `if st.replan_exhausted
                # else None`, so a plan that failed a step and still produced an
                # answer told the synthesiser nothing about the failure.
                # Measured live: `file_read README.md` raised FileNotFoundError,
                # the loop dropped it, and the agent — asked whether the file
                # exists — could only answer "cannot be determined". The tool
                # had told it. Nothing carried the answer to synthesis.
                # `<failure_context>` is not an `<evidence>` block, so this
                # gives the failure a voice without giving it citation power.
                failure_history=(
                    st.failure_history if st.replan_exhausted
                    else world_facing_failures(st.failure_history)
                ),
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
                    # Обрыв — не забывчивость. Маркер стоит в КОНЦЕ ответа, и
                    # ответ, срезанный потолком, не мог его донести. Без этого
                    # поля `parse=missing` от обрыва неотличим от `missing` от
                    # модели, которая маркер просто не написала: живой прогон
                    # 2026-09-17 дал ровно первое (выход 5 x 2048, все ноги
                    # израсходованы), а читался как второе. Клиент знал факт,
                    # строка вердикта — нет.
                    "truncated": bool(getattr(
                        _synth_llm if _synth_llm is not None else self.llm,
                        "last_answer_was_truncated",
                        False,
                    )),
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
            self._rewrite_if_off_topic(st, _do_synthesize)
            self._last_synth_degraded = _ladder.degraded
            self._read_what_it_left_unverified(st, _do_synthesize)
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
