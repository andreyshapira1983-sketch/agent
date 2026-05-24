"""
brain/explainer.py — Explainability & Justification

The Brain must be able to explain every decision it makes.
Pure logic — no LLM calls, no I/O, no async.

Why this matters:
    1. Human Approval gate — user sees WHY before approving
    2. Audit log — every action has a traceable justification
    3. Debugging — engineers can see how Brain reasoned
    4. Trust — agent that can explain itself is safer

What it produces per decision:
    - summary       — one-sentence human-readable explanation
    - reasoning_chain — step-by-step breakdown of how Brain got here
    - risk_level    — LOW / MEDIUM / HIGH / CRITICAL
    - factors       — dict of contributing factors and their effect
    - recommended   — what Brain recommends the human do (if approval needed)

Risk model:
    CRITICAL  — stop / irreversible destructive actions
    HIGH      — tool_call + low confidence OR unknown context
    MEDIUM    — tool_call with normal confidence
    LOW       — respond / clarify / wait

Usage:
    explainer = Explainer()
    explanation = explainer.explain(
        result=think_result,
        context=context_dict,
        uncertainty=uncertainty_result,   # optional
    )
    print(explanation.summary)
    print(explanation.for_human_approval())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import ThinkResult
    from .uncertainty import UncertaintyResult

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Risk levels
# ------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"

    def emoji(self) -> str:
        return {
            "low":      "✅",
            "medium":   "⚠️",
            "high":     "🔴",
            "critical": "🚨",
        }[self.value]


# ------------------------------------------------------------------
# Explanation dataclass
# ------------------------------------------------------------------

@dataclass
class Explanation:
    """
    Full explanation of a Brain decision.
    Produced by Explainer.explain().
    """
    action: str
    summary: str
    reasoning_chain: list[str]
    risk_level: RiskLevel
    factors: dict[str, str]
    recommended: str                       # Instruction for human reviewer
    confidence: float
    needs_approval: bool
    raw_reasoning: str = ""               # Original reasoning from LLM/Brain
    uncertainty_signals: dict[str, float] = field(default_factory=dict)

    def for_human_approval(self) -> str:
        """
        Formatted string shown to user when Human Approval is needed.
        Designed to be clear, concise, and actionable.
        """
        lines = [
            f"{self.risk_level.emoji()} Action: {self.action.upper()}",
            f"Summary: {self.summary}",
            "",
            "Reasoning:",
        ]
        for i, step in enumerate(self.reasoning_chain, 1):
            lines.append(f"  {i}. {step}")

        if self.factors:
            lines.append("")
            lines.append("Contributing factors:")
            for factor, effect in self.factors.items():
                lines.append(f"  • {factor}: {effect}")

        lines.append("")
        lines.append(f"Recommendation: {self.recommended}")
        lines.append(f"Confidence: {self.confidence:.0%}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "action":            self.action,
            "summary":           self.summary,
            "reasoning_chain":   self.reasoning_chain,
            "risk_level":        self.risk_level.value,
            "factors":           self.factors,
            "recommended":       self.recommended,
            "confidence":        round(self.confidence, 3),
            "needs_approval":    self.needs_approval,
            "raw_reasoning":     self.raw_reasoning,
        }


# ------------------------------------------------------------------
# Explainer
# ------------------------------------------------------------------

class Explainer:
    """
    Produces human-readable explanations for Brain decisions.

    Stateless — can be shared across sessions.
    Call explain() once per ThinkResult.
    """

    def explain(
        self,
        result: "ThinkResult",
        context: dict[str, Any],
        uncertainty: "UncertaintyResult | None" = None,
    ) -> Explanation:
        """
        Produce a full explanation for a Brain decision.

        Args:
            result:      The ThinkResult from Brain.think()
            context:     The context dict used for this decision
            uncertainty: Optional UncertaintyResult for signal breakdown

        Returns:
            Explanation with summary, reasoning chain, risk level, and factors
        """
        action      = result.action
        confidence  = result.confidence
        reasoning   = getattr(result, "reasoning", "") or ""
        needs_approval = getattr(result, "needs_human_approval", False)

        risk = self._assess_risk(action, confidence, context, uncertainty)
        chain = self._build_reasoning_chain(result, context, uncertainty)
        factors = self._extract_factors(result, context, uncertainty)
        summary = self._build_summary(action, confidence, risk, context)
        recommended = self._build_recommendation(action, risk, needs_approval)

        explanation = Explanation(
            action=action,
            summary=summary,
            reasoning_chain=chain,
            risk_level=risk,
            factors=factors,
            recommended=recommended,
            confidence=confidence,
            needs_approval=needs_approval,
            raw_reasoning=reasoning,
            uncertainty_signals=uncertainty.signals if uncertainty else {},
        )

        logger.debug(
            "[Explainer] action=%s risk=%s confidence=%.2f needs_approval=%s",
            action, risk.value, confidence, needs_approval,
        )

        return explanation

    # ------------------------------------------------------------------
    # Risk assessment
    # ------------------------------------------------------------------

    def _assess_risk(
        self,
        action: str,
        confidence: float,
        context: dict[str, Any],
        uncertainty: "UncertaintyResult | None",  # reserved for signal-based risk in future
    ) -> RiskLevel:
        """Determine risk level based on action type, confidence, and context."""
        _ = uncertainty  # reserved: e.g. extra HIGH risk when calibration is low
        # Stop is always critical — it terminates the agent
        if action == "stop":
            return RiskLevel.CRITICAL

        # Tool calls carry execution risk
        if action == "tool_call":
            if confidence < 0.5:
                return RiskLevel.HIGH
            if not context.get("goals"):
                return RiskLevel.HIGH   # No goal = unclear intent
            return RiskLevel.MEDIUM

        # Wait and clarify are safe
        if action in {"wait", "clarify"}:
            return RiskLevel.LOW

        # Respond: low risk unless confidence is very low
        if action == "respond":
            if confidence < 0.4:
                return RiskLevel.MEDIUM
            return RiskLevel.LOW

        # Unknown action — treat as high risk
        return RiskLevel.HIGH

    # ------------------------------------------------------------------
    # Reasoning chain
    # ------------------------------------------------------------------

    def _build_reasoning_chain(
        self,
        result: "ThinkResult",
        context: dict[str, Any],
        uncertainty: "UncertaintyResult | None",
    ) -> list[str]:
        chain: list[str] = []

        # What input Brain received
        raw_input = context.get("input", "")
        if raw_input:
            preview = raw_input[:80] + "..." if len(raw_input) > 80 else raw_input
            chain.append(f'Brain received input: "{preview}"')

        # Goals
        goals = context.get("goals", [])
        if goals:
            goal_texts = [g.get("text", "") for g in goals[:3]]
            chain.append(f"Active goals: {', '.join(goal_texts)}")
        else:
            chain.append("No active goals — operating without explicit objective")

        # Context richness
        history_len = len(context.get("history", []))
        facts_len   = len(context.get("facts", []))
        chain.append(
            f"Context: {history_len} history message(s), {facts_len} relevant fact(s)"
        )

        # Uncertainty signals
        if uncertainty:
            chain.append(
                f"Calibrated confidence: {uncertainty.calibrated_confidence:.0%} "
                f"(threshold: {uncertainty.threshold_used:.0%})"
            )
            chain.append(uncertainty.reasoning)
        else:
            chain.append(f"LLM confidence: {result.confidence:.0%}")

        # Raw reasoning from LLM/Brain
        raw = getattr(result, "reasoning", "") or ""
        if raw:
            chain.append(f"Brain reasoning: {raw}")

        # Final decision
        chain.append(f"Decision: {result.action.upper()}")

        return chain

    # ------------------------------------------------------------------
    # Factor extraction
    # ------------------------------------------------------------------

    def _extract_factors(
        self,
        result: "ThinkResult",
        context: dict[str, Any],
        uncertainty: "UncertaintyResult | None",
    ) -> dict[str, str]:
        factors: dict[str, str] = {}

        confidence = result.confidence
        if confidence >= 0.8:
            factors["confidence"] = f"High ({confidence:.0%}) — Brain is certain"
        elif confidence >= 0.6:
            factors["confidence"] = f"Moderate ({confidence:.0%}) — proceeding carefully"
        else:
            factors["confidence"] = f"Low ({confidence:.0%}) — high uncertainty"

        goals = context.get("goals", [])
        if goals:
            factors["goal_alignment"] = f"{len(goals)} active goal(s) provide direction"
        else:
            factors["goal_alignment"] = "No goals set — action may lack focus"

        history = context.get("history", [])
        if len(history) >= 3:
            factors["context_depth"] = f"{len(history)} messages — strong conversation context"
        elif history:
            factors["context_depth"] = f"{len(history)} message(s) — limited context"
        else:
            factors["context_depth"] = "No history — cold start"

        if uncertainty:
            signals = uncertainty.signals
            ctx_q = signals.get("context_quality", 0.0)
            if ctx_q > 0.6:
                factors["context_quality"] = "Rich context improves reliability"
            elif ctx_q < 0.3:
                factors["context_quality"] = "Sparse context increases uncertainty"

        if getattr(result, "needs_human_approval", False):
            factors["approval_required"] = "Action flagged for human review"

        return factors

    # ------------------------------------------------------------------
    # Summary and recommendation
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        action: str,
        confidence: float,
        risk: RiskLevel,
        context: dict[str, Any],
    ) -> str:
        goal_text = ""
        goals = context.get("goals", [])
        if goals:
            goal_text = f" toward goal '{goals[0].get('text', '')}'"

        summaries = {
            "respond":   f"Brain will respond to user{goal_text} "
                         f"(confidence: {confidence:.0%}, risk: {risk.value})",
            "tool_call": f"Brain requests tool execution{goal_text} "
                         f"(confidence: {confidence:.0%}, risk: {risk.value})",
            "wait":      f"Brain is pausing — insufficient confidence or ambiguous input "
                         f"(confidence: {confidence:.0%})",
            "clarify":   f"Brain needs clarification before acting "
                         f"(confidence: {confidence:.0%})",
            "stop":      f"Brain is requesting shutdown "
                         f"(risk: {risk.value})",
        }
        return summaries.get(
            action,
            f"Brain will perform '{action}' (confidence: {confidence:.0%}, risk: {risk.value})",
        )

    def _build_recommendation(
        self,
        action: str,  # noqa: ARG002 — reserved for action-specific recommendations
        risk: RiskLevel,
        needs_approval: bool,
    ) -> str:
        _ = action  # reserved for action-specific wording in future
        if not needs_approval:
            return "No approval required — Brain will proceed automatically."

        recommendations = {
            RiskLevel.CRITICAL: (
                "CRITICAL action — review carefully before approving. "
                "This cannot be undone."
            ),
            RiskLevel.HIGH: (
                "High-risk action — verify intent and scope before approving."
            ),
            RiskLevel.MEDIUM: (
                "Moderate risk — confirm this is the intended action."
            ),
            RiskLevel.LOW: (
                "Low risk — approve or cancel."
            ),
        }
        return recommendations[risk]
