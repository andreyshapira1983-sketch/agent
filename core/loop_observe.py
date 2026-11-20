"""Observe, interpret and model-selection phases, cut out of core/loop.py."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.data_classifier import DataClass, classify
from core.model_router import ModelRole
from core.models import Goal, Observation
from core.redaction import collect_pii_findings, scan


class AgentLoopObserve:
    """Observe and Interpret phases: what was asked and what to answer it with."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        log: Any
        role_router: Any
        model_router: Any
        last_role_context: Any
        # As in AgentLoop.__init__: a list, or None before the loop starts.
        _cycle_findings: list[dict[str, Any]] | None

        # Declared as a callable attribute: an empty-body stub reads to analyzers as "function without return" (false E1111).

    def _observe_and_route(
        self,
        user_question: str,
        *,
        file_hint: str | None,
        # Any, not bool: the host passes an optional core.deep_escalation.OperatorEscalation that the model router reads.
        deep_escalation: Any,
        _cp: Any,
    ) -> tuple[Goal, Any, Any]:
        """The turn's goal and two models for it (planner, synthesizer)."""
        observation = Observation(
            source="cli",
            modality="text",
            content={
                "question": user_question,
                "file_hint": file_hint,
            },
            provenance="user",
        )
        self.log.log("observe", observation)
        _cp.save_observe(question=user_question, file_hint=file_hint)

        # Classify the question itself: a pasted secret must be caught BEFORE the LLM sees it.
        q_cls = classify(user_question, source="cli")
        self.log.log(
            "data_classified",
            {
                "label": "user_question",
                "class": q_cls.cls.value,
                "source": q_cls.source,
                "reasons": q_cls.reasons,
            },
        )
        if q_cls.cls == DataClass.SECRET:
            q_findings = scan(user_question)
            kinds = sorted({f.kind for f in q_findings})
            self.log.log(
                "secret_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )
        elif q_cls.cls == DataClass.SENSITIVE:
            q_findings = collect_pii_findings(user_question)
            kinds = sorted({f"pii-{f.kind}" for f in q_findings})
            self.log.log(
                "sensitive_detected",
                {
                    "label": "user_question",
                    "kinds": kinds,
                    "count": len(q_findings),
                    "surface": "user_input",
                },
            )
            self._cycle_findings.append(
                {"label": "user_question", "kinds": kinds, "count": len(q_findings)}
            )

        self.last_role_context = self.role_router.route(user_question)
        self.log.log("role_route", self.last_role_context.to_log_payload())

        # Adaptive model routing by task complexity (LIGHT cheap, DEEP strongest, STANDARD default). The RoleRouter verdict is forwarded
        # so a repair/programming task is never downgraded to LIGHT. A TypeError here is a call-signature defect and must PROPAGATE;
        # every other failure falls back to the default LLM but is recorded.
        try:
            _task_role = getattr(self.last_role_context, "role", None)
            _task_planner_llm = self.model_router.for_task(
                ModelRole.PLANNER,
                user_question,
                escalation=deep_escalation,
                task_role=_task_role,
            )
            _task_synth_llm = self.model_router.for_task(
                ModelRole.SYNTHESIZER,
                user_question,
                escalation=deep_escalation,
                task_role=_task_role,
            )
            _planner_model = getattr(_task_planner_llm, "model", None)
            _synth_model = getattr(_task_synth_llm, "model", None)
            _route_reason = getattr(
                getattr(_task_planner_llm, "route", None), "reason", "default"
            )
            self.log.log(
                "adaptive_route",
                {
                    "question_chars": len(user_question),
                    "task_role": _task_role,
                    "planner_model": _planner_model,
                    "synth_model": _synth_model,
                    "route_reason": _route_reason,
                },
            )
        except TypeError:
            # Visible on purpose: see the routing comment above.
            raise
        except Exception as exc:  # noqa: BLE001
            _task_planner_llm = None
            _task_synth_llm = None
            self.log.log(
                "adaptive_route_error",
                {
                    "error": type(exc).__name__,
                    "detail": str(exc)[:200],
                    "question_chars": len(user_question),
                    "fallback": "default_llm",
                },
            )

        # 2. Interpret -> Goal
        goal = self._interpret(observation)
        self.log.log("interpret", goal)

        return goal, _task_planner_llm, _task_synth_llm

    def _interpret(self, observation: Observation) -> Goal:
        question = observation.content["question"]
        # The criterion about citing sources is conditional on evidence existing, so it is satisfiable on every turn.
        return Goal(
            description=f"Answer the question: {question}",
            success_criteria=(
                "A direct answer; claims backed by collected evidence cite "
                "their sources, claims without a collected source are "
                "presented as unverified — citations are never invented."
            ),
        )
