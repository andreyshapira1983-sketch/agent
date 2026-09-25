# Agent Anatomy

Grouped module index for the `core/` package, organized by the
architecture sections (§1–§12). Modules physically live flat in `core/`
— their paths are used as semantic identifiers elsewhere (planner
self-build targets, locators, audits), so this map groups them
*logically* without moving files.

Kept in sync with the codebase by `scripts/agent_anatomy_check.py`
(read-only drift check, TD-029). Regenerate with
`python scripts/gen_anatomy.py` whenever a module is added or removed.

_Total: 254 modules across 12 groups._

## Interface & Interaction (§1)

_Operator-facing I/O, intent routing, output shaping._

| Module | Purpose |
| ------ | ------- |
| `core/operator_intent` | Conversational routing for operator-control requests. |
| `core/proof_demand` | Просьба ПОКАЗАТЬ делом, а не рассказать: такой ход не отвечается перечнем. |
| `core/operator_intent_patterns` | Trigger phrases and matchers behind the no-LLM operator-intent router, including the one-inserted-word tolerance and the suppression guards that stay strict. |
| `core/intent_understanding` | Intent understanding — the translator between plain human language and the autonomous agent's actions. |
| `core/activity_decider` | Activity-type decider: the door must not choose the mind. |
| `core/workspace_reference` | Does this text name something that exists in the workspace? |
| `core/file_request_intent` | What kind of file request is this question? |
| `core/answer_format` | Как ответ выглядит: контракт вывода, человеческая печать, цитаты. |
| `core/warning_words` | Предупреждения проверки — человеческими словами у самого утверждения. |
| `core/lang_match` | Language-aware term matching for question routing. |
| `core/output_policy` | Ranker-to-output policy. |
| `core/user_profile` | User Profile — Layer 4 (User Mental Model). |
| `core/truth_hype_filter` | Truth/Hype Filter — the first LEARNING antibody (правда vs шумиха). |
| `core/conversation_contract` | Правила человеческого разговора: судья исходящей реплики (ступень 1). |
| `core/social_turn` | Болтовня без дела идёт коротким путём: без планировщика, без инструментов, без отчёта. |
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
| `core/repo_provenance` | Происхождение файла: лежит он в истории репозитория или просто в папке. |
| `core/rule_approved_apply` | Применение того, на что разрешение даёт ПРАВИЛО, а не человек. |
| `core/command_subjects` | Команда как ПРЕДМЕТ цели: `:team-run` → модуль, где живёт её обработчик. |

## Cognitive Core & Agent Cycle (§3)

_Planning, verification, clarification, control loop._

| Module | Purpose |
| ------ | ------- |
| `core/loop` | Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond. |
| `core/loop_step_execution` | Исполнение одного шага плана — вырезано из ``core/loop.py`` дословно. |
| `core/loop_sensor` | Запись о сбое наблюдательного сенсора — один метод, и это его дом. |
| `core/step_references` | Наблюдённое значение доезжает из шага A в аргументы шага B. |
| `core/loop_knowledge` | Запись знаний, добытых конвейером, в долгую память. |
| `core/loop_memory_commands` | Операторские команды памяти: запомнить, забыть, показать. |
| `core/loop_repair` | Фасад операторских команд починки на объекте агента. |
| `core/loop_hygiene` | Фасад команд гигиены памяти на объекте агента. |
| `core/repair_commands` | Operator repair commands: propose, roll back, clean up backups. |
| `core/memory_hygiene_commands` | Memory hygiene as operator and daemon commands: expire, dedupe, prune, archive. |
| `core/loop_memory_read` | Чтение памяти циклом: долгая, опытная, сводка. |
| `core/loop_memory_write` | Запись памяти циклом — и право на неё. |
| `core/loop_response_deciders` | Черновик ответа и решатели над ним — вырезано из ``core/loop.py`` дословно. |
| `core/loop_synthesis` | Синтез ответа — метод `_synthesize`, вырезанный из ``core/loop.py`` дословно. |
| `core/loop_synthesis_state` | State holder for `core.loop_synthesis`. |
| `core/loop_synthesis_helpers` | Helper functions used by `core.loop_synthesis.AgentLoopSynthesis`. |
| `core/loop_evidence_chain` | Досборка цепочки улик — вырезано из ``core/loop.py`` дословно. |
| `core/loop_verification` | Проверка черновика и сенсоры вокруг неё — вырезано из ``core/loop.py``. |
| `core/loop_observe` | Наблюдение, разбор запроса и выбор модели — вырезано из ``core/loop.py``. |
| `core/loop_run_tail` | Хвост прогона: ответ готов, эпизод ещё не записан — из ``core/loop.py``. |
| `core/loop_context` | Контекст хода до планирования — вырезано из ``core/loop.py`` дословно. |
| `core/loop_attempt` | Цикл попыток: план → исполнение → вердикт → перепланирование. |
| `core/loop_verify_replan` | Проверка ответа и перепланирование по неразрешённым цитатам. |
| `core/loop_init` | Сборка ``AgentLoop`` — конструктор, вырезанный из ``core/loop.py`` дословно. |
| `core/loop_gates` | Ворота цикла: четыре места, где ход заканчивается, не начавшись. |
| `core/observation_round` | Прочитанное — не конец хода: после успешного пакета шагов решает планировщик. |
| `core/planner` | LLM-driven Planner (§3 Cognitive Core: Planning). |
| `core/causal_store` | Наблюдения переживают ход — первая перекладина причинной лестницы. |
| `core/causal_climb` | Подъём по причинной лестнице: гипотезы, вмешательства, обобщение. |
| `core/causal_claim_store` | Хранилище причинных утверждений выше первой ступени + выжимка уроков. |
| `core/causal_climb_action` | Слайс 1 органа подъёма: наблюдение → конкурирующие объяснения (MIR-096). |
| `core/lesson_provenance` | Causal-provenance meter for lessons (read-only, no delivery organ here). |
| `core/attribute_sieve` | Attribute-phantom sieve: attribute access is verified like call kwargs. |
| `core/lesson_ab_experiment` | The differentiating experiment: lesson OFF vs ON, everything else equal. |
| `core/charter_goal` | Агент выбирает следующую цель кампании сам — отталкиваясь от хартии. |
| `core/placeholder_text` | Шаблон там, где должен стоять адрес или содержимое. |
| `core/planner_prompt` | The planner's system prompt (§3 Cognitive Core: Planning). |
| `core/plan_parsing` | Parsing of the planner LLM's raw output (§3 Cognitive Core: Planning). |
| `core/doc_routing` | Question classification and governing-doc routing for the planner. |
| `core/host_tools_context` | Host-tool context: which desktop tools exist and when to mention them. |
| `core/step_sanitizer` | Admission rules for one planner step — the whitelist the model cannot argue with. |
| `core/verifier` | MVP-14.4 — Verifier. |
| `core/verifier_core` | The verifier's `verify()` entry point: turns a draft answer and its evidence chain into a per-claim verdict report. |
| `core/verifier_models` | Verifier value types: a citation, a claim chunk, and the verification report. |
| `core/verifier_patterns` | Compiled patterns the verifier matches with: citations, sentence splits, headings, and statistical figures. |
| `core/verification_summary` | MIR-069, phase 1 — the five-point verification explanation. |
| `core/sensor_journal` | Один канал для сбоев наблюдательных сенсоров (MIR-077). |
| `core/verifier_utils` | Verifier text utilities: chunk splitting, citation parsing and matching, and statistical-claim detection. |
| `core/verifier_absence` | Certifying a claim of ABSENCE — the half of verification a citation cannot do. |
| `core/claim_arithmetic` | Deterministic evaluation of arithmetic claims against a key=value excerpt. |
| `core/entailment_scope` | Какие утверждения со ссылкой сверять по смыслу моделью (MIR-060). |
| `core/replan` | MVP-12 — Re-planning policy: structured failure types + retry budgets. |
| `core/reflection` | Reflection engine — self-improvement feedback loop. |
| `core/clarification_gate` | Clarification Gate — режим переспроса (ask, don't build). |
| `core/clarification_policy` | Clarification Policy (§3 Cognitive Core — Clarification Policy). |
| `core/pending_clarification` | The question a clarification asked ABOUT, kept until the operator answers. |
| `core/answer_contradiction` | Одно утверждение, объявленное и Фактом, и Непроверенным — в одном ответе. |
| `core/turn_provenance` | Порождено этим ходом или только использовано — по диску, а не по словам. |
| `core/instruction_conflict_gate` | Instruction Conflict Gate — турникет между уверенным приказом и ``git add``. |
| `core/directive_extractor` | Turn instruction *text* into ranked ``Directive`` objects. |
| `core/root_principles` | Корневые принципы агента — выше любого одобрения (слово оператора 24.09). |
| `core/assumption_registry` | Assumption Registry — Layer 5 (Explicit Planning Assumptions). |
| `core/referent_resolver` | Referent resolution for local critique / show-only turns (plan critique PR1). |
| `core/evidence_support` | Evidence support — how well the gathered sources back THIS answer; telemetry, not a gate. |
| `core/confidence_vector` | Decompose answer confidence into a three-axis vector. |
| `core/relevance_judge` | Судья относимости: отвечает ли ответ на заданный вопрос — по утверждениям. |
| `core/draft_refutation` | Черновик, который противоречит своим же уликам, переписывается один раз. |
| `core/patch_route` | Самопочинка без человека в петле: дефект → правка → patch_check → свой код. |
| `core/note_contract` | Договор конспекта: что обязано быть в следе учебной задачи, решает код. |
| `core/defect_intake` | Свой сбой становится дефектом реестра, а не только наблюдением. |
| `core/stuck_route` | Уткнулся — спроси: сначала интернет, потом партнёра. |
| `core/write_at_execution` | Текст записи собирается в момент исполнения, а не при планировании. |
| `core/reasoning_action_check` | Reasoning ↔ action consistency check — MAST FM-2.6 (13.2%). |
| `core/best_next_action` | Priority intelligence: choose the single most important next action. |
| `core/best_next_action_helpers` | Helpers extracted verbatim from ``core/best_next_action.py`` by the incremental splitter. |
| `core/task_complexity` | Task Complexity Assessment — automatic model tier selection. |
| `core/low_evidence_policy` | Low-evidence answer policy. |
| `core/unsupported_claims` | Claim-level answer enforcement (critique plan PR3) — long-answer truncation is always on, while `AGENT_ENFORCE_UNSUPPORTED_CLAIMS` gates only the claim- level short path. |
| `core/subsystem_disagreement` | Detect disagreements between cognitive subsystems on the same turn. |
| `core/completion_marker` | An attempt-bound channel for the synthesizer's completion declaration. |
| `core/completion_obligation` | Did this cycle incur an obligation to observe or act, and leave it unmet? |
| `core/completion_contract` | What must EXIST or have CHANGED when this request is done (MIR-067). |
| `core/request_checklist` | Чек-лист поручения: требования человека как вопросы «да/нет». |
| `core/success_check` | Критерий успеха: та его часть, которую можно наблюдать без модели. |
| `core/response_draft` | The answer under construction — an object the deciders contribute to. |
| `core/synth_resilience` | Synthesizer resilience ladder. |
| `core/strategy_router` | Strategy Router: deliberation kernel layer BEFORE the LLM planner. |
| `core/role_router` | Role / mode routing for the agent core. |
| `core/prompt_registry` | §3.x Prompt Registry — centralised tracking of all LLM system prompts. |
| `core/runtime_self` | Что агент знает о себе ИЗМЕРЕНИЕМ, а не из подсказки. |
| `core/compactor` | Conversation history compaction (Anthropic 2025 — context engineering). |

## Memory & Knowledge Governance (§4)

_Working/persistent memory, hygiene, ingestion, evidence._

| Module | Purpose |
| ------ | ------- |
| `core/memory` | Working Memory (§4 Memory & Knowledge Governance — short-term, session-scoped). |
| `core/persistent_memory` | Persistent Memory Record store (§4 — long-term, JSONL on disk). |
| `core/smart_memory` | Episodic, procedural and consolidation memory for autonomous operation. |
| `core/failure_cards` | Карточки прошлых ошибок: «эта ошибка уже была — вот что тогда помогло». |
| `core/smart_memory_helpers` | Helpers extracted verbatim from ``core/smart_memory.py`` by the incremental splitter. |
| `core/memory_policy` | Memory Write Policy + Memory Retrieval Policy (§4 + §12.4). |
| `core/memory_echo_antibody` | Memory Echo Antibody (A1) — refuse agent-auto memory that *echoes* itself. |
| `core/bilingual_terms` | Russian question, English record — one domain vocabulary between them. |
| `core/topic_tokens` | Из текста — тема, и вес темы: насколько слово вообще что-то разрешает. |
| `core/memory_hygiene` | Гигиена памяти: просрочка, дедупликация, сводка, архивация. |
| `core/episodic_hygiene` | Episodic memory hygiene — staleness scoring and pruning. |
| `core/knowledge_use_policy` | Contextual memory-use policy. |
| `core/knowledge_pipeline` | Knowledge pipeline integration. |
| `core/learned_conclusion` | Что ход ВЫЯСНИЛ — в долговременную память, а не что он прочитал по пути. |
| `core/memory_consolidation` | Сверка нового вывода с памятью ПЕРЕД записью — фаза обновления Mem0. |
| `core/memory_embeddings` | Поиск по смыслу для долговременной памяти: multilingual-e5-large-instruct. |
| `core/cache_freshness` | Можно ли отдать прошлый результат шага вместо нового вызова. |
| `core/ingestion` | Controlled document/code ingestion. |
| `core/ingestion_reports` | Ingestion result types: what a file, web or RSS ingest run reports back. |
| `core/ingestion_utils` | Ingestion helpers: workspace-confined path resolution, project file walking, and text chunking. |
| `core/structured_facts` | Structured fact extraction for tool outputs. |
| `core/evidence` | Evidence + Provenance model: LLM — не источник истины; каждое утверждение ответа привязывается к типизированной записи Evidence. |
| `core/evidence_classes` | Evidence classes — *what kind* of support a claim actually needs (issue #119). |
| `core/evidence_budget` | Evidence Budget — caps context sent to the synthesizer LLM. |
| `core/conflict_review` | Operator-facing conflict review for the Source Registry. |
| `core/conflict_episode` | Procedural memory for instruction conflicts: инструкция → конфликт → решение. |
| `core/source_registry` | Source Registry and extracted claims. |
| `core/source_registry_store` | Persistent store for SourceRegistry. |
| `core/source_library` | Curated online source library for controlled web learning. |
| `core/source_ranker` | MVP-14.3 — Source Ranker / Evidence Trust Layer. |
| `core/read_sources_registry` | Прочитанное в работе становится источником, а не только конспектом. |
| `core/source_connectors` | Source Connector Registry. |

## Tools, Actions & Execution (§5)

_Effect gateways, receipts, compensation, VCS safety._

| Module | Purpose |
| ------ | ------- |
| `core/self_stop_record` | Records the agent's self-stop decision as a single journal entry so the next run can reconstruct why and how the agent stopped. |
| `core/actuation_gateway` | Actuation gateway — checked door for effectful actions (REPL, runtime, daemon). |
| `core/gateway_consult` | Gateway hard-stop consult helpers (G5a). |
| `core/tool_receipts` | Append-only tool receipt ledger — Stage 1 evidence layer (slice 1a + G5b). |
| `core/receipt_consumer` | Tool receipts slice 1c — minimal consumer for verifier integration. |
| `core/backup_cleanup` | Уборка резервных копий `.bak.<ts>` из рабочего каталога. |
| `core/compensation` | Compensation System (§5 Undo) — first introduced for MVP-11 shell_exec. |
| `core/safe_vcs` | Narrow, safe VCS helper for the trusted self-apply lane (TD-023). |
| `core/supply_chain` | Release/supply-chain audit helpers. |
| `core/bounded_subprocess` | Bounded subprocess: a timeout that ends the wait, not one that promises to. |

## Runtime, State & Orchestration (§6)

_Autonomous loop, scheduling, budgets, state durability._

| Module | Purpose |
| ------ | ------- |
| `core/capability_events` | Журнал изменений способностей: когда мир агента стал другим. |
| `core/autonomous_runtime` | Autonomous runtime orchestrator. |
| `core/autonomous_runtime_proposals` | Proposals and self-build — cut out of ``core/autonomous_runtime`` verbatim. |
| `core/autonomous_runtime_types` | Data carried between the autonomous runtime and its callers. |
| `core/scheduler` | Persistent scheduler for autonomous runtime tasks. |
| `core/campaign` | 24/48h autonomous work campaign engine. |
| `core/campaign_io` | Campaign I/O helpers: journal writes, cost totals, and the default signal-gathering and action-executing callbacks. |
| `core/campaign_ledger` | Append-only campaign ledger: one record per cycle, plus loading and summarising the rows. |
| `core/drives` | Внутренние драйвы — физиология вокруг модели, считаемая из журналов. |
| `core/drive_goal` | Задача от драйва: что сейчас важно — решает внутреннее состояние, что делать — модель. |
| `core/campaign_types` | Campaign value types: configuration, per-action outcome, and the result of a finished campaign. |
| `core/campaign_verdict` | Вердикт кампании: сошёлся ли ЕЁ критерий, и записан ли этот факт. |
| `core/work_session` | MVP-17.1  Long Work Session Skeleton. |
| `core/task_queue` | Persistent task queue for autonomous runtime work. |
| `core/task_lifecycle` | One place that decides what a finished run does to its queue row (MIR-039). |
| `core/checkpoint` | §3.5 Checkpoint / Resume — durable mid-run state. |
| `core/circuit_breaker` | Circuit breaker for bounded autonomous runtime runs. |
| `core/termination_guard` | Termination awareness — addresses MAST FM-1.5 and FM-3.1. |
| `core/step_repetition` | Step repetition detector — addresses MAST FM-1.3 (step repetition, 15.7%). |
| `core/rate_limiter` | CLI session rate limiter — token bucket (T8 / §6 Security). |
| `core/budget_governor` | Budget governor for autonomous runtime loops. |
| `core/budget_ledger` | Persistent budget windows for long-running autonomous work. |
| `core/budget_kill_switch` | Persistent budget kill-switch for autonomous / daemon execution (TD-022). |
| `core/spend_report` | Зеркало трат: что агент потратил и что за это получил. |
| `core/usd_spend` | Расход в долларах по журналу вызовов модели — и предел в час (план субботы, пункт з). |
| `core/mentor_channel` | Канал наставника: вопросы к агенту с совещательной властью. |
| `core/run_context` | Run-scoped identity for one agent cycle. |
| `core/state_integrity` | Integrity helpers for small JSONL state stores. |
| `core/state_store_drill` | Live state-store recovery drill for operator readiness checks. |
| `core/file_lock` | Small cross-platform file lock for JSONL state stores. |
| `core/heartbeat_io` | Daemon liveness record — write, read, age, staleness. |
| `core/backlog_selector` | Grounded backlog selector for the self-build producer (TD-036, Phase 1). |
| `core/backlog_signals` | Read-only parsers for grounded self-build backlog signals (TD-036, Phase 1). |
| `core/backlog_target_mapper` | Deterministic mapper from abstract backlog items to concrete self-build targets. |

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
| `core/code_state` | Отпечаток проверенного кода: какой именно код сейчас под руками. |
| `core/anatomy_groups` | Таблица групп карты анатомии — ДАННЫЕ, вынесенные в core намеренно. |

## Learning & Self-Improvement (§9)

_Reflection-driven repair, self-build, value gating._

| Module | Purpose |
| ------ | ------- |
| `core/self_repair` | MVP-13.2 self-repair controller. |
| `core/self_repair_models` | Self-repair value types: the proposal, the per-step record, and the report a repair run produces. |
| `core/self_repair_utils` | Self-repair helpers: reading test output, judging a diagnosis or an empty diff, and summarising approval state. |
| `core/repair_proposal` | MVP-13.3 repair proposal generation. |
| `core/self_apply_bridge` | Approval -> trusted self-apply lane bridge (TD-024). |
| `core/self_apply_lane` | Trusted low-risk self-apply lane (TD-023). |
| `core/burn_in_sandbox` | Явное полномочие песочницы: самопочинка изолированной копии без человека. |
| `core/burn_in_supervisor` | Принимающий: кто переводит проверенный кандидат в рабочее состояние опыта. |
| `core/self_build_producer` | Writes at most ONE low-risk ``self_apply_lane.run`` proposal into the approval inbox, with full file content, for a human to bless. |
| `core/anatomy_sync` | Keeping the anatomy map and its group table in step with a proposal. |
| `core/self_build_supervisor` | Lightweight, read-only self-build supervisor cycle. |
| `core/self_build_memory` | Record self-build / self-apply attempt outcomes into episodic memory. |
| `core/self_build_rules` | Hard rules learned from self-build rollbacks. |
| `core/veto_cause` | Was a self-build veto a verdict on the target, or our own pipeline breaking? |
| `core/builder_reply_diagnosis` | Say what was wrong with a builder reply, in words rather than in silence. |
| `core/self_task_producer` | Stage A: propose a grounded coding task plus its FAILING acceptance test, and drop exactly one ``self_build_task.approve`` item for a human. |
| `core/self_task_builder` | Stage B of the coding-skill ladder (roadmap Ступень 1): write code to make a HUMAN-APPROVED, FROZEN acceptance test pass. |
| `core/writer_completion` | The completion verdict a mechanical writer settles from its own outcome. |
| `core/self_improvement_issues` | Durable lifecycle registry for self-improvement failures. |
| `core/self_defect_reminder` | Свои открытые записи о промахах — перед глазами в момент действия. |
| `core/incremental_splitter` | Incremental splitter for oversized Python modules (junior-plan item #5). |
| `core/split_proof` | Доказательство того, что модуль надо переделать, — а не его толщина. |
| `core/splitter_refusals` | Журнал отказов раскольщика — пауза для файла, который нельзя разрезать. |
| `core/dependency_map` | Project import/dependency map for self-build changes. |
| `core/causal_lesson` | От замеченного отклонения до усвоенного правила — состояниями, не подписями. |
| `core/learning_planner` | Learning planner. |
| `core/weak_spot_retrieval` | Слабое место рефлексии → улики → файлы кода (MIR-106). |
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
| `core/model_registry_audit` | Operator-facing audit for model registry and active routes. |
| `core/degraded_route` | Ответ написала не та модель, которую выбрал маршрут. |
| `core/model_outcomes` | Какая модель на какой роли реально даёт подтверждённые ответы. |
| `core/model_roster` | The agent's eye on its own models: which providers exist, which have a key, which roles they serve, whether they are healthy, what they cost, what was spent today and how much of the day's ceiling is left. |
| `core/model_routing_policy` | The agent's own routing policy: which model answers which role, and why. |

## Multi-Agent / Subagents (§6)

_Subagent proposals, registry, execution, teams._

| Module | Purpose |
| ------ | ------- |
| `core/subagent_contract` | Canonical subagent contract bridge. |
| `core/subagent_contract_audit` | Pure post-run audit policy for canonical subagent contracts. |
| `core/subagent_memory_scope` | MVP-18.1  Autonomous Subagent Proposal Contract. |
| `core/subagent_quarantine` | Карантин находок субагента: запись есть, права влиять — нет. |
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

## Where things are wired (outside core/)

- Tools are registered in `app/bootstrap.py` — `registry.register(SomeTool(...))`; there is no separate registry module.
- `Tool` and `ToolRegistry` live in `tools/base.py`; a tool's `risk` is a plain string (`Risk = Literal["read_only", "reversible", "irreversible", "external"]`), not an enum.
- A model call goes through a role object: `model_router.for_role(ModelRole.X).complete(system=..., user=...)` (`core/model_router.py`); that wrapper records tokens and dollars and checks the budget. A tool that needs a model is given `model_router` in `app/bootstrap.py`, as `tools/spawn_subagent.py` is.
- Self-repair: an edit is written to `proposals/selffix/<name>/edits.txt` (FILE: + SEARCH/REPLACE or LINES blocks) and tried by `patch_check` on a copy of the repo.

| Tool module | Purpose |
| ----------- | ------- |
| `tools/agent_mcp_server` | MCP transport for the read-only agent view. |
| `tools/agent_state_view` | Read-only view of this agent's own state, for whoever is watching it. |
| `tools/base` | Tool abstraction and registry. |
| `tools/convert_file` | `convert_file` — программы для файлов клиента, запущенные безопасно. |
| `tools/current_time` | Current Time tool — pure read-only clock query. |
| `tools/diff_file` | MVP-13.1 — `diff_file` tool: unified diff vs proposed content. |
| `tools/file_read` | File Read tool — sandboxed to the workspace root. |
| `tools/file_write` | File Write tool — sandboxed, secret-aware, backup-on-overwrite. |
| `tools/find_in_files` | Find in Files — поиск по рабочей папке: файлы по имени и строки по тексту. |
| `tools/journal_append` |  |
| `tools/json_view` | Компактный вид JSON-ответа: факты не тонут в длинных полях. |
| `tools/lesson_provenance_tool` | The provenance meter in the planner's hands (read-only). |
| `tools/list_dir` | List Directory tool — sandboxed to the workspace root. |
| `tools/memory_bank` |  |
| `tools/memory_recall` | The agent's own read door into durable memory: a bounded, low-trust search. |
| `tools/model_roster` | `model_roster` — the agent asks what models it has. |
| `tools/model_route` | The agent's door to his own routing policy: set or release a role's model. |
| `tools/network_safety` | Network safety helpers for read-only HTTP tools. |
| `tools/patch_check` | `patch_check` — проверить свою правку в отдельной копии, не трогая живой код. |
| `tools/python_probe` | Python Probe — лаборатория: маленький эксперимент над СВОЕЙ средой. |
| `tools/read_logs` | MVP-13.1 — `read_logs` tool: structured JSONL audit reader. |
| `tools/reddit_feed` | Reddit читается через свою ленту `.rss`, а не через страницу. |
| `tools/rss_fetch` | RSS / Atom fetch tool. |
| `tools/run_tests` | MVP-13.1 — `run_tests` tool: sandboxed pytest runner. |
| `tools/semantic_scholar_search` | Semantic Scholar Academic Search tool. |
| `tools/shell_exec` | Shell Exec tool — narrow, sandboxed, with mandatory compensation plan. |
| `tools/spawn_subagent` | spawn_subagent tool — agent-as-tool pattern for parallel sub-task delegation. |
| `tools/web_fetch` | MVP-14.2 — `web_fetch` tool: turn a web pointer into a verifiable source. |
| `tools/web_search` | Web Search tool — read-only, no API key (DuckDuckGo via ddgs). |
