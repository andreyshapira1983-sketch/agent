# Agent Anatomy

Grouped module index for the `core/` package, organized by the
architecture sections (§1–§12). Modules physically live flat in `core/`
— their paths are used as semantic identifiers elsewhere (planner
self-build targets, locators, audits), so this map groups them
*logically* without moving files.

Kept in sync with the codebase by `scripts/agent_anatomy_check.py`
(read-only drift check, TD-029). Regenerate with
`python scripts/gen_anatomy.py` whenever a module is added or removed.

_Total: 286 modules across 12 groups._

## Interface & Interaction (§1)

_Operator-facing I/O, intent routing, output shaping._

| Module | Purpose |
| ------ | ------- |
| `core/operator_intent` | Conversational routing for operator-control requests. |
| `core/proof_demand` | Detect a request to demonstrate an ability by doing it, not by listing it. |
| `core/operator_intent_patterns` | Trigger phrases and matchers behind the no-LLM operator-intent router, including the one-inserted-word tolerance and the suppression guards that stay strict. |
| `core/intent_understanding` | Intent understanding: translates plain human language into one of the agent's real actions, or plain conversation. |
| `core/activity_decider` | Classify operator text into conversation, bounded action, persistent goal or goal control. |
| `core/workspace_reference` | Does this text name something that exists in the workspace? |
| `core/file_request_intent` | Deterministic classifiers for file requests: read vs change vs multi-file intent, and safe user-path validation (no LLM, no state). |
| `core/answer_format` | Answer shape: output contract, human-readable rendering, citations. |
| `core/tool_output_render` | How tool output looks to the model: `format_artifact` (web_search as a list, a read file through the evidence budget, folder listings as is, anything else via `str()`) and `number_lines` (per-request line numbering, built but deliberately not wired in; see tests/test_the_model_can_see_line_numbers.py and docs/CODE_NOTES.md). |
| `core/warning_words` | Verification warnings in human words, next to the claim they qualify. |
| `core/lang_match` | Language-aware term matching: casefold with ё -> е, Unicode word tokens, and matching of a term as a run of whole tokens where long enough tokens may match as a stem prefix (inflection). |
| `core/output_policy` | Ranker-to-output policy: apply source-ranker constraints to the answer after verification. |
| `core/user_profile` | User profile (layer 4): who the user is across sessions, so the agent adapts output style, terminology and verbosity (heuristic, no LLM). |
| `core/truth_hype_filter` | Truth/hype filter: a pure deterministic policy classifying text as `substantive` or `hype` (with signals); `hype` needs both a clear excess of promotional language and a lack of checkable substance, so real information is never dropped for sounding confident. |
| `core/conversation_contract` | Outgoing-reply judge (stage 1 of the human-conversation rules): pure, mechanical verdict on a (question, reply) pair. |
| `core/social_turn` | Short path for idle chatter: no planner, no tools, no report. |
| `core/alert_ack` | Operator acknowledgement of advisory alerts: retire accepted signals without deleting them. |

## Perception & Adversarial Defense (§2)

_Input handling and injection/exfiltration defense._

| Module | Purpose |
| ------ | ------- |
| `core/injection_guard` | Indirect prompt-injection defence: scan tool outputs for injection patterns before they reach the synthesizer. |
| `core/egress_flow` | Outbound data flow check: private data read this turn must not leave to an address taken from read material. |
| `core/redaction` | Universal, non-reversible redaction of secrets and PII for logs, LLM prompts and user-facing output. |
| `core/data_classifier` | Classify text into public / private / sensitive / secret data classes with reasons. |
| `core/dlp` | DLP helpers for sensitive personal data. |
| `core/secret_scanner` | Single source of truth for credential detection (kernel security, not LLM). |
| `core/repo_provenance` | File provenance: whether a file lies in the repository history or just in a folder. |
| `core/rule_approved_apply` | Apply what a RULE, not a human, permits: the loop for one narrow product class, new documents. |
| `core/command_subjects` | Map a command used as a goal subject (`:team-run`) to the module holding its handler, parsed from the dispatcher source. |

## Cognitive Core & Agent Cycle (§3)

_Planning, verification, clarification, control loop._

| Module | Purpose |
| ------ | ------- |
| `core/loop` | Control Loop — Observe -> Interpret -> Plan -> Act -> Verify -> Respond. |
| `core/loop_step_execution` | Execution of one plan step (policy and approval, tool call, result parsing, data classification, injection guard, compensation plan, parallel read-only steps); mixed into ``AgentLoop``, state lives on the composed loop. |
| `core/loop_sensor` | Record of an observation sensor failure: a single method. |
| `core/step_references` | Carries an observed value from step A into the arguments of step B via `{{step:<id or order>.output}}`: only the output of an already executed step, no computation or slicing; resolved before the recipient runs; an unresolved reference stays a named error; a whole-argument reference keeps the value's TYPE; a reference form in PROSE is not a reference when plan steps are known (a dangling number still is an error). |
| `core/loop_knowledge` | Writing knowledge gathered by the pipeline into long-term memory. |
| `core/loop_memory_commands` | Operator memory commands: remember, forget, show. |
| `core/loop_repair` | Facade of the operator repair commands on the agent object. |
| `core/loop_hygiene` | Facade for the memory-hygiene commands on the agent object. |
| `core/repair_commands` | Operator repair commands (propose, roll back, clean up backups); dependencies arrive as ARGUMENTS, never as an agent object. |
| `core/memory_hygiene_commands` | Memory hygiene as operator and daemon commands: expire, dedupe, prune, archive. |
| `core/loop_memory_read` | Loop-side memory reads: long-term, experience, summary. |
| `core/loop_memory_write` | Loop-side memory writing and the permission for it. |
| `core/loop_response_deciders` | Answer draft and its deciders: an ``AgentLoop`` mixin between synthesis and composition. |
| `core/loop_synthesis` | Answer synthesis: the `_synthesize` method cut verbatim out of ``core/loop.py`` (third piece of the split, after `loop_step_execution` and `loop_response_deciders`). |
| `core/loop_synthesis_state` | State holder for `core.loop_synthesis`. |
| `core/loop_synthesis_helpers` | Helper functions of `core.loop_synthesis.AgentLoopSynthesis`, split out to keep that module small. |
| `core/loop_evidence_chain` | Evidence chain assembly, moved verbatim out of ``core/loop.py``. |
| `core/loop_verification` | First draft verification and the sensors around it (subsystem disagreement, confidence vector, evidence support); cut out of core/loop.py and mixed into AgentLoop. |
| `core/loop_observe` | Observe, interpret and model-selection phases, cut out of core/loop.py. |
| `core/loop_run_tail` | Run tail, carved from ``core/loop.py``: the answer is final, the episode is not yet written. |
| `core/loop_context` | Turn context gathered before planning (history, self-analysis classification, referent, long-term and experience memory), carved verbatim from ``core/loop.py``. |
| `core/loop_attempt` | Attempt loop: plan, execute, verdict, replan; the tenth piece of the core/loop.py split, mixed into AgentLoop. |
| `core/loop_verify_replan` | Answer verification and re-planning on unresolved citations. |
| `core/loop_init` | Construction of ``AgentLoop``: the constructor body moved verbatim out of ``core/loop.py``. |
| `core/loop_gates` | Loop gates: four places where a turn ends before it starts. |
| `core/observation_round` | Reading is not the end of a turn: after a successful step batch the planner decides. |
| `core/planner` | LLM-driven Planner (§3 Cognitive Core: Planning). |
| `core/causal_store` | Causal observation store: observations survive turns (first rung of the causal ladder). |
| `core/causal_climb` | Causal ladder climb: hypotheses, interventions, generalisation. |
| `core/causal_claim_store` | Store of causal claims above the first rung, plus distilled lesson cards for the planner. |
| `core/word_overlap` | Rough text similarity: word set and Jaccard overlap. |
| `core/causal_climb_action` | Causal climb actions: explain observations, discriminate by probes, prove a cause by removal. |
| `core/lesson_provenance` | Causal-provenance meter for lessons: read-only receipt chain, no delivery organ here. |
| `core/attribute_sieve` | Attribute-phantom sieve: attribute access is verified like call kwargs. |
| `core/lesson_ab_experiment` | The differentiating experiment: lesson OFF vs ON, everything else equal. |
| `core/charter_goal` | The agent picks the next campaign goal itself, starting from the charter (goal judge: verbatim anchor, novelty, authority invariant, success_check). |
| `core/workspace_inventory` | Workspace inventory for goal choice: what is on disk and which tools are open. |
| `core/placeholder_text` | A template where an address or content should stand (unfilled placeholder detection). |
| `core/planner_prompt` | The planner's system prompt (instruction sheet) and its Prompt Registry registration. |
| `core/plan_parsing` | Parses the planner LLM's raw output: fence stripping, DLP-redacted previews and tolerant JSON extraction into a plan dict plus a parse-diagnostics record. |
| `core/doc_routing` | Question classification and governing-doc routing for the planner. |
| `core/host_tools_context` | Host-tool context: which desktop tools exist and when to mention them. |
| `core/step_sanitizer` | Admission rules for one planner step: the whitelist the model cannot argue with. |
| `core/verifier` | Verifier: marks each synthesized claim [unverified] or [verified:<kind>:<source>] against the cycle's ProvenanceChain. |
| `core/verifier_core` | The verifier's `verify()` entry point: turns a draft answer and its evidence chain into a per-claim verdict report. |
| `core/verifier_models` | Verifier value types: a citation, a claim chunk and the verification report; extracted from `core/verifier`. |
| `core/verifier_patterns` | Compiled patterns the verifier matches with: citations, sentence splits, headings, and statistical figures. |
| `core/verification_summary` | Five-point verification explanation: ``verified`` and ``dialogue_supported`` count as confirmed (the tail says how many rest on this conversation); point 4 names the other claim verdicts, ``user_asserted`` included; nothing examined gives no answer tail but still explains itself for the journal. |
| `core/sensor_journal` | One channel for failures of observational sensors. |
| `core/verifier_utils` | Verifier text utilities: chunk splitting, citation parsing and matching, and statistical-claim detection. |
| `core/verifier_absence` | Certifying a claim of ABSENCE — the half of verification a citation cannot do. |
| `core/claim_arithmetic` | Deterministic evaluation of arithmetic claims against a key=value excerpt. |
| `core/entailment_scope` | Select which cited claims get a model entailment check: only derived claims the arithmetic check did not resolve, capped per answer. |
| `core/replan` | Re-planning policy: structured failure types and per-type retry budgets; side-effect-free, the loop owns logging. |
| `core/reflection` | Reflection engine — self-improvement feedback loop. |
| `core/clarification_gate` | Clarification Gate: deterministic, LLM-free switch between PROCEED and CLARIFY (ask, do not build) mode. |
| `core/clarification_policy` | Decide whether to ask a clarifying question before planning or act on a safe default (regex heuristics, no LLM). |
| `core/pending_clarification` | The question a clarification asked ABOUT, kept until the operator answers. |
| `core/answer_contradiction` | Detects one claim declared both as Fact and as Unverified in the same answer. |
| `core/turn_provenance` | Produced by this turn or only used, judged by disk and mtime, not by words: if the turn acted (wrote, built, converted) but an output file named in the answer is older than the turn start, a journal statement is attached to the answer. |
| `core/instruction_conflict_gate` | Instruction conflict gate: rank sources by authority; on any conflict block every state-mutating action except reporting it. |
| `core/directive_extractor` | Turn instruction text into ranked ``Directive`` objects by mapping each sentence to a stance on a known axis. |
| `core/root_principles` | Root principles of the agent: a prompt block in planner and answer-builder prompts that no approval, grant or request lifts. |
| `core/assumption_registry` | Explicit planning assumptions: heuristic (regex, no LLM) extraction from questions and tool plans, in-memory registry per run, durable JSONL store. |
| `core/referent_resolver` | Referent resolution for local critique / show-only turns: the user message is data (``analysis_target_excerpt``), never world-fact evidence; no evidence chain is written, no auto file_read, conflicts become ``ambiguous``. |
| `core/evidence_support` | Evidence support: how well the gathered sources back THIS answer; telemetry, not a gate. |
| `core/confidence_vector` | Three-axis answer confidence vector (evidence, coherence, relevance) combined into an overall score by a weighted geometric mean (weakest link). |
| `core/relevance_judge` | Relevance judge: whether an answer addresses the asked question, claim by claim (yes / no / idk; score = (yes + idk) / total); None means no score (off, empty, malformed verdict) and the old measure applies. |
| `core/draft_refutation` | A draft that contradicts its own evidence is rewritten once before the answer. |
| `core/patch_route` | Self-repair without a human in the loop: defect, edit, patch_check, own code. |
| `core/note_contract` | Note contract: code, not the model, decides what a study-task trace must contain (source, verbatim quote, check with a number). |
| `core/defect_intake` | A repeated own failure becomes a registry defect via `upsert_failure`, not just an observation. |
| `core/stuck_route` | Stuck route: gather evidence and decide, ask a human only when the step is not ours. |
| `core/own_decisions` | Whose decision it is: the agent decides about its own code, checks and tests (reversible, tests judge); a human only for HUMAN_ONLY. |
| `core/write_at_execution` | Compose file text at execution time, not at planning: a plan may carry a write_instruction instead of the text. |
| `core/reasoning_action_check` | Reasoning vs action consistency check (MAST FM-2.6); observational only: it reports, never blocks. |
| `core/best_next_action` | Priority intelligence: choose the single most important next action. |
| `core/best_next_action_helpers` | Helpers split out of ``core/best_next_action.py``; the original module re-exports every name. |
| `core/task_complexity` | Task Complexity Assessment — automatic model tier selection. |
| `core/low_evidence_policy` | Low-evidence answer policy: keeps only chunks the verifier marked ``verified``, states that evidence was insufficient (EN or RU), downgrades the Output Contract ``Confidence`` line to ``low``, and routes the suppressed bulk into the ``Unverified`` section. |
| `core/unsupported_claims` | Claim-level answer enforcement: long-answer truncation is always on, while the `AGENT_ENFORCE_UNSUPPORTED_CLAIMS` flag (off \| shadow \| on) gates only the claim-level short path. |
| `core/subsystem_disagreement` | Detect disagreements between cognitive subsystems on the same turn (pure function, logging only). |
| `core/completion_marker` | Attempt-bound channel for the synthesizer's completion declaration. |
| `core/completion_obligation` | Detect obligations to observe or act that this cycle incurred and left silently unmet. |
| `core/completion_contract` | Completion contract: what must EXIST or have CHANGED when a request is done, derived before the work; an unreadable duty becomes an ambiguity to ask, never a guess. |
| `core/request_checklist` | Request checklist: the human's requirements as yes/no questions (TICK / RLCF style), checked against the finished answer. |
| `core/success_check` | Success criterion: the part of it observable without a model, i.e. the paths NAMED in the criterion, read from disk independently of the executor's word; verdicts ``verified`` / ``missing`` / ``unverifiable`` (unknown is not yes). |
| `core/response_draft` | The answer under construction: deciders contribute a claim-bearing ``body`` and claim-free notices. |
| `core/requested_format` | A human-requested answer shape is the deliverable: honor requested last-line formats (e.g. "FINAL ANSWER: ...") by finding the value, converting it in a separate step if needed, and putting the line last and clean, with a caveat line above it when the value is unverified. |
| `core/synth_resilience` | Synthesizer resilience ladder: ordinary retry, then adapted retry (shrunk context/output), then an honest degraded Output-Contract answer; ``ModelBudgetExceeded`` and other fatal types are never retried. |
| `core/strategy_router` | Strategy Router: deterministic, pure keyword routing of operator input before the LLM planner. |
| `core/role_router` | Deterministic role/mode routing for the agent core: the router is policy, not another LLM prompt. |
| `core/prompt_registry` | §3.x Prompt Registry — centralised tracking of all LLM system prompts. |
| `core/runtime_self` | What the agent knows about itself by MEASUREMENT, not from a prompt hint. |
| `core/compactor` | Conversation history compaction: older turns fold into one synthetic summary turn. |

## Memory & Knowledge Governance (§4)

_Working/persistent memory, hygiene, ingestion, evidence._

| Module | Purpose |
| ------ | ------- |
| `core/memory` | Working memory: short-term, session-scoped turn log and tool-artifact cache with strict bounds (max_turns, max_chars); no persistence. |
| `core/persistent_memory` | Persistent Memory Record store: long-term JSONL on disk. |
| `core/memory_door` | Write door to persistent memory for the agent's own records: kind dictionary, code-likeness filter, write-policy call with the recent-writes log, and read-back confirmation. memory_door_write(store, policy, text, kind, provenance) -> mem_id \| None; conclusions pass, raw code / duplicates / frozen sources / unmapped kinds do not. |
| `core/smart_memory` | Episodic, procedural and consolidation memory for autonomous operation. |
| `core/workflow_memory` | Workflow templates after Agent Workflow Memory (Wang et al., arXiv 2409.07429), stored separately from procedural memory. |
| `core/failure_cards` | Failure cards: when a known error recurs, show what helped last time. |
| `core/smart_memory_helpers` | Helpers extracted verbatim from ``core/smart_memory.py`` by the incremental splitter. |
| `core/memory_policy` | Memory write and retrieval policies: what may be persisted and what is recalled into a prompt. |
| `core/bm25` | BM25 lexical ranking (k1=1.2, b=0.75, Lucene-style IDF) and its tokenizer. |
| `core/work_kinds` | Work kind: what the person is doing now, not the words used; a closed classifier shared by memory retrieval (`MemoryRetrievalPolicy`) and workflow memory (core/workflow_memory.py). |
| `core/memory_echo_antibody` | Memory Echo Antibody: refuse agent-auto memory that echoes itself. |
| `core/bilingual_terms` | Russian-to-English domain term table so a Russian question can match English records. |
| `core/topic_tokens` | Topic words from text, and topic weight: how much a word resolves at all. |
| `core/memory_hygiene` | Memory hygiene: expiry, deduplication, summarisation, archival. |
| `core/episodic_hygiene` | Episodic memory hygiene: staleness scoring and pruning of old low-value episodes. |
| `core/knowledge_use_policy` | Contextual memory-use policy. |
| `core/knowledge_pipeline` | Knowledge pipeline integration. |
| `core/learned_conclusion` | Write what a turn found out (the conclusion, not what it read on the way) to long-term memory, through the usual `remember` path, when the episode is admitted to experience and the answer rests on this turn's source. |
| `core/self_knowledge` | Self-knowledge versus subject tasks: which memory records are NOT mixed in when reading (consumer: core/loop_memory_read.py). |
| `core/memory_consolidation` | Reconcile a new conclusion with memory BEFORE writing it (Mem0 update phase). |
| `core/memory_embeddings` | Semantic search for long-term memory: multilingual-e5-large-instruct. |
| `core/cache_freshness` | Reuse a previous step result only if the world cannot have changed. |
| `core/replay_freshness` | Decide which stored answers may be served again without a model call. |
| `core/jev_judge` | Jev (TypeSafe System One): probability that a new finding replaces each earlier similar one. |
| `core/ingestion` | Controlled local ingestion: files -> evidence chain -> source registry -> claims -> conflict check -> optional gated memory write. |
| `core/ingestion_reports` | Ingestion result types: what a file, web or RSS ingest run reports back. |
| `core/ingestion_utils` | Ingestion helpers: workspace-confined path resolution, project file walking, and text chunking. |
| `core/structured_facts` | Structured fact extraction for tool outputs. |
| `core/evidence` | Evidence + provenance model: every claim in an answer is tied to a typed Evidence record; one tool output gives at most one evidence, and a tool error means no source, not a weak one. |
| `core/evidence_classes` | Evidence classes: what kind of support a claim needs, plus self-analysis and dialogue-scope detection. |
| `core/evidence_budget` | Evidence budget: caps context sent to the synthesizer (per-artifact excerpting, total trim with demoted blocks paying first). |
| `core/arithmetic_check` | Check an evidence-free arithmetic answer by computing the question's expression. |
| `core/conflict_review` | Operator-facing conflict review for the Source Registry. |
| `core/conflict_episode` | Procedural memory for instruction conflicts: instruction -> conflict -> decision. |
| `core/source_registry` | Source Registry and extracted claims. |
| `core/source_registry_store` | Persistent store for SourceRegistry. |
| `core/source_library` | Curated online source library for controlled web learning. |
| `core/source_ranker` | Source ranker / evidence trust layer: deterministic, local scoring of how far each Evidence record can be trusted for the current question. |
| `core/unit_score` | Unit-interval scores: source trust and claim confidence. |
| `core/read_sources_registry` | Register what a turn actually read (web pages via web_fetch) in the source registry at turn end, so reading leaves a source, not only a note. |
| `core/source_connectors` | Source connector registry: a read-only routing table over concrete source tools (kind of source, wired/partial/planned, auth needs, budget counters, when to prefer or avoid). |

## Tools, Actions & Execution (§5)

_Effect gateways, receipts, compensation, VCS safety._

| Module | Purpose |
| ------ | ------- |
| `core/self_stop_record` | Records the agent's self-stop decision as a single journal entry so the next run can reconstruct why and how the agent stopped. |
| `core/actuation_gateway` | Actuation gateway: checked door for effectful actions (REPL, runtime, daemon). |
| `core/gateway_consult` | Gateway hard-stop consult helpers: kill-switch and readiness blockers for core.actuation_gateway. |
| `core/tool_receipts` | Append-only tool receipt ledger: redacted bounded records of tool executions and gateway decisions. |
| `core/receipt_consumer` | Minimal receipt consumer for verifier integration. |
| `core/backup_cleanup` | Cleanup of `.bak.<ts>` backup files in the workspace. |
| `core/compensation` | Compensation (undo) plans for mutating tools: best-effort, audit-trailed rollback via delete_path_if_created, restore_from_backup, noop. |
| `core/safe_vcs` | Narrow, safe VCS helper for the trusted self-apply lane. |
| `core/supply_chain` | Release/supply-chain audit helpers. |
| `core/bounded_subprocess` | Run a subprocess with a timeout that kills the whole process tree and never re-reads the pipes. |

## Runtime, State & Orchestration (§6)

_Autonomous loop, scheduling, budgets, state durability._

| Module | Purpose |
| ------ | ------- |
| `core/capability_events` | Log of capability changes: when the agent's world became different. |
| `core/autonomous_runtime` | Autonomous runtime orchestrator. |
| `core/market_client` | Agent Market client (NEAR AI, market.near.ai) for the worker agent: self-hosted mode, polling. |
| `core/market_worker` | Agent Market worker loop: poll, assign, run agent, submit, check status. |
| `core/market_ledger` | Per-order profit ledger for Agent Market: payment minus platform fee, model cost and other costs, plus manual intervention and minutes columns. |
| `core/work_usefulness` | Usefulness measure: whether a cycle's work was used afterwards (approved, read_later, memory_used, code_survived, goal_verified, tests_passed); only counts, never steers goal selection. |
| `core/autonomous_runtime_proposals` | Mixin that generates, parses and de-duplicates self-improvement proposals and drives the self-build producer. |
| `core/autonomous_runtime_types` | Data carried between the autonomous runtime and its callers; `core/autonomous_runtime` re-exports every name. |
| `core/scheduler` | Persistent scheduler for autonomous runtime tasks: a tick enqueues due tasks, advances their next run and exits. |
| `core/campaign` | Bounded 24/48h autonomous campaign loop: pure loop plus injectable signal-gathering and action-execution collaborators. |
| `core/campaign_io` | Campaign I/O helpers: journal writes, cost totals and the default signal-gathering and action-executing callbacks; extracted from `core/campaign`. |
| `core/goal_progress` | Progress file of a human's goal: a pass reads it first and appends to it at the end. |
| `core/campaign_ledger` | Append-only campaign ledger: one record per cycle, plus loading and summarising the rows. |
| `core/drives` | Internal drives: physiology around the model, computed from on-disk logs (measurement only). |
| `core/drive_goal` | Task from a drive: internal state decides what matters, the model decides what to do (`python -m core.drive_goal [--dry]`). |
| `core/campaign_types` | Campaign value types: configuration, per-action outcome, and the result of a finished campaign. |
| `core/campaign_verdict` | Campaign verdict: whether the campaign's own criterion converged, and the recorded fact. |
| `core/judge_queue` | Judge queue: the agent's final decisions are judged by Claude, outside the turn and by a different model. |
| `core/goal_content_judge` | Goal judge that reads the product itself and checks each requirement by a quoted passage found in the file. |
| `core/work_session` | Long work session skeleton (MVP-17.1): a bounded multi-cycle session of autonomous health passes that stops on ``max_cycles``, the wall-clock ``minutes`` budget, an open circuit breaker or convergence (identical outcome signature ``convergence_window`` times in a row); progress goes to stderr every ``report_every`` cycles; no side effects in dry-run (delegates to AutonomousRuntime). |
| `core/task_queue` | Persistent task queue for autonomous runtime work. |
| `core/record_fields` | Strict parsing of stored record fields: UTC stamps and boolean flags. |
| `core/task_lifecycle` | One place that decides what a finished run does to its queue row. |
| `core/checkpoint` | Durable mid-run checkpoints: append-only JSONL per trace, and resume loading (replays only from the last `respond` checkpoint). |
| `core/circuit_breaker` | Circuit breaker for bounded autonomous runtime runs. |
| `core/termination_guard` | Termination awareness (MAST FM-1.5, FM-3.1): two observational checks that flag the loop when it should recognise a terminal state. |
| `core/step_repetition` | Step repetition detector: counts identical (tool, args) executions within a run. |
| `core/rate_limiter` | CLI session rate limiter: a token bucket (capacity ``max_requests``, refill ``max_requests / window_seconds`` per second); single-threaded CLI use only, no lock. |
| `core/budget_governor` | Outer runtime budget for autonomous loops: cycles, test runs, learning passes and approval requests. |
| `core/budget_ledger` | Persistent budget windows for long-running autonomous work. |
| `core/budget_kill_switch` | Persistent budget kill-switch for autonomous / daemon execution. |
| `core/control_files` | Agent brakes: files only the operator changes; write tools refuse them before approval and no approval lifts it. |
| `core/protected_paths` | Paths that agent self-repair never touches: one list and one path comparison. |
| `core/code_citations` | Memory references to code are checked against the code at read time. |
| `core/wake_events` | What wakes a sleeping campaign: a change in the world, not the clock. |
| `core/subagent_predictions` | Helper prediction against fact: reconciliation when a subagent finishes. |
| `core/pressure_gate` | Budget pressure as a number: near the end of the budget, actions with consequences wait for a human. |
| `core/spend_report` | Spend mirror: pairs of "spent -> got" from the agent's own journals, shown without conclusions; every money row carries a denominator. |
| `core/usd_spend` | Dollar spend from the model-call journal, and an hourly limit. |
| `core/mentor_channel` | Mentor channel: questions to the agent with advisory authority only. |
| `core/run_context` | Run-scoped identity and restrictions for one agent cycle (task_id spans retries, run_id is one attempt). |
| `core/state_integrity` | Integrity helpers for small JSONL state stores. |
| `core/state_store_drill` | Live state-store recovery drill on an isolated file; proves the integrity layer quarantines bad rows without touching production stores. |
| `core/file_lock` | Small cross-platform file lock for JSONL state stores. |
| `core/heartbeat_io` | Daemon liveness record — write, read, age, staleness. |
| `core/backlog_selector` | Grounded backlog selector for the self-build producer: ranks candidates from registered read-only signals and picks the top one. |
| `core/backlog_signals` | Read-only, pure parsers for grounded self-build backlog signals. |
| `core/backlog_target_mapper` | Deterministic mapper from abstract backlog items to concrete self-build targets; local evidence only, no LLM, network or writes. |

## Security, Policy & Autonomy Governance (§7)

_Policy gate, approvals, escalation, domain limits._

| Module | Purpose |
| ------ | ------- |
| `core/policy` | Policy Gate — pre-execution checkpoint for every Action. |
| `core/governance` | Governance modes (diagnostic, learning, repair, improvement, governance) for safe autonomous growth: which kind of autonomous behaviour is allowed. |
| `core/approval` | Approval providers: ask a human to authorise escalated actions and return a typed ApprovalDecision. |
| `core/approval_inbox` | Approval inbox: items a human reviews later while the unattended run stays stopped or dry-run. |
| `core/approval_triage` | Pure, read-only triage of the approval inbox: a recommendation per item plus cluster and duplicate views. |
| `core/deep_escalation` | Deep/Opus escalation gate: a pure, deterministic decision on whether ``ModelRouter.for_task`` may use the DEEP tier. |
| `core/operational_domain` | Operational Design Domain detector: REFUSE harmful/illegal requests, ESCALATE ones needing human authority; pure regex, no LLM, no I/O. |
| `core/incident` | Incident handling skeleton: a structured incident record plus a small append-only store. |
| `core/capability_request` | Proposes bounded, read-only capability requests that require human approval; no activation, no tool execution, no LLM call. |

## Evaluation & Monitoring (§8)

_Logging, identifiers, architecture auditing._

| Module | Purpose |
| ------ | ------- |
| `core/logger` | Structured JSONL logger for the agent loop. |
| `core/ids` | Short unique identifiers for trace correlation. |
| `core/architecture_audit` | Static architecture gap audit for the autonomous agent project. |
| `core/code_state` | Fingerprint of the verified code: which code is at hand right now. |
| `core/anatomy_groups` | Data table of anatomy-map groups, kept in core on purpose so new core modules can register without touching scripts/. |

## Learning & Self-Improvement (§9)

_Reflection-driven repair, self-build, value gating._

| Module | Purpose |
| ------ | ------- |
| `core/self_repair` | Self-repair controller: one guarded repair transaction. |
| `core/self_repair_models` | Self-repair value types: the proposal, the per-step record and the report of a repair run. |
| `core/self_repair_utils` | Self-repair helpers: reading test output, judging a diagnosis or an empty diff, summarising approval state. |
| `core/repair_proposal` | Repair proposal generation: the agent thinks up a repair, while SelfRepairController stays the only layer that may apply it. |
| `core/self_apply_bridge` | Approval -> trusted self-apply lane bridge: runs a persisted, validated inbox proposal through the lane (never auto-triggered, no free-text patches, no network). |
| `core/self_apply_lane` | Trusted low-risk self-apply lane. |
| `core/burn_in_sandbox` | Explicit sandbox authority: unattended self-repair of an isolated copy, enabled only by an env flag plus a marker file. |
| `core/burn_in_supervisor` | Adopter: moves the experiment head to a verified candidate only after re-verification. |
| `core/self_build_producer` | Writes at most ONE low-risk ``self_apply_lane.run`` proposal into the approval inbox, with full file content, for a human to bless. |
| `core/anatomy_sync` | Keep the anatomy map and its group table in step with a proposal. |
| `core/self_build_supervisor` | Lightweight, read-only self-build supervisor cycle: one short pass decides whether the agent should wait, stop, or propose exactly one evidence-backed candidate. |
| `core/self_build_memory` | Record self-build / self-apply attempt outcomes into episodic memory (best-effort, never raises). |
| `core/self_improvement_signals` | Self-improvement failure signals: where they come from and where they go. |
| `core/self_build_rules` | Hard rules from self-build rollbacks: a symbol that failed to import must stay importable, enforced before apply. |
| `core/self_build_lessons` | Self-build lessons with provenance (what broke, what was done, how it was verified), kept in ``data/self_build_lessons.jsonl``. |
| `core/veto_cause` | Tell whether a self-build veto judged the target or our own pipeline failing. |
| `core/builder_reply_diagnosis` | Explain in words what was wrong with a builder reply. |
| `core/self_task_producer` | Stage A: propose a grounded coding task plus its FAILING acceptance test and drop exactly one ``self_build_task.approve`` item for a human; writes no implementation code, every failure is a hard veto. |
| `core/self_task_builder` | Stage B of the coding-skill ladder: write code that makes a HUMAN-APPROVED, FROZEN acceptance test pass. |
| `core/writer_completion` | The completion verdict a mechanical writer settles from its own outcome. |
| `core/self_improvement_issues` | Durable lifecycle registry for self-improvement failures. |
| `core/self_defect_reminder` | Own open defect records, put before the agent's eyes at the moment of acting. |
| `core/incremental_splitter` | Deterministic incremental splitter for oversized Python modules (function, mixin and dedup modes). |
| `core/split_proof` | Proof that a module should be reworked, not its thickness: dup (verbatim duplicate definition elsewhere) or multi_subject (unrelated definition groups). |
| `core/splitter_refusals` | Journal of splitter refusals: pauses a file the splitter cannot cut until its content changes. |
| `core/dependency_map` | Project import/dependency map for self-build changes: who imports which symbols of a target module (stdlib + AST, read-only, best-effort). |
| `core/causal_lesson` | Causal lesson ladder: states from observed deviation to learned rule, advanced by evidence, never by author identity. |
| `core/learning_planner` | Learning planner: decides which local sources are worth feeding into controlled ingestion for a learning goal. |
| `core/weak_spot_retrieval` | Reflection weak spot -> evidence -> code files. |
| `core/value_review` | TD-032: human value-review verdicts for self-build / self-apply outcomes. |
| `core/proposal_value_gate` | Deterministic pre-publish value gate for self-build proposals: hard veto for changes with no code effect, soft flags for overclaiming summaries. |
| `core/release_hygiene` | Release artifact hygiene checks: local secrets and developer artifacts must never ship in an archive handed to another person or machine. |

## Model Management (§6 / §12)

_Model discovery, routing, usage accounting._

| Module | Purpose |
| ------ | ------- |
| `core/model_catalog` | Dynamic Model Catalog — discovers provider models and picks the best one per complexity tier. |
| `core/model_discovery` | Model discovery and provider-catalog diff: a local no-network audit and a dry-run discovery that never writes the catalog, switches models or surfaces secrets. |
| `core/model_router` | Role-based model routing. |
| `core/model_usage` | Model usage ledger and budget checks. |
| `core/model_registry_audit` | Operator-facing audit of the model registry (all candidates), active routes (one model per role) and the models actually run according to the usage ledger, reporting drift in both directions. |
| `core/degraded_route` | Notice that a different model than the routed one wrote the answer. |
| `core/model_outcomes` | Which model on which role actually gives verified answers. |
| `core/model_roster` | The agent's view of its own models: providers, keys, roles, health, cost, today's spend and remaining ceiling; read-only, never decides, switches or prints a key value. |
| `core/model_routing_policy` | The agent's own routing policy: which model answers which role and why; decisions are journaled without secrets. |

## Multi-Agent / Subagents (§6)

_Subagent proposals, registry, execution, teams._

| Module | Purpose |
| ------ | ------- |
| `core/subagent_contract` | Canonical subagent contract bridge. |
| `core/subagent_contract_audit` | Pure post-run audit policy for canonical subagent contracts. |
| `core/subagent_memory_scope` | Autonomous subagent proposal contract. |
| `core/subagent_quarantine` | Quarantine of subagent findings: recorded, but with no right to influence anything. |
| `core/subagent_registry` | Subagent role performance ledger. |
| `core/subagent_runner` | SubAgent Runner: executes one bounded sub-agent contract using AgentLoop. |
| `core/team_executor` | Executor for bounded subagent contracts: dry-run (default) walks the plan and validates contracts; real mode needs an injected SubAgentRunner and blocks on approval_required items. |
| `core/team_plan` | Dry-run multi-agent team planning: first layer of the multi-agent model. |

## Cross-Cutting: Data Models & LLM (§12)

_Core data models and the LLM client wrapper._

| Module | Purpose |
| ------ | ------- |
| `core/models` | Core data models for the agent (§12.1): every object crossing a domain boundary is one of these. |
| `core/llm` | Thin LLM client wrapper. |
| `core/reasoning_roster` | Roster of models that spend the output budget on reasoning (the 'silent' ones), kept in its own journal (``data/reasoning_roster.jsonl``). |

## Who judges an answer, and in what order

| Stage | Module | How | What it judges |
| ----- | ------ | --- | -------------- |
| synthesis | `core/draft_refutation` | code | a draft that contradicts its own evidence is rewritten once |
| verify | `core/verifier_core` | code + model | each claim needs a matching cited source; a model checks the source entails it |
| verify | `core/relevance_judge` | model | does the answer address what was asked (per claim: yes / no / idk) |
| compose | `core/answer_contradiction` | code | one claim stated both as a fact and as unverified |
| compose | `core/unsupported_claims` | code | claims whose citations do not match this turn's sources are cut |
| compose | `core/low_evidence_policy` | code | an answer with too little verified support is shortened, guesses hidden |
| compose | `core/output_policy` | code | the source ranking's warnings reach the answer |
| after compose | `core/requested_format` | code (+ model to rewrite) | the line the user prescribed ends the answer, clean |
| after compose | `core/completion_obligation` | code | an obligation the turn incurred and left unmet |
| after compose | `core/request_checklist` | model | each requirement of the request as a yes/no question (TICK) |
| goal closure | `core/campaign_verdict` | code | the goal's criterion observed in the world, traces newer than the run |
| goal closure | `core/goal_content_judge` | model | what the product SAYS meets the criterion; every yes needs a quote |
| out of band | `core/judge_queue` | Claude, outside the run | every 'goal met' is provisional until Claude rules |

Exams judge from outside the agent: the nightly anchor exam and the work exam check files and tests, not the agent's words.

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
