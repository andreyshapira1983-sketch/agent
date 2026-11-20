"""Loop gates: four places where a turn ends before it starts."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from core.replay_freshness import replay_note, with_replay_note

if TYPE_CHECKING:  # pragma: no cover — annotation only
    from core.clarification_policy import ClarificationResult
    from core.operational_domain import DomainResult


#: Narrow markers of a reference to the previous step ("previous step/result"); deliberately not general anaphora.
_PRIOR_STEP_RE = re.compile(
    r"(?i)предыдущ\w*\s+(?:шаг|запрос|ответ|результат)"
    r"|результат\w*\s+предыдущ|прошл\w*\s+шаг"
    r"|previous\s+(?:step|result|answer)"
)


def _names_its_material(question: str) -> bool:
    """True when the question itself names files, so "previous step" is a description, not a dangling reference."""
    from core.file_request_intent import extract_path_mentions

    return bool(extract_path_mentions(question or ""))


class AgentLoopGates:
    """Early exits: the turn is decided before planning, or not decided."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        memory: Any
        odd_enabled: Any
        clarification_enabled: Any
        pending_clarification_path: Any
        episodic_replay: Any
        _stream_on_token: Any
        _last_best_similar_episode: Any
        _last_best_similar_score: Any
        # As in AgentLoop.__init__: a list, or None before the loop starts.
        _cycle_findings: list[dict[str, Any]] | None

        # Declared as callable attributes: an empty-body stub reads to analyzers as "function without return" (false E1111).
        _record_experience_memory: Any
        # Left in core/loop.py on purpose: both name AgentLoop in their body (threshold constants live on the class); resolved via MRO.
        _fast_path_allows_replay: Any

    def _odd_gate(self, user_question: str) -> str | None:
        """Refusal if the request is outside the applicability domain, else None."""
        # ODD gate (§7): pure heuristic, no LLM; stops before any planning with an honest refusal/escalation message.
        if self.odd_enabled:
            _odd = self._check_operational_domain(user_question)
            if _odd.blocks:
                self.log.log(
                    "out_of_domain",
                    {
                        "verdict": _odd.verdict,
                        "action": _odd.action,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _odd.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _odd.message
        return None

    def _resume_clarification(self, user_question: str) -> tuple[str, bool]:
        """Re-attach the question a clarification asked ABOUT to the answer; returns (effective question, is_resumption).

        A resumed run is never re-gated, or it would ask forever.
        """
        pending = None
        if getattr(self, "pending_clarification_path", None) is not None:
            from core.pending_clarification import pending_clarification
            pending = pending_clarification(self.pending_clarification_path)
        if not pending:
            return user_question, False
        self.log.log(
            "clarification_resumed",
            {"original_chars": len(pending), "reply_chars": len(user_question)},
        )
        return pending + "\n\n" + user_question, True

    def _park_clarification(self, user_question: str) -> None:
        """Park the question a clarification was raised about; also sets the "turn answered with a question" flag the unattended runtime reads."""
        self.last_answer_was_clarification = True
        if getattr(self, "pending_clarification_path", None) is not None:
            from core.pending_clarification import pending_clarification
            pending_clarification(self.pending_clarification_path, user_question)

    def _clarification_gate(self, user_question: str) -> str | None:
        """Clarifying question if the request is ambiguous, else None."""
        # Clarification Policy (§3): pure heuristic; returns the question to the caller before planning.
        if self.clarification_enabled:
            _clarif = self._check_clarification(user_question)
            if _clarif.should_ask:
                self.log.log(
                    "clarification_request",
                    {
                        "question": _clarif.question,
                        "findings": [
                            {"kind": f.kind, "evidence": f.evidence, "confidence": f.confidence}
                            for f in _clarif.findings
                        ],
                    },
                )
                self._stream_on_token = None
                return _clarif.question
        return None

    def _contract_ambiguity_gate(self, contract: Any) -> str | None:
        """Sixth gate: an ambiguous contract asks."""
        if not self.clarification_enabled:
            return None
        ambiguities = tuple(getattr(contract, "ambiguities", ()) or ())
        if not ambiguities:
            return None
        question_text = f"Уточнение перед выполнением: {ambiguities[0]}"
        self.log.log("clarification_request", {
            "question": question_text,
            "findings": [{"kind": "contract_ambiguity",
                          "evidence": a[:120], "confidence": 1.0}
                         for a in ambiguities[:3]],
        })
        self._stream_on_token = None
        return question_text

    def _prior_step_gate(self, user_question: str) -> str | None:
        """Fifth gate: a previous step that does not exist."""
        from core.referent_resolver import referent_resolver_mode
        if not self.clarification_enabled:
            return None
        if referent_resolver_mode() == "off":
            return None
        if not _PRIOR_STEP_RE.search(user_question or "") or _names_its_material(user_question):
            return None
        memory = getattr(self, "memory", None)
        if memory is not None and getattr(memory, "turns", None):
            return None
        question_text = (
            "В этой сессии ещё нет предыдущего шага — уточните, какой "
            "результат использовать."
        )
        self.log.log("clarification_request", {
            "question": question_text,
            "findings": [{"kind": "prior_turn_no_antecedent",
                          "evidence": user_question[:120],
                          "confidence": 1.0}],
        })
        self._stream_on_token = None
        return question_text

    def _episodic_fast_path(
        self,
        user_question: str,
        *,
        file_hint: str | None,
        goal: Any,
        local_critique_active: bool,
    ) -> str | None:
        """Stored answer if the turn exactly repeats a past one, else None."""
        # Episodic fast path: Jaccard >= 0.85 and quality >= 0.70 serve the stored answer, skipping planner and synthesizer.
        # Disabled when: episodic_replay is False (the unattended profile may read experience but not replay it), file_hint is set,
        # the question starts with ':', full_answer is empty, the cached episode acted or read something that changed
        # (core/replay_freshness.py), or local_critique_active.
        _fp_ep = self._last_best_similar_episode
        _fp_score = self._last_best_similar_score
        if (
            self.episodic_replay
            and not local_critique_active
            and not file_hint
            and not user_question.strip().startswith(":")
            and self._fast_path_allows_replay(_fp_ep, _fp_score)
        ):
            self.log.log(
                "episodic_fast_path",
                {
                    "episode_id": _fp_ep.id,
                    "similarity": round(_fp_score, 4),
                    "quality": round(_fp_ep.answer_quality_score, 4),
                    "answer_chars": len(_fp_ep.full_answer),
                },
            )
            # A replay produces no new evidence: unverified=1, not verified_chunks=1 (no self-reinforcing trust) and not 0/0 (an empty
            # chain scores 1.0); the source episode is named in source_labels so the copy stays traceable.
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=_fp_ep.full_answer,
                tools_used=[],
                source_labels=[f"memory:{_fp_ep.id}"],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
                # No verifier ran on a replay: this flag means "the verifier threw", not "no verification happened".
                verifier_failure=False,
            )
            if self.memory is not None:
                self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="episodic fast path — cached answer",
                    tools_used=[],
                    artifact_labels=[f"memory:{_fp_ep.id}"],
                    answer=_fp_ep.full_answer,
                )
            self._stream_on_token = None
            return _fp_ep.full_answer
        return None

    def _noted_replay(self, answer: str) -> str:
        """The served stored answer plus one line saying it came from memory."""
        episode = self._last_best_similar_episode
        return with_replay_note(answer, replay_note(episode)) if episode is not None else answer

    def _multi_file_refusal(
        self,
        multi_file: dict[str, Any],
        *,
        user_question: str,
        file_hint: str | None,
        goal: Any,
    ) -> str | None:
        """Refusal of multi-file analysis if one was issued, else None."""
        if multi_file["kind"] == "refusal":
            answer = str(multi_file["message"])
            self.log.log("multi_file_review_refused", multi_file)
            self.log.log(
                "respond",
                {
                    "chars": len(answer),
                    "sources": [f"file:{file_hint}"] if file_hint else [],
                    "redactions": len(self._cycle_findings or []),
                    "attempts_used": 0,
                    "replan_exhausted": False,
                },
            )
            if self.memory is not None:
                turn = self.memory.record_turn(
                    question=user_question,
                    planner_reasoning="kernel multi-file review refusal",
                    tools_used=[],
                    artifact_labels=[],
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
            self._record_experience_memory(
                goal_description=goal.description,
                question=user_question,
                answer=answer,
                tools_used=[],
                source_labels=[f"file:{file_hint}"] if file_hint else [],
                verified_chunks=0,
                unverified_chunks=1,
                replan_exhausted=False,
                # The refusal returns before planning, so verification never started (not a crash).
                verifier_failure=False,
            )
            self._stream_on_token = None
            return answer
        return None



    def _check_clarification(self, user_question: str) -> ClarificationResult:
        """Run the Clarification Policy (§3): heuristic, no LLM."""
        from core.clarification_policy import check_clarification
        return check_clarification(user_question)

    def _check_operational_domain(self, user_question: str) -> DomainResult:
        """Run the Operational Design Domain gate (§7): pure, no LLM."""
        from core.operational_domain import check_operational_domain
        return check_operational_domain(user_question)
