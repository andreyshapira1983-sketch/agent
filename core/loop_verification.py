"""First draft verification and the sensors around it (subsystem disagreement, confidence vector, evidence support); cut out of core/loop.py and mixed into AgentLoop. A verifier failure is a soft failure: the draft is kept and `verifier_failure` is raised, so a broken check is told apart from a check that did not confirm."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.evidence import ProvenanceChain
from core.evidence_support import evaluate_evidence_support
from core.low_evidence_policy import is_evidence_expected
from core.models import Plan
from core.replan import ReplanTrigger

if TYPE_CHECKING:  # pragma: no cover — только для подписи
    from core.verifier_models import VerificationReport


class AgentLoopVerification:
    """First draft check: verdict plus telemetry; the members are host-contract declarations (no assignments, so nothing is created or shadowed at runtime)."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        last_verification: Any
        last_provenance: Any
        last_role_context: Any
        last_source_ranking: Any

        # Declared as CALLABLE attributes: a stub function with an empty body is read by analyzers as a function without return (false E1111).
        _sensor_failed: Any

        # Taken from a sibling mixin via the MRO; the cross-module link must be written down.
        _file_read_workspace_root: Any
        # Set by core/loop_synthesis.py, reset per turn by core/loop.py; a getattr default would turn a missing link into a silent verdict by a foreign rule.
        _synthesis_expects_contract_headers: Any

    def _verify_draft(
        self,
        draft_answer: str,
        *,
        chain: ProvenanceChain,
        user_question: str,
        attempt: int,
        plan: Plan,
        artifacts: dict[str, dict[str, Any]],
        failure_history: list[ReplanTrigger],
        _disagreement_shadow: list[dict[str, Any]],
    ) -> tuple[VerificationReport, bool]:
        """Verdict on the draft and the "verifier failed" flag; the flag means the check broke, not "not confirmed", and the answer must not be punished for it. `_disagreement_shadow` is mutated IN PLACE (shadow S5)."""
        # Transfer seams named in tests/test_loop_verification_split.py; imports stay local as in the loop.
        verifier_failure = False
        from core.verifier import verify as _verify
        from core.verifier_models import VerificationReport as _VRSoft

        try:
            report = _verify(
                answer=draft_answer,
                chain=chain,
                user_question=user_question,
                expects_contract_headers=self._synthesis_expects_contract_headers,
                **self._verification_receipt_kwargs(),
            )
        except Exception as _ver_exc:  # noqa: BLE001 — reason stated above
            # Soft-fail: keep draft; do not pretend "insufficient evidence".
            verifier_failure = True
            self.log.log(
                "verifier_failure",
                {
                    "error_type": type(_ver_exc).__name__,
                    "error": str(_ver_exc)[:300],
                    "draft_chars": len(draft_answer),
                    "phase": "initial",
                },
            )
            report = _VRSoft(
                total_chunks=0,
                verified_chunks=0,
                unverified_chunks=0,
                cited_but_unmatched_chunks=0,
                self_declared_chunks=0,
                structural_chunks=0,
                chunks=(),
                annotated_answer=draft_answer,
                fully_unverified=False,
                chain_was_empty=True,
                disclaimer=None,
                malformed_output=False,
            )
        self.log.log("verification", report.to_log_payload())

        # P1: observational cross-subsystem audit; compare planner outcome with the verifier verdict and emit a `subsystem_disagreement` event per conflicting pair; logging only.
        _disagreements: list[dict] = []
        try:
            from core.subsystem_disagreement import detect_disagreements
            _disagreements = detect_disagreements(
                attempt=attempt,
                plan_steps=plan.steps,
                artifacts=artifacts,
                report=report,
                failure_history=failure_history,
            )
            for _ev in _disagreements:
                self.log.log("subsystem_disagreement", _ev)
                # Shadow accounting: what a connected S5 would have done, never acted on; a full planner/verifier contradiction escalates, the rest is a replan.
                _disagreement_shadow.append({
                    "kind": _ev.get("kind"),
                    "severity": _ev.get("severity"),
                    "attempt": _ev.get("attempt"),
                    "would_action": (
                        "escalate" if _ev.get("severity") == "high"
                        else "replan"
                    ),
                })
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("subsystem_disagreement", exc)

        # P1/P2: confidence vector (evidence / coherence / relevance), reported through the verification summary.
        try:
            self.last_confidence_vector = None  # never a previous run's axes
            from core.confidence_vector import compute_vector
            _cv = compute_vector(
                report=report, disagreements=_disagreements,
                question=user_question, answer=draft_answer,
                judged_relevance=self._judged_relevance(user_question, draft_answer),
            )
            # Kept on the loop, not only logged: the summary reports the relevance axis; reset per run in `loop.py`.
            self.last_confidence_vector = _cv
            self.log.log("confidence_vector", _cv.to_log_payload())
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("confidence_vector", exc)
        if report.malformed_output:
            self.log.log(
                "output_contract_violation",
                {
                    "reason": "LLM answer contains no Output Contract section headers",
                    "total_chunks": report.total_chunks,
                    "structural_chunks": report.structural_chunks,
                },
            )
        self.last_verification = report
        self.last_provenance = chain

        # Evidence support is telemetry, never a gate; emitted on every verified turn including not-applicable ones. Applicability is asked with the same inputs as the enforcing layer, so observer and enforcer cannot disagree.
        try:
            self.last_evidence_support = None  # never a previous run's verdict
            _ev_expected = is_evidence_expected(
                role=getattr(self.last_role_context, "role", ""),
                chain_was_empty=bool(
                    getattr(report, "chain_was_empty", False)
                ),
                realtime_required=bool(
                    getattr(self.last_source_ranking, "realtime_required", True)
                ),
                answer=draft_answer,
                question=user_question,
            )
            _support = evaluate_evidence_support(
                report, evidence_expected=_ev_expected
            )
            # Kept on the loop, not only logged: the verification summary must read the same applicability verdict, or it reports zero confidence for a turn that owed no evidence.
            self.last_evidence_support = _support
            self.log.log("evidence_support", _support.to_log_payload())
        except Exception as exc:  # noqa: BLE001 — наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("evidence_support", exc)

        return report, verifier_failure

    def _judged_relevance(self, question: str | None, answer: str | None) -> float | None:
        """Relevance judge score if enabled, else None (word count); the judge runs as role `verifier` through the router so its calls are in the cost accounting; the old measure is journaled beside it."""
        from core.relevance_judge import judge_enabled, judge_relevance

        router = getattr(self, "model_router", None)
        if router is None or not judge_enabled():
            return None
        try:
            from core.confidence_vector import relevance_score
            from core.model_router import ModelRole

            verdict = judge_relevance(router.for_role(ModelRole.VERIFIER), question, answer)
            payload = verdict.to_log_payload() if verdict else {"score": None}
            self.log.log("relevance_judge", {
                **payload, "lexical": round(relevance_score(question, answer), 3)})
        except Exception as exc:  # noqa: BLE001 — судья не вправе ронять ход
            self._sensor_failed("relevance_judge", exc)
            return None
        return verdict.score if verdict else None

    def _verification_receipt_kwargs(self) -> dict[str, Any]:
        from core.tool_receipts import ToolReceiptLedger, default_receipts_path

        log = getattr(self, "log", None)
        trace_id = str(getattr(log, "trace_id", "") or "") if log is not None else ""
        from core.entailment_scope import judge_for

        # A derived claim with a reference is checked by meaning with a cheap model, limited per answer.
        judge = judge_for(getattr(self, "model_router", None), log)
        root = self._file_read_workspace_root()
        if root is None:
            return {"receipt_ledger": None, "trace_id": trace_id or None, "entailment_llm": judge}
        return {
            "receipt_ledger": ToolReceiptLedger(default_receipts_path(root)),
            "trace_id": trace_id or None,
            "entailment_llm": judge,
        }
