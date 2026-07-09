#!/usr/bin/env python3
"""Generate docs/AGENT_ANATOMY.md as a grouped module index.

Groups the flat core/*.py modules under the architecture sections (see
"архитектура автономного Агента.txt" / AGENT_DOCTRINE) so the anatomy map is
navigable. Modules physically stay in core/ (paths are semantic data elsewhere),
so every row is still `core/<name>` and the read-only drift check keeps working.

Run:  python scripts/gen_anatomy.py
"""
from __future__ import annotations

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")

# Ordered logical groups. Each module name may appear in exactly one group.
GROUPS: list[tuple[str, str, list[str]]] = [
    ("Interface & Interaction (§1)", "Operator-facing I/O, intent routing, output shaping.", [
        "operator_intent", "output_policy", "user_profile", "truth_hype_filter",
        "alert_ack",
    ]),
    ("Perception & Adversarial Defense (§2)", "Input handling and injection/exfiltration defense.", [
        "injection_guard", "redaction", "data_classifier", "dlp", "secret_scanner",
    ]),
    ("Cognitive Core & Agent Cycle (§3)", "Planning, verification, clarification, control loop.", [
        "loop", "planner", "verifier", "replan", "reflection", "clarification_gate",
        "clarification_policy", "assumption_registry", "confidence_gate", "confidence_vector",
        "reasoning_action_check", "best_next_action", "task_complexity", "low_evidence_policy",
        "subsystem_disagreement", "strategy_router", "role_router", "prompt_registry",
        "compactor",
    ]),
    ("Memory & Knowledge Governance (§4)", "Working/persistent memory, hygiene, ingestion, evidence.", [
        "memory", "persistent_memory", "smart_memory", "memory_policy", "memory_echo_antibody",
        "hygiene", "episodic_hygiene", "knowledge_use_policy", "knowledge_pipeline",
        "ingestion", "structured_facts", "evidence", "evidence_budget", "conflict_review",
        "source_registry", "source_registry_store", "source_library", "source_ranker",
        "source_connectors",
    ]),
    ("Tools, Actions & Execution (§5)", "Effect gateways, receipts, compensation, VCS safety.", [
        "actuation_gateway", "gateway_consult", "tool_receipts", "receipt_consumer",
        "compensation", "safe_vcs", "supply_chain",
    ]),
    ("Runtime, State & Orchestration (§6)", "Autonomous loop, scheduling, budgets, state durability.", [
        "autonomous_runtime", "scheduler", "campaign", "work_session", "task_queue",
        "checkpoint", "circuit_breaker", "termination_guard", "step_repetition",
        "rate_limiter", "budget_governor", "budget_ledger", "budget_kill_switch",
        "state_integrity", "state_store_drill", "file_lock",
        "backlog_selector", "backlog_signals", "backlog_target_mapper",
    ]),
    ("Security, Policy & Autonomy Governance (§7)", "Policy gate, approvals, escalation, domain limits.", [
        "policy", "governance", "approval", "approval_inbox", "approval_triage",
        "deep_escalation", "operational_domain", "incident", "capability_request",
    ]),
    ("Evaluation & Monitoring (§8)", "Logging, identifiers, architecture auditing.", [
        "logger", "ids", "architecture_audit",
    ]),
    ("Learning & Self-Improvement (§9)", "Reflection-driven repair, self-build, value gating.", [
        "self_repair", "repair_proposal", "self_apply_bridge", "self_apply_lane",
        "self_build_producer", "self_build_supervisor", "learning_planner",
        "value_review", "proposal_value_gate", "release_hygiene",
    ]),
    ("Model Management (§6 / §12)", "Model discovery, routing, usage accounting.", [
        "model_catalog", "model_discovery", "model_router", "model_usage",
        "model_registry_audit",
    ]),
    ("Multi-Agent / Subagents (§6)", "Subagent proposals, registry, execution, teams.", [
        "subagent_memory_scope", "subagent_registry", "subagent_runner",
        "team_executor", "team_plan",
    ]),
    ("Cross-Cutting: Data Models & LLM (§12)", "Core data models and the LLM client wrapper.", [
        "models", "llm",
    ]),
]


def _first_doc_line(stem: str) -> str:
    path = os.path.join(CORE, f"{stem}.py")
    try:
        doc = ast.get_docstring(ast.parse(open(path, encoding="utf-8").read())) or ""
    except Exception:
        doc = ""
    line = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    return line.replace("|", "\\|")


def _actual_modules() -> set[str]:
    return {
        e[:-3] for e in os.listdir(CORE)
        if e.endswith(".py") and e != "__init__.py"
    }


def main() -> int:
    actual = _actual_modules()
    grouped: list[str] = []
    seen: set[str] = set()
    for _title, _desc, mods in GROUPS:
        for m in mods:
            if m in seen:
                raise SystemExit(f"module listed twice in GROUPS: {m}")
            seen.add(m)
            grouped.append(m)

    missing = sorted(actual - seen)
    unknown = sorted(seen - actual)
    if missing:
        raise SystemExit(f"modules in core/ not grouped: {missing}")
    if unknown:
        raise SystemExit(f"grouped names with no core/ module: {unknown}")

    out: list[str] = []
    out.append("# Agent Anatomy")
    out.append("")
    out.append("Grouped module index for the `core/` package, organized by the")
    out.append("architecture sections (§1–§12). Modules physically live flat in `core/`")
    out.append("— their paths are used as semantic identifiers elsewhere (planner")
    out.append("self-build targets, locators, audits), so this map groups them")
    out.append("*logically* without moving files.")
    out.append("")
    out.append("Kept in sync with the codebase by `scripts/agent_anatomy_check.py`")
    out.append("(read-only drift check, TD-029). Regenerate with")
    out.append("`python scripts/gen_anatomy.py` whenever a module is added or removed.")
    out.append("")
    out.append(f"_Total: {len(actual)} modules across {len(GROUPS)} groups._")
    out.append("")
    for title, desc, mods in GROUPS:
        out.append(f"## {title}")
        out.append("")
        out.append(f"_{desc}_")
        out.append("")
        out.append("| Module | Purpose |")
        out.append("| ------ | ------- |")
        for m in mods:
            out.append(f"| `core/{m}` | {_first_doc_line(m)} |")
        out.append("")

    doc_dir = os.path.join(ROOT, "docs")
    os.makedirs(doc_dir, exist_ok=True)
    with open(os.path.join(doc_dir, "AGENT_ANATOMY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))
    print(f"Wrote docs/AGENT_ANATOMY.md: {len(actual)} modules, {len(GROUPS)} groups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
