"""Answer synthesis: the `_synthesize` method cut verbatim out of ``core/loop.py`` (third piece of the split, after `loop_step_execution` and `loop_response_deciders`).

Builds the synthesizer prompt (history, long-term memory, profile, assumptions, role), applies the prompt budget with rebuilt trimmed memory, the cheap path and local critique, allowed citations, `<host_environment>` as reference-not-evidence, and runs the resilient synthesis ladder (`run_synthesizer_ladder`: cheap tier choice, one nonce per ATTEMPT, completion-marker parsing, pause on exhausted budget) under explicit `SynthesisState`.
The body is pinned symbol for symbol by the AST check in `tests/test_loop_synthesis_split.py`. Mixed into ``AgentLoop``; state lives on the composed loop.
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
    chat_contract,
    file_scope_notice,
    format_allowed_citations_block,
    output_contract_requires_headers,
)
from core.completion_marker import marker_instruction as completion_marker_instruction
from core.completion_marker import new_nonce as new_completion_nonce
from core.completion_marker import parse_completion_marker
from core.draft_refutation import revise_refuted_draft
from core.model_router import ModelRole
from core.model_usage import ModelBudgetExceeded
from core.models import Goal
from core.redaction import redact_dlp_text
from core.referent_resolver import (
    ReferentDecision,
    citation_token_for_referent,
    is_show_only_directive,
)
from core.replan import ReplanTrigger, failures_for_synthesis
from core.root_principles import with_root_principles
from core.runtime_self import runtime_self_block
from core.smart_memory import _COMPLETION_DECLARATIONS
from core.synth_resilience import (
    SynthAttempt,
    build_degraded_synthesis_answer,
    run_synthesizer_ladder,
)
from core.user_profile import profile_to_prompt_block


def _safety_notes_block(cycle_findings: list[dict[str, Any]] | None) -> str:
    """Kernel-built safety notes the LLM must surface in the answer; they describe what the kernel redacted, not what the LLM did."""
    if not cycle_findings:
        return ""
    lines = ["<safety_notes>"]
    lines.extend(
        f"- label={f['label']} kinds={f['kinds']} "
        f"count={f['count']} (kernel-redacted)"
        for f in cycle_findings
    )
    lines.append("</safety_notes>")
    return "\n".join(lines) + "\n\n"


def _failure_context_block(failure_history: list[ReplanTrigger] | None) -> str:
    """Failure context: the cumulative trigger list, so the synthesizer can write an honest Conclusion instead of an empty answer.

    No claim about replan exhaustion: the block also carries failures of an attempt that SUCCEEDED on another step; each entry states its own `attempt=N`.
    """
    if not failure_history:
        return ""
    lines = [
        "<failure_context>",
        (
            "Steps that failed this turn. This is a FACT about the turn: "
            "say what did not work and why. It is context, NOT evidence — "
            "do not cite it as a source."
        ),
    ]
    lines.extend(
        f"- attempt={trig.attempt} code={trig.code} "
        f"tool={trig.tool_name or '(none)'}: {trig.reason}"
        for trig in failure_history
    )
    lines.append("</failure_context>")
    return "\n".join(lines) + "\n\n"


#: Below this match with the question the draft counts as "not about it".
_OFF_TOPIC_RELEVANCE = 0.35


class AgentLoopSynthesis:
    """Answer phase: builds the synthesizer prompt and calls the model.

    Members below are host-contract declarations (``AgentLoop`` creates them in ``__init__``); no assignments, so nothing is created or shadowed at run time.
    """

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        # Tool list for the "about me" block: registered minus those closed by policy.
        registry: Any
        policy: Any
        llm: Any
        last_provenance: Any
        last_role_context: Any
        last_user_profile: Any
        # Read only for `session_id` in the runtime facts block: the agent must be able to name its session.
        memory: Any
        memory_record_lines: Any
        _synthesis_expects_contract_headers: Any

        # Taken from a neighbouring mixin via MRO; the cross-module link must be written down.
        _sensor_failed: Any
        _cycle_findings: Any
        _save_budget_pause_checkpoint: Any
        last_referent_decision: Any
        model_router: Any
        # Own open records of misses: set by turn context reading (`core/loop_context.py`), placed beside <failure_context> by synthesis.
        _self_defects_block: Any

    def _resolve_synthesis_contract(self) -> str:
        """The active output contract, and a fallback that admits itself.

        Read from the prompt registry so an env/registry override takes effect. The built-in `SYSTEM_ANSWER` requires section headers and a task contract need not, so a silent registry failure would make the verifier flag `malformed_output` wrongly. The fallback stays (a missing registry must not fail a turn) but not silent.
        """
        try:
            from core.prompt_registry import get_prompt as _get_prompt
            return chat_contract(_get_prompt("synthesizer.system"))
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # Reported through `_sensor_failed`, which journals it; scripts/except_audit.py looks only for a literal `.log(` call, so this comment carries the justification.
            self._sensor_failed("synthesis_contract_registry", exc)
            return chat_contract(SYSTEM_ANSWER)

    def _runtime_self_prompt_block(self) -> str:
        """Runtime facts block, or empty: a sensor has no right to fail the turn."""
        try:
            from core.run_context import current_run
            ctx = current_run()
            block = runtime_self_block(
                trace_id=str(getattr(self.log, "trace_id", "") or ""),
                run_id=str(getattr(ctx, "run_id", "") or ""),
                session_id=getattr(self.memory, "session_id", None),
                stores=_organ_map(self),
                # `None` goes as is: it means "all sinks", not "nothing".
                durable_writes=getattr(self, "durable_writes", ()),
                tools=[t.name for t in self.registry.list()
                       if t.name not in (getattr(self.policy, "blocked_tools", None) or ())],
            )
        except Exception as exc:  # noqa: BLE001 — observing sensor: failure is journaled
            # Silent emptiness here is exactly what the silence ratchet guards: the operator reading the journal would not learn the agent could not name its own makeup.
            self._sensor_failed("runtime_self", exc)
            return ""
        return f"{block}\n\n" if block else ""

    def _self_defects_prompt(self) -> str:
        """Own open records of misses, shown to whoever writes "I cannot".

        Turn context reading creates the block; synthesis is also called without it, so empty is a legal answer.
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
        # Cheap path: drop heavy, question-irrelevant injections (long-term memory, profile, assumptions) for a trivial greeting / config echo.
        # Local critique: keep profile but drop memory, assumptions, role and conversation_history (target is explicit).
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
            # Facts about the agent's own models and spend travel with long-term memory: the answerer must see what the planner saw.
            if spend_block.strip():
                long_term_block += f"{spend_block}\n\n"
            role_block = self.last_role_context.to_prompt_block() + "\n\n"
            profile_block = (
                profile_to_prompt_block(self.last_user_profile) + "\n\n"
                if self.last_user_profile is not None
                else ""
            )
            # Next to the OPERATOR profile: checkable facts about SELF, so "who are you" comes from measurement, not from the prompt line. The block declares no persona.
            profile_block += self._runtime_self_prompt_block()
            # Layer 5: inject active assumptions into the synthesizer.
            _assumptions_src = getattr(self, "_run_assumptions_current", None)
            assumptions_block = (
                _assumptions_src.to_prompt_block() + "\n\n"
                if _assumptions_src is not None and len(_assumptions_src) > 0
                else ""
            )

        safety_block = _safety_notes_block(cycle_findings)
        failure_block = _failure_context_block(failure_history)

        # The question travels into the LLM prompt; redact credentials and PII the user pasted.
        safe_question, _q_findings, _q_pii_findings = redact_dlp_text(question)

        # Read the active contract from the prompt registry so an env/registry override takes effect.
        system_prompt = with_root_principles(self._resolve_synthesis_contract())
        if completion_nonce:
            # Appended to the system prompt, not to one of the three user-prompt branches, so every synthesis shape carries it.
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

            # Long-term memory competes for the SAME budget as this cycle's evidence and is demoted (`trim_first`): spent before any fresh evidence is touched, so an old "fixed" record cannot outlive the code proving it.
            memory_label = MEMORY_BLOCK_LABEL
            while memory_label in artifacts:      # never shadow a real artifact
                memory_label += "_"
            memory_payload = long_term_block.strip()
            if memory_payload:
                raw_blocks.append((memory_label, memory_payload))

            # Below one whole record memory rebuilds into nothing, so a stub keeps chars and no citable id.
            _lines = self.memory_record_lines(getattr(self, "_last_persistent_records", []))
            trimmed_blocks, was_trimmed = apply_total_budget(
                raw_blocks, trim_first_labels={memory_label},
                min_useful=({memory_label: len(f"{MEMORY_OPEN_TAG}\n{_lines[0]}")}
                            if _lines else None),
            )
            memory_trimmed = False
            # None = memory was not trimmed (all records citable); a set = only these survived.
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

            # The one truth two consumers need: did any WHOLE record reach the model. `None` means never trimmed, so all did.
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
            # A record the trim removed must leave the citable list, or the verifier books a cited-but-unmatched citation.
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
                # Offered only when the block is in the prompt and holds records (not characters): a drop notice leaves a non-empty block with nothing citable.
                + (
                    " If long_term_memory contains a relevant record you may "
                    "cite it with source label [memory:<record_id>]."
                    if memory_has_records
                    else ""
                )
            )
        else:
            # No tools were called: general-knowledge question, a follow-up answerable from history/memory, or re-planning exhausted (failure_history present). The Output Contract still applies.
            extra_guidance = ""
            if failure_history:
                extra_guidance = (
                    "Use the <failure_context> block to write an honest "
                    "Conclusion: state plainly what could not be collected, "
                    "list what was tried (one bullet per attempt), and put the "
                    "unmet information need under Unverified. Cite each fact as "
                    "[general-knowledge] when relying on prior knowledge."
                )
            # On a no-tool turn the sensor blocks (model roster, spend mirror) were in the prompt but not citable; the chain already carries them as `sensor:` evidence, so offer the tokens as on a tool turn.
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

        # Record whether the active contract is the generic Conclusion/Facts one; a task-specific contract must not make the verifier flag missing generic headers.
        self._synthesis_expects_contract_headers = output_contract_requires_headers(
            system_prompt
        )

        # Defence-in-depth: one more redaction pass over the built prompt (e.g. secret in planner_reasoning).
        safe_user_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        from core.host_tools_context import _build_host_tools_block, host_tools_relevant
        # Local critique must not pull host_tools context; otherwise inject only when the turn is about host tools, so .env paths do not ride along on unrelated questions.
        _tools_used = [a.get("tool") for a in artifacts.values() if isinstance(a, dict)]
        if local_critique is None and host_tools_relevant(
            f"{question}\n{planner_reasoning}", _tools_used
        ):
            host_block = _build_host_tools_block()
            # Reference context ONLY, NOT evidence: as an <evidence> block it flipped the synthesizer to evidence-only mode and produced unmatchable [tool:host_tools] citations. A <host_environment> block must not be cited and never switches off general-knowledge answering.
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
        """One rewritten draft when the answer is not about the question asked.

        Relevance is computed (`core/confidence_vector.relevance_score`); this acts on it. The second build uses the SAME evidence, no new steps or network; only the demand to answer the question changes. The better draft by the same measure stays.
        """
        import os

        from core.confidence_vector import relevance_applicable, relevance_score
        from core.replan import ReplanTrigger
        from core.synth_resilience import SynthAttempt

        # Switch, off by default (like `AGENT_OBSERVE_BEFORE_ANSWER`): relevance is a rough word-overlap measure and a trigger costs an extra model call.
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
        except Exception as exc:  # noqa: BLE001 — a rewrite may not drop the answer
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
        """A file of its own folder listed under "Not verified": read it and answer.

        A door, not a wall: files are read by the same tool (read only, at most three), become evidence, and the draft is rebuilt once with a demand to settle those items.
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
            except Exception as exc:  # noqa: BLE001 — an unread file stays unverified
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
        except Exception as exc:  # noqa: BLE001 — re-reading may not drop the answer
            self._sensor_failed("unverified_self_read", exc)
            return
        self.log.log("unverified_own_files_read", {"paths": read, "rewritten": bool(second)})
        if second:
            st.draft_answer = second

    def _run_synthesizer_ladder(self, st: SynthesisState) -> None:
        """Bring the draft answer through synthesizer failures.

        Returns nothing: draft and completion verdict are in `st`. `ModelBudgetExceeded` propagates (end of budget, not a synthesis failure).
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
                # Falling back to the normal model is right, but quietly is not: the cheap path exists to cut cost, and the absent `cheap_path_synth_model` event would read as "no cheap path taken".
                _synth_llm = st._task_synth_llm
                self._sensor_failed("cheap_path_model_tier", _tier_exc)
        _saved_on_token = getattr(self, "_stream_on_token", None)

        # Run-local, deliberately NOT an instance attribute: a `self._last_*` field survives its run and early-return paths bank without entering here, so a declaration would be attributed to the next run's episode.
        st._declared: dict[str, str | None] = {"value": None}

        def _do_synthesize(_attempt: SynthAttempt) -> str:
            # Retries must not double-stream tokens: only the first attempt streams to the console.
            if _attempt.index > 0:
                self._stream_on_token = None
            # Cleared BEFORE the call that can raise, so a dying attempt does not leave the previous verdict.
            st._declared["value"] = None
            # One nonce per ATTEMPT, not per run: a marker copied from a discarded attempt must not validate against the banked one.
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
                # Every failed step, not only those that survived to replan exhaustion, so a plan that failed a step and still answered tells the synthesiser about the failure. `<failure_context>` is not an `<evidence>` block: a voice without citation power.
                failure_history=failures_for_synthesis(
                    st.failure_history, exhausted=st.replan_exhausted),
                llm=_synth_llm,
                # Shrink the prompt/output on the adapted attempt: recovery for a request the model could not finish.
                lean_context=st.cheap_path_active or _attempt.adapt_context,
                local_critique=(
                    self.last_referent_decision
                    if st.local_critique_active
                    else None
                ),
            )
            # Strip here, once: the verifier, the user's answer and the stored `full_answer` all derive from this return value, so the nonce reaches none of them.
            _parsed = parse_completion_marker(
                _raw, nonce=_nonce, valid_tokens=_COMPLETION_DECLARATIONS
            )
            st._declared["value"] = _parsed.declared
            self.log.log(
                "completion_declaration",
                {
                    # The nonce is a secret of the attempt and is never logged.
                    "attempt": _attempt.index,
                    "parse": _parsed.status,
                    "declared": _parsed.declared,
                    # Truncation is not forgetfulness: the marker is at the END, so an answer cut by the output ceiling cannot carry it. This field keeps `parse=missing` from truncation apart from a model that did not write it.
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
            revise_refuted_draft(self, st, _do_synthesize)
            if _ladder.degraded:
                # The answer the user gets came from the fallback, not the attempt that declared; keeping that declaration would attribute a verdict to text its author never wrote.
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
