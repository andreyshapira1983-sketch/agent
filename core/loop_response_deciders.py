"""Черновик ответа и решатели над ним — вырезано из ``core/loop.py`` дословно.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Второй кусок раскола `core/loop.py` (первым был `loop_step_execution`) и
первый — раскола метода: файл держался на 3408 строках, из которых 2213
занимал один `_run_inner`. После этого куска: 3174 и 1981.

Здесь живёт участок между синтезом и композицией: сырой ответ становится
``ResponseDraft``, и шесть решателей высказываются о нём — объяснение
проверки (MIR-069), сильный причинный кредит памяти (MIR-074), переспрос при
нулевой проверке самоанализа (MIR-075), политика ранжировщика источников,
гейт уточнений при исчерпанном перепланировании и структурное принуждение
ответа. Каждый либо переписывает утверждения (``set_body``), либо навешивает
что-то о них (``add_notice``); склейка — одна, в ``render()`` у вызывающего.
До черновика все писали в одну переменную и побеждал последний, из-за чего
усечение могло удалить уточняющие вопросы, которые цикл только что решил
задать (измерено; см. `core/response_draft.py`).

Границу выбрали не на глаз: у этого участка из всего вороха run-локалей
`_run_inner` на входе всего шесть имён, а наружу он отдаёт ровно черновик.
Тела перенесены символ в символ, что пинится AST-сверкой с историей в
`tests/test_loop_response_deciders_split.py`.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.answer_format import file_scope_notice
from core.low_evidence_policy import is_evidence_expected
from core.output_policy import apply_ranker_output_policy
from core.response_draft import ResponseDraft
from core.unsupported_claims import apply_answer_enforcement
from core.verification_summary import build_verification_summary

#: What the user gets when the answer-safety check itself broke. Deterministic
#: and free of factual claims on purpose: the draft it replaces is the one
#: enforcement was about to remove, so repeating any of it would defeat the
#: refusal. No traceback reaches the reader — the stage and the exception type
#: go to the journal, where they belong.
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
    """The safe refusal could not be built either — a controlled failure.

    Raised rather than returning the original draft. The measured damage is
    that the original carries a confident unsupported claim; handing it over
    because the recovery path also broke would deliver exactly what the whole
    mechanism exists to withhold.
    """


class AgentLoopResponseDeciders:
    """Сборка черновика ответа: кто и что вправе о нём сказать.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        clarification_gate_enabled: Any
        last_verification: Any
        last_provenance: Any
        last_self_analysis: Any
        last_source_ranking: Any
        last_role_context: Any

        # Объявляем ВЫЗЫВАЕМЫМИ атрибутами: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _durable_learning_suppressed: Any
        _sensor_failed: Any

    def _safe_answer_after_enforcement_failure(
        self, *, stage: str, exc: BaseException,
    ) -> ResponseDraft:
        """A refusal built on a FRESH object, never through the broken one.

        Order matters and is the reason this is a separate method: the failure
        is recorded first, then the replacement is built independently. Writing
        the safe text through `draft.set_body` would be calling the mechanism
        that may have just raised — and on a `set_body` failure that recursion
        ends with the dangerous original going out anyway.
        """
        # One guard, not two: recording the failure is best effort, and the
        # refusal must leave even if nothing could be written about it. Both
        # steps live under the same `except` because they share that rule and
        # splitting them only doubles the suppression.
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
            return ResponseDraft(body=ENFORCEMENT_FAILURE_ANSWER)
        except Exception as build_exc:
            raise EnforcementFallbackUnavailable(
                f"answer-safety check failed at {stage} and the safe refusal "
                "could not be built; the unverified draft is not returned"
            ) from build_exc

    def _credit_memory_records_used_in_the_answer(self) -> None:
        """Strong causal credit for memory that actually held up.

        Lifted out of `_build_response_draft` because it is not about
        building a draft at all: it is memory accounting, and it landed
        there only because the verifier verdict and the evidence chain
        happen to both be in scope at that point (census entry for this
        file). The length ratchet asked for the same cut independently.
        """
        # MIR-074 phase 1 (operator ruling): STRONG causal credit. A record
        # cited [memory:<id>] in a chunk the verifier marked `verified` has
        # completed the full chain — retrieved → changed the answer →
        # independently checked. Injection alone stays a near-zero signal
        # (access_count); this is the one that counts.
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
                    # One load, all increments in memory, ONE rewrite — an
                    # answer crediting N records must not trigger N full-file
                    # rewrites (review round #294).
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
                        # Through the public bulk operation now. This site had
                        # the right idea first — "one load, all increments in
                        # memory, ONE rewrite" (review #294) — but reached past
                        # the API to get it, because none existed. It does now.
                        self.persistent_store.update_many(
                            r for r in _new_records if r.id in _seen_rids
                        )
                        self.log.log(
                            "memory_causal_credit",
                            {"record_ids": _updated, "count": len(_updated)},
                        )
            except Exception as _cc_exc:
                # Credit must never break the answer — and its failure must
                # not be invisible (the MIR-077 rule).
                try:
                    self.log.log(
                        "memory_causal_credit_failed",
                        {
                            "error_type": type(_cc_exc).__name__,
                            "error": str(_cc_exc)[:300],
                        },
                    )
                except Exception:
                    pass

    def _enforce_answer_safety(
        self,
        draft: ResponseDraft,
        *,
        user_question: str,
        local_critique_active: bool,
        verifier_failure: bool,
    ) -> ResponseDraft:
        """The structural layer, and its failure path.

        Lifted out of `_build_response_draft` because it is one
        responsibility with one failure contract — and because the
        function-length ratchet said so when the failure path was added.
        Returns the draft to use: the same object when enforcement
        succeeded, a fresh safe refusal when it did not.
        """
        # Answer enforcement (PR3): low-evidence truncation, local-critique
        # empty-rewrite skip, verifier soft-fail, claim-level short path.
        # Evidence support stays observational; this is the structural layer.
        #
        # The handler below used to be a bare `except: pass`, and measuring what
        # that cost settled the design (census A2, 2026-08-05). Reproduced on a
        # draft the policy really truncates: healthy, 1291 chars became 460 and
        # the answer opened "no claim could be backed by the sources gathered
        # this cycle"; with an exception injected, the user received the whole
        # 1291 chars opening "the API returns 42 on every call" — a confident
        # factual claim the evidence did not support. Both events that would
        # have said so were the ones that vanished.
        #
        # So returning the original draft is FORBIDDEN by measurement: it is
        # precisely the text enforcement existed to remove. Failing closed on
        # CONTENT without taking the cycle down is the only option the evidence
        # leaves.
        #
        # `_stage` names which of the six operations broke. Six, not one — and
        # `set_body` is among them, which is why the safe answer below is built
        # on an independent object rather than written through the mechanism
        # that may have just failed.
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
            )
            # Enforcement judges the CLAIMS, so it is handed the body alone.
            # Handing it the composed text would let it measure — and delete —
            # notices that are not claims and that no verdict about the evidence
            # can make untrue.
            _stage = "apply_enforcement"
            _enf = apply_answer_enforcement(
                answer=draft.body,
                report=_report,
                question=user_question,
                evidence_expected=_evidence_expected,
                local_critique_active=local_critique_active,
                verifier_failure=verifier_failure,
            )
            _stage = "log_enforcement"
            self.log.log("answer_enforcement", _enf.to_log_payload())
            _stage = "log_truncation"
            if _enf.outcome == "insufficient_evidence" and _enf.applied:
                self.log.log(
                    "low_evidence_truncation",
                    _enf.low_evidence_payload or _enf.to_log_payload(),
                )
            _stage = "set_body"
            if _enf.applied:
                draft.set_body(_enf.answer, by="answer_enforcement")
        except Exception as _enf_exc:  # noqa: BLE001 — отчёт в помощнике ниже
            # Reported, not swallowed: `_safe_answer_after_enforcement_failure`
            # writes `answer_enforcement_failed` and banks the defect signal
            # before building the refusal. The report is one call away rather
            # than inline because the refusal must be built on a FRESH object.
            draft = self._safe_answer_after_enforcement_failure(
                stage=_stage, exc=_enf_exc,
            )

        return draft

    def _build_response_draft(
        self,
        answer: str,
        *,
        user_question: str,
        artifacts: dict[str, dict[str, Any]],
        replan_exhausted: bool,
        local_critique_active: bool,
        verifier_failure: bool,
    ) -> ResponseDraft:
        """Черновик ответа после всех решателей, до композиции.

        Вызывающий склеивает его сам (``render()``) — так единственная точка
        арбитража остаётся в цикле, на виду, а не прячется за этим методом.
        """
        draft = ResponseDraft(body=answer)

        # MIR-069 (phase 1): the five-point verification explanation — what was
        # checked, how, on what evidence, what remains unverified, how
        # confident. Full text goes to the journal; the compact tail rides the
        # notice ledger so a later body rewrite cannot delete it. Nothing
        # examined → no tail (the disclaimers already speak for that case).
        if self.last_verification is not None:
            try:
                _vsummary = build_verification_summary(
                    self.last_verification, chain=self.last_provenance
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
            except Exception as _vs_exc:
                # The explanation must never break the answer — but its
                # failure must not be invisible either (review round #283):
                # the journal says why this turn carries no explanation.
                try:
                    self.log.log(
                        "verification_explained_failed",
                        {
                            "error_type": type(_vs_exc).__name__,
                            "error": str(_vs_exc)[:300],
                        },
                    )
                except Exception:
                    pass

        self._credit_memory_records_used_in_the_answer()

        # MIR-075: ask back instead of only philosophising unsupported. Fires
        # ONLY when the self-analysis sensor marked this turn AND the answer's
        # own verification counted zero verified chunks over a non-empty claim
        # set — the operator's measured «он не переспрашивает» shape. Question
        # wording is never inspected (the lexical route died in #263).
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
            except Exception as _ab_exc:
                try:
                    self.log.log(
                        "clarification_ask_back_failed",
                        {
                            "error_type": type(_ab_exc).__name__,
                            "error": str(_ab_exc)[:300],
                        },
                    )
                except Exception:
                    pass

        policy_result = apply_ranker_output_policy(
            answer=draft.body,
            ranking=self.last_source_ranking,
            question=user_question,
            replan_exhausted=replan_exhausted,
        )
        if policy_result.applied:
            # Body edits (capped Confidence, downgraded realtime tags) are
            # corrections to the claims; the warnings are about the run and are
            # composed onto whatever body survives.
            draft.set_body(policy_result.answer, by="output_policy")
            for _warning in policy_result.warnings:
                draft.add_notice(
                    author="output_policy",
                    channel="unverified_note",
                    text=_warning,
                )
            self.log.log("output_policy", policy_result.to_log_payload())

        # B-1 Clarification Gate — режим переспроса. When the loop is STUCK
        # (replan exhausted == loop_suspected), the mature response is to ASK,
        # not to keep building. The gate's minimal clarifying questions go above
        # the honest answer so the operator can narrow the frame. Pure and
        # deterministic (no LLM, no I/O); best-effort so it can never take down
        # the response path.
        if replan_exhausted and self.clarification_gate_enabled:
            try:
                from core.clarification_gate import clarification_for_replan_exhausted
                _clarify = clarification_for_replan_exhausted()
                if draft.add_notice(
                    author="clarification_gate",
                    channel="prepend",
                    text=_clarify.prompt(),
                ):
                    self.log.log("clarification_gate", _clarify.to_dict())
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("clarification_gate", exc)

        draft = self._enforce_answer_safety(
            draft,
            user_question=user_question,
            local_critique_active=local_critique_active,
            verifier_failure=verifier_failure,
        )

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
