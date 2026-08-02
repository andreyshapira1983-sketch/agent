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
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")

# Ordered logical groups. Each module name may appear in exactly one group.
GROUPS: list[tuple[str, str, list[str]]] = [
    ("Interface & Interaction (§1)", "Operator-facing I/O, intent routing, output shaping.", [
        "operator_intent", "operator_intent_patterns", "intent_understanding",
        "file_request_intent",
        "lang_match", "output_policy", "user_profile", "truth_hype_filter",
        "alert_ack",
    ]),
    ("Perception & Adversarial Defense (§2)", "Input handling and injection/exfiltration defense.", [
        "injection_guard", "redaction", "data_classifier", "dlp", "secret_scanner",
    ]),
    ("Cognitive Core & Agent Cycle (§3)", "Planning, verification, clarification, control loop.", [
        "loop", "loop_helpers", "loop_methods", "loop_methods2",
        "planner", "step_sanitizer", "verifier", "verifier_core", "verifier_models", "verifier_patterns",
        "verifier_utils", "replan", "reflection", "clarification_gate",
        "clarification_policy", "instruction_conflict_gate", "directive_extractor",
        "assumption_registry", "referent_resolver",
        "evidence_support", "confidence_vector",
        "reasoning_action_check", "best_next_action", "task_complexity", "low_evidence_policy",
        "unsupported_claims", "subsystem_disagreement", "completion_marker",
        "completion_obligation", "response_draft", "synth_resilience",
        "strategy_router", "role_router", "prompt_registry",
        "compactor",
    ]),
    ("Memory & Knowledge Governance (§4)", "Working/persistent memory, hygiene, ingestion, evidence.", [
        "memory", "persistent_memory", "smart_memory", "memory_policy", "memory_echo_antibody",
        "hygiene", "episodic_hygiene", "knowledge_use_policy", "knowledge_pipeline",
        "ingestion", "ingestion_reports", "ingestion_utils",
        "structured_facts", "evidence", "evidence_classes", "evidence_budget",
        "conflict_review", "conflict_episode",
        "source_registry", "source_registry_store", "source_library", "source_ranker",
        "source_connectors",
    ]),
    ("Tools, Actions & Execution (§5)", "Effect gateways, receipts, compensation, VCS safety.", [
        "actuation_gateway", "gateway_consult", "tool_receipts", "receipt_consumer",
        "compensation", "safe_vcs", "supply_chain",
    ]),
    ("Runtime, State & Orchestration (§6)", "Autonomous loop, scheduling, budgets, state durability.", [
        "autonomous_runtime", "scheduler", "campaign", "campaign_io", "campaign_ledger",
        "campaign_types", "work_session", "task_queue", "task_lifecycle",
        "checkpoint", "circuit_breaker", "termination_guard", "step_repetition",
        "rate_limiter", "budget_governor", "budget_ledger", "budget_kill_switch",
        "run_context", "state_integrity", "state_store_drill", "file_lock",
        "heartbeat_io",
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
        "self_repair", "self_repair_models", "self_repair_utils", "repair_proposal",
        "self_apply_bridge", "self_apply_lane",
        "self_build_producer", "self_build_supervisor", "self_build_memory",
        "self_build_rules", "self_task_producer", "self_task_builder",
        "writer_completion",
        "self_improvement_issues", "incremental_splitter", "dependency_map",
        "learning_planner", "value_review", "proposal_value_gate", "release_hygiene",
    ]),
    ("Model Management (§6 / §12)", "Model discovery, routing, usage accounting.", [
        "model_catalog", "model_discovery", "model_router", "model_usage",
        "model_registry_audit",
    ]),
    ("Multi-Agent / Subagents (§6)", "Subagent proposals, registry, execution, teams.", [
        "subagent_contract", "subagent_contract_audit", "subagent_memory_scope",
        "subagent_registry", "subagent_runner", "team_executor", "team_plan",
    ]),
    ("Cross-Cutting: Data Models & LLM (§12)", "Core data models and the LLM client wrapper.", [
        "models", "llm",
    ]),
]


def _first_doc_line(stem: str) -> str:
    """First *sentence* of the module docstring, rejoined across wrapped lines.

    Reading `splitlines()[0]` used to cut the summary wherever the author had
    wrapped it, producing map rows that ended mid-clause ("... and the",
    "... by the"). The docstring's opening paragraph is the summary; a physical
    line break inside it carries no meaning.
    """
    path = os.path.join(CORE, f"{stem}.py")
    try:
        doc = ast.get_docstring(ast.parse(open(path, encoding="utf-8").read())) or ""
    except Exception:
        doc = ""
    if not doc.strip():
        return ""

    # The opening paragraph, unwrapped: everything before the first blank line.
    paragraph = " ".join(
        part.strip()
        for part in doc.strip().split("\n\n")[0].splitlines()
        if part.strip()
    )

    # Cut at the first sentence end, so a long paragraph does not fill the cell.
    # A dot only ends a sentence when a space follows and the next word starts a
    # new one, which keeps "e.g." and "core/loop.py" intact.
    match = re.search(r"(?<!\be\.g)(?<!\bi\.e)[.?!](?=\s+[^a-z]|\s*$)", paragraph)
    sentence = paragraph[: match.end()] if match else paragraph
    return sentence.strip().replace("|", "\\|")


def _actual_modules() -> set[str]:
    return {
        e[:-3] for e in os.listdir(CORE)
        if e.endswith(".py") and e != "__init__.py"
    }


def build_document() -> str:
    """Render the whole map as text. Pure apart from reading `core/`.

    Split out from :func:`main` so a test can compare the committed document
    against what the generator would write, without writing anything. Nothing
    enforced that equality before, which is exactly how `GROUPS` drifted 37
    modules behind `core/` while the map itself looked in sync — the drift check
    compares module *names*, and hand-edits kept those correct.

    Raises :class:`ValueError` on a bad `GROUPS`, never `SystemExit`: this is a
    library function now, and killing the caller's process is not its decision.
    `SystemExit` derives from `BaseException`, so an ordinary
    ``except Exception`` around it would not even catch it. :func:`main` turns
    the error into a message and exit code 1, exactly as before.
    """
    actual = _actual_modules()
    seen: set[str] = set()
    for _title, _desc, mods in GROUPS:
        for m in mods:
            if m in seen:
                raise ValueError(f"module listed twice in GROUPS: {m}")
            seen.add(m)

    missing = sorted(actual - seen)
    unknown = sorted(seen - actual)
    if missing:
        raise ValueError(f"modules in core/ not grouped: {missing}")
    if unknown:
        raise ValueError(f"grouped names with no core/ module: {unknown}")

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

    return "\n".join(out)


def main() -> int:
    try:
        text = build_document()
    except ValueError as exc:
        # Same operator-visible contract the old `raise SystemExit(msg)` had:
        # the message on stderr, exit code 1.
        print(exc, file=sys.stderr)
        return 1
    doc_dir = os.path.join(ROOT, "docs")
    os.makedirs(doc_dir, exist_ok=True)
    with open(os.path.join(doc_dir, "AGENT_ANATOMY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(
        f"Wrote docs/AGENT_ANATOMY.md: {len(_actual_modules())} modules, "
        f"{len(GROUPS)} groups."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
