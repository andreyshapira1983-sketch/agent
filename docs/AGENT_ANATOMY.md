# Agent Anatomy

Module index for the `core/` package. This document is the source of truth
for which modules exist in the cognitive/runtime core, and is kept in sync
with the codebase by `scripts/agent_anatomy_check.py` (read-only drift check,
TD-029).

Whenever a module is added to or removed from `core/`, update the table below
so the anatomy check stays green.

## Module index

| Module | Purpose |
| ------ | ------- |
| `core/actuation_gateway` | Actuation gateway — checked door for effectful actions (REPL, runtime, daemon). |
| `core/alert_ack` | Operator acknowledgement for advisory alerts — retire accepted signals. |
| `core/approval` | Approval Providers (§7 Security, Policy & Autonomy Governance — Human Approval). |
| `core/approval_inbox` | Approval inbox for autonomous runtime decisions. |
| `core/approval_triage` | Read-only triage for the approval inbox. |
| `core/architecture_audit` | Static architecture gap audit for the autonomous agent project. |
| `core/assumption_registry` | Assumption Registry — Layer 5 (Explicit Planning Assumptions). |
| `core/autonomous_runtime` | Autonomous runtime orchestrator. |
| `core/backlog_selector` | Grounded backlog selector for the self-build producer (TD-036, Phase 1). |
| `core/backlog_signals` | Read-only parsers for grounded self-build backlog signals (TD-036, Phase 1). |
| `core/backlog_target_mapper` | Deterministic mapper from abstract backlog items to concrete self-build targets. |
| `core/best_next_action` | Priority intelligence: choose the single most important next action. |
| `core/budget_governor` | Budget governor for autonomous runtime loops. |
| `core/budget_kill_switch` | Persistent budget kill-switch for autonomous / daemon execution (TD-022). |
| `core/budget_ledger` | Persistent budget windows for long-running autonomous work. |
| `core/campaign` | 24/48h autonomous work campaign engine. |
| `core/capability_request` | Autonomous capability request proposals. |
| `core/checkpoint` | §3.5 Checkpoint / Resume — durable mid-run state. |
| `core/circuit_breaker` | Circuit breaker for bounded autonomous runtime runs. |
| `core/clarification_gate` | Clarification Gate — режим переспроса (ask, don't build). |
| `core/clarification_policy` | Clarification Policy (§3 Cognitive Core — Clarification Policy). |
| `core/compactor` | Conversation history compaction (Anthropic 2025 — context engineering). |
| `core/compensation` | Compensation System (§5 Undo) — first introduced for MVP-11 shell_exec. |
| `core/confidence_gate` | Post-verifier confidence gate. |
| `core/confidence_vector` | Decompose answer confidence into a three-axis vector. |
| `core/conflict_review` | Operator-facing conflict review for the Source Registry. |
| `core/data_classifier` | Data Classifier (§7 Data Governance). |
| `core/deep_escalation` | Deep/Opus escalation gate — "Opus is an event, not a habit". |
| `core/dlp` | DLP helpers for sensitive personal data. |
| `core/episodic_hygiene` | Episodic memory hygiene — staleness scoring and pruning. |
| `core/evidence` | MVP-14.1 — Evidence + Provenance model. |
| `core/evidence_budget` | Evidence Budget — caps context sent to the synthesizer LLM. |
| `core/file_lock` | Small cross-platform file lock for JSONL state stores. |
| `core/gateway_consult` | Gateway hard-stop consult helpers (G5a). |
| `core/governance` | Governance modes for safe autonomous growth. |
| `core/hygiene` | Memory Hygiene (§4 Memory Governance — cleanup, dedup, expiry, summarise). |
| `core/ids` | Short unique identifiers for trace correlation. |
| `core/incident` | Incident Handling skeleton (§7 Security — Incident Handling / B-04 Safety). |
| `core/ingestion` | Controlled document/code ingestion. |
| `core/injection_guard` | Indirect Prompt Injection Defence (§2 Adversarial Defense). |
| `core/knowledge_pipeline` | Knowledge pipeline integration. |
| `core/knowledge_use_policy` | Contextual memory-use policy. |
| `core/learning_planner` | Learning planner. |
| `core/llm` | Thin LLM client wrapper. |
| `core/logger` | Structured JSONL logger for the agent loop. |
| `core/loop` | Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond. |
| `core/low_evidence_policy` | Low-evidence answer policy. |
| `core/memory` | Working Memory (§4 Memory & Knowledge Governance — short-term, session-scoped). |
| `core/memory_echo_antibody` | Memory Echo Antibody (A1) — refuse agent-auto memory that *echoes* itself. |
| `core/memory_policy` | Memory Write Policy + Memory Retrieval Policy (§4 + §12.4). |
| `core/model_catalog` | Dynamic Model Catalog — discovers available models from provider APIs. |
| `core/model_discovery` | Live Model Discovery + Provider Catalog diff — read-only / dry-run (TD-011/012). |
| `core/model_registry_audit` | Operator-facing audit for model registry and active routes. |
| `core/model_router` | Role-based model routing. |
| `core/model_usage` | Model usage ledger and budget checks. |
| `core/models` | Core data models for the agent (§12.1 of the architecture). |
| `core/operational_domain` | Operational Design Domain detector (§7 Autonomy Governance — ODD / B-05). |
| `core/operator_intent` | Conversational routing for operator-control requests. |
| `core/output_policy` | Ranker-to-output policy. |
| `core/persistent_memory` | Persistent Memory Record store (§4 — long-term, JSONL on disk). |
| `core/planner` | LLM-driven Planner (§3 Cognitive Core: Planning). |
| `core/policy` | Policy Gate — pre-execution checkpoint for every Action. |
| `core/prompt_registry` | §3.x Prompt Registry — centralised tracking of all LLM system prompts. |
| `core/proposal_value_gate` | Deterministic pre-publish value gate for self-build proposals (TD-035). |
| `core/rate_limiter` | CLI session rate limiter — token bucket (T8 / §6 Security). |
| `core/reasoning_action_check` | Reasoning ↔ action consistency check — MAST FM-2.6 (13.2%). |
| `core/receipt_consumer` | Tool receipts slice 1c — minimal consumer for verifier integration. |
| `core/redaction` | Universal redaction layer (§7). |
| `core/reflection` | Reflection engine — self-improvement feedback loop. |
| `core/release_hygiene` | Release artifact hygiene checks. |
| `core/repair_proposal` | MVP-13.3 repair proposal generation. |
| `core/replan` | MVP-12 — Re-planning policy: structured failure types + retry budgets. |
| `core/role_router` | Role / mode routing for the agent core. |
| `core/safe_vcs` | Narrow, safe VCS helper for the trusted self-apply lane (TD-023). |
| `core/scheduler` | Persistent scheduler for autonomous runtime tasks. |
| `core/secret_scanner` | Secret Scanner — single source of truth for credential detection (§7). |
| `core/self_apply_bridge` | Approval -> trusted self-apply lane bridge (TD-024). |
| `core/self_apply_lane` | Trusted low-risk self-apply lane (TD-023). |
| `core/self_build_producer` | Subagent-backed full self-apply proposal producer (TD-025). |
| `core/self_build_supervisor` | Lightweight, read-only self-build supervisor cycle. |
| `core/self_repair` | MVP-13.2 self-repair controller. |
| `core/smart_memory` | Episodic, procedural and consolidation memory for autonomous operation. |
| `core/source_connectors` | Source Connector Registry. |
| `core/source_library` | Curated online source library for controlled web learning. |
| `core/source_ranker` | MVP-14.3 — Source Ranker / Evidence Trust Layer. |
| `core/source_registry` | Source Registry and extracted claims. |
| `core/source_registry_store` | Persistent store for SourceRegistry. |
| `core/state_integrity` | Integrity helpers for small JSONL state stores. |
| `core/state_store_drill` | Live state-store recovery drill for operator readiness checks. |
| `core/step_repetition` | Step repetition detector — addresses MAST FM-1.3 (step repetition, 15.7%). |
| `core/strategy_router` | Strategy Router: deliberation kernel layer BEFORE the LLM planner. |
| `core/structured_facts` | Structured fact extraction for tool outputs. |
| `core/subagent_memory_scope` | MVP-18.1  Autonomous Subagent Proposal Contract. |
| `core/subagent_registry` | Subagent role performance ledger (TD-028). |
| `core/subagent_runner` | SubAgent Runner — executes one bounded sub-agent contract using AgentLoop. |
| `core/subsystem_disagreement` | Detect disagreements between cognitive subsystems on the same turn. |
| `core/supply_chain` | Release/supply-chain audit helpers. |
| `core/task_complexity` | Task Complexity Assessment — automatic model tier selection. |
| `core/task_queue` | Persistent task queue for autonomous runtime work. |
| `core/team_executor` | Executor for bounded subagent contracts. |
| `core/team_plan` | Dry-run multi-agent team planning. |
| `core/termination_guard` | Termination awareness — addresses MAST FM-1.5 and FM-3.1. |
| `core/tool_receipts` | Append-only tool receipt ledger — Stage 1 evidence layer (slice 1a + G5b). |
| `core/truth_hype_filter` | Truth/Hype Filter — the first LEARNING antibody (правда vs шумиха). |
| `core/user_profile` | User Profile — Layer 4 (User Mental Model). |
| `core/value_review` | TD-032 — human value-review verdicts for self-build / self-apply outcomes. |
| `core/verifier` | MVP-14.4 — Verifier. |
| `core/work_session` | MVP-17.1  Long Work Session Skeleton. |

_Total: 108 modules._
