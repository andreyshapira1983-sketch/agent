"""
brain/policy.py — PolicyEngine: the single safety predicate.

Every potentially-consequential action the agent takes should be funneled
through PolicyEngine.evaluate(...) at some point in its lifecycle. The
engine answers exactly one question:

    Given (kind, target, attributes, autonomy), should this action
    be ALLOWED, REQUIRE_APPROVAL, or DENIED?

Design principles
─────────────────
1. **One verdict, three outcomes.** No middle ground, no boolean flags
   sprinkled across the codebase. Allow / require_approval / deny is the
   complete vocabulary.

2. **Stricter wins.** When multiple rules match the same action, the
   strictest verdict (DENY > REQUIRE_APPROVAL > ALLOW) is returned.
   Custom DENY rules cannot be silently overridden by lax defaults.

3. **Open by default.** No matching rule = ALLOW. Operators express
   policy by *adding* rules, not by enumerating every permitted action.

4. **Stateless evaluation.** PolicyEngine does not remember past
   verdicts. The same input produces the same output (assuming the same
   rules). Auditing is the caller's responsibility — and a clean audit
   log layer is on the roadmap.

5. **Reusable across surfaces.** The same engine is consulted by:
       - Brain.think()   when emitting a tool_call ThinkResult
       - ToolExecutor    just before invoking the tool
       - MemoryInterface when writing PII-class data
       - LLMAdapter      when checking budget
   Only the first is wired today; the rest follow without API changes.

Default rules (v1)
──────────────────
    explicit_approval_hint           hint=True & autonomy<5  → REQUIRE_APPROVAL
    destructive_at_low_autonomy      destructive=True & autonomy<3 → REQUIRE_APPROVAL
    high_risk_requires_approval      risk_class="high"       → REQUIRE_APPROVAL
    autonomy_5_auto_allow            autonomy≥5 & risk≠high  → ALLOW

Use a `PolicyEngine([])` to start from a blank slate. Use
`PolicyEngine()` to get the defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Decision
# ══════════════════════════════════════════════════════════════════════

class PolicyDecision(str, Enum):
    """The three possible verdicts. String-valued for clean serialisation."""

    ALLOW            = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY             = "deny"

    @property
    def strictness(self) -> int:
        """Higher = stricter. ALLOW=0, REQUIRE_APPROVAL=1, DENY=2."""
        return _STRICTNESS[self]

    @classmethod
    def stricter(cls, a: "PolicyDecision", b: "PolicyDecision") -> "PolicyDecision":
        return a if a.strictness >= b.strictness else b


_STRICTNESS: dict[PolicyDecision, int] = {
    PolicyDecision.ALLOW:            0,
    PolicyDecision.REQUIRE_APPROVAL: 1,
    PolicyDecision.DENY:             2,
}


# ══════════════════════════════════════════════════════════════════════
# Action request — what the policy sees
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ActionRequest:
    """
    A single action the agent wants to take.

    `kind` is the verb (tool_call / memory_write / llm_call). `target`
    identifies the object (tool name / namespace / model id). Everything
    else is metadata the rules can match against.

    Frozen so rules can't accidentally mutate it. Pass metadata via
    `metadata=` for anything custom that rules want to read.
    """

    kind:                    str
    target:                  str
    is_destructive:          bool = False
    requires_approval_hint:  bool = False    # caller's hint — Brain sets this from Interpreter
    risk_class:              str  = "low"    # low | medium | high
    autonomy_level:          int  = 2        # 0..5
    profession_id:           str | None = None
    metadata:                dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════
# Verdict
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PolicyVerdict:
    """The engine's answer."""

    decision: PolicyDecision
    rule_id:  str
    reason:   str

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOW

    @property
    def requires_approval(self) -> bool:
        return self.decision == PolicyDecision.REQUIRE_APPROVAL

    @property
    def denied(self) -> bool:
        return self.decision == PolicyDecision.DENY

    def to_dict(self) -> dict[str, str]:
        return {
            "decision": self.decision.value,
            "rule_id":  self.rule_id,
            "reason":   self.reason,
        }


# ══════════════════════════════════════════════════════════════════════
# Rule
# ══════════════════════════════════════════════════════════════════════

PredicateFn = Callable[[ActionRequest], bool]


@dataclass
class PolicyRule:
    """One rule: a predicate plus the verdict it produces when matched."""

    id:          str
    description: str
    predicate:   PredicateFn
    verdict:     PolicyDecision
    reason:      str
    priority:    int = 0   # tie-breaker among same-verdict matches; higher wins


# ══════════════════════════════════════════════════════════════════════
# Engine
# ══════════════════════════════════════════════════════════════════════

class PolicyEngine:
    """
    Evaluates ActionRequests against a list of PolicyRules.

    Construction:
        PolicyEngine()           # ships with default_rules()
        PolicyEngine([])         # no rules — everything is ALLOW
        PolicyEngine([rule1, ...])  # explicit ruleset

    Use add_rule() to extend a running engine — useful in tests and for
    profession-specific rules layered on top of defaults.
    """

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules: list[PolicyRule] = list(rules) if rules is not None else default_rules()
        logger.info("[PolicyEngine] initialised with %d rule(s)", len(self._rules))

    # ─────────────────────────────────────────────────────────────────

    def evaluate(self, action: ActionRequest) -> PolicyVerdict:
        """
        Return the final verdict for `action`. Never raises — a rule that
        throws is logged and skipped, not propagated.
        """
        matched: list[PolicyRule] = []
        for rule in self._rules:
            try:
                if rule.predicate(action):
                    matched.append(rule)
            except Exception as exc:  # noqa: BLE001 — rule code is user-supplied
                logger.warning(
                    "[PolicyEngine] rule '%s' raised: %s — skipping",
                    rule.id, exc,
                )

        if not matched:
            return PolicyVerdict(
                decision=PolicyDecision.ALLOW,
                rule_id="default_allow",
                reason="No matching policy rule",
            )

        strictest = matched[0].verdict
        for rule in matched[1:]:
            strictest = PolicyDecision.stricter(strictest, rule.verdict)

        winning = [r for r in matched if r.verdict == strictest]
        winner = max(winning, key=lambda r: r.priority)
        verdict = PolicyVerdict(
            decision=strictest,
            rule_id=winner.id,
            reason=winner.reason,
        )
        logger.debug(
            "[PolicyEngine] %s/%s → %s (rule=%s)",
            action.kind, action.target, verdict.decision.value, verdict.rule_id,
        )
        return verdict

    # ─────────────────────────────────────────────────────────────────

    def add_rule(self, rule: PolicyRule) -> None:
        """Append a rule. Later rules don't override earlier ones; the
        engine always considers all matches and takes the strictest."""
        self._rules.append(rule)
        logger.info("[PolicyEngine] added rule '%s'", rule.id)

    def rules(self) -> list[PolicyRule]:
        """Read-only snapshot of current rules."""
        return list(self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    def __repr__(self) -> str:
        return f"PolicyEngine(rules={[r.id for r in self._rules]})"


# ══════════════════════════════════════════════════════════════════════
# Default rules
# ══════════════════════════════════════════════════════════════════════

def default_rules() -> list[PolicyRule]:
    """
    The conservative starting point.

    Semantics:
        * At autonomy ≥ 5 with risk_class ≠ "high", any tool call is
          auto-approved — this is what "full autonomy" actually means.
        * At any autonomy below 5, an explicit approval hint from the
          Interpreter is honoured.
        * Destructive tools below autonomy 3 always need approval.
        * High-risk professions always need approval (regardless of
          autonomy) — but never DENY by default, because a human can
          still approve them manually.
    """
    return [
        PolicyRule(
            id="explicit_approval_hint",
            description="Caller's hint is honoured below full autonomy.",
            predicate=lambda a: a.requires_approval_hint and a.autonomy_level < 5,
            verdict=PolicyDecision.REQUIRE_APPROVAL,
            reason="Caller flagged this action as requiring approval",
            priority=10,
        ),
        PolicyRule(
            id="destructive_at_low_autonomy",
            description="Destructive tool_calls below autonomy 3 need approval.",
            predicate=lambda a: (
                a.kind == "tool_call"
                and a.is_destructive
                and a.autonomy_level < 3
            ),
            verdict=PolicyDecision.REQUIRE_APPROVAL,
            reason="Destructive tool at autonomy level < 3",
            priority=50,
        ),
        PolicyRule(
            id="high_risk_requires_approval",
            description="High-risk profession always requires approval.",
            predicate=lambda a: a.kind == "tool_call" and a.risk_class == "high",
            verdict=PolicyDecision.REQUIRE_APPROVAL,
            reason="High-risk profession class",
            priority=60,
        ),
        PolicyRule(
            id="autonomy_5_auto_allow",
            description="Autonomy 5 auto-allows safe actions.",
            predicate=lambda a: a.autonomy_level >= 5 and a.risk_class != "high",
            verdict=PolicyDecision.ALLOW,
            reason="Autonomy level 5 with non-high-risk action",
            priority=100,
        ),
    ]


# ══════════════════════════════════════════════════════════════════════
# Convenience builders for custom rules
# ══════════════════════════════════════════════════════════════════════

def deny_tool(tool_name: str, reason: str | None = None) -> PolicyRule:
    """Build a rule that hard-denies a specific tool by name."""
    return PolicyRule(
        id=f"deny_tool_{tool_name}",
        description=f"Hard-deny tool '{tool_name}'.",
        predicate=lambda a: a.kind == "tool_call" and a.target == tool_name,
        verdict=PolicyDecision.DENY,
        reason=reason or f"Tool '{tool_name}' is in the deny-list",
        priority=200,
    )


def require_approval_for_tool(tool_name: str, reason: str | None = None) -> PolicyRule:
    """Build a rule that forces approval for a specific tool."""
    return PolicyRule(
        id=f"approve_tool_{tool_name}",
        description=f"Approval required for tool '{tool_name}'.",
        predicate=lambda a: a.kind == "tool_call" and a.target == tool_name,
        verdict=PolicyDecision.REQUIRE_APPROVAL,
        reason=reason or f"Tool '{tool_name}' is in the approval list",
        priority=70,
    )
