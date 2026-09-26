"""Таблица групп карты анатомии — ДАННЫЕ, вынесенные в core намеренно.

Первый одобренный раскол (2026-08-27) откатился анатомическими сторожами:
новый core-модуль требует строки группировки, а таблица жила в
`scripts/gen_anatomy.py` — каталоге, который полоса самоприменения трогать не
вправе. Сторож требовал строку, денилист запрещал её добавить, и каждый
инкрементальный раскол был обречён на откат (MIR-180).

Здесь таблица лежит там, куда полоса ВПРАВЕ предлагать правки, поэтому
предложение расщепителя несёт и новый модуль, и его группировку одним пакетом.
Правило наследования детерминированное: новый модуль встаёт в группу своего
исходника — семантику расщепитель знать не может, происхождение знает точно.

Файл обязан оставаться ЧИСТЫМ ЛИТЕРАЛОМ без импортов: скрипты читают его
`ast.literal_eval`-разбором, не исполняя, — их правило «не импортировать код
агента» сохраняется. Потребители: scripts/gen_anatomy.py и предложение
расщепителя (`_sync_anatomy_groups` в core/anatomy_sync.py).
"""

GROUPS: list[tuple[str, str, list[str]]] = [
    ("Interface & Interaction (§1)", "Operator-facing I/O, intent routing, output shaping.", [
        "operator_intent", "proof_demand", "operator_intent_patterns", "intent_understanding",
        "activity_decider",
        "workspace_reference",
        "file_request_intent",
        "answer_format", "tool_output_render", "warning_words", "lang_match", "output_policy", "user_profile", "truth_hype_filter",
        "conversation_contract", "social_turn",
        "alert_ack",
    ]),
    ("Perception & Adversarial Defense (§2)", "Input handling and injection/exfiltration defense.", [
        "injection_guard", "egress_flow", "redaction", "data_classifier", "dlp", "secret_scanner",
        "repo_provenance",
        "rule_approved_apply",
        "command_subjects",
    ]),
    ("Cognitive Core & Agent Cycle (§3)", "Planning, verification, clarification, control loop.", [
        "loop", "loop_step_execution", "loop_sensor",
        "step_references",
        "loop_knowledge", "loop_memory_commands", "loop_repair", "loop_hygiene",
        # B1: реализация уехала из двух примесей сюда; `agent` остался фасадом.
        "repair_commands", "memory_hygiene_commands",
        "loop_memory_read", "loop_memory_write",
        "loop_response_deciders", "loop_synthesis",
        "loop_synthesis_state", "loop_synthesis_helpers", "loop_evidence_chain",
        "loop_verification", "loop_observe", "loop_run_tail", "loop_context",
        "loop_attempt", "loop_verify_replan", "loop_init", "loop_gates",
        "observation_round",
        "planner", "causal_store", "causal_climb", "causal_claim_store", "word_overlap",
        "causal_climb_action",
        "lesson_provenance", "attribute_sieve", "lesson_ab_experiment", "charter_goal", "workspace_inventory", "placeholder_text", "planner_prompt", "plan_parsing", "doc_routing", "host_tools_context", "step_sanitizer", "verifier", "verifier_core", "verifier_models", "verifier_patterns", "verification_summary", "sensor_journal",
        "verifier_utils", "verifier_absence", "claim_arithmetic", "entailment_scope", "replan", "reflection", "clarification_gate",
        "clarification_policy", "pending_clarification", "answer_contradiction", "turn_provenance",
        "instruction_conflict_gate", "directive_extractor", "root_principles",
        "assumption_registry", "referent_resolver",
        "evidence_support", "confidence_vector", "relevance_judge", "draft_refutation", "patch_route", "note_contract", "defect_intake", "stuck_route", "own_decisions", "write_at_execution",
        "reasoning_action_check", "best_next_action",
        "best_next_action_helpers", "task_complexity", "low_evidence_policy",
        "unsupported_claims", "subsystem_disagreement", "completion_marker",
        "completion_obligation", "completion_contract", "request_checklist", "success_check",
        "response_draft", "requested_format", "synth_resilience",
        "strategy_router", "role_router", "prompt_registry", "runtime_self",
        "compactor",
    ]),
    ("Memory & Knowledge Governance (§4)", "Working/persistent memory, hygiene, ingestion, evidence.", [
        "memory", "persistent_memory", "memory_door", "smart_memory", "workflow_memory", "failure_cards",
        "smart_memory_helpers", "memory_policy", "work_kinds", "memory_echo_antibody",
        "bilingual_terms", "topic_tokens",
        "memory_hygiene", "episodic_hygiene", "knowledge_use_policy", "knowledge_pipeline",
        "learned_conclusion", "self_knowledge", "memory_consolidation", "memory_embeddings", "cache_freshness",
        "ingestion", "ingestion_reports", "ingestion_utils",
        "structured_facts", "evidence", "evidence_classes", "evidence_budget",
        "conflict_review", "conflict_episode",
        "source_registry", "source_registry_store", "source_library", "source_ranker", "unit_score",
        "read_sources_registry",
        "source_connectors",
    ]),
    ("Tools, Actions & Execution (§5)", "Effect gateways, receipts, compensation, VCS safety.", [
        "self_stop_record",
        "actuation_gateway", "gateway_consult", "tool_receipts", "receipt_consumer",
        "backup_cleanup",
        "compensation", "safe_vcs", "supply_chain",
        "bounded_subprocess",
    ]),
    ("Runtime, State & Orchestration (§6)", "Autonomous loop, scheduling, budgets, state durability.", [
        "capability_events",
        "autonomous_runtime", "market_client", "market_worker", "market_ledger", "work_usefulness", "autonomous_runtime_proposals", "autonomous_runtime_types", "scheduler", "campaign", "campaign_io", "goal_progress", "campaign_ledger", "drives", "drive_goal",
        "campaign_types", "campaign_verdict", "judge_queue", "goal_content_judge", "work_session", "task_queue", "record_fields", "task_lifecycle",
        "checkpoint", "circuit_breaker", "termination_guard", "step_repetition",
        "rate_limiter", "budget_governor", "budget_ledger", "budget_kill_switch", "control_files", "code_citations", "wake_events", "subagent_predictions", "pressure_gate",
        "spend_report", "usd_spend",
        "mentor_channel",
        "run_context", "state_integrity", "state_store_drill", "file_lock",
        "heartbeat_io",
        "backlog_selector", "backlog_signals", "backlog_target_mapper",
    ]),
    ("Security, Policy & Autonomy Governance (§7)", "Policy gate, approvals, escalation, domain limits.", [
        "policy", "governance", "approval", "approval_inbox", "approval_triage",
        "deep_escalation", "operational_domain", "incident", "capability_request",
    ]),
    ("Evaluation & Monitoring (§8)", "Logging, identifiers, architecture auditing.", [
        "logger", "ids", "architecture_audit", "code_state",
        "anatomy_groups",
    ]),
    ("Learning & Self-Improvement (§9)", "Reflection-driven repair, self-build, value gating.", [
        "self_repair", "self_repair_models", "self_repair_utils", "repair_proposal",
        "self_apply_bridge", "self_apply_lane", "burn_in_sandbox", "burn_in_supervisor",
        "self_build_producer", "anatomy_sync", "self_build_supervisor", "self_build_memory", "self_improvement_signals",
        "self_build_rules", "self_build_lessons", "veto_cause", "builder_reply_diagnosis",
        "self_task_producer", "self_task_builder",
        "writer_completion",
        "self_improvement_issues", "self_defect_reminder", "incremental_splitter", "split_proof", "splitter_refusals", "dependency_map",
        "causal_lesson",
        "learning_planner", "weak_spot_retrieval", "value_review", "proposal_value_gate", "release_hygiene",
    ]),
    ("Model Management (§6 / §12)", "Model discovery, routing, usage accounting.", [
        "model_catalog", "model_discovery", "model_router", "model_usage",
        "model_registry_audit", "degraded_route", "model_outcomes", "model_roster",
        "model_routing_policy",
    ]),
    ("Multi-Agent / Subagents (§6)", "Subagent proposals, registry, execution, teams.", [
        "subagent_contract", "subagent_contract_audit", "subagent_memory_scope",
        "subagent_quarantine", "subagent_registry", "subagent_runner",
        "team_executor", "team_plan",
    ]),
    ("Cross-Cutting: Data Models & LLM (§12)", "Core data models and the LLM client wrapper.", [
        "models", "llm", "reasoning_roster",
    ]),
]
