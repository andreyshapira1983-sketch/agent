# Agent Anatomy

Grouped module index for the `core/` package, organized by the
architecture sections (§1–§12). Modules physically live flat in `core/`
— their paths are used as semantic identifiers elsewhere (planner
self-build targets, locators, audits), so this map groups them
*logically* without moving files.

Kept in sync with the codebase by `scripts/agent_anatomy_check.py`
(read-only drift check, TD-029). Regenerate with
`python scripts/gen_anatomy.py` whenever a module is added or removed.

_Total: 134 modules across 12 groups._

## Interface & Interaction (§1)

_Operator-facing I/O, intent routing, output shaping._

| Module | Purpose |
| ------ | ------- |
| `core/operator_intent` | Conversational routing for operator-control requests. |
| `core/operator_intent_patterns` | Extracted from `core/operator_intent` by autonomous self-build module split. |
| `core/output_policy` | Ranker-to-output policy. |
| `core/user_profile` | User Profile — Layer 4 (User Mental Model). |
| `core/truth_hype_filter` | Truth/Hype Filter — the first LEARNING antibody (правда vs шумиха). |
| `core/alert_ack` | Operator acknowledgement for advisory alerts — retire accepted signals. |

## Perception & Adversarial Defense (§2)

_Input handling and injection/exfiltration defense._

| Module | Purpose |
| ------ | ------- |
| `core/injection_guard` | Indirect Prompt Injection Defence (§2 Adversarial Defense). |
| `core/redaction` | Universal redaction layer (§7). |
| `core/data_classifier` | Data Classifier (§7 Data Governance). |
| `core/dlp` | DLP helpers for sensitive personal data. |
| `core/secret_scanner` | Secret Scanner — single source of truth for credential detection (§7). |

## Cognitive Core & Agent Cycle (§3)

_Planning, verification, clarification, control loop._

| Module | Purpose |
| ------ | ------- |
| `core/loop` | Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond. |
| `core/referent_resolver` | Deterministic referent resolution for local critique and show-only turns. |
| `core/loop_helpers` | Extracted from `core/loop` by autonomous self-build module split. |
| `core/loop_methods` | Extracted from `core/loop` by autonomous self-build module split. |
| `core/loop_methods2` | Extracted from `core/loop` by autonomous self-build module split. |
| `core/planner` | LLM-driven Planner (§3 Cognitive Core: Planning). |
| `core/lang_match` | Token-boundary, morphology-aware term matching (RU/EN) for routing heuristics. |
| `core/verifier` | MVP-14.4 — Verifier. |
| `core/verifier_patterns` | Extracted from `core/verifier` by autonomous self-build module split. |
| `core/verifier_models` | Extracted from `core/verifier` by autonomous self-build module split. |
| `core/verifier_utils` | Extracted from `core/verifier` by autonomous self-build module split. |
| `core/verifier_core` | Extracted from `core/verifier` by autonomous self-build module split. |
| `core/replan` | MVP-12 — Re-planning policy: structured failure types + retry budgets. |
| `core/reflection` | Reflection engine — self-improvement feedback loop. |
| `core/clarification_gate` | Clarification Gate — режим переспроса (ask, don't build). |
| `core/clarification_policy` | Clarification Policy (§3 Cognitive Core — Clarification Policy). |
| `core/assumption_registry` | Assumption Registry — Layer 5 (Explicit Planning Assumptions). |
| `core/confidence_gate` | Post-verifier confidence gate. |
| `core/confidence_vector` | Decompose answer confidence into a three-axis vector. |
| `core/reasoning_action_check` | Reasoning ↔ action consistency check — MAST FM-2.6 (13.2%). |
| `core/best_next_action` | Priority intelligence: choose the single most important next action. |
| `core/task_complexity` | Task Complexity Assessment — automatic model tier selection. |
| `core/low_evidence_policy` | Low-evidence answer policy. |
| `core/subsystem_disagreement` | Detect disagreements between cognitive subsystems on the same turn. |
| `core/strategy_router` | Strategy Router: deliberation kernel layer BEFORE the LLM planner. |
| `core/role_router` | Role / mode routing for the agent core. |
| `core/prompt_registry` | §3.x Prompt Registry — centralised tracking of all LLM system prompts. |
| `core/compactor` | Conversation history compaction (Anthropic 2025 — context engineering). |

## Memory & Knowledge Governance (§4)

_Working/persistent memory, hygiene, ingestion, evidence._

| Module | Purpose |
| ------ | ------- |
| `core/memory` | Working Memory (§4 Memory & Knowledge Governance — short-term, session-scoped). |
| `core/persistent_memory` | Persistent Memory Record store (§4 — long-term, JSONL on disk). |
| `core/smart_memory` | Episodic, procedural and consolidation memory for autonomous operation. |
| `core/memory_policy` | Memory Write Policy + Memory Retrieval Policy (§4 + §12.4). |
| `core/memory_echo_antibody` | Memory Echo Antibody (A1) — refuse agent-auto memory that *echoes* itself. |
| `core/hygiene` | Memory Hygiene (§4 Memory Governance — cleanup, dedup, expiry, summarise). |
| `core/episodic_hygiene` | Episodic memory hygiene — staleness scoring and pruning. |
| `core/knowledge_use_policy` | Contextual memory-use policy. |
| `core/knowledge_pipeline` | Knowledge pipeline integration. |
| `core/ingestion` | Controlled document/code ingestion. |
| `core/ingestion_reports` | Extracted from `core/ingestion` by autonomous self-build module split. |
| `core/ingestion_utils` | Extracted from `core/ingestion` by autonomous self-build module split. |
| `core/structured_facts` | Structured fact extraction for tool outputs. |
| `core/evidence` | MVP-14.1 — Evidence + Provenance model. |
| `core/evidence_budget` | Evidence Budget — caps context sent to the synthesizer LLM. |
| `core/conflict_review` | Operator-facing conflict review for the Source Registry. |
| `core/source_registry` | Source Registry and extracted claims. |
| `core/source_registry_store` | Persistent store for SourceRegistry. |
| `core/source_library` | Curated online source library for controlled web learning. |
| `core/source_ranker` | MVP-14.3 — Source Ranker / Evidence Trust Layer. |
| `core/source_connectors` | Source Connector Registry. |

## Tools, Actions & Execution (§5)

_Effect gateways, receipts, compensation, VCS safety._

| Module | Purpose |
| ------ | ------- |
| `core/actuation_gateway` | Actuation gateway — checked door for effectful actions (REPL, runtime, daemon). |
| `core/gateway_consult` | Gateway hard-stop consult helpers (G5a). |
| `core/tool_receipts` | Append-only tool receipt ledger — Stage 1 evidence layer (slice 1a + G5b). |
| `core/receipt_consumer` | Tool receipts slice 1c — minimal consumer for verifier integration. |
| `core/compensation` | Compensation System (§5 Undo) — first introduced for MVP-11 shell_exec. |
| `core/safe_vcs` | Narrow, safe VCS helper for the trusted self-apply lane (TD-023). |
| `core/supply_chain` | Release/supply-chain audit helpers. |

## Runtime, State & Orchestration (§6)

_Autonomous loop, scheduling, budgets, state durability._

| Module | Purpose |
| ------ | ------- |
| `core/autonomous_runtime` | Autonomous runtime orchestrator. |
| `core/scheduler` | Persistent scheduler for autonomous runtime tasks. |
| `core/campaign` | 24/48h autonomous work campaign engine. |
| `core/campaign_types` | Extracted from `core/campaign` by autonomous self-build module split. |
| `core/campaign_ledger` | Extracted from `core/campaign` by autonomous self-build module split. |
| `core/campaign_io` | Extracted from `core/campaign` by autonomous self-build module split. |
| `core/work_session` | MVP-17.1  Long Work Session Skeleton. |
| `core/task_queue` | Persistent task queue for autonomous runtime work. |
| `core/checkpoint` | §3.5 Checkpoint / Resume — durable mid-run state. |
| `core/circuit_breaker` | Circuit breaker for bounded autonomous runtime runs. |
| `core/termination_guard` | Termination awareness — addresses MAST FM-1.5 and FM-3.1. |
| `core/step_repetition` | Step repetition detector — addresses MAST FM-1.3 (step repetition, 15.7%). |
| `core/rate_limiter` | CLI session rate limiter — token bucket (T8 / §6 Security). |
| `core/budget_governor` | Budget governor for autonomous runtime loops. |
| `core/budget_ledger` | Persistent budget windows for long-running autonomous work. |
| `core/budget_kill_switch` | Persistent budget kill-switch for autonomous / daemon execution (TD-022). |
| `core/state_integrity` | Integrity helpers for small JSONL state stores. |
| `core/state_store_drill` | Live state-store recovery drill for operator readiness checks. |
| `core/file_lock` | Small cross-platform file lock for JSONL state stores. |
| `core/backlog_selector` | Grounded backlog selector for the self-build producer (TD-036, Phase 1). |
| `core/backlog_signals` | Read-only parsers for grounded self-build backlog signals (TD-036, Phase 1). |
| `core/backlog_target_mapper` | Deterministic mapper from abstract backlog items to concrete self-build targets. |
| `core/dependency_map` | Project import/dependency map: who imports a module, which symbols, which tests — contract input for self-build changes. |
| `core/self_build_rules` | Hard rules learned from rollbacks (e.g. ImportError symbols that must stay importable), enforced deterministically by the self-build critic. |
| `core/incremental_splitter` | Deterministic (no-LLM) incremental splitter for oversized modules: moves one dependency-closed block per step verbatim into a sibling module (or a class mixin), keeping every import path working. |

## Security, Policy & Autonomy Governance (§7)

_Policy gate, approvals, escalation, domain limits._

| Module | Purpose |
| ------ | ------- |
| `core/policy` | Policy Gate — pre-execution checkpoint for every Action. |
| `core/governance` | Governance modes for safe autonomous growth. |
| `core/approval` | Approval Providers (§7 Security, Policy & Autonomy Governance — Human Approval). |
| `core/approval_inbox` | Approval inbox for autonomous runtime decisions. |
| `core/approval_triage` | Read-only triage for the approval inbox. |
| `core/deep_escalation` | Deep/Opus escalation gate — "Opus is an event, not a habit". |
| `core/operational_domain` | Operational Design Domain detector (§7 Autonomy Governance — ODD / B-05). |
| `core/incident` | Incident Handling skeleton (§7 Security — Incident Handling / B-04 Safety). |
| `core/capability_request` | Autonomous capability request proposals. |

## Evaluation & Monitoring (§8)

_Logging, identifiers, architecture auditing._

| Module | Purpose |
| ------ | ------- |
| `core/logger` | Structured JSONL logger for the agent loop. |
| `core/ids` | Short unique identifiers for trace correlation. |
| `core/architecture_audit` | Static architecture gap audit for the autonomous agent project. |

## Learning & Self-Improvement (§9)

_Reflection-driven repair, self-build, value gating._

| Module | Purpose |
| ------ | ------- |
| `core/self_repair` | MVP-13.2 self-repair controller. |
| `core/self_repair_models` | Extracted from `core/self_repair` by autonomous self-build module split. |
| `core/self_repair_utils` | Extracted from `core/self_repair` by autonomous self-build module split. |
| `core/repair_proposal` | MVP-13.3 repair proposal generation. |
| `core/self_apply_bridge` | Approval -> trusted self-apply lane bridge (TD-024). |
| `core/self_apply_lane` | Trusted low-risk self-apply lane (TD-023). |
| `core/self_build_producer` | Subagent-backed full self-apply proposal producer (TD-025). |
| `core/self_build_memory` | Journals self-build/self-apply outcomes (and why) into episodic memory. |
| `core/self_improvement_issues` | Durable open/verified/resolved lifecycle registry for self-improvement failures. |
| `core/self_task_producer` | Stage-A coding-task producer: turns a code TODO/FIXME into a task + failing acceptance test for human approval (roadmap Ступень 1). |
| `core/self_task_builder` | Stage-B coding-task builder: implements one approved coding task so its frozen acceptance test passes, then proposes it to the self-apply lane (roadmap Ступень 1). |
| `core/self_build_supervisor` | Lightweight, read-only self-build supervisor cycle. |
| `core/learning_planner` | Learning planner. |
| `core/value_review` | TD-032 — human value-review verdicts for self-build / self-apply outcomes. |
| `core/proposal_value_gate` | Deterministic pre-publish value gate for self-build proposals (TD-035). |
| `core/release_hygiene` | Release artifact hygiene checks. |

## Model Management (§6 / §12)

_Model discovery, routing, usage accounting._

| Module | Purpose |
| ------ | ------- |
| `core/model_catalog` | Dynamic Model Catalog — discovers available models from provider APIs. |
| `core/model_discovery` | Live Model Discovery + Provider Catalog diff — read-only / dry-run (TD-011/012). |
| `core/model_router` | Role-based model routing. |
| `core/model_usage` | Model usage ledger and budget checks. |
| `core/synth_resilience` | Synthesizer resilience ladder — retry, adapt, then honest degraded answer on model failure. |
| `core/model_registry_audit` | Operator-facing audit for model registry and active routes. |

## Multi-Agent / Subagents (§6)

_Subagent proposals, registry, execution, teams._

> Lifecycle governance (normative spec, not a `core/` module):
> `docs/SUBAGENT_LIFECYCLE.md` — how sub-agents are proposed, bounded, trusted,
> evaluated, quarantined and retired.

| Module | Purpose |
| ------ | ------- |
| `core/subagent_contract` | Canonical versioned subagent contract and legacy compatibility adapters. |
| `core/subagent_contract_audit` | Pure post-run pass/fail/unknown policy over typed runtime execution receipts. |
| `core/subagent_memory_scope` | MVP-18.1  Autonomous Subagent Proposal Contract. |
| `core/subagent_registry` | Subagent role performance ledger (TD-028). |
| `core/subagent_runner` | SubAgent Runner — executes one bounded sub-agent contract using AgentLoop. |
| `core/team_executor` | Executor for bounded subagent contracts. |
| `core/team_plan` | Dry-run multi-agent team planning. |

## Cross-Cutting: Data Models & LLM (§12)

_Core data models and the LLM client wrapper._

| Module | Purpose |
| ------ | ------- |
| `core/models` | Core data models for the agent (§12.1 of the architecture). |
| `core/llm` | Thin LLM client wrapper. |
