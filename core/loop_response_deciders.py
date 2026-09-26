"""Черновик ответа и решатели над ним — миксин ``AgentLoop`` между синтезом и композицией.

Каждый решатель либо переписывает утверждения (``set_body``), либо навешивает
заметку (``add_notice``); склейка одна — ``render()`` у вызывающего.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.answer_contradiction import (
    _report_head,
    action_report_mismatch,
    headline_contradicts_facts,
)
from core.answer_format import file_scope_notice
from core.degraded_route import substituted_routes, substitution_notice
from core.low_evidence_policy import is_evidence_expected
from core.output_policy import apply_ranker_output_policy
from core.response_draft import ResponseDraft
from core.run_context import current_run
from core.turn_provenance import older_than_turn
from core.unsupported_claims import apply_answer_enforcement
from core.verification_summary import build_verification_summary

#: Answer when the answer-safety check itself broke. Free of factual claims on
#: purpose: repeating the withheld draft would defeat the refusal.
ENFORCEMENT_FAILURE_ANSWER = (
    "Conclusion: I could not verify the claims in the draft, and the "
    "answer-safety check failed [general-knowledge].\n"
    "Facts: the check that removes unsupported claims raised an error, so the "
    "original response is withheld rather than presented as reliable "
    "[general-knowledge].\n"
    "Sources: none\n"
    "Confidence: low\n"
    "Unverified: everything the draft asserted\n"
    "Safety: the unverified draft was not delivered"
)


class EnforcementFallbackUnavailable(RuntimeError):
    """The safe refusal could not be built either; raised instead of returning the draft."""


class AgentLoopResponseDeciders:
    """Сборка черновика ответа: кто и что вправе о нём сказать."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        clarification_gate_enabled: Any
        last_verification: Any
        last_provenance: Any
        last_self_analysis: Any
        last_source_ranking: Any
        last_confidence_vector: Any
        last_evidence_support: Any
        last_role_context: Any
        # Сюда кладётся `self_contradiction` — дорога от принятия ответа к допуску в обучение.
        _defect_signals: Any

        # Атрибуты, а не заглушки-функции: иначе каждый вызов ложно помечается E1111.
        _durable_learning_suppressed: Any
        _sensor_failed: Any
        _file_read_workspace_root: Any

    def _safe_answer_after_enforcement_failure(
        self, *, stage: str, exc: BaseException,
    ) -> ResponseDraft:
        """Refusal on a FRESH object, never via `draft.set_body`, which may be what just failed."""
        # Recording is best effort: the refusal must leave even if nothing is written.
        try:
            self.log.log(
                "answer_enforcement_failed",
                {
                    "stage": stage,
                    "exception_type": type(exc).__name__,
                    "fallback_applied": True,
                    "original_withheld": True,
                },
            )
            signals = getattr(self, "_defect_signals", None)
            if signals is not None:
                signals.append("answer_enforcement_failed")
        except Exception:  # noqa: BLE001, S110 — последний рубеж вокруг записи
            pass  # nosec B110 — безопасный отказ важнее записи о нём
        try:
            return ResponseDraft(body=ENFORCEMENT_FAILURE_ANSWER, answer_withheld=True)
        except Exception as build_exc:
            raise EnforcementFallbackUnavailable(
                f"answer-safety check failed at {stage} and the safe refusal "
                "could not be built; the unverified draft is not returned"
            ) from build_exc

    def _disclose_substituted_model(self, draft: Any) -> None:
        """Сказать в ОТВЕТЕ, если его написал запасной поставщик.

        Маршрут читается из леджера расходов — второй источник той же правды мог бы
        разойтись. См. docs/CODE_NOTES.md, «The answer was not written by the model you chose».
        """
        try:
            ledger = getattr(getattr(self, "model_router", None), "usage_ledger", None)
            routes = substituted_routes(
                getattr(ledger, "records", None),
                run_id=getattr(current_run(), "run_id", None),
            )
            notice = substitution_notice(routes, rejected_draft=draft.answer_withheld)
            if not notice:
                return
            self.log.log("model_substituted", {
                "roles": [r.role for r in routes],
                "answered": sorted({r.answered for r in routes}),
                "intended": sorted({r.intended for r in routes}),
                "refusals": sum(r.refusals for r in routes),
            })
            draft.add_notice(
                author="degraded_route", channel="append", text=notice,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            # Узко: кривая запись леджера или черновик без `add_notice`. Ответ
            # не роняем, но о провале пишем в журнал.
            self.log.log("model_substitution_disclosure_failed", {
                "error_type": type(exc).__name__, "error": str(exc)[:300],
            })

    def _credit_memory_records_used_in_the_answer(self) -> None:
        """Strong causal credit (MIR-074) for memory records cited in verified chunks."""
        # A [memory:<id>] cite in a `verified` chunk completes retrieved → used → checked;
        # injection alone stays a near-zero signal (access_count).
        if (
            self.last_verification is not None
            and self.last_provenance is not None
            and self.persistent_store is not None
            and not self._durable_learning_suppressed("access_stats")
        ):
            try:
                _ev_by_id = {
                    ev.id: ev for ev in self.last_provenance.evidences
                }
                _credited: list[str] = []
                _seen_rids: set[str] = set()
                for _chunk in self.last_verification.chunks:
                    if _chunk.verdict != "verified":
                        continue
                    for _mid in _chunk.matched_evidence_ids:
                        _ev = _ev_by_id.get(_mid)
                        if (
                            _ev is not None
                            and _ev.obtained_via == "memory"
                            and _ev.source_id.startswith("memory:mem")
                        ):
                            _rid = _ev.source_id.removeprefix("memory:")
                            if _rid not in _seen_rids:
                                _seen_rids.add(_rid)
                                _credited.append(_rid)
                if _credited:
                    # One load, ONE rewrite: N credited records must not cause N full-file rewrites.
                    _records = self.persistent_store.load()
                    _updated: list[str] = []
                    _new_records = []
                    for _rec in _records:
                        if _rec.id in _seen_rids:
                            _new_records.append(
                                _rec.model_copy(
                                    update={"causal_use": _rec.causal_use + 1}
                                )
                            )
                            _updated.append(_rec.id)
                        else:
                            _new_records.append(_rec)
                    if _updated:
                        self.persistent_store.update_many(
                            r for r in _new_records if r.id in _seen_rids
                        )
                        self.log.log(
                            "memory_causal_credit",
                            {"record_ids": _updated, "count": len(_updated)},
                        )
            except Exception as _cc_exc:  # noqa: BLE001 — reason stated above
                # Credit must never break the answer, nor fail invisibly (MIR-077).
                try:
                    self.log.log(
                        "memory_causal_credit_failed",
                        {
                            "error_type": type(_cc_exc).__name__,
                            "error": str(_cc_exc)[:300],
                        },
                    )
                except Exception:  # noqa: BLE001, S110 — reason stated above
                    pass

    def _headline_check(self, draft: ResponseDraft) -> None:
        """Доклад о действиях, свежесть и заголовок против фактов ответа — видно, но не карантин.

        Сигнал о заголовке наблюдающий (нет в DISQUALIFYING_DEFECT_SIGNALS).
        """
        # Доклад о записи сверяется с тем, что цикл реально выполнил.
        ledger = action_report_mismatch(draft.body, list(getattr(self, "_executed_tools", []) or []))
        if ledger:
            self._defect_signals.append("action_report_mismatch")
            self.log.log("action_report_mismatch", {"notice": ledger})
            draft.add_notice(author="action_ledger", channel="append", text=ledger)
        # Порождено ли названное в выводе этим ходом (core/turn_provenance.py).
        run = current_run()
        root_of = getattr(self, "_file_read_workspace_root", None)
        stale = older_than_turn(_report_head(draft.body or ""), root_of() if callable(root_of) else None,
                                run.started_at if run else 0.0,
                                list(getattr(self, "_executed_tools", []) or []))
        if stale:
            self._defect_signals.append("result_older_than_turn")
            self.log.log("result_older_than_turn", {"notice": stale})
            draft.add_notice(author="action_ledger", channel="append", text=stale)
        found = headline_contradicts_facts(draft.body)
        if not found:
            return
        self._defect_signals.append("headline_contradicts_facts")
        self.log.log("headline_contradicts_facts",
                     {"contradictions": [c.to_log_payload() for c in found]})
        names = ", ".join(f"`{c.subject}`" for c in found[:3])
        draft.add_notice(
            author="headline_check", channel="append",
            text=(f"⚠️ Вывод ответа противоречит его же фактам: {names} — в выводе "
                  "заявлено наличие, в фактах названо отсутствие. Верить стоит фактам."),
        )

    def _enforce_answer_safety(
        self,
        draft: ResponseDraft,
        *,
        user_question: str,
        local_critique_active: bool,
        verifier_failure: bool,
        completion_contract: Any = None,
    ) -> ResponseDraft:
        """Structural answer enforcement; returns the same draft, or a fresh safe refusal."""
        # Fail closed on content: returning the original draft on error would deliver
        # the very claims enforcement removes. `_stage` names the step that broke.
        _stage = "read_state"
        try:
            _ranking = self.last_source_ranking
            _report = self.last_verification
            _chain_empty = bool(
                getattr(_report, "chain_was_empty", False)
            ) if _report is not None else True
            _realtime = (
                bool(getattr(_ranking, "realtime_required", True))
                if _ranking is not None
                else True
            )
            _stage = "evidence_expected"
            _evidence_expected = is_evidence_expected(
                role=getattr(self.last_role_context, "role", ""),
                chain_was_empty=_chain_empty,
                realtime_required=_realtime,
                answer=draft.body,
                question=user_question,
            )
            # Enforcement judges CLAIMS, so it gets the body alone — notices are not claims.
            _stage = "apply_enforcement"
            # Recorded failed attempts (429, blocked, empty) back an honest «could not confirm».
            from core.low_evidence_policy import count_blocked_attempts

            _enf = apply_answer_enforcement(
                answer=draft.body,
                report=_report,
                question=user_question,
                evidence_expected=_evidence_expected,
                local_critique_active=local_critique_active,
                verifier_failure=verifier_failure,
                contract=completion_contract,
                blocked_attempts=count_blocked_attempts(self.last_provenance),
            )
            _stage = "log_enforcement"
            self.log.log("answer_enforcement", _enf.to_log_payload())
            _stage = "log_truncation"
            if _enf.outcome == "insufficient_evidence" and _enf.applied:
                self.log.log(
                    "low_evidence_truncation",
                    _enf.low_evidence_payload or _enf.to_log_payload(),
                )
            _stage = "bank_contradiction"
            # `decide_usage_eligibility` по сигналу не пускает ответ в опыт. Ставится по
            # НАХОДКЕ, а не по исходу: исход могло забрать более сильное действие.
            if getattr(_enf, "contradictions", ()):
                self._defect_signals.append("self_contradiction")
            # R4: фабрикация цитат — тот же класс ложности, судья другой.
            if _enf.outcome == "citation_integrity":
                self._defect_signals.append("citation_fabricated")
                draft.answer_withheld = True
            elif _enf.outcome == "citation_excised":  # ответ ушёл, выдуманное снято
                self._defect_signals.append("citation_excised")
            _stage = "set_body"
            if _enf.applied:
                draft.set_body(_enf.answer, by="answer_enforcement")
            _stage = "headline_check"
            self._headline_check(draft)
        except Exception as _enf_exc:  # noqa: BLE001 — отчёт в помощнике ниже
            draft = self._safe_answer_after_enforcement_failure(
                stage=_stage, exc=_enf_exc,
            )

        return draft

    def _add_verification_summary(self, draft: ResponseDraft, user_question: str = "") -> None:
        """Name the verified text after enforcement has decided what survived."""
        # MIR-069: the tail rides the notice ledger so a later body rewrite cannot delete it.
        # Светская реплика хвоста не несёт — отчёт о надёжности беседы бессмыслен.
        from core.conversation_contract import classify_register
        from core.social_turn import is_marked_social
        if user_question and (classify_register(user_question) == "small_talk"
                              or is_marked_social(self, user_question)):
            self.log.log("verification_tail_skipped", {"reason": "small_talk"})
            return
        if self.last_verification is not None:
            try:
                _vsummary = build_verification_summary(
                    self.last_verification, chain=self.last_provenance,
                    vector=self.last_confidence_vector,
                    evidence_support=getattr(
                        self, "last_evidence_support", None
                    ),
                    rejected_draft=draft.answer_withheld,
                )
                self.log.log(
                    "verification_explained", _vsummary.to_log_payload()
                )
                if _vsummary.tail:
                    draft.add_notice(
                        author="verification_summary",
                        channel="append",
                        text=_vsummary.tail,
                    )
            except Exception as _vs_exc:  # noqa: BLE001 — reason stated above
                # Never breaks the answer; the journal says why no explanation came.
                try:
                    self.log.log(
                        "verification_explained_failed",
                        {
                            "error_type": type(_vs_exc).__name__,
                            "error": str(_vs_exc)[:300],
                        },
                    )
                except Exception:  # noqa: BLE001, S110 — reason stated above
                    pass

    def _build_response_draft(
        self,
        answer: str,
        *,
        user_question: str,
        artifacts: dict[str, dict[str, Any]],
        replan_exhausted: bool,
        local_critique_active: bool,
        verifier_failure: bool,
        # Параметром, не полем: состоянию с «completion» в имени нельзя переживать ход.
        completion_contract: Any = None,
        failure_history: Any = (),
    ) -> ResponseDraft:
        """Черновик ответа после всех решателей, до композиции (``render()`` — у вызывающего)."""
        draft = ResponseDraft(body=answer)

        self._credit_memory_records_used_in_the_answer()

        # MIR-075: ask back when a self-analysis turn verified zero of a non-empty
        # claim set. Question wording is never inspected.
        if (
            self.last_verification is not None
            and self.last_verification.total_chunks > 0
            and self.last_verification.verified_chunks == 0
            and getattr(self.last_self_analysis, "is_self_analysis", False)
        ):
            try:
                from core.clarification_gate import build_self_analysis_ask_back
                _ask = build_self_analysis_ask_back()
                if draft.add_notice(
                    author="clarification_gate",
                    channel="append",
                    text=_ask,
                ):
                    self.log.log(
                        "clarification_ask_back",
                        {
                            "reason": "self_analysis_zero_verified",
                            "total_chunks": self.last_verification.total_chunks,
                            "self_declared_chunks": (
                                self.last_verification.self_declared_chunks
                            ),
                        },
                    )
            except Exception as _ab_exc:  # noqa: BLE001 — the failure is recorded and logged
                try:
                    self.log.log(
                        "clarification_ask_back_failed",
                        {
                            "error_type": type(_ab_exc).__name__,
                            "error": str(_ab_exc)[:300],
                        },
                    )
                except Exception:  # noqa: BLE001, S110 — the logger must never break the answer path
                    pass

        policy_result = apply_ranker_output_policy(
            answer=draft.body,
            ranking=self.last_source_ranking,
            question=user_question,
            replan_exhausted=replan_exhausted,
        )
        if policy_result.applied:
            # Body edits correct the claims; warnings are about the run and ride as notices.
            draft.set_body(policy_result.answer, by="output_policy")
            for _warning in policy_result.warnings:
                draft.add_notice(
                    author="output_policy",
                    channel="unverified_note",
                    text=_warning,
                )
            self.log.log("output_policy", policy_result.to_log_payload())

        # B-1 Clarification Gate: when STUCK (replan exhausted), ASK narrowing questions first.
        # Застрял на собственном черновике (ссылки, арифметика) — рамку человек не прояснит.
        from core.clarification_gate import frame_questions_help
        failure_codes = [getattr(t, "code", "") for t in failure_history or ()]
        frame_clear = completion_contract is not None and not getattr(completion_contract, "ambiguities", None) \
            and not getattr(completion_contract, "needs_clarification", False)
        if replan_exhausted and self.clarification_gate_enabled and not frame_questions_help(
                failure_codes, gathered=bool(artifacts), frame_clear=frame_clear):
            self.log.log("clarification_gate_skipped", {
                "reason": "stuck_on_own_draft", "failure_codes": sorted(set(failure_codes)),
            })
        elif replan_exhausted and self.clarification_gate_enabled:
            try:
                from core.clarification_gate import clarification_for_replan_exhausted
                _clarify = clarification_for_replan_exhausted()
                if draft.add_notice(
                    author="clarification_gate",
                    channel="prepend",
                    text=_clarify.prompt(),
                ):
                    self.log.log("clarification_gate", _clarify.to_dict())
            except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("clarification_gate", exc)

        draft = self._enforce_answer_safety(
            draft,
            user_question=user_question,
            local_critique_active=local_critique_active,
            verifier_failure=verifier_failure,
        )
        self._add_verification_summary(draft, user_question)
        self._disclose_substituted_model(draft)

        scope_notice = file_scope_notice(user_question, artifacts)
        if draft.add_notice(
            author="file_scope",
            channel="prepend",
            text=scope_notice,
        ):
            self.log.log(
                "file_scope_notice",
                {
                    "notice": scope_notice,
                    "artifact_labels": list(artifacts.keys()),
                },
            )

        return draft
