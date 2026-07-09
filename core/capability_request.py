"""Autonomous capability request proposals.

This layer lets the agent notice that a goal needs a missing capability and
prepare a bounded, human-readable request before any connector, subagent, model
tier, memory write, or long-running mode is activated.

It is deliberately deterministic and read-only:
- no connector activation;
- no tool execution;
- no LLM call;
- approval is required by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from core.ids import new_id


CapabilityType = Literal[
    "telegram",
    "email",
    "upwork",
    "subagent",
    "web_monitor",
    "file_workspace_access",
    "long_work_session",
    "model_tier_upgrade",
    "persistent_memory_write",
    "unknown",
]

RiskLevel = Literal["low", "medium", "high"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CapabilityRequest:
    """A proposed capability boundary waiting for human approval."""

    goal: str
    capability_type: CapabilityType
    why_needed: str
    requested_access: str
    read_scope: tuple[str, ...]
    write_scope: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    budget_limits: dict[str, int]
    human_risk_summary: str
    fallback_if_denied: str
    risk_level: RiskLevel = "medium"
    approval_required: bool = True
    request_id: str = field(default_factory=lambda: new_id("capreq"))
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("capability request goal must be non-empty")
        if self.approval_required is not True:
            raise ValueError("capability requests require approval in this MVP")
        for key, value in self.budget_limits.items():
            if not isinstance(key, str) or not key:
                raise ValueError("budget limit keys must be non-empty strings")
            if not isinstance(value, int) or value < 0:
                raise ValueError("budget limit values must be non-negative integers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "goal": self.goal,
            "capability_type": self.capability_type,
            "why_needed": self.why_needed,
            "requested_access": self.requested_access,
            "read_scope": list(self.read_scope),
            "write_scope": list(self.write_scope),
            "forbidden_actions": list(self.forbidden_actions),
            "budget_limits": dict(self.budget_limits),
            "human_risk_summary": self.human_risk_summary,
            "fallback_if_denied": self.fallback_if_denied,
            "risk_level": self.risk_level,
            "approval_required": self.approval_required,
            "created_at": self.created_at,
        }

    def user_summary(self) -> str:
        lines = [
            "=== capability request ===",
            f"id: {self.request_id}",
            f"type: {self.capability_type}",
            f"goal: {self.goal}",
            f"why_needed: {self.why_needed}",
            f"requested_access: {self.requested_access}",
            f"risk: {self.risk_level}",
            "read_scope:",
        ]
        lines.extend(f"  - {item}" for item in self.read_scope)
        lines.append("write_scope:")
        lines.extend(f"  - {item}" for item in (self.write_scope or ("none",)))
        lines.append("forbidden_actions:")
        lines.extend(f"  - {item}" for item in self.forbidden_actions)
        lines.append("budget_limits:")
        lines.extend(f"  - {key}: {value}" for key, value in sorted(self.budget_limits.items()))
        lines.extend(
            [
                f"human_risk_summary: {self.human_risk_summary}",
                f"fallback_if_denied: {self.fallback_if_denied}",
                "approval_required: true",
            ]
        )
        return "\n".join(lines)


def detect_capability_type(goal: str) -> CapabilityType:
    """Classify the missing capability implied by a natural-language goal."""

    if not isinstance(goal, str):
        return "unknown"
    text = goal.casefold()
    if _has_any(text, ("telegram", "телеграм", "уведом", "сообщал мне", "сообщай мне", "когда нужно решение")):
        return "telegram"
    if _has_any(text, ("email", "e-mail", "почт", "письм", "gmail", "imap", "smtp")):
        return "email"
    if _has_any(text, ("upwork", "апворк", "freelance", "фриланс", "job", "ваканс", "клиент")):
        return "upwork"
    if _has_any(text, ("подагент", "subagent", "sub-agent", "агента для", "отдельный агент")):
        return "subagent"
    if _has_any(text, ("7 часов", "семь часов", "long work", "долго", "в фоне", "background", "постоянно работал")):
        return "long_work_session"
    if _has_any(text, ("дорог", "premium", "opus", "gpt-5", "model tier", "модель выше", "лучшую модель")):
        return "model_tier_upgrade"
    if _has_any(text, ("запомин", "память", "memory write", "учился", "сохранял знания")):
        return "persistent_memory_write"
    if _has_any(text, ("файл", "workspace", "папк", "доступ к проекту", "читать проект")):
        return "file_workspace_access"
    if _has_any(text, ("web", "сайт", "rss", "интернет", "страниц", "мониторь сайт", "следи за сайтом")):
        return "web_monitor"
    return "unknown"


def propose_capability_request(goal: str) -> CapabilityRequest:
    """Build a safe request for the capability implied by *goal*."""

    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("capability request goal must be non-empty")
    goal = goal.strip()
    capability_type = detect_capability_type(goal)
    spec = _CAPABILITY_SPECS.get(capability_type, _CAPABILITY_SPECS["unknown"])
    return CapabilityRequest(
        goal=goal,
        capability_type=capability_type,
        why_needed=spec["why_needed"],
        requested_access=spec["requested_access"],
        read_scope=tuple(spec["read_scope"]),
        write_scope=tuple(spec["write_scope"]),
        forbidden_actions=tuple(spec["forbidden_actions"]),
        budget_limits=dict(spec["budget_limits"]),
        human_risk_summary=spec["human_risk_summary"],
        fallback_if_denied=spec["fallback_if_denied"],
        risk_level=spec["risk_level"],
        approval_required=True,
    )


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


_CAPABILITY_SPECS: dict[str, dict[str, Any]] = {
    "telegram": {
        "why_needed": "The goal requires proactive owner notification or approval questions.",
        "requested_access": "Send notifications and approval questions to the owner's Telegram chat only.",
        "read_scope": ("approval inbox summary", "task status", "budget warnings"),
        "write_scope": ("owner notifications", "owner approval questions"),
        "forbidden_actions": (
            "message third parties",
            "join groups",
            "change Telegram settings",
            "send secrets",
        ),
        "budget_limits": {"max_messages_per_day": 10, "max_model_calls": 0, "max_cycles": 1},
        "human_risk_summary": "External messages can interrupt the owner or leak sensitive context if scoped badly.",
        "fallback_if_denied": "Keep approvals in the local inbox and wait for manual operator checks.",
        "risk_level": "medium",
    },
    "email": {
        "why_needed": "The goal requires monitoring or triaging incoming email.",
        "requested_access": "Read new email metadata and body; optionally prepare drafts for review.",
        "read_scope": ("new message subject", "sender", "timestamp", "message body"),
        "write_scope": ("draft replies only",),
        "forbidden_actions": (
            "send email without approval",
            "delete email",
            "archive email",
            "mark email as read without approval",
            "forward email to third parties",
        ),
        "budget_limits": {"max_emails_per_day": 20, "max_model_calls": 5, "max_cycles": 2},
        "human_risk_summary": "Email contains private data and any outbound action can affect real people.",
        "fallback_if_denied": "Ask the owner to paste selected emails or run local read-only checks manually.",
        "risk_level": "high",
    },
    "upwork": {
        "why_needed": "The goal requires recurring review of freelance opportunities.",
        "requested_access": "Read opportunity listings and the owner's skill/profile notes.",
        "read_scope": ("job title", "job description", "budget", "client metadata", "owner skill profile"),
        "write_scope": ("local opportunity report",),
        "forbidden_actions": (
            "send proposals",
            "contact clients",
            "accept contracts",
            "spend money",
            "change profile",
        ),
        "budget_limits": {"max_jobs_per_day": 10, "max_model_calls": 5, "max_web_fetches": 10},
        "human_risk_summary": "Freelance platforms involve money, reputation and client contact.",
        "fallback_if_denied": "Use pasted listings or a manually exported local file.",
        "risk_level": "high",
    },
    "subagent": {
        "why_needed": "The goal is broad enough to benefit from a bounded specialist role.",
        "requested_access": "Create a proposed subagent role with explicit memory, tool and budget scopes.",
        "read_scope": ("task goal", "project facts", "source registry summary"),
        "write_scope": ("proposal only",),
        "forbidden_actions": ("launch subagent without approval", "run tools without a contract"),
        "budget_limits": {"max_model_calls": 3, "max_cycles": 1, "max_cost_units": 5},
        "human_risk_summary": "Delegation can multiply model/tool usage unless scoped tightly.",
        "fallback_if_denied": "Keep the task in the main agent and produce a single-agent plan.",
        "risk_level": "medium",
    },
    "web_monitor": {
        "why_needed": "The goal requires checking changing web or RSS sources.",
        "requested_access": "Fetch public web/RSS pages inside egress and freshness policy.",
        "read_scope": ("public pages", "RSS entries", "HTTP metadata", "content hash"),
        "write_scope": ("source registry records", "local reports"),
        "forbidden_actions": ("log in to sites", "submit forms", "bypass paywalls", "download large binaries"),
        "budget_limits": {"max_web_fetches_per_day": 25, "max_model_calls": 5, "max_cycles": 2},
        "human_risk_summary": "Web monitoring can spend tokens and fetch untrusted content.",
        "fallback_if_denied": "Use existing local sources and ask for specific URLs.",
        "risk_level": "medium",
    },
    "file_workspace_access": {
        "why_needed": "The goal requires reading project files rather than relying on memory.",
        "requested_access": "Read explicit workspace-relative files or approved project source sets.",
        "read_scope": ("workspace files explicitly mentioned by the owner",),
        "write_scope": ("none",),
        "forbidden_actions": ("read outside workspace", "write files", "read secrets", "read directories without ingestion command"),
        "budget_limits": {"max_files_per_request": 10, "max_file_bytes": 1048576, "max_model_calls": 0},
        "human_risk_summary": "Project files can contain private data or secrets if the scope is too broad.",
        "fallback_if_denied": "Ask the owner to provide excerpts or use a single --file hint.",
        "risk_level": "medium",
    },
    "long_work_session": {
        "why_needed": "The goal requires repeated bounded work over time.",
        "requested_access": "Run dry-run work-session cycles with time, budget and circuit-breaker limits.",
        "read_scope": ("runtime status", "source registry", "task queue", "test results"),
        "write_scope": ("local reports", "approval requests"),
        "forbidden_actions": ("enable effects without approval", "ignore budget stop", "run indefinitely"),
        "budget_limits": {"max_minutes": 60, "max_cycles": 6, "max_model_calls": 10},
        "human_risk_summary": "Long sessions can consume model budget if limits are wrong.",
        "fallback_if_denied": "Run one short dry-run health pass and stop.",
        "risk_level": "medium",
    },
    "model_tier_upgrade": {
        "why_needed": "The goal appears to require a stronger or more expensive model tier.",
        "requested_access": "Temporarily route selected roles to a higher-quality model.",
        "read_scope": ("task prompt", "local evidence", "model usage ledger"),
        "write_scope": ("model usage ledger",),
        "forbidden_actions": ("use premium model for cheap summaries", "exceed budget limits", "change default model policy permanently"),
        "budget_limits": {"max_model_calls": 3, "max_cost_units": 15, "max_tokens": 30000},
        "human_risk_summary": "Premium model routing can quickly increase API cost.",
        "fallback_if_denied": "Use the current balanced/cost policy and mark confidence limits.",
        "risk_level": "medium",
    },
    "persistent_memory_write": {
        "why_needed": "The goal requires storing reusable verified knowledge.",
        "requested_access": "Write only verified, source-backed facts through memory policy.",
        "read_scope": ("source registry claims", "evidence chain", "existing memory records"),
        "write_scope": ("approved semantic/procedural memory records",),
        "forbidden_actions": ("store secrets", "store PII without purpose", "store unverified claims", "overwrite memory silently"),
        "budget_limits": {"max_records_per_run": 20, "max_model_calls": 2, "max_cycles": 1},
        "human_risk_summary": "Bad memory writes make future reasoning worse and can persist private data.",
        "fallback_if_denied": "Keep findings in the current session and do not promote to persistent memory.",
        "risk_level": "medium",
    },
    "unknown": {
        "why_needed": "The goal implies a missing capability, but the type is not clear enough.",
        "requested_access": "No access requested yet; ask the owner for a narrower capability boundary.",
        "read_scope": ("goal text only",),
        "write_scope": ("none",),
        "forbidden_actions": ("activate connectors", "launch subagents", "spend model budget", "write memory"),
        "budget_limits": {"max_model_calls": 0, "max_cycles": 1},
        "human_risk_summary": "Ambiguous capability requests can accidentally grant too much access.",
        "fallback_if_denied": "Return an implementation plan with no new capability.",
        "risk_level": "low",
    },
}
