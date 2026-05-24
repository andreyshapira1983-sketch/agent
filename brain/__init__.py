"""
brain/ — Cognitive Core of the Autonomous Agent

Architecture principle:
    The Brain owns the LLM. The LLM is a tool the Brain uses.
    The LLM does NOT drive decisions — the Brain does.

Flow:
    Input → ContextBuilder → Brain.think() → Interpreter → Action
                                  ↕
                             Goal Stack
                                  ↕
                          LLMInterface (tool)
"""

from .audit import AuditEntry, AuditLog, GENESIS_HASH, IntegrityReport
from .budget import BudgetController, BudgetExceeded, BudgetLimits, BudgetUsage
from .core import Brain, ThinkResult
from .brain_loop import BrainLoop, InputMessage
from .explainer import Explainer, Explanation, RiskLevel
from .learning import LearningRecord, LearningReport, learn_from_outcome
from .memory.retrieval_policy import DEFAULT_POLICY, RetrievalPolicy
from .metrics import MetricsService, MetricsSnapshot
from .policy import (
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    PolicyVerdict,
    default_rules,
    deny_tool,
    require_approval_for_tool,
)
from .privacy import PIIFilter, PIIRedactor
from .secrets import Secret, SecretNotFoundError, SecretsVault
from .uncertainty import UncertaintyEstimator
from .planner import Plan, PlanCheckpointStore, PlanStatus, Planner, Step, StepStatus
from .status_server import StatusServer

__all__ = [
    "ActionRequest",
    "AuditEntry", "AuditLog", "GENESIS_HASH", "IntegrityReport",
    "Brain", "BrainLoop", "InputMessage",
    "BudgetController", "BudgetExceeded", "BudgetLimits", "BudgetUsage",
    "DEFAULT_POLICY", "RetrievalPolicy",
    "Explainer", "Explanation", "RiskLevel",
    "LearningRecord", "LearningReport",
    "MetricsService", "MetricsSnapshot",
    "PIIFilter", "PIIRedactor",
    "Plan", "PlanCheckpointStore", "PlanStatus", "Planner", "Step", "StepStatus",
    "PolicyDecision", "PolicyEngine", "PolicyRule", "PolicyVerdict",
    "Secret", "SecretNotFoundError", "SecretsVault",
    "StatusServer",
    "ThinkResult",
    "UncertaintyEstimator",
    "default_rules", "deny_tool", "require_approval_for_tool",
    "learn_from_outcome",
]
