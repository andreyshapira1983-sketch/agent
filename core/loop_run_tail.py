"""Run tail, carved from ``core/loop.py``: the answer is final, the episode is not yet written.

Covers final redaction, shadow sensor accounting (S2/S5), working-memory write, compaction, profile update and assumption
persistence. Episode writing deliberately stays OUT: it must be the last step of the cycle, or a later failure would
leave a success already banked and `run_id` idempotence would refuse to correct it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.completion_obligation import evaluate_completion_obligations
from core.redaction import redact_dlp_text
from core.request_checklist import judge_contract_checklist
from core.requested_format import honor, llm_converter, requested_labels


class AgentLoopRunTail:
    """Between a ready answer and a written episode; members are host-contract declarations with no runtime assignments."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        memory: Any
        assumption_store: Any
        user_profile_store: Any
        last_verification: Any
        last_user_profile: Any
        last_assumptions: Any
        _current_attempt: int

        # Declared as a CALLABLE attribute: an empty-body stub function is read by analyzers as "no return", and every call gets a false E1111.
        _sensor_failed: Any
        _defect_signals: Any

        # Taken from a neighbouring mixin via the MRO; the cross-module link must be written down or it only shows at run time.
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
        """Redacted answer, verification report and weak-chunk count.

                The first element may DIFFER from the input (the final redaction strips what must not reach the user);
                it is returned, not mutated, so the episode records exactly the text the user saw.
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

        # Sensor shadow accounting (S2, S5), emitted at the end of the run: "would stopping there have changed
        # anything?" can only be answered once the remaining attempts' results are known. Reported, never acted on.
        if _stagnation_shadow is not None:
            try:
                _at = int(_stagnation_shadow.get("attempt") or 0)
                _seen_then = set(_stagnation_shadow.get("artifacts_at_detection") or ())
                _new_after = sorted(set(artifacts) - _seen_then)
                self.log.log("stagnation_shadow", {
                    **_stagnation_shadow,
                    "would_stop": True,
                    "would_save_attempts": max(0, self._current_attempt - _at),
                    # The honest form of "would it have changed the result": did anything new arrive after the stop point?
                    "would_change_result": bool(_new_after),
                    "artifacts_gained_after_detection": _new_after,
                    "replan_exhausted": replan_exhausted,
                })
            except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
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
            except Exception:  # noqa: BLE001, S110 — the logger must never break the answer path
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
            # Compact older turns into one summary Turn instead of silently dropping them when max_turns is exceeded;
            # no-op until the threshold is crossed.
            try:
                if self.memory.compact_if_needed():
                    self.log.log(
                        "memory_compacted",
                        {
                            "session_id": self.memory.session_id,
                            "turns_after": len(self.memory.turns),
                        },
                    )
            except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("memory_compaction", exc)

        verification = self.last_verification
        weak_chunks = 0
        if verification:
            weak_chunks = (
                verification.subagent_asserted_chunks
                + verification.cited_but_unmatched_chunks
                + verification.receipt_missing_chunks
                + verification.topic_supported_but_claim_unverified_chunks
                # A self-analysis answer is shippable but NOT a reusable procedure: counted as weak so the episode is banked
                # `partial`, not `success`, and "explaining my own mistake" is not promoted into procedural memory.
                + verification.dialogue_supported_chunks
                # Support that is only the operator's own words must not bank a clean success: counted weak, so the episode
                # lands `partial` when user-echo is all or most of what the answer leans on.
                + verification.user_asserted_chunks
                + verification.refuted_chunks
            )
            # Refuted is a fact, not a share: the signal disqualifies the episode from experience and credit
            # (docs/CODE_NOTES.md, "REFUTED is a polarity").
            if verification.refuted_chunks:
                self._defect_signals.append("content_refuted")
        # Update the user profile from this interaction.
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
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # Must never abort the run, and must not be invisible: silence reads exactly like "no update was due".
                self._sensor_failed("user_profile_update", exc)

        # Persist assumptions and expose them via last_assumptions.
        self.last_assumptions = _run_assumptions
        if (
            may_assumptions
            and self.assumption_store is not None
            and _run_assumptions.new_assumptions
        ):
            try:
                self.assumption_store.save_many(_run_assumptions.new_assumptions)
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # The worse of the two: a run that lost every assumption journalled exactly like a healthy one.
                self._sensor_failed("assumption_store_save", exc)

        return answer, verification, weak_chunks


    def _honor_requested_format(
        self, answer: str, user_question: str, draft_answer: str, synth_llm: Any,
    ) -> str:
        """The human-specified answer line goes last and without our markers.

                After composition and marker removal, before obligations (they read the exact text the human gets);
                see `core/requested_format.py`.
        """
        if not requested_labels(user_question):
            return answer
        llm = synth_llm if synth_llm is not None else getattr(self, "llm", None)
        honored = honor(answer, question=user_question, draft=draft_answer or "",
                        convert=llm_converter(llm, user_question))
        self.log.log("requested_format", {"origins": honored.origins})
        return honored.text

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
        """Ask about the turn's obligations from the answer, not from keywords.

                Observational: the verdict goes to the journal and defect signals, the turn is unchanged. Runs AFTER
                composition because three of four obligation states depend on whether the operator was told, which is
                readable only from the text they receive. The old keyword detector still fires above; its verdict is
                carried here so the two can be compared on live traffic.
        """
        # Premature completion asked as an OBLIGATION question, not a keyword question (S3); observational, compared
        # with the old detector in the journal.
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
            # Shadow comparison against the detector this replaces, so their disagreement is a number in the journal.
            _payload["shadow_keyword_detector"] = bool(_premature_keyword_fired)
            self.log.log("completion_obligation", _payload)
            # What this turn read becomes a SOURCE in the registry, not only a summary: the working agent must be able
            # to extend its own knowledge without CLI commands.
            try:
                from core.read_sources_registry import register_read_sources

                register_read_sources(self, chain)
            except Exception as exc:  # noqa: BLE001 — знание не валит ход
                self.log.log("read_sources_unregistered",
                             {"error": f"{type(exc).__name__}: {exc}"[:200]})
            # Recorded here, enforced at banking, not mid-run. This signal IS authoritative: `assemble_completion_verdict`
            # lowers `achieved` to `partially_achieved` when it is present, which also withholds procedure credit.
            # Nothing is stopped or replanned while the cycle runs; what changes is the verdict it is banked under.
            if _obl.triggered:
                self._defect_signals.append("obligation_silently_missing")
            # Neighbouring signal with the same one-sided authority but a different ground: a part of the operator's
            # contract could not be REPRESENTED, so there is nothing to certify its fulfilment against.
            if _obl.contract_coverage == "partial":
                self._defect_signals.append("user_contract_unrepresented")
            # Separate signal: the units WERE representable and still not addressed; a lost part of the task, not an
            # extractor limit.
            if _obl.unaddressed_units:
                self._defect_signals.append("named_units_unaddressed")
            # Task checklist (TICK/RLCF): an important item answered "no" lowers "done"; same one-sided authority as the neighbours.
            if judge_contract_checklist(self, user_question, answer, completion_contract):
                self._defect_signals.append("checklist_unmet")
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("completion_obligation", exc)
