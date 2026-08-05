"""Хвост прогона: ответ готов, эпизод ещё не записан — из ``core/loop.py``.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Седьмой кусок раскола `core/loop.py` и пятый — раскола `_run_inner`.

Всё, что происходит между «текст ответа окончателен» и «эпизод записан»:
последняя редакция на выходе, теневой учёт сенсоров S2/S5, запись хода в
рабочую память, уплотнение памяти, обновление профиля пользователя и
сохранение допущений.

Редакция здесь — эшелонированная оборона, а не дубль: модель, выдумавшая
учётку или ПДн, не должна пройти мимо ядра даже если её пропустили все
проверки выше.

Теневые сенсоры считаются именно ЗДЕСЬ, в конце, потому что интересный
вопрос — «а изменила бы что-нибудь остановка тогда?» — можно задать только
когда известно, что дали оставшиеся попытки. Они сообщают и никогда не
действуют.

Запись эпизода сюда НЕ переехала намеренно: она обязана оставаться последней
в цикле. Успех, записанный раньше, оставляет окно, в котором последующий сбой
роняет ход с уже забаненным успехом, и идемпотентность по `run_id` потом
откажется это исправить.

Тело перенесено символ в символ, что пинится AST-сверкой с историей в
`tests/test_loop_run_tail_split.py`.

Класс подмешивается в ``AgentLoop``; состояние по-прежнему живёт на
композированном цикле, а не здесь.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.completion_obligation import evaluate_completion_obligations
from core.redaction import redact_dlp_text


class AgentLoopRunTail:
    """Между готовым ответом и записанным эпизодом.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        memory: Any
        assumption_store: Any
        user_profile_store: Any
        last_verification: Any
        last_user_profile: Any
        last_assumptions: Any
        _current_attempt: int

        # Объявляем ВЫЗЫВАЕМЫМ атрибутом: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _sensor_failed: Any
        _defect_signals: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _cycle_findings: Any
        last_source_ranking: Any

    def _finalize_run_tail(
        self,
        answer: str,
        *,
        user_question: str,
        artifacts: dict[str, dict[str, Any]],
        planner_out: Any,
        replan_exhausted: bool,
        may_profile: bool,
        may_assumptions: bool,
        _run_assumptions: Any,
        _stagnation_shadow: dict[str, Any] | None,
        _disagreement_shadow: list[dict[str, Any]],
        _cp: Any,
    ) -> tuple[str, Any, int]:
        """Отредактированный ответ, отчёт проверки и число слабых чанков.

        Первый элемент может ОТЛИЧАТЬСЯ от входного: последняя редакция
        вырезает то, что не должно уехать пользователю. Возвращаем его, а не
        меняем на месте, — вызывающий записывает в эпизод именно тот текст,
        который увидел пользователь.

        Имена с подчёркиванием (`_cp`, `_run_assumptions`, теневые сенсоры)
        сохранены как в цикле: перенос дословный.
        """
        safe_answer, answer_findings, answer_pii_findings = redact_dlp_text(answer)
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
        if answer_pii_findings:
            pii_kinds = sorted({f"pii-{f.kind}" for f in answer_pii_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "final_answer",
                    "kinds": pii_kinds,
                    "count": len(answer_pii_findings),
                    "surface": "user_output",
                },
            )
        answer = safe_answer

        # ── Sensor shadow accounting (S2, S5) ───────────────────────────────
        # Emitted at the end of the run because the interesting question —
        # "would stopping there have changed anything?" — can only be answered
        # once it is known what the remaining attempts actually produced.
        # Reported, never acted on: neither sensor stops or replans anything.
        if _stagnation_shadow is not None:
            try:
                _at = int(_stagnation_shadow.get("attempt") or 0)
                _seen_then = set(_stagnation_shadow.get("artifacts_at_detection") or ())
                _new_after = sorted(set(artifacts) - _seen_then)
                self.log.log("stagnation_shadow", {
                    **_stagnation_shadow,
                    "would_stop": True,
                    "would_save_attempts": max(0, self._current_attempt - _at),
                    # The honest form of "would it have changed the result":
                    # did anything new actually arrive after the stop point?
                    "would_change_result": bool(_new_after),
                    "artifacts_gained_after_detection": _new_after,
                    "replan_exhausted": replan_exhausted,
                })
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("stagnation_shadow_outcome", exc)
        if _disagreement_shadow:
            try:
                self.log.log("subsystem_disagreement_shadow", {
                    "events": _disagreement_shadow,
                    "would_escalate": sum(
                        1 for d in _disagreement_shadow
                        if d.get("would_action") == "escalate"
                    ),
                    "would_replan": sum(
                        1 for d in _disagreement_shadow
                        if d.get("would_action") == "replan"
                    ),
                    "attempts_used": self._current_attempt,
                    "replan_exhausted": replan_exhausted,
                })
            except Exception:
                pass

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
        _cp.save_respond(answer=answer)

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
            # Anthropic 2025 context engineering — compact older turns into
            # one summary Turn instead of silently dropping them when
            # max_turns is exceeded. No-op until the threshold is crossed.
            try:
                if self.memory.compact_if_needed():
                    self.log.log(
                        "memory_compacted",
                        {
                            "session_id": self.memory.session_id,
                            "turns_after": len(self.memory.turns),
                        },
                    )
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("memory_compaction", exc)

        verification = self.last_verification
        weak_chunks = 0
        if verification:
            weak_chunks = (
                verification.subagent_asserted_chunks
                + verification.cited_but_unmatched_chunks
                + verification.receipt_missing_chunks
                + verification.topic_supported_but_claim_unverified_chunks
                # Issue #119: a self-analysis answer is legitimately shippable
                # and legitimately NOT a reusable procedure. Counted as weak so
                # `episode_from_agent_cycle` banks it `partial`, not `success` —
                # otherwise verified=0/unverified=0 would score a perfect run
                # (MIR-002) and promote "explaining my own mistake" into
                # procedural memory.
                + verification.dialogue_supported_chunks
                # Operator ruling 2026-08-03 (MIR-028): support that is only
                # the operator's own words must not bank a clean success —
                # counted weak, so the episode lands `partial` when user-echo
                # is all (or most of) what the answer leans on.
                + verification.user_asserted_chunks
            )
        # Layer 4 — update user profile from this interaction.
        if may_profile and self.user_profile_store is not None:
            try:
                updated = self.user_profile_store.update_from_interaction(
                    question=user_question,
                    response=answer,
                    base=self.last_user_profile,
                )
                self.last_user_profile = updated
                self.log.log(
                    "user_profile_update",
                    {
                        "expertise": updated.expertise,
                        "verbosity": updated.verbosity,
                        "language": updated.language,
                        "interaction_count": updated.interaction_count,
                        "interests": updated.interests,
                        "expert_signals": updated.expert_signals,
                        "novice_signals": updated.novice_signals,
                    },
                )
            except Exception as exc:
                # A7: must never abort the run, and must not be invisible.
                # Silence here reads exactly like "no update was due".
                self._sensor_failed("user_profile_update", exc)

        # Layer 5 — persist assumptions and expose via last_assumptions.
        self.last_assumptions = _run_assumptions
        if (
            may_assumptions
            and self.assumption_store is not None
            and _run_assumptions.new_assumptions
        ):
            try:
                self.assumption_store.save_many(_run_assumptions.new_assumptions)
            except Exception as exc:
                # A7, the worse of the two: measured, a run that lost every
                # assumption journalled exactly like a healthy one.
                self._sensor_failed("assumption_store_save", exc)

        return answer, verification, weak_chunks


    def _check_completion_obligations(
        self,
        answer: str,
        *,
        user_question: str,
        file_hint: str | None,
        artifacts: dict[str, dict[str, Any]],
        chain: Any,
        plan: Any,
        failure_history: list[Any],
        completion_contract: Any,
        _premature_keyword_fired: bool,
    ) -> None:
        """Спросить об обязательствах хода — по ответу, а не по словам.

        Наблюдательно: вердикт кладётся в журнал и в сигналы дефектов, ход не
        меняется. Стоит ПОСЛЕ композиции, потому что три из четырёх состояний
        обязательства зависят от того, сказали ли оператору, — а это читается
        только с того текста, который он получит.

        Старый ключевой детектор продолжает срабатывать выше, и его вердикт
        едет сюда же: два способа спросить одно и то же сравниваются на живом
        трафике прежде, чем что-то решать.
        """
        # Premature completion, asked as an OBLIGATION question rather than a
        # keyword question (S3). Runs here, after composition, because three of
        # the four obligation states turn on whether the operator was actually
        # told — and that can only be read off the answer they receive.
        # Observational; the old keyword detector still fires above, so the two
        # can be compared in the journal before anything is decided.
        try:
            _denied = tuple(
                str(getattr(t, "tool_name", "") or "")
                for t in failure_history
                if getattr(t, "code", "") == "policy_blocked"
            )
            _obl = evaluate_completion_obligations(
                question=user_question,
                answer=answer,
                plan_steps=list(getattr(plan, "steps", ()) or ()),
                artifacts=artifacts,
                chain_size=len(chain),
                realtime_required=bool(
                    getattr(self.last_source_ranking, "realtime_required", False)
                ),
                file_hint=file_hint,
                failure_codes=[
                    str(getattr(t, "code", "") or "") for t in failure_history
                ],
                denied_tools=_denied,
                contract=completion_contract,
            )
            _payload = _obl.to_log_payload()
            # Shadow comparison against the detector this replaces, so the
            # disagreement between them is a number in the journal rather than
            # something a later reader has to reconstruct.
            _payload["shadow_keyword_detector"] = bool(_premature_keyword_fired)
            self.log.log("completion_obligation", _payload)
            # Recorded here, enforced at banking — not mid-run. This signal IS
            # authoritative: `assemble_completion_verdict` lowers a claim of
            # `achieved` to `partially_achieved` when it is present, which also
            # withholds procedure credit. Nothing is stopped or replanned while
            # the cycle is still running, so the run's own path is unchanged;
            # what changes is the verdict it is banked under. S3's ruling was
            # "keep the requirement, replace the detector", and the requirement
            # is what carries that authority.
            if _obl.triggered:
                self._defect_signals.append("obligation_silently_missing")
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("completion_obligation", exc)
