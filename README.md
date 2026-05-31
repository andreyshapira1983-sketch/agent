# Autonomous Agent — MVP

Minimal working core of the autonomous agent described in `архитектура автономного Агента.txt`.

Project direction and non-negotiable principles are captured in
[`AGENT_DOCTRINE.md`](AGENT_DOCTRINE.md): the agent is a governed personal
operating system, not just an LLM with tools.

This MVP runs the full §3 Control Loop with **two read-only tools and
one reversible/irreversible write tool**, an **LLM-driven planner** that
picks them, **session-scoped Working Memory** for continuous dialogue,
**persistent long-term memory** (JSONL on disk) gated by a strict
Memory Write Policy, a **human approval gate** for escalated
(irreversible / external) actions, a **kernel-side safety layer** that
scans, classifies, and redacts secrets across every surface (logs, LLM
prompts, memory, final answer), **bounded re-planning** that recovers
from tool errors, failed validation, denied approvals, and exhausted
attempts without ever looping forever, and **argument-aware risk
classification** (`Tool.risk_for`) so `file_write` is reversible for
new files but escalates for overwrites.

- `file_read` — read a UTF-8 file from inside the workspace (read_only)
- `web_search` — search the public web via DuckDuckGo (read_only, no API key)
- `file_write` — write a UTF-8 file inside the workspace; new files are
  reversible (no approval needed), overwrites take a timestamped `.bak.<ts>`
  copy and require human approval. **Every successful write registers a
  `CompensationPlan` so `:rollback` deletes the new file or restores the
  original from its backup** (MVP-11 integration on top of MVP-9).
  Credentials in `content` are refused

The user asks a question. The planner — the LLM — sees the conversation so far
**plus any relevant long-term records**, decides whether to read the file,
search the web, do both, or call nothing. The Executor runs the chosen plan,
short-circuits identical tool calls via the artifact cache, and the Synthesizer
produces a continuity-aware Output Contract. Curated text/code sources can be
ingested with `:ingest-source` / `:ingest-project`; useful facts can be saved
across sessions with `:remember` or controlled knowledge writes. The Write
Policy refuses secrets, blocked tags, and unconsented noise.

A hermetic test suite (**1440 cases, zero network**) backs every layer:
tool safety, planner routing, policy gate enforcement, working memory,
persistent memory + write policy + retrieval policy, human approval gate
(escalate → approve / deny / abort), secret scanning + universal
redaction + data classification (PROVEN: no raw secret ever reaches the
JSONL trace, LLM prompts, or user-facing answer), **bounded re-planning
(PROVEN: tool_error / verify_failed / approval_deny each trigger one
fresh plan; after `max_replan_attempts` the loop honestly stops)**,
plus full Control Loop integration. Run `python -m pytest -v`.

## What's wired up

| Architecture section | Implemented in |
| --- | --- |
| §3 Control Loop / Agent Cycle | [`core/loop.py`](core/loop.py) |
| §3 Cognitive Core: LLM-driven Planning | [`core/planner.py`](core/planner.py) |
| §3 Model Router — role-based model selection | [`core/model_router.py`](core/model_router.py) |
| §3 Model Usage Ledger — calls / tokens / rough cost units | [`core/model_usage.py`](core/model_usage.py) + `:model-usage` |
| §3 Model Registry Audit — active routes vs candidate catalog | [`core/model_registry_audit.py`](core/model_registry_audit.py) + `:model-registry-audit` |
| §6 Persistent Budget Windows — hour/day spend caps across sessions | [`core/budget_ledger.py`](core/budget_ledger.py) + `:budget-window-status` |
| §4 Memory: Working Memory + artifact cache | [`core/memory.py`](core/memory.py) |
| §4 Memory: Persistent Long-Term Store (JSONL) | [`core/persistent_memory.py`](core/persistent_memory.py) |
| §4 + §12.4 Memory Write Policy + Retrieval Policy | [`core/memory_policy.py`](core/memory_policy.py) |
| §4 Memory Hygiene — TTL / dedup / summarise / backup cleanup (MVP-10) | [`core/hygiene.py`](core/hygiene.py) + `AgentLoop.expire_persistent / dedupe_persistent / summarise_persistent / cleanup_backups` |
| §7 Human Approval (escalate gate, providers) | [`core/approval.py`](core/approval.py) |
| §7 Secret Scanner (single source of truth) | [`core/secret_scanner.py`](core/secret_scanner.py) |
| §7 Universal Redaction (logs, prompts, output) | [`core/redaction.py`](core/redaction.py) |
| §7 Data Classifier (public / private / sensitive / secret) | [`core/data_classifier.py`](core/data_classifier.py) |
| §7 Release Hygiene Guard — excludes `.env`, `.git`, `.venv`, caches | [`core/release_hygiene.py`](core/release_hygiene.py) + `:release-audit` |
| §3 Re-planning (`ReplanTrigger`, attempt counter, exhaustion error) | [`core/loop.py`](core/loop.py) |
| §3 Re-planning policy (MVP-12: `FailureType`, `FailureBudget`, `ReplanPolicy.decide()`, `forbidden_actions`) | [`core/replan.py`](core/replan.py) |
| §12.1 Core Data Models (11 of 14, incl. MemoryRecord + ApprovalRequest + ApprovalDecision) | [`core/models.py`](core/models.py) |
| §5 Tools, Actions & Execution + Tool Catalog | [`tools/base.py`](tools/base.py), [`tools/file_read.py`](tools/file_read.py), [`tools/web_search.py`](tools/web_search.py), [`tools/file_write.py`](tools/file_write.py), [`tools/shell_exec.py`](tools/shell_exec.py) |
| §5 Action Risk & Reversibility — argument-aware risk (`risk_for`) | `Tool.risk_for()` in [`tools/base.py`](tools/base.py) + overrides in [`tools/file_write.py`](tools/file_write.py) + [`tools/shell_exec.py`](tools/shell_exec.py) |
| §5 Undo / Compensation System (MVP-11) | [`core/compensation.py`](core/compensation.py) + `AgentLoop.rollback` + `AgentLoop.compensation_log` |
| §5 Tool Result Validation | `Tool.validate_output()` in every tool |
| §12.4 Policy Gates (consult `risk_for` per call) | [`core/policy.py`](core/policy.py) |
| §12.4 Governance Modes (learning / repair / improvement approval boundaries) | [`core/governance.py`](core/governance.py) |
| §14.1 Evidence + Provenance Chain | [`core/evidence.py`](core/evidence.py) |
| §14.3 Source Ranker / Evidence Trust Layer | [`core/source_ranker.py`](core/source_ranker.py) |
| §14.3x Ranker-to-Output Policy | [`core/output_policy.py`](core/output_policy.py) + [`core/loop.py`](core/loop.py) |
| §14.3b Source Registry + extracted claims catalog | [`core/source_registry.py`](core/source_registry.py) |
| §14.3c Knowledge Pipeline Integration | [`core/knowledge_pipeline.py`](core/knowledge_pipeline.py) + [`core/source_registry_store.py`](core/source_registry_store.py) |
| §14.3c Conflict Review / Resolver UX | [`core/conflict_review.py`](core/conflict_review.py) + `:conflicts` |
| §14.3d Controlled Source Ingestion (`:ingest-source`, `:ingest-project`) | [`core/ingestion.py`](core/ingestion.py) + [`main.py`](main.py) |
| §14.3f Online Source Library (`:source-library`, `:ingest-web`) | [`core/source_library.py`](core/source_library.py) + [`core/ingestion.py`](core/ingestion.py) |
| §14.3g RSS / Atom Source Ingestion (`:ingest-rss`) | [`tools/rss_fetch.py`](tools/rss_fetch.py) + [`core/ingestion.py`](core/ingestion.py) |
| §14.3h Source Connector Registry (`:connectors`, `:connector-plan`) | [`core/source_connectors.py`](core/source_connectors.py) + [`main.py`](main.py) |
| §14.3e Role Router / Knowledge Use / Learning Planner | [`core/role_router.py`](core/role_router.py), [`core/knowledge_use_policy.py`](core/knowledge_use_policy.py), [`core/learning_planner.py`](core/learning_planner.py) |
| §6 Autonomous Runtime + Budget Governor + Circuit Breaker + Approval Inbox | [`core/autonomous_runtime.py`](core/autonomous_runtime.py), [`core/budget_governor.py`](core/budget_governor.py), [`core/circuit_breaker.py`](core/circuit_breaker.py), [`core/approval_inbox.py`](core/approval_inbox.py) |
| §6 Persistent Task Queue + Scheduler Tick | [`core/task_queue.py`](core/task_queue.py), [`core/scheduler.py`](core/scheduler.py), [`main.py`](main.py) |
| §15 Multi-Agent Organization: dry-run Team Plan + subagent contracts | [`core/team_plan.py`](core/team_plan.py) + `:team-plan` |
| §15.1 Team Executor Dry-Run — contract walk / budget / verifier handoff | [`core/team_executor.py`](core/team_executor.py) + `:team-run` |
| Operator Architecture Audit — implemented layers vs multi-agent gaps | [`core/architecture_audit.py`](core/architecture_audit.py) + `:architecture-audit` |
| §13.2 Self-Repair Controller (diagnose -> diff -> approval -> write -> tests -> rollback) | [`core/self_repair.py`](core/self_repair.py) + `AgentLoop.repair()` |
| §13.3 Repair Proposal Generator (tests/logs/code -> validated proposal) | [`core/repair_proposal.py`](core/repair_proposal.py) + `AgentLoop.propose_repair()` |
| §13.4 Self-Repair E2E Hardening (success / deny / rollback / low confidence) | [`tests/test_self_repair_e2e.py`](tests/test_self_repair_e2e.py) |
| §1 Output Contract (Conclusion / Facts / Sources / Confidence / Unverified) | `SYSTEM_ANSWER` in [`core/loop.py`](core/loop.py) |
| §8 Monitoring & Logging (structured JSONL) | [`core/logger.py`](core/logger.py) |
| §1 Interface & Communication (CLI surface) | [`main.py`](main.py) |

Multi-agent/sub-agent manager, RL, deployment, SDK, embeddings/vector RAG,
edge — explicitly **not yet**.

## Setup

1. Install dependencies:
   ```powershell
   python -m pip install --require-hashes -r requirements.lock
   ```

2. Ensure `.env` has at least one valid API key (or use `mock`).

3. Pick LLM provider via env vars:
   ```
   AGENT_PROVIDER=anthropic         # default — requires ANTHROPIC_API_KEY
   AGENT_PROVIDER=openai            # requires OPENAI_API_KEY
   AGENT_PROVIDER=huggingface       # requires HF_TOKEN (free HF Inference Router)
   AGENT_PROVIDER=mock              # offline deterministic stub — no key needed
   AGENT_MODEL=claude-sonnet-4-5    # or "gpt-4o-mini", "meta-llama/Llama-3.3-70B-Instruct"
   AGENT_MAX_TOKENS=2048
   ```

   `mock` returns a deterministic stub without any network call — use it to verify
   the loop, policy, tools and logging without depending on any external LLM.

   Automatic model selection is deliberately policy-controlled so a new key
   does not silently change spend. The default `conservative` policy preserves
   explicit role env vars, then custom registry models, then the default model.
   Enable automatic role/cost selection explicitly:
   ```powershell
   $env:AGENT_MODEL_POLICY="balanced"   # planner/repair quality, memory/verifier cheaper
   $env:AGENT_MODEL_POLICY="cost"       # prefer cheaper available role models
   $env:AGENT_MODEL_POLICY="quality"    # prefer strongest available role models
   $env:AGENT_MODEL_POLICY="offline"    # prefer mock routes, no paid calls
   $env:AGENT_MODEL_MAX_COST="medium"   # free | low | medium | high | unknown
   ```

   Registry entries are selectable only when their provider is supported and
   the required key is present (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HF_TOKEN`).
   Use `:models` to see the active routes, policy, availability and registry.
   Use `:model-registry-audit` when the route list looks "too small": it
   explains that routes are selected per role, while the registry is the wider
   candidate catalog. It also lists unavailable models, unsupported providers
   and available candidates that were not selected by the current policy.

   Built-in model names are safe defaults, not a live provider catalog. To keep
   model names fresh without editing Python code, copy the example registry:
   ```powershell
   Copy-Item .\config\model_registry.example.json .\config\model_registry.json
   ```

   Then replace the `model` values after checking the provider documentation
   or API model list. The agent reads `config/model_registry.json`
   automatically at startup. A different file can be selected with:
   ```powershell
   $env:AGENT_MODEL_REGISTRY_PATH="C:\path\to\model_registry.json"
   ```

   Knowledge pipeline persistence is local. Source/claim catalog writes go
   to `data/source_registry.jsonl` when persistent storage is enabled.
   Automatic writes from approved claims into long-term memory are off by
   default; enable them explicitly:
   ```
   AGENT_KNOWLEDGE_AUTO_WRITE=true
   ```

   Optional role-specific routing lets the core use different models for
   different jobs while keeping one Agent Core:
   ```
   AGENT_PLANNER_PROVIDER=openai
   AGENT_PLANNER_MODEL=gpt-4o-mini
   AGENT_SYNTHESIZER_PROVIDER=anthropic
   AGENT_SYNTHESIZER_MODEL=claude-sonnet-4-5
   AGENT_REPAIR_PROVIDER=openai
   AGENT_REPAIR_MODEL=gpt-4o-mini
   AGENT_MEMORY_PROVIDER=mock
   AGENT_MEMORY_MODEL=mock-1
   AGENT_VERIFIER_PROVIDER=mock
   AGENT_VERIFIER_MODEL=mock-1
   ```

   New models under an already-supported provider do not require code
   changes. Add them to the registry and assign roles:
   ```powershell
   $env:AGENT_MODEL_REGISTRY_JSON='[
     {
       "id": "future-coder",
       "provider": "openai",
       "model": "gpt-future-coder",
       "roles": ["planner", "repair_proposal"],
       "quality_tier": "frontier",
       "cost_tier": "medium",
       "context_window": 256000,
       "requires_env": ["OPENAI_API_KEY"]
     }
   ]'
   ```

   Precedence is: explicit `AGENT_<ROLE>_MODEL` variables, then custom registry
   entries from `config/model_registry.json` / `AGENT_MODEL_REGISTRY_PATH` /
   `AGENT_MODEL_REGISTRY_JSON`, then built-in defaults. The registry only
   selects providers that the local `LLM` adapter supports today: `anthropic`,
   `openai`, `huggingface`, and `mock`. New model names under those providers
   can be added through JSON without a code change.

   Model usage is recorded locally in `data/model_usage.jsonl`. Use
  `:model-usage` to inspect calls, tokens and rough `cost_units` by role/model.
   These are budget-control estimates, not provider billing numbers. Optional
  per-session caps stop the next model call before it is sent:
   ```powershell
   $env:AGENT_MODEL_MAX_CALLS_PER_SESSION="20"
   $env:AGENT_MODEL_MAX_TOKENS_PER_SESSION="100000"
   $env:AGENT_MODEL_MAX_COST_UNITS_PER_SESSION="300"
   ```

   Persistent budget windows protect across CLI restarts and long work
   sessions. They are stored in `data/budget_ledger.jsonl`; zero means
   "not enforced":
   ```powershell
   $env:AGENT_BUDGET_HOUR_LLM_CALLS="20"
   $env:AGENT_BUDGET_DAY_LLM_CALLS="100"
   $env:AGENT_BUDGET_DAY_MODEL_TOKENS="300000"
   $env:AGENT_BUDGET_DAY_MODEL_COST_UNITS="500"
   ```

   Inspect them with:
   ```text
   :budget-window-status
   :budget-window-status --json
   ```

   Release hygiene checks the package boundary before sharing an artifact:
   ```text
   :release-audit
   :release-audit --json
   ```
   The release manifest excludes local-only artifacts such as `.env`, `.git`,
   `.venv`, `.pytest_cache`, `logs/`, `data/`, `credentials.json`,
   `token.json`, `client_secret.json`, `*.pem` and `*.key`. If these exist
   locally, the report lists them as present-but-excluded so packaging cannot
   silently leak developer state.

   Supply-chain hygiene checks whether the repository can be rebuilt from
   GitHub with controlled direct dependencies and CI release gates:
   ```text
   :supply-chain-audit
   :supply-chain-audit --json
   python scripts/audit_release.py
   ```
   `requirements.in` documents the direct dependency intent; `requirements.txt`
   pins the reviewed direct versions; `requirements.lock` locks the complete
   transitive environment with `sha256` hashes. `sbom.cdx.json` is generated
   from the lock file by `scripts/generate_sbom.py`, and CI checks it with
   `scripts/generate_sbom.py --check` before running the release audit. The CI
   workflow installs with `pip install --require-hashes -r requirements.lock`,
   runs `pip check`, release/supply audit, pytest, and branch coverage with an
   85% threshold.

   Architecture drift can be inspected without asking the LLM:
   ```text
   :architecture-audit
   :architecture-audit --json
   ```
   This command checks concrete code/test evidence for major layers and marks
   the current multi-agent state. Today the project has dry-run team contracts
   and a dry-run executor. Real multi-agent execution still needs long work
   session mode, persistent budget windows and subagent memory scopes.

## Usage

Two modes:

- **One-shot** (`--ask "…"`) — stateless. No working memory. Single
  Observe→Respond cycle. Persistent memory is still loaded read-only (so
  saved long-term facts can still flavour the answer).
- **Interactive** (no `--ask`) — a session with Working Memory **and**
  Persistent Memory. The planner and synthesizer see every prior turn plus
  any long-term records that share keywords with the question. Identical
  tool calls are served from the artifact cache. Type `:memory`,
  `:ingest-source`, `:ingest-project`, `:clear`, `:remember`, `:forget`,
  `:models`, `:model-registry-audit`, `:architecture-audit`, `:hygiene`,
  `:rollback`, `:help`, `:quit` for control.

One-shot examples:
```powershell
# General-knowledge question — planner returns empty plan, sources=[general-knowledge]
python main.py --ask "How does Dijkstra's algorithm work?"

# Web question — planner calls web_search
python main.py --ask "What is the latest stable Python 3 release?"

# File question — pass the file as a hint, planner calls file_read
python main.py --file "архитектура автономного Агента.txt" --ask "Сколько доменов в файле?"

# Compare question — planner calls BOTH tools
python main.py --file "архитектура автономного Агента.txt" `
  --ask "Сравни наши 12 доменов с публичными определениями"
```

Interactive (multi-turn dialogue + persistent memory):
```powershell
python main.py
> Что такое DuckDuckGo?              # turn 1: web_search
> А кто его основатель?              # turn 2: planner sees turn 1, decides what to do
> :remember preference,fact I prefer concise Russian answers
                                      # write-policy gated; saved to data/persistent_memory.jsonl
> :ingest-source "архитектура автономного Агента.txt"
                                      # read one UTF-8 text/code file into source_registry.jsonl
> :ingest-project . --limit 40 --dry-run
                                      # preview project ingestion without writing registry/memory
> :ingest-project . --limit 40 --write-memory
                                      # ingest project files and save approved claims to memory
> :memory                             # working memory JSON + persistent record list
> :forget mem_abc12345               # delete one persistent record by id
> :forget                             # delete ALL persistent records
> :hygiene                            # expire + dedupe + cleanup backups (one shot)
> :hygiene summarise project          # merge records tagged 'project' via LLM
> :hygiene backups --dry-run          # preview without deleting
> :rollback                           # undo the most recent shell_exec / file_write mutation
> :rollback list                      # show registered compensation plans
> :models                             # inspect active model routes + registry
> :model-registry-audit               # explain selected routes vs available candidates
> :model-usage                        # inspect LLM calls/tokens/cost units
> :architecture-audit                 # inspect implemented layers and multi-agent gaps
> :team-plan "AI news and business opportunity radar"
                                      # dry-run subagent contracts; does not run agents
> :team-run "AI news and business opportunity radar"
                                      # dry-run contract walk; no subagents/tools/models executed
> :propose-repair core/foo.py tests/test_foo.py
                                      # generate a validated RepairProposal without writing
> :repair core/foo.py tmp/foo.proposed.py tests/test_foo.py
                                      # guarded self-repair transaction:
                                      # diff -> approval -> write -> tests -> rollback on red
> :clear                              # wipe WORKING memory only (persistent untouched)
> :quit                               # exit
```

Interactive with file hint (artifact cache demo):
```powershell
python main.py --file "архитектура автономного Агента.txt"
> What is in the file?               # turn 1: file_read runs, artifact cached
> Show me another section.           # turn 2: planner picks file_read again
                                     #         -> Executor catches cache HIT,
                                     #            skips policy + tool + verify
```

CLI flags:
```
--ask "..."                   One-shot question (no memory). Omit for interactive REPL.
--file PATH                   Optional file hint. The planner MAY call file_read with it.
                              Without this hint, file_read is never used (planner rule).
--workspace PATH              Workspace root (default: cwd).
--auto-approve off|approve|deny
                              Approval policy for escalated (irreversible / external) actions.
                              off (default)  = CLI prompt in REPL, refusal in one-shot
                              approve        = auto-approve everything (tests / scripts only)
                              deny           = auto-deny everything (dry runs)
```

## What the cycle does

For each question the agent runs the full §3 cycle once:

```
Observe (question + optional file hint)
  -> Interpret (build Goal)
     -> Memory Inject (last N turns appended to prompts, if memory is on)
     -> Persistent Memory Inject (long-term records overlapping with the
        question, if any, gated by MemoryRetrievalPolicy)
        -> for attempt in 1..max_replan_attempts:
            -> Planner (LLM picks 0..N tools, sees conversation history
                        + <long_term_memory> + <replan_context> when attempt>1)
              -> Plan (one PlanStep per chosen tool)
                 for each step:
                   if (tool, arguments) is in memory cache:
                     -> Cache Hit (skip policy + tool + verify)
                   else:
                     -> Act -> Policy Gate
                        if Policy says ESCALATE:
                          -> Approval Request -> Approval Decision
                             approve -> continue
                             deny / abort / no provider
                                 -> error + ReplanTrigger; step fails
                        -> Tool -> ToolResult
                           tool exception -> ReplanTrigger(tool_error)
                        -> Tool Result Validation
                           rejected -> ReplanTrigger(verify_failed)
                        -> Data Classifier (public|private|sensitive|secret)
                        -> Secret Scan -> Redact (deep)
                           [ secret_detected event if any kind found ]
                        -> Cache Store (REDACTED artifact only)
              if plan was non-empty AND no artifact survived:
                -> emit `replan` event with cumulative triggers
                -> next attempt; planner gets a <replan_context> block
              else:
                -> exit replan loop, go to Respond
            after the final attempt:
              -> emit error.code=replan_exhausted (synth still runs)
        -> Respond (LLM synthesizes Output Contract;
                    history + evidence + long_term_memory + failure_context
                    all available)
        -> Memory Write (turn appended)

(Out-of-band: `:remember` writes go through MemoryWritePolicy on demand
  -> Persistent Memory Write (save / reject + reasons))
```

Every phase emits one structured log line. See `logs/<trace_id>.jsonl`.

## The Planner (MVP-3)

Implemented in [`core/planner.py`](core/planner.py). At temperature 0.0 the LLM
returns a strict JSON object:

```json
{
  "reasoning": "Why these tools were chosen",
  "steps": [
    {"tool": "file_read", "arguments": {"path": "..."}, "rationale": "..."},
    {"tool": "web_search", "arguments": {"query": "...", "max_results": 5}, "rationale": "..."}
  ]
}
```

Hard rules the Planner enforces in code (not just in the prompt):

- Tools not in the `ToolRegistry` are dropped.
- `file_read.path` MUST equal the user-provided `--file` hint; mismatches get
  remapped or dropped (the model cannot wander the workspace).
- `web_search.max_results` is clamped to `[1, 10]`.
- Markdown fences (` ```json ... ``` `) are stripped automatically.
- Malformed JSON falls back to an empty plan + logged warning.

The four routing scenarios — verified end-to-end:

| Question style | Planner output | Sources in answer |
| --- | --- | --- |
| About the file | `[file_read]` | `[file:...]` |
| External / current info | `[web_search]` | `[web:...]` |
| Compare file vs world | `[file_read, web_search]` | both |
| Pure general knowledge | `[]` | `[general-knowledge]` |

To run the full loop offline (no API key), use `AGENT_PROVIDER=mock`. The mock
LLM contains a keyword heuristic that emits the same JSON shape, so the
Planner→Executor→Synthesizer wiring is exercised without spending tokens.

## Working Memory (MVP-4)

Implemented in [`core/memory.py`](core/memory.py). Two views over the same session:

**Conversation log.** Each completed turn is recorded as a `Turn` with
`question / tools_used / artifact_labels / answer`. The last 5 turns are
formatted as `<conversation_history>` and injected into both the planner's
prompt (so it can avoid redundant tool calls) and the synthesizer's prompt
(so the dialogue stays coherent). Bounded by `max_turns=10` and
`max_context_chars=8_000`.

**Artifact cache.** Tool outputs are keyed by `(tool_name, JSON(args))` and
reused across turns. The planner does not need to know about the cache: even
if it picks `file_read` again with the same path, the Executor short-circuits
the call with a `memory_cache_hit` log event. This is defense-in-depth —
a smart planner skips the call upfront, a dumb planner still gets caught.

Interactive REPL commands:
```
:mem       show session_id, turn count, cached artifact labels
:clear     wipe memory and start fresh (logged as memory_clear)
:help      list commands
:quit      exit
```

Logged memory events:
```
memory_inject     before planning: which turns + how many chars + cache size
memory_cache_hit  in the Executor: which tool call was skipped, when stored
memory_write      after the response: which turn was appended
memory_clear      when the user runs :clear
```

## Persistent Memory (MVP-5)

Implemented in [`core/persistent_memory.py`](core/persistent_memory.py) +
[`core/memory_policy.py`](core/memory_policy.py). One JSONL file on disk,
one record per line, append-only writes, full-file load on read. Explicitly
no SQLite, no embeddings, no vector index, no RAG — the brains live in the
two policies below.

**Default location:** `<workspace>/data/persistent_memory.jsonl`.

**What lands on disk.** Only what the user explicitly tells the agent to
remember, or what carries a consent tag (`preference`, `fact`, `decision`,
`insight`, `user-approved`, `project`). Everything else is rejected without
hitting disk. The full reject ladder (every branch covered by tests):

| Reject reason | Trigger |
| --- | --- |
| empty content | content is whitespace or missing |
| too short / too long | < 4 chars or > 4 000 chars |
| matches secret pattern | OpenAI / Anthropic / GitHub / HuggingFace / AWS / PEM blocks |
| contains secret keyword | `api_key`, `password`, `private_key`, `Authorization:`, … |
| tool-result dump | content contains `"url": "https://..."` (looks like raw `web_search` JSON) |
| blocked tag | tags include `transient`, `temporary`, `do-not-save`, `ephemeral` |
| no consent signal | source is not `user-explicit` AND no consent tag |

Pattern + keyword rejects are non-negotiable: they trigger even with
`source="user-explicit"` and a `fact` tag. The user does not get to opt into
leaking their own keys.

**What gets retrieved.** [`MemoryRetrievalPolicy`](core/memory_policy.py)
runs before every cycle:
- tokenises the question and each record (stopword-filtered, EN + RU)
- scores by keyword overlap (content + tags)
- requires `score >= 1`, sorts by score desc, then by recency
- keeps the top 3, truncates each to 400 chars
- formats as `<long_term_memory>` and prepends to the planner + synthesizer
  prompts
- records may then be cited in the answer with `[memory:<record_id>]`

When no record scores above threshold, the block is omitted entirely — the
synthesiser falls back to evidence + history + general knowledge as before.

**Public façade on `AgentLoop`** (used by the CLI, fully testable):
```python
agent.remember(content, tags, source="user-explicit") -> (decision, record|None)
agent.list_persistent() -> list[MemoryRecord]
agent.forget(record_id=None) -> int   # None = wipe all
```

REPL commands:
```
:memory                          working memory JSON + persistent record list
:remember [tags] <text>          save (gated by MemoryWritePolicy)
                                 default tag when omitted: user-approved
                                 e.g. :remember decision keep planner LLM-driven
:forget [id|all]                 delete one record by id, or wipe everything
:hygiene [subcmd] [--dry-run]    memory hygiene (MVP-10); subcmds:
                                   backups    — drop old `.bak.<ts>` files (keep last 3, >14d old)
                                   expire     — drop persistent records past their TTL
                                   dedupe     — collapse near-duplicates (oldest kept as canonical)
                                   summarise <tag>  — merge records sharing <tag> via LLM
                                   (no subcmd)      — runs expire, then dedupe, then backups
:rollback [plan_id|list]         compensation rollback (MVP-11): LIFO pop if no arg,
                                   apply specific plan by id, or `list` to see registered plans
:clear                           wipe WORKING memory only (persistent untouched)
```

Logged persistent-memory events:
```
persistent_memory_load     at session start: path + records_loaded + ids
persistent_memory_inject   before planning: total / selected / ids / chars
persistent_memory_write    on :remember: decision (save|reject) + reasons + record_id
persistent_memory_delete   on :forget: scope (one|all) + record_id + deleted count
persistent_memory_expire   on :hygiene expire: scanned + expired_count + expired_ids
persistent_memory_dedupe   on :hygiene dedupe: threshold + scanned + groups + deleted_ids
persistent_memory_summarise on :hygiene summarise <tag>: tag + summarised_count + new_record_id
backup_cleanup             on :hygiene backups: scanned + deleted + kept + dry_run + paths
compensation_registered    after every successful mutating tool_call: plan_id + tool_name + tool_call_id
compensation_apply         on :rollback: plan_id + action_count + ok/noop/error counts + per-action outcomes
```

## Human Approval (MVP-6)

Implemented in [`core/approval.py`](core/approval.py) + new branch in
[`core/loop.py`](core/loop.py) `_execute_step`. The PolicyGate already
labels every Action with a risk verdict; MVP-6 takes the `escalate` branch
and routes it to a human.

**Decision flow per step:**
```
PolicyGate.check(action)
  ├─ allow      → run the tool
  ├─ deny       → ErrorObject(policy_blocked)
  └─ escalate   → ApprovalProvider.request(ApprovalRequest)
                    ├─ approve     → run the tool
                    ├─ deny        → ErrorObject(approval_deny)
                    ├─ abort       → ErrorObject(approval_abort)
                    └─ (no provider configured)
                                   → ErrorObject(approval_unavailable)
```

**Three provider implementations** (selected at agent build time):

| Provider | When | Behaviour |
| --- | --- | --- |
| `None` | one-shot `--ask` with default `--auto-approve off` | escalated actions are refused outright (`approval_unavailable`) — the safe default |
| `CLIApprovalProvider` | interactive REPL with default flags | prints the request to stderr and reads `y/yes/да` (approve), `n/no/нет` (deny), anything else (abort) from stdin |
| `AutoApprover(default=…)` | tests, scripts, or `--auto-approve approve\|deny` | always returns the configured verdict — never asks the human |

**Input parsing (`_classify`).** Case-insensitive, trimmed. Accepts both
English and Russian shortcuts:
```
approve  ← y, yes, YES, Yes, д, да, approve, ok, okay
deny     ← n, no, NO, нет, deny, cancel
abort    ← "", "   ", any unrecognised string, EOF, KeyboardInterrupt
```

**CLI flag.** `--auto-approve {off|approve|deny}` switches the provider:
- `off` (default): CLI prompt in REPL, refusal in one-shot
- `approve`: every escalation auto-approved (use for scripts / regression tests only)
- `deny`: every escalation auto-denied (useful for dry runs)

**Acceptance criteria — all proven by tests:**
1. `read_only` tool runs without an approval event
2. `irreversible` tool emits an `approval_request`
3. `approve` → `tool_call` fires, output flows through verification
4. `deny` → NO `tool_call` / `tool_result` / `verify` events; `error.code=approval_deny`
5. empty / unrecognised / EOF → `abort`, NO tool execution; `error.code=approval_abort`
6. JSONL contains both `approval_request` AND `approval_decision`, linked by `request_id` ↔ `policy_decision_id`
7. No provider wired → escalated action refused with `approval_unavailable`

Logged approval events:
```
approval_request    risk + tool_name + arguments + reasons + policy_decision_id
approval_decision   approve|deny|abort + responder (user|auto|timeout) + reasons + raw_input
```

Event order on an escalated step:
```
act → policy(escalate) → approval_request → approval_decision
                                            ├─ approve → tool_call → tool_result → verify
                                            └─ deny/abort → error
```

## Safety Layer (MVP-7)

**Principle:** *безопасность решает не LLM, безопасность решает ядро.*
The model can propose an action; the kernel decides whether the inputs,
the outputs, and the logs are allowed to contain what they contain.

Three kernel modules, all in `core/`:

| Module | Job |
| --- | --- |
| [`secret_scanner.py`](core/secret_scanner.py) | Single source of truth for credential detection. Regex rules (OpenAI / Anthropic / GitHub / HuggingFace / AWS / Bearer / PEM / `KEY=VALUE` assignments) + keyword rules (`password`, `api_key`, `Authorization:`, …). Every consumer (Memory Write Policy, Redaction, Data Classifier, the loop) asks **this** module — nobody rolls their own patterns. |
| [`dlp.py`](core/dlp.py) | Detects sensitive PII markers (email, SSN, international `+phone`) and returns typed findings for logs, memory and prompt-boundary policy. |
| [`redaction.py`](core/redaction.py) | `redact_text(s) → (safe, findings)` remains the credential-only primitive. `redact_dlp_text(s)` masks both credentials and PII; `redact_payload(obj)` deep-walks dicts / lists / tuples for the logger. Handles overlapping matches and preserves shape. |
| [`data_classifier.py`](core/data_classifier.py) | Returns one of `public / private / sensitive / secret` for any text + source hint. SECRET signals beat everything; PII (email / SSN / `+phone`) → SENSITIVE; otherwise per-source default (`web` → public, `file` / `cli` → private). |

**Hard invariants, proven end-to-end by the integration test:**

1. **A raw secret or sensitive PII in a `file_read` output never reaches the JSONL trace.**
   `TraceLogger` runs every payload (and `extra` kwargs) through
   `redact_payload` BEFORE serialisation. The stderr pretty-print sees
   the same redacted view.
2. **A raw secret or sensitive PII never reaches the LLM.** `LLMPlanner.plan` redacts the
   `user` prompt; `AgentLoop._synthesize` redacts the synthesizer prompt
   (including the user question + planner reasoning + every evidence
   block). FakeLLM tests assert no raw secret or PII appears in `system` or
   `user` for any of the recorded calls.
3. **Even an LLM hallucinating a credential or PII gets scrubbed.** The final
   answer goes through one more `redact_dlp_text` pass on the way out. Credential
   leaks are logged as `secret_detected`; PII leaks are logged as
   `sensitive_detected`, both with `surface=user_output`.
4. **Working memory caches the REDACTED artifact**, so a cache hit on a
   future turn replays redacted text, not the raw value.
5. **Persistent memory still refuses to save anything carrying secret
   signals** (regex OR keyword), even with explicit consent. This was
   already true in MVP-5; MVP-7 routes the check through the same
   single source of truth so adding a new pattern propagates everywhere.
6. **Persistent memory rejects PII unless explicitly tagged with
   `sensitive-data-consent`, and even then stores only the redacted form.**

**Per-cycle events:**
```
data_classified    one per surface (user_question + each tool output).
                   payload: label, class (public|private|sensitive|secret), reasons
secret_detected    fires whenever the classifier returns SECRET.
                   payload: label, surface (user_input | tool_output | user_output),
                            kinds, count
sensitive_detected fires whenever PII is found on a protected boundary.
                   payload: label, surface (user_input | tool_output |
                            user_output | persistent_memory), kinds, count
respond            now also reports `redactions` count for the cycle
```

**Data Owner Tagging (§7 «данные других людей»).** MemoryRecord's `owner`
field gates persistence:

| Owner value | Behaviour |
| --- | --- |
| `self` / `user` / `session` (first-party whitelist) | save allowed |
| anything else (e.g. `client`, `partner`, …) | **reject** unless the record carries the `cross-owner-consent` tag |

The default owner is first-party, so existing call sites keep working.
The gate fires only when someone explicitly says "this is somebody
else's data" — which is exactly when we want a deliberate consent step.

**Output Contract — new `Safety:` section.** When the kernel redacts
anything during a cycle it builds a `<safety_notes>` block and feeds it
into the synthesizer prompt. The LLM is told to summarise this block
under `Safety:` in plain language ("the kernel — not the model —
redacted N credentials of kind X from surface Y"). Clean cycles render
`Safety: nothing` and emit no `secret_detected` events at all.

**What's still TODO** (deliberately deferred to keep MVP-7 narrow):
secret-vault integration / rotation, structured `data_owner` field on
every Action + Observation, `allowed_purpose` and retention TTL fields,
external DLP allow/deny rules for web-search egress.

## Re-planning (MVP-8)

**Principle:** *re-planning is not a retry loop — it is a new plan with
explicit evidence of what went wrong.* The agent does not blindly re-run
the same step on failure; it asks the planner for a different approach,
and it always stops after a hard ceiling so the loop cannot become
unbounded.

Wired-up failure → trigger mapping (every code drives a `replan` event
plus a `<replan_context>` block on the next attempt):

| Failure during step execution | `ReplanTrigger.code` |
| --- | --- |
| PolicyGate denied (unknown tool, …) | `policy_blocked` |
| Approval gate returned `deny` | `approval_deny` |
| Approval gate returned `abort` (empty / unrecognised input) | `approval_abort` |
| Escalated step but no `ApprovalProvider` wired | `approval_unavailable` |
| Tool raised / returned `status=error` | `tool_error` |
| Tool Result Validation rejected the output | `verify_failed` |

The planner is told about every code and is given concrete guidance per
code in `PLANNER_SYSTEM`:

- `tool_error` → change arguments or pick another tool;
- `verify_failed` → output was empty / invalid (e.g. `web_search`
  returned `[]`); try different arguments or drop the step;
- `approval_deny` / `approval_abort` → a human refused this risk —
  propose a **safer** alternative (typically a read-only tool) or
  return `"steps": []` and let the synthesizer explain honestly;
- `approval_unavailable` → no approval channel exists; same response;
- `policy_blocked` → the tool you picked isn't registered; pick a
  different one or empty plan.

**Hard ceiling, configurable per AgentLoop:**

```python
agent = AgentLoop(
    ...,
    max_replan_attempts=3,   # default. 1 disables replan entirely.
)                            # 0 is rejected at construction time.
```

**Acceptance criteria proven by `test_replan.py` (11 cases):**

1. A clean first attempt fires **zero** `replan` events.
2. A 0-step plan (general-knowledge answer) is success, not a failure.
3. `tool_error` on attempt 1 → planner re-invoked → attempt 2 succeeds.
4. `verify_failed` on attempt 1 → planner re-invoked → attempt 2 succeeds.
5. `approval_deny` on attempt 1 → planner re-invoked → **the irreversible
   tool never executes**, the safer read-only tool runs on attempt 2.
6. When every attempt fails the same way: `max_replan_attempts` attempts
   run, the loop emits `error.code=replan_exhausted`, **the synthesizer
   still runs and produces a structured Output Contract reply** (so the
   user gets an honest summary instead of a bare error string).
7. Attempt N (N ≥ 2) always sees a `<replan_context>` block in the
   planner prompt with `attempt="<N>"`, `code=<code>`, `tool=<name>`,
   and a human-readable `reason:` line per prior failure.
8. Every `planner` and `plan` event carries an `attempt` field. The
   `respond` event reports `attempts_used` and `replan_exhausted`.
9. `max_replan_attempts=1` is legal and disables re-planning (one shot,
   then fail-honestly). `max_replan_attempts=0` is rejected by the
   constructor — the kernel always runs at least one attempt.

**New events:**

```
replan              between failed attempts.
                    payload: attempt, next_attempt, max_attempts,
                             triggers (codes), details (per-step)
error               with code=replan_exhausted after the final attempt.
                    payload: context.attempts, context.failure_codes
planner / plan      now carry `attempt` (planner also has
                    `replan_context_chars` so audits can see whether
                    the planner was informed).
respond             now carries `attempts_used` + `replan_exhausted`.
```

**Output Contract — `<failure_context>` block.** When `replan_exhausted`
fires, `_synthesize` builds a `<failure_context>` block from
`failure_history` and injects it into the synthesizer prompt. The LLM is
explicitly told to write an **honest** Conclusion: list what was tried,
mark the original information need as Unverified, and avoid inventing
content the tools failed to provide.

**What's still TODO** (deliberately deferred to keep MVP-8 narrow):
selective per-step retries (only re-run the failed step instead of
re-planning the whole cycle), per-failure-type strategies (e.g. retry
on `web_empty` once before replanning), and exponential backoff /
jitter for flaky external services.

## file_write — first non-read-only tool (MVP-9)

**Principle:** *side effects cost trust*. The first tool that can
change the workspace is built so the kernel — not the LLM — decides
whether a write is safe enough to dispatch.

**Argument-aware risk classification (§5).** Before MVP-9 every tool
had a fixed risk class. `file_write` cannot — *creating* a new file is
reversible (the user can delete it), but *overwriting* an existing one
crosses a trust boundary. So the Tool base class learnt a new method:

```python
class Tool(ABC):
    risk: Risk = "read_only"            # static fallback

    def risk_for(self, arguments: dict) -> Risk:
        """Override to inspect arguments and pick a stricter / laxer
        class. PolicyGate calls this method, NOT `self.risk` directly.
        """
        return self.risk
```

`PolicyGate.check()` now reads `tool.risk_for(action.parameters)`.
Tools that don't override it keep the old behaviour (15 of 15 existing
policy tests still pass). `FileWriteTool.risk_for` resolves the path
against the workspace, returns `reversible` when the path is new and
`irreversible` when it already exists or looks like a sandbox escape.
The static fallback `risk = "irreversible"` is the strict bucket — any
code path that bypasses `risk_for` lands in the same place approval
must protect.

**Five hard rules in `FileWriteTool.run()`** (defence in depth — every
one is enforced inside the tool too, not just by the planner / gate):

1. `path` must be a non-empty string.
2. `content` must be a `str` (refuses `bytes`, `list`, …).
3. UTF-8-encoded content must be `<= MAX_BYTES` (default 1 MiB).
4. Resolved path must stay inside `workspace_root` (`PermissionError:
   Path escapes workspace`).
5. `content` must not contain any high-confidence credential pattern
   (`core.secret_scanner.contains_secret`); on hit, the tool raises
   `PermissionError: refusing to write credentials to disk: <kind>`
   and writes nothing — **not even the backup is created**, so a
   blocked write cannot leak the previous file into a `.bak.*`
   side-effect.

**Backup-on-overwrite.** When the resolved path exists, `run()`
`shutil.copy2`s it to `<path>.bak.<YYYYMMDDTHHMMSSZ>` *before* writing
the new content. The backup path is workspace-relative and surfaces in
the tool's structured output:

```json
{
  "path": "out/doc.txt",
  "mode": "overwrite",
  "bytes_written": 24,
  "backup_path": "out/doc.txt.bak.20260525T160338Z"
}
```

**Acceptance criteria proven** (8 end-to-end tests in
`tests/test_file_write_integration.py`, plus 42 unit tests in
`tests/test_file_write.py`):

1. **New file create runs without approval.** No `approval_request` /
   `approval_decision` events; `policy.reasons` includes `reversible
   action (file_write)`.
2. **Outside-workspace path is rejected.** Surfaces as `tool_result.
   status=error` with `escapes workspace`; no file is created above
   the workspace root.
3. **Overwrite escalates.** `policy.decision=escalate`,
   `approval_request` event carries the full args + risk;
   `AutoApprover` is called exactly once.
4. **Deny / abort → file untouched.** No `tool_call` / `tool_result`
   events; no `.bak.*` file is created (the tool never ran).
5. **Approve → file changed.** Tool runs, output contains
   `mode="overwrite"` and the resolved `backup_path`.
6. **Backup is created before overwrite.** The `.bak.<ts>` file holds
   the original content byte-for-byte.
7. **Secrets in `content` are refused.** Even on the create codepath
   where the gate allows the write, the tool itself returns
   `tool_result.status=error` with `credentials`; the JSONL trace
   contains zero copies of the raw secret.
8. **JSONL records the full sequence.** `policy` → `approval_request`
   → `approval_decision` → `tool_call` → `tool_result(mode, backup_path)`
   → `verify` → `data_classified(source=tool_output)`.
9. **No approval provider wired → overwrite is impossible.** Loop
   emits `error.code=approval_unavailable`; original file is untouched.

**Planner is now told about three tools.** The `PLANNER_SYSTEM` prompt
describes `file_write(path, content)` with explicit guard rails
("ONLY when the user explicitly asks to save / write / store",
"NEVER include any credential", "NEVER write paths starting with
`/`, `\\`, or containing `..`"). The planner's `_sanitize_step`
catches escape attempts before they cost a re-plan slot.

**Live-verified** end-to-end with real Anthropic Claude: create
without approval, overwrite without provider correctly blocked with
`approval_unavailable` + MVP-8 graceful empty-plan replan, overwrite
with `--auto-approve approve` creates the backup and writes the new
content, and an AWS access key smuggled in the user question is
sterilised by MVP-7 redaction *before* Claude composes the file —
nothing leaks to disk.

## Output Contract

The LLM is instructed to produce this exact structure (in the user's language):

```
Conclusion: <1-2 sentences>

Facts:
  - Fact A ... [file:path/to/doc.txt]
  - Fact B ... [web:https://example.com/page]

Sources:
  1. file:path/to/doc.txt - path/to/doc.txt
  2. web:https://example.com/page - https://example.com/page

Confidence: low | medium | high

Unverified: <what the evidence does NOT cover, or "nothing">

Safety: <plain-language summary of any kernel-side redaction this cycle, or "nothing">
```

Validator warnings (empty web results, missing snippets, …) are forwarded to the LLM
under `<validator_notes>` so they can surface in the `Unverified:` section.
The kernel-built `<safety_notes>` block (when present) is summarised under
`Safety:` — the LLM is explicitly told the redaction was performed by
the kernel, not by itself, and that `[REDACTED:*]` tokens must NEVER be
expanded back into their underlying values. When re-planning was
exhausted, the kernel-built `<failure_context>` block lists every prior
attempt's `code` / `tool` / `reason`, and the synthesizer is told to
write an honest Conclusion describing what was tried and put the
original information need under `Unverified:`.

## Logs

Logs live in `logs/<trace_id>.jsonl`. One line per phase, with full payloads.
Useful for replay-debugging and for the future §6 Replay / Time-Travel Debugging.

Phases recorded (MVP-8):
```
session_start
[ persistent_memory_load ]                     # if persistent store wired
observe  interpret
data_classified                                # user_question
[ secret_detected ]                            # if the question itself leaked a credential
[ memory_inject ]                              # if working memory has prior turns
[ persistent_memory_inject ]                   # if any long-term records overlap

# Re-planning loop — repeats up to `max_replan_attempts` times.
for attempt in 1..max_replan_attempts:
  planner    (reasoning + tools_chosen + warnings + attempt + replan_context_chars)
  plan       (PlanSteps; extra carries attempt)
  [ per step:
      memory_cache_hit                         # OR
      act -> policy
         if policy=escalate:
           approval_request -> approval_decision
             approve  -> tool_call -> tool_result -> verify
                         data_classified                    # tool output
                         [ secret_detected, surface=tool_output ]
             deny     -> error(approval_deny)
             abort    -> error(approval_abort)
         else:
           tool_call -> tool_result -> verify
           data_classified
           [ secret_detected, surface=tool_output ]
           [ compensation_registered ]                # if tool emitted a non-noop plan (MVP-11)
  ]
  if attempt failed (plan had steps AND no artifact survived):
    if attempt < max_replan_attempts:
      replan   (attempt, next_attempt, max_attempts, triggers[], details[])
    else:
      error    (code=replan_exhausted)        # synth still runs after this

respond    (chars + sources + redactions + attempts_used + replan_exhausted)
[ secret_detected, surface=user_output ]       # defence-in-depth on the final answer
[ memory_write ]                               # if working memory is on

Out-of-band (REPL only):
[ persistent_memory_write    ]                 # on :remember
[ persistent_memory_delete   ]                 # on :forget
memory_clear                                   # on :clear
[ persistent_memory_expire   ]                 # on :hygiene expire
[ persistent_memory_dedupe   ]                 # on :hygiene dedupe
[ persistent_memory_summarise]                 # on :hygiene summarise <tag>
[ backup_cleanup             ]                 # on :hygiene backups
[ compensation_apply         ]                 # on :rollback
```

## Memory hygiene (MVP-10)

Persistent memory, the working-memory cache, and `file_write` backups
all accumulate. MVP-10 adds four **deliberate** cleanup policies — each
is an explicit operation, never a side effect of another action, and
each emits exactly one audit event carrying its typed report.

| Policy | Trigger | What it removes | Safety floor |
| --- | --- | --- | --- |
| **Backup cleanup** | `:hygiene backups` | `<file>.bak.<ts>` files in the workspace, where MORE than `keep_last` newer backups exist for the same target AND the file is older than `max_age_days` | `keep_last=3` is unconditional — a sole backup is never deleted, regardless of age |
| **Write-time dedup** | every `:remember` / `agent.remember()` call | refuses to persist a record whose normalised content is ≥ `0.85` similar (word-Jaccard + substring containment) to any existing record | secret check + tool-dump heuristic + consent gate still run first, so a `password=…` near-duplicate is refused for being a secret, not for being a duplicate |
| **Post-hoc dedup** | `:hygiene dedupe` | among records already on disk, collapses near-duplicate groups, keeping the OLDEST as canonical (deterministic; second run finds zero groups) | empty store / single record → no-op; threshold can be tightened per call |
| **TTL / expiration** | `:hygiene expire` | persistent records whose `created_at + ttl_seconds < now` | records with `ttl_seconds in (None, 0)` are NEVER expired (default semantics: "keep until forgotten") |
| **Summarisation** | `:hygiene summarise <tag>` | up to `max_records=10` records sharing `<tag>` are merged via the LLM into ONE record tagged `<tag>` + `summarised`; originals are removed | already-summarised records (`summarised` tag) are excluded → re-runs are no-ops; LLM exceptions / empty output leave disk untouched; tag must be non-empty and not `summarised` |

Every cleanup function accepts `dry_run=True` — the report shows what
WOULD be removed, no disk mutation. `:hygiene` without a subcommand
runs `expire → dedupe → backups` in order (summarise is excluded
because it needs an explicit tag). Bulk-run is intentionally
deterministic: each step's report is logged independently so an audit
of `--dry-run` mirrors exactly what the real run will do.

### Acceptance — proven by tests

- **Live, end-to-end:** seeded 5 backups for one file (1 of them 15 days old), called `:hygiene backups` → exactly 1 file removed, the 4 within the floor + cutoff window untouched, active file untouched.
- **Live, end-to-end:** `:remember` the same fact twice → first saved, second rejected with `reasons=['duplicate of mem_xxxx (similarity=1.00 >= 0.85)']`, exactly 1 record on disk.
- **Live, end-to-end:** added 3 near-duplicate legacy rows directly to JSONL, called `:hygiene dedupe` → 2 deletions, oldest kept as canonical, `persistent_memory_dedupe` audit event carries both removed ids.
- **Live, end-to-end:** record with `ttl_seconds=60` created 1 h ago + a `ttl_seconds=None` record → `:hygiene expire` removes only the first, `persistent_memory_expire` event carries the removed id.
- **Live, end-to-end:** 3 records tagged `project` + real Anthropic provider → `:hygiene summarise project` produces ONE merged record carrying both `project` and `summarised` tags; second run is a no-op (`skipped_reason='no records'`).

## shell_exec — narrow sandboxed shell (MVP-11)

`shell_exec` is the agent's second irreversible tool — and the first
one that runs subprocesses. The contract is **deliberately tiny**:

| Class | Whitelist | Risk | Approval | Compensation |
| --- | --- | --- | --- | --- |
| **read_only** | `whoami`, `hostname`, `where` / `which` | `read_only` | not required | `noop` plan (audit-uniform) |
| **mutating** | `mkdir <path>`, `touch <path>` (exactly one path arg, inside workspace) | `irreversible` | required | `delete_path_if_created` plan, built **before** the side effect |
| **everything else** | — | `external` | required, AND `run()` still refuses (defence in depth) | — |

### Hard rules — enforced in three places

1. **Planner sanitiser** (`core/planner.py`) drops the step at build time.
2. **PolicyGate** (`core/policy.py`) uses `tool.risk_for(arguments)` to escalate or deny.
3. **Tool `run()`** (`tools/shell_exec.py`) re-validates everything just before dispatch — every layer above could be bypassed and the tool still refuses.

What every layer enforces:

- argv must be a **list of non-empty strings**, length ≤ 16
- `argv[0]` must be in the whitelist (lowercase-normalised)
- no element may contain any of `; | & < > \` $ ( ) [ ] { } \n \r \t \0`
- no element may start with `~` or contain `$`
- mutating commands accept exactly one path argument; path must NOT start with `/` or `\`, must NOT carry a drive letter (`C:\…`), must NOT contain `..`, and must resolve **inside** `workspace_root`
- mutating commands run **in-process** (`Path.mkdir` / `Path.touch`) — `cmd.exe` is never invoked, so shell-builtin escape hatches do not exist on Windows
- read-only commands run via `subprocess.run(shell=False, cwd=workspace, env={PATH, [SystemRoot]}, timeout=5s)`
- stdout/stderr are **truncated to 64 KiB** and run through `core.redaction.redact_text` before they leave the tool

### Compensation plan — built BEFORE the side effect

Every `shell_exec` invocation produces a typed `CompensationPlan`:

- read-only commands carry a single `noop` action (the registry skips noops to keep `:rollback` semantically meaningful)
- `mkdir <path>` on a path that **did not exist before** carries a single `delete_path_if_created` action targeting that path
- `mkdir <path>` on a path that **already existed** refuses with a clean error — no half-baked state, no compensation registered
- `touch <path>` on a path that already existed produces a `noop` plan (we did not create the file, so rollback should not delete it)

The plan rides on `tool_result.output["compensation_plan"]`. `AgentLoop`
extracts it after `tool_call` succeeds, skips noop plans, and appends
the rest to `self.compensation_log` (LIFO). `compensation_registered`
is logged with `plan_id`, `tool_name`, `description`, `action_count`,
and the originating `tool_call_id`, so every rollback can be traced
back to its source call.

### `:rollback` — apply compensation by LIFO or by id

```
:rollback                 # apply the most recent plan, pop it
:rollback comp_abcd1234   # apply this specific plan, pop it
:rollback list            # show registered plans (oldest first)
```

Inside the workspace root the action set is small and idempotent:

- `delete_path_if_created` removes a path — succeeds if already absent (`status='noop'`)
- `restore_from_backup` copies a `.bak.<ts>` file back over the target then deletes the backup — succeeds if backup is already gone (`status='noop'`)
- Every action that fails the sandbox check (path escapes workspace) returns `status='error'` and does NOT abort the rest of the plan — the per-action result is recorded individually in the `CompensationReport`

### Acceptance — proven by tests AND live

Every one of the nine MVP-11 acceptance criteria is pinned by a test
class in [`tests/test_shell_exec_integration.py`](tests/test_shell_exec_integration.py) AND
verified end-to-end with a real Anthropic provider:

1. **safe command inside workspace** — `whoami` runs with no `approval_*` events; `mkdir foo` creates `<workspace>/foo`.
2. **dangerous blocked** — `rm -rf <target>` is dropped by the planner sanitiser; a hand-crafted plan with `argv=['rm', '-rf', 'x']` reaches the tool, which fails with `whitelist`-mentioning `tool_result.status='error'`, target file untouched.
3. **external / irreversible needs approval** — `mkdir foo` emits `policy:escalate(risk=irreversible) → approval_request(risk=irreversible) → approval_decision → tool_call → tool_result` in that order. The approval request reports the EFFECTIVE risk from `risk_for`, not the static fallback.
4. **deny / abort / no-provider → no execution** — `mkdir foo` with deny / abort / no approver wired all collapse to `error.code in {approval_deny, approval_abort, approval_unavailable}`; no `tool_call` / `tool_result` events; filesystem untouched.
5. **timeout halts** — when `subprocess.TimeoutExpired` fires, the tool surfaces `timed_out=True, exit_code=None` and the partial stdout / stderr captured before the kill, both passed through redaction.
6. **redaction** — a credential pattern in stdout (`sk-1111…`) **never** appears in the JSONL or in the final answer; the kernel-side `data_classified` + `secret_detected(surface=tool_output)` events fire on the tool's output.
7. **JSONL** — every successful escalated call emits `policy → approval_request → approval_decision → tool_call → tool_result → compensation_registered` in order; the `tool_result.output.compensation_plan` payload carries the typed `actions` array.
8. **plan created BEFORE execution** — the registry is populated from `tool_result.output`, which is the SAME structured dict the tool synthesised before `Path.mkdir()` returned; a failed mutation (e.g. `mkdir` on existing path) produces NO `compensation_registered` event because the tool refused to mutate at all.
9. **rollback works** — `mkdir foo` → `:rollback` removes the directory; `touch bar.txt` → `:rollback` removes the file; `:rollback` on an empty log is a no-op with `skipped_reason='no plans registered'`; `:rollback <plan_id>` removes ONLY the plan with that id even when multiple plans are stacked.

<a id="mvp-12--structured-replan-policy"></a>
## MVP-12 — Structured re-planning policy

The previous loop tracked exactly one number — `max_replan_attempts` —
and applied it uniformly. A flaky `tool_error` was treated identically
to a hard `approval_deny`, which meant the LLM could (and did) propose
the same denied dangerous action again. MVP-12 replaces the single
counter with a typed, per-failure-type policy.

### The taxonomy

[`core/replan.py`](core/replan.py) defines nine `FailureType`s. Each has
its own `FailureBudget`:

| FailureType            | Budget | Different action required? | Behaviour                                               |
| ---------------------- | ------ | -------------------------- | ------------------------------------------------------- |
| `tool_error`           | 2      | no                         | Retry with different args / tool                        |
| `verify_failed`        | 2      | no                         | Try a different tool or richer args                     |
| `web_empty`            | 2      | no                         | Reformulate the search query                            |
| `timeout`              | 2      | no                         | Reduce scope, faster path                               |
| `approval_deny`        | 2      | **yes**                    | One retry, but the SAME (tool, args) pair is forbidden  |
| `approval_abort`       | 2      | **yes**                    | One retry with a safer alternative                      |
| `approval_unavailable` | 1      | **yes**                    | Stop — no provider wired, retry can't help              |
| `policy_blocked`       | 2      | **yes**                    | Pick a different registered tool                        |
| `unknown`              | 1      | no                         | Safety net: stop after the first hit                    |

On top of per-type budgets, `max_total_replans` (default 3) is a global
ceiling — even mixed never-exhausted failures cannot exceed it. Both
limits run together; whichever fires first stops the loop.

### How the loop uses it

For every attempt after the first, `AgentLoop.run()` calls
`ReplanPolicy.decide(failure_history, completed_attempts)` and respects
the result:

- `continue` → emit `replan` event, carry advice + `forbidden_actions`
  into the next planner call, log a `replan_attempt` event when the
  next attempt actually begins.
- `abort_no_retry` → a per-type budget exhausted; emit
  `replan_exhausted` event with `decision_action='abort_no_retry'`,
  hand off to the synthesizer for an honest final answer.
- `abort_exhausted` → global cap reached; emit `replan_exhausted` with
  `decision_action='abort_exhausted'`.

`forbidden_actions` is a tuple of `(tool, canonical-args-json)` pairs.
The planner sanitiser ([`core/planner.py`](core/planner.py)) refuses to
emit any step whose `(tool, args)` pair matches the forbidden set, so
even a stubborn LLM that re-proposes the same denied dangerous action
gets dropped at sanitiser time — defence in depth.

The `<replan_context>` block fed to the planner now also carries an
`<advice>` paragraph (composed from per-type budgets) and a
`<forbidden>` list. Real LLM providers read both; the mock provider
ignores them and tests use `FakePlanner` / `SequencedPlanner` that
honour the forbidden list deterministically.

### Acceptance — every one of the 8 criteria has a pinning test

The full acceptance suite is in
[`tests/test_replan_policy_integration.py`](tests/test_replan_policy_integration.py)
(9 tests) backed by 32 unit tests on the policy itself
([`tests/test_replan_policy.py`](tests/test_replan_policy.py)):

1. **`web_empty` → new search query** — `TestAcceptanceWebEmpty`:
   first attempt's empty result is reclassified as `web_empty`;
   attempt 2's `<replan_context>` includes the literal word
   `REFORMULATE` and a fresh query is run.
2. **`tool_error` retry bounded** — `TestAcceptanceToolErrorBounded`:
   two consecutive `tool_error`s stop with
   `replan_exhausted.decision_action='abort_no_retry'`; the tool ran
   exactly twice.
3. **`approval_deny` never repeats the same action** —
   `TestAcceptanceApprovalDeny`: even with a planner that
   stubbornly re-proposes the denied step every time, `approval_request`
   fires exactly once and the dangerous tool is never invoked.
4. **`policy_blocked` → safe alternative** —
   `TestAcceptancePolicyBlocked`: attempt 1 names an unknown tool
   (gets dropped → `policy_blocked` trigger); attempt 2 the planner
   picks a registered safe tool and it runs.
5. **`verify_failed` → new plan** — `TestAcceptanceVerifyFailed`:
   first tool returns empty string (validator rejects), second attempt
   switches to a different tool.
6. **Max attempts → honest final failure** —
   `TestAcceptanceExhaustedHonest`: the synthesizer still produces an
   Output Contract reply; `respond.replan_exhausted=True`.
7. **All decisions logged** —
   `TestAcceptanceDecisionsLogged`: for a 3-attempt run we see exactly
   3 `planner`, 2 `replan`, 2 `replan_attempt`, and 1
   `replan_exhausted` events, with `failure_counts_so_far` carried
   inside each `replan_attempt`.
8. **No infinite loop** — `TestAcceptanceNoInfiniteLoop`: parametrised
   over `tool_error` and `verify_failed` pathological patterns, the
   loop terminates with a bounded number of tool invocations every
   time.

## What to add next (in this order)

> Note: "MVP" at this point is short for *runtime foundation*, not
> *minimum viable*. The agent now has a planner with bounded
> re-planning, working + persistent memory with hygiene policies
> (TTL / dedup / summarise / backup cleanup), a policy gate, human
> approval, a kernel-side safety layer with secret scanning +
> redaction + classification, argument-aware risk (`risk_for`),
> a sandboxed reversible/irreversible write tool, a sandboxed
> whitelisted shell tool with mandatory compensation + rollback,
> 1533 hermetic tests covering every production module, and
> zero-network determinism. The numbered slots below extend that foundation;
> they are not the smallest possible thing.

**Mock planner caveat**: the `mock` provider routes by deterministic
keyword heuristics — it never adapts. Real LLM planners (Anthropic /
OpenAI / HuggingFace) read the `<replan_context>` block on retries and
will change strategy; `mock` will keep proposing the same failed step
until `max_replan_attempts` runs out (or, with MVP-12, the per-type
budget bites). Use a real provider when demonstrating adaptive
re-planning live.

**MVP-12 — Re-planning policy hardening (DONE).** Re-planning is no
longer a single global counter. Every step failure is now classified
into one of nine `FailureType`s, each with its own retry budget and
written-in advice that the planner reads on the next attempt. See the
[MVP-12 section](#mvp-12--structured-replan-policy) below for the full
taxonomy and acceptance proof.

**MVP-14 — Evidence Layer (DONE).** Before MVP-13.2 (Self-Repair
Controller) we taught the agent to PROVE its answers, not just emit
them. The Verifier rewrites every cited claim to `[verified:<kind>:<src>]`
when it can resolve the citation to a real `Evidence` record in this
cycle's `ProvenanceChain`, and tags every uncited claim with
`[unverified]`. A fully-uncited answer carries an explicit disclaimer.
See the [MVP-14 section](#mvp-14--evidence-layer) below.

**MVP-13.1 — Self-repair diagnostic primitives (DONE).** Before
giving the agent the power to modify its own code (MVP-13.2), it
needs to *see* its own project. Three new read-only / reversible
tools form the diagnostic surface for the self-repair loop: `run_tests`
(sandboxed pytest runner), `read_logs` (its own JSONL audit), and
`diff_file` (preview a proposed change without writing it). See the
[MVP-13.1 section](#mvp-131--self-repair-diagnostic-primitives) below.

**Governance modes (STARTED).** [`core/governance.py`](core/governance.py)
defines the high-level safety envelope for self-learning, self-repair
and self-evolution: Diagnostic may inspect only, Learning may save only
verified knowledge automatically, Repair may propose fixes but code
writes require approval plus tests and rollback, and Improvement /
Governance changes are approval-gated by design. This sits above the
lower-level `PolicyGate`, which still evaluates individual tool calls.

**MVP-13.2 — Self-Repair Controller (STARTED).** The first real
controller slice lives in [`core/self_repair.py`](core/self_repair.py).
It accepts a concrete `RepairProposal`, runs baseline diagnostics,
previews the diff, requests approval through the existing approval
provider, writes via `file_write`, runs tests, and applies compensation
rollback if post-write verification fails. The REPL exposes this as
`:repair <target> <proposal_file> [test_path...] [--pattern PAT]`.
No new write primitive was added; the controller composes existing
tools and governance gates.

**MVP-13.3 — Repair Proposal Generator (STARTED).**
[`core/repair_proposal.py`](core/repair_proposal.py) adds the thinking
step before the controller: run tests, optionally read logs, read the
target file, ask the LLM for strict JSON, then validate `target_file`,
`proposed_content`, `evidence`, `confidence`, diff size and secret
hygiene. It returns a `RepairProposal` but does not apply it. The REPL
exposes this as `:propose-repair <target> [test_path...] [--pattern PAT]
[--trace TRACE]`.

Roadmap beyond MVP-14:

1. **MVP-13.2 — Self-repair controller (STARTED)**: `RepairProposal`
   model plus an orchestrator that drives diff -> approval -> write ->
   run_tests -> rollback-on-red, exposed via a `:repair` REPL command.
   Next slice: let the planner draft proposals, while this controller
   remains the only path that may apply them.
2. **MVP-13.3 — Repair proposal generator (STARTED)**: failed tests +
   logs + target code -> LLM JSON -> validated `RepairProposal`. It is
   intentionally read-only; applying the proposal still requires the
   MVP-13.2 controller.
3. **MVP-13.4 — Self-repair E2E hardening (STARTED)**: the live audit is
   now an automated contract covering success, approval denial, rollback
   after red tests, and low-confidence blocking before approval/write.
4. **MVP-13.5 — Learning loop**: agent reads docs / specs, extracts a
   summary, finds where to apply it, drafts a patch, and routes it
   through the same controller. The Verifier guarantees the summary
   is tied to fetched pages (`kind=web_page`), not LLM imagination.
5. **MVP-14.3 — Source ranker (STARTED)**: deterministic
   `core/source_ranker.py` scores every Evidence by source tier,
   freshness and realtime suitability, logs `source_ranking`, and
   exposes `agent.last_source_ranking`.
6. **MVP-14.3b — Source registry (STARTED)**: `core/source_registry.py`
   turns the per-run Evidence chain into a catalog of source records and
   extracted claims, logs `source_registry`, and exposes
   `agent.last_source_registry`.
7. **MVP-14.3c — Knowledge pipeline integration (STARTED)**:
   `core/knowledge_pipeline.py` connects Evidence -> SourceRegistry ->
   claim extraction -> conflict detection -> source catalog persistence
   -> optional long-term knowledge memory.
8. **MVP-14.6 — Conflict resolver**: when two evidence records
   contradict each other, surface the conflict explicitly in the
   answer instead of silently picking one. (Renumbered: 14.5 is now
   taken by the unresolved-citation re-plan loop below.)
9. **MVP-15 — Multi-agent**: dry-run team planning now emits bounded
   `SubagentContract`s before any delegation happens. Real delegation still
   requires approval, budget gates and an audited message-passing runtime.

## MVP-14 — Evidence Layer

The single most important change since the kernel was first stood up.
Before MVP-14, the LLM was implicitly the "source of truth" — whatever
it asserted became the user-facing answer, modulo redaction. After
MVP-14, **the LLM is a draft writer**; the Verifier examines its draft
and refuses to let any claim through without a verifiable source.

### 14.1 — Evidence + Provenance model (`core/evidence.py`)

  - `EvidenceKind` taxonomy with 12 typed sources from
    `user_explicit` (confidence 1.00) down to `llm_claim` (0.20).
  - Frozen `Evidence` dataclass: id, kind, source_id, obtained_via,
    `content_hash` (sha256), `fetched_at` (ISO-8601), confidence,
    claim, excerpt (≤ 800 chars).
  - `ProvenanceChain` — per-cycle ordered collection; supports
    `by_kind`, `by_source_id`, `highest_confidence`,
    `to_log_payload` (compact form without excerpts).
  - `evidence_from_tool_result(tool_name, arguments, output, status)`
    factory: inspects the (tool, output) pair and produces a typed
    Evidence — `file_read` → `kind=file`, `web_fetch` →
    `kind=web_page`, `run_tests` → `kind=test_result`, etc.
    Failed tool results yield `None` (an error is the absence of a
    source, not a weaker source).
  - `file_write` is **explicitly NOT a source** — writes are actions,
    audit-tracked separately. Memory records get a `–0.15` confidence
    penalty when they have no provenance.
  - **Integration**: `AgentLoop` builds the chain in parallel with
    `artifacts`, emits an `evidence_collected` JSONL event, exposes
    the chain as `agent.last_provenance`.

### 14.2 — `web_fetch` tool (`tools/web_fetch.py`)

Turns a `web_search` pointer into a verifiable source.

  - HTTPS by default. Plain HTTP is rejected unless the host is explicitly
    listed in `AGENT_FETCH_ALLOW_HTTP_HOSTS` or passed as an allowlist to the
    tool. `file://`, `data:`, `ftp:`, `javascript:` are rejected at the
    planner AND the tool layer.
  - Egress policy: optional `AGENT_FETCH_ALLOW_HOSTS` and
    `AGENT_FETCH_DENY_HOSTS`, DNS resolution checks, redirect re-validation,
    and `https -> http` downgrade redirects are refused.
  - Local-network block-list: `localhost`, `127.0.0.0/8`, `0.0.0.0`,
    `10/8`, `192.168/16`, `172.16-31/12`, `169.254/16`
    (AWS metadata!), IPv6 loopback. Defends against SSRF.
  - ASCII URL, ≤ 2048 chars, content-type allow-list
    (text/html, text/plain, application/json, application/xml,
    text/xml, application/xhtml+xml), 1 MiB body cap, 10 s timeout.
  - Returns `{url, status_code, content_type, fetched_at,
    content_hash, text, text_truncated, bytes, elapsed_ms,
    compensation_plan}` — same shape every other tool follows.
  - Gzip auto-decompressed with a second post-inflate size cap; HTML stripped
    to plain text by a dependency-free regex pipeline; charset honoured from the
    `Content-Type` header; redaction applied to the returned text.
  - Risk: `read_only` — no approval required.

### 14.3 — Source Ranker (`core/source_ranker.py`)

Verifier checks whether a citation resolves. Source Ranker checks how
good that resolved source is for the current question.

First live slice:

  - `SourceRank` records source tier, freshness status, support level,
    final score, confidence ceiling and reasons.
  - `rank_chain(chain, question=...)` scores the whole
    `ProvenanceChain` and returns a `SourceRankingReport`.
  - Realtime questions are detected from market / weather / latest /
    "right now" language. Ordinary web pages and search snippets are
    capped as `insufficient_for_realtime`; market-data domains such as
    CoinMarketCap / CoinGecko can support realtime claims only when the
    source/tool content exposes a live value timestamp.
  - `AgentLoop` emits a `source_ranking` JSONL event after
    `evidence_collected` and exposes the latest report as
    `agent.last_source_ranking`.

### 14.3x — Ranker-to-Output Policy (`core/output_policy.py`)

Verifier checks whether a citation matched evidence. Ranker-to-Output Policy
checks whether matched evidence is strong enough for the question type before
the answer reaches the user.

Current rules:

  - realtime question + no direct live source => final `Confidence` is capped
    to `low`;
  - realtime claims backed only by ordinary web/search evidence are rewritten
    from `[verified:web:...]` / `[verified:search:...]` to
    `[unverified:insufficient_for_realtime]`;
  - the `Unverified` section receives an explicit warning instead of silently
    saying `nothing`;
  - `replan_exhausted=True` is also surfaced in `Unverified`, so audit state
    and user-facing state stay aligned.

The loop logs this as `output_policy` when it changes an answer.

### 14.3b — Source Registry (`core/source_registry.py`)

Evidence is a per-run proof item. Source Registry is the catalog view:
source type, title, locator/path/URL, trust level, read timestamp, and
the claims extracted from that source.

First live slice:

  - `SourceType` covers books, PDFs, articles, documentation, video,
    podcasts, files, logs, test results, code repositories, official
    sites, web pages, forums, memory records and user directives.
  - `SourceRecord` stores type, title, locator, author, published date,
    trust level, read timestamp and metadata.
  - `ClaimRecord` stores the extracted claim text, locator
    (page/chapter/line/timestamp), confidence, status, support sources
    and conflict sources.
  - `SourceRegistry.from_provenance(chain, ranking=...)` converts the
    current Evidence chain plus SourceRanker scores into catalog entries.
  - `AgentLoop` emits a `source_registry` JSONL event and exposes
    `agent.last_source_registry`.

This still does not ingest whole books/PDFs/videos. It creates the
stable catalog target used by the controlled text/code ingestion commands.

### 14.3c — Knowledge Pipeline Integration (`core/knowledge_pipeline.py`)

This slice connects the pieces that used to be adjacent but separate:

```
Evidence
  -> SourceRanker
  -> SourceRegistry
  -> ClaimExtractor
  -> ConflictResolver
  -> SourceRegistryStore
  -> KnowledgeWritePolicy
  -> Persistent Memory (optional)
```

What is wired:

  - `ClaimExtractor` deterministically extracts sentence-level claims
    from Evidence excerpts and rejects secret-shaped claims.
  - `ConflictResolver` marks obvious same-subject/different-value
    contradictions as `conflicted`.
  - `ConflictReview` turns conflicted claims into operator-visible
    suggestions: competing claims, source trust, claim confidence,
    suggested winner or `needs_review`.
  - `SourceRegistryStore` persists source records and extracted claims
    to `data/source_registry.jsonl` with duplicate suppression.
  - `KnowledgeWritePolicy` gates claims before they can become long-term
    memory: no secrets, no unverified/conflicted claims, confidence and
    source-trust thresholds, and weak source-type rejection.
  - `AgentLoop` emits `knowledge_pipeline`, exposes
    `agent.last_knowledge_pipeline`, and can write approved knowledge
    to persistent memory when `AGENT_KNOWLEDGE_AUTO_WRITE=true`.

By default, automatic memory writes are off. The source catalog still
persists locally when a `SourceRegistryStore` is configured.

Conflict review is read-only:

```text
:conflicts
:conflicts --limit 5
:conflict-status --json
```

### 14.3d — Controlled Source Ingestion (`core/ingestion.py`)

Interactive commands now connect local sources to the knowledge pipeline:

  - `:ingest-source <path>` reads one UTF-8 `.txt/.md/.py/...` file inside
    the workspace, chunks it, builds Evidence, extracts claims, persists the
    Source Registry, and logs `ingest`, `source_registry`, and
    `knowledge_pipeline` events.
  - `:ingest-project [path] --limit N` scans curated text/code files while
    skipping generated/runtime directories such as `.git`, `.venv`, `data`,
    `logs`, `node_modules`, caches and build outputs.
  - `--dry-run` builds the in-memory report without writing registry or memory.
  - `--write-memory` explicitly enables approved knowledge writes for that
    command; `--no-memory` forces registry-only ingestion.

The command uses a neutral SourceRanker prompt (`controlled document
ingestion`) so filenames like `knowledge.txt` cannot accidentally trigger
realtime-source downgrades via substring matches such as `now`.

### 14.3f — Online Source Library (`core/source_library.py`)

The agent has a curated catalog of online source families. It still uses
`web_search` and `web_fetch`; the library constrains where it searches and
which result domains are acceptable.

Source groups:

```text
wikis       wikipedia, wikibooks, wikisource
books       wikibooks, wikisource, project_gutenberg, internet_archive, open_library
science     arxiv, pubmed
docs        python_docs, mdn, microsoft_learn, rfc_editor
all         every registered source family
```

Interactive commands:

```text
:source-library
:source-library books
:source-library --json
:ingest-web "autonomous agent" --sources wikis,science --limit 4 --dry-run
:ingest-web "python asyncio" --sources docs --limit 3
```

`ingest-web` performs controlled online learning:

```text
source library entry -> web_search(site:...) -> domain filter
  -> web_fetch(URL) -> Evidence -> SourceRanker -> SourceRegistry
  -> ConflictResolver -> optional KnowledgeWritePolicy
```

Memory writes remain off by default. Use `--write-memory` only when the
source-backed claims should become reusable long-term knowledge.

### 14.3g — RSS / Atom Ingestion (`tools/rss_fetch.py`)

RSS/Atom feeds let the agent watch sources without repeatedly spending web
search calls. The tool fetches one feed URL, parses structured entries, and
turns them into source-backed claims through the same Evidence pipeline.

```text
:ingest-rss https://www.python.org/blogs/rss/ --limit 5 --dry-run
:ingest-rss https://github.blog/feed/ --limit 10
```

The fetch is read-only and guarded like `web_fetch`: http(s) only, ASCII URL,
no localhost/private IPs, size cap, timeout, content hash, and redaction.

### 14.3h — Source Connector Registry (`core/source_connectors.py`)

The Source Connector Registry is the routing table above concrete tools and
commands. It tells the agent which source channel fits a task, whether the
connector is wired or planned, whether it needs auth, and what rough budget
counters it consumes.

Connectors:

```text
local            wired     files/logs/tests/project docs
web              wired     curated web_search + web_fetch
rss              wired     RSS/Atom feed monitoring
openalex         planned   no-key scholarly metadata API
arxiv            partial   currently via :ingest-web --sources arxiv
github_public    partial   public README/releases/issues via web fetch
government_data  planned   open-data portals and public datasets
```

Interactive commands:

```text
:connectors
:connectors wired
:connectors --json
:connector-plan "monitor Python releases" --limit 3
:connector-plan "research papers about autonomous agents" --json
```

This layer is read-only. It recommends the source route; actual fetching still
runs through `:ingest-source`, `:ingest-web`, `:ingest-rss`, or future
connector-specific tools.

### 14.3e — Role Router / Knowledge Use / Learning Planner

This slice stops memory and learning from being "one bucket for everything":

  - `RoleRouter` classifies the current task as `operator_chat`,
    `technical_report`, `programmer`, `researcher`, `learning`, or `repair`,
    with tone, output style, knowledge scopes, and allowed memory tags.
  - `KnowledgeUsePolicy` filters persistent records before keyword retrieval:
    quarantined/obsolete records are rejected, records must match the current
    role by tag or question overlap, and the final keyword retrieval still
    decides what actually enters the prompt.
  - `LearningPlanner` implements `:learn` / `:learn-project`: it chooses a
    bounded set of local sources from README, architecture docs, `core/`,
    `tools/`, `runtime/`, and focused tests, then feeds that set into controlled
    ingestion.

`AgentLoop` now emits `role_route` and `knowledge_use_policy` audit events.
The role context is injected into synthesis, but not disguised as conversation
history, so old memory/cache contracts remain intact.

### 16.1 — Autonomous Runtime / Budget / Circuit / Approval Inbox

This slice turns the separate safe abilities into a first bounded autopilot:

  - `AutonomousRuntime` runs a project-health queue: status snapshot,
    dry-run learning ingestion, and an optional pytest health check.
  - `BudgetGovernor` caps outer-loop spend (`cycles`, `learning_runs`,
    `test_runs`, future `llm_calls` / `web_fetches`) and records denials.
  - `CircuitBreaker` opens on repeated failures or budget denial so the
    runtime stops instead of spinning.
  - `ApprovalInbox` stores deferred human-review items for autonomous
    decisions. Non-dry-run runtime is blocked in this MVP and creates an
    inbox item instead of silently enabling effects.

CLI surface:

```text
:auto-run [goal] --dry-run --limit 5 --learning-limit 5
:auto-run [goal] --no-tests
:auto-run --allow-effects      # blocked; queues approval item
:auto-status
```

`--dry-run` is the default. The runtime may inspect status, run controlled
dry-run ingestion, and run tests, but it does not write memory/code or enable
external side effects.

### 16.2 — Persistent Task Queue / Scheduler Tick

This slice gives the runtime memory between launches:

  - `TaskQueueStore` persists autonomous tasks in
    `data/runtime_tasks.jsonl`.
  - `SchedulerStore` persists recurring schedule definitions in
    `data/runtime_schedules.jsonl`.
  - A scheduler `tick` enqueues due tasks and advances `next_run_at`.
  - `AutonomousRuntime.run_task_queue(...)` claims due pending tasks, runs
    bounded dry-run runtime work, and marks tasks `done` / `failed`.

This is still not a daemon. It is the safe core that a daemon or Windows
Task Scheduler job can call later.

CLI surface:

```text
:task-add [goal] --no-tests --limit 2
:task-list [pending|running|done|failed|cancelled|all]
:task-run --limit 1
:task-cancel <task_id>

:schedule-add 60 project health --name hourly-health --no-tests --limit 2
:schedule-list [active|paused|all]
:schedule-tick
:schedule-tick --run --limit 1
```

### 16.3 — Model Usage Ledger

The model router wraps every role-specific `complete()` call with a local usage
ledger:

  - `model_call_start` and `model_call_end` events are emitted to the trace.
  - `data/model_usage.jsonl` stores role, provider, model, route reason,
    input/output/total tokens, rough `cost_units`, status and duration.
  - `:model-usage` reports historical totals and current-session totals.
  - `:budget-status` includes the same model usage snapshot.
  - Session caps (`AGENT_MODEL_MAX_CALLS_PER_SESSION`,
    `AGENT_MODEL_MAX_TOKENS_PER_SESSION`,
    `AGENT_MODEL_MAX_COST_UNITS_PER_SESSION`) block the next LLM call before it
    is sent. CLI entry points catch `ModelBudgetExceeded` and return a clean
    user-facing stop message instead of a traceback.

`cost_units` are deliberately approximate. They are for budget governance and
route comparison, not billing.

### MVP-15 — Multi-Agent Organization Layer

This slice starts with team design, not delegation. `:team-plan <goal>` creates
a bounded dry-run plan of subagent contracts:

  - `BudgetWatchAgent` estimates and caps team spend.
  - `NewsSignalAgent` collects read-only web/RSS signals.
  - `BusinessOpportunityAgent` maps signals to client pain and MVP offers.
  - `CoderSignalAgent` identifies candidate code/test work without writing.
  - `ArchitectureImpactAgent` maps a goal to architecture layers.
  - `VerifierAgent` checks outputs, evidence, conflicts and scope.

Each `SubagentContract` defines objective, inputs, outputs, allowed/forbidden
tools, model role, max iterations, max model calls, cost budget, verifier and
stop conditions. This prevents a "zoo" of uncontrolled agents: no subagent is
run merely because it sounds useful.

`:team-run <goal>` is the next dry-run bridge. It builds the same plan, then
walks the contracts in order without launching subagents, tools or model calls.
It reserves planned model calls/cost units, stops before a contract that would
exceed the team budget, marks approval-required contracts, and creates verifier
handoffs. Useful flags:

```text
:team-run "AI news and business opportunity radar" --max-model-calls 10 --max-cost-units 20
:team-run "AI news business code architecture" --limit 4 --json
```

This proves sequencing and budgets before any real delegation is allowed.

### 14.4 — Verifier (`core/verifier.py`)

The Verifier runs AFTER `_synthesize` and BEFORE the final
defence-in-depth redaction pass. It:

  - splits the draft answer into sentence-level chunks;
  - parses each citation (`[file:foo.txt]`, `[web:URL]`, `[test:cmd]`,
    `[log:trace_id]`, `[shell:cmd]`, `[diff:path]`,
    `[memory:mem_id]`, `[user]`, `[search:query]`);
  - matches each citation against the cycle's `ProvenanceChain`
    by expected-kind + source_id substring;
  - REWRITES matched citations to `[verified:<kind>:<source>]`;
  - tags every uncited chunk with `[unverified]`;
  - if **every** chunk is uncited, appends an explicit disclaimer
    (`DISCLAIMER_FULLY_UNVERIFIED` when the chain wasn't empty —
    "we had sources but you cited none"; `DISCLAIMER_NO_CHAIN` when
    the chain was empty — "no external sources gathered, you're
    reading prior knowledge").

The synthesizer prompt is updated so the LLM is taught the full
citation grammar. `AgentLoop.last_verification` exposes the report;
the JSONL `verification` event captures
`{total_chunks, verified_chunks, unverified_chunks,
cited_but_unmatched_chunks, fully_unverified, chain_was_empty,
disclaimer_set, verdicts}`.

`verifier_enabled=False` returns the draft unchanged — used by
legacy tests that pin the synthesizer output byte-for-byte.

### 14.4.x — Verifier polish (from live REPL feedback)

Two quality-of-life improvements after the first live REPL session
exposed shortcomings of the v1 verifier:

  - **Structural-chunk skip.** Output Contract section headers
    (`Conclusion:`, `Facts:`, `Sources:`, `Confidence:`,
    `Unverified:`, `Safety:`), markdown headings (`# ...`), and
    bare list markers (`-`, `*`, `1.`, `2)`) are no longer tagged
    `[unverified]` — they carry no claim. `VerificationReport` gains
    `structural_chunks` so the counts add up cleanly:
    `total = verified + unverified + cited_but_unmatched + self_declared`.
  - **`[general-knowledge]` becomes a first-class verdict.** When the
    LLM honestly admits "this is from my training data, not from a
    source", it cites `[general-knowledge]`. The Verifier rewrites
    that to `[declared:general-knowledge]` and counts it as
    `self_declared` — neither `verified` nor `unverified`. A whole
    answer made of self-declared chunks earns
    `DISCLAIMER_ALL_SELF_DECLARED` so the user knows where the
    information came from. A mixed answer (some verified, some
    self-declared) gets no disclaimer — the honesty is rewarded.

### 14.4.y — Planner self-documentation allowlist

The first live REPL test (introspective question, no `--file` hint)
revealed the planner had no way to reach `README.md`, so it fell back
to `read_logs` / `shell_exec` and produced a mostly-unverified answer.
Fix: tiny, validated allowlist of paths the planner is allowed to
read EVEN WITHOUT a hint. Default: `("README.md",)`. Override via
`LLMPlanner(self_documentation_paths=...)`. The validator drops any
entry that fails the workspace identifier policy (absolute, drive
letter, `..`, non-ASCII, non-string, empty). Combined with the
updated planner system prompt — which now teaches the LLM that
introspective questions ("what do you understand about yourself?",
"describe your architecture", "what is your roadmap?") map to
`file_read README.md` — this lifted introspective answer verification
from 16 % to 80 % verified chunks in the live REPL test.

### 14.5 — Unresolved-citation re-plan loop (Verifier-driven)

The second live REPL test (web research: *"find the current definition
of 'AI agent' on the internet"*) exposed a real architectural gap:
single-pass planning cannot handle dependent tool steps. The first
plan was just `web_search`; the LLM happily cited
`[web:https://.../...]` URLs in its draft, but no `web_fetch` ever
ran, so the Verifier marked all those citations
`cited_but_unmatched` and the answer ended with
`DISCLAIMER_FULLY_UNVERIFIED`.

MVP-14.5 closes that loop. The Verifier is no longer a passive
gatekeeper at the end of the pipeline — it now **drives** a
post-synthesis re-plan whenever the chain cannot resolve a cited URL:

  1. After the first verify, `extract_unresolved_web_urls(report)`
     collects every `[web:URL]` citation that ended up
     `cited_but_unmatched`. Only http/https URLs are kept; placeholder
     or scheme-less bodies (`[web:Wikipedia]`) are filtered out.
  2. A `ReplanTrigger(code="unresolved_citation", arguments={"urls":
     [...]})` is appended to `failure_history` and the SAME
     `ReplanPolicy.decide()` that already governs tool-level failures
     decides whether to retry. Budget: `max_occurrences=2` per cycle,
     `requires_different_action=False` (the WHOLE POINT is to add
     `web_fetch` for the cited URL, so repetition is allowed).
  3. On `continue`, the planner is called again with an advice block
     that lists the unresolved URLs explicitly:

         URLs that MUST be opened via web_fetch (one step each):
           - https://...
           - https://...

  4. The planner returns `web_fetch` steps for those URLs; the loop
     executes them, adds `kind=web_page` evidence to the chain.
  5. The ORIGINAL draft answer is **re-verified** against the enriched
     chain — no second LLM synthesis is needed because the draft
     already cites those URLs. `match_citation` now finds matches and
     rewrites them to `[verified:web:URL]`.

Three layers of bounding prevent infinite loops:

  - `FailureBudget.max_occurrences=2` (per-type cap in
    `ReplanPolicy`);
  - `max_total_replans=3` (global cap);
  - a hard cap of 2 verify-driven iterations inside the Verifier loop
    itself (`VERIFY_REPLAN_HARD_CAP`).

If a fetch round adds zero evidence (planner returned an empty plan,
sanitiser dropped every URL, all fetches failed), the loop emits a
`verify_replan_noop` event and exits gracefully — the unresolved
citations stay marked as such, but the agent never spins.

New / extended audit events:

  - `replan` with `payload.phase="verify"` and the list of
    `unresolved_urls`;
  - `verification` with `payload.phase="verify"` and `iteration` +
    `evidence_added` counts;
  - `evidence_collected` re-emitted with `phase="verify"` after each
    fetch round;
  - `verify_replan_noop` / `verify_replan_capped` for the two
    graceful-exit branches.

Live REPL evidence (same web-research question as before):

| Phase | verified | cited_but_unmatched | disclaimer |
|---|---|---|---|
| Before MVP-14.5 (single pass) | 0 / 3 | 3 | `FULLY_UNVERIFIED` |
| After MVP-14.5 (verify-replan) | **3 / 3** | 0 | none |

The same LLM, the same Output Contract, the same tools — the only
new behaviour is the Verifier driving one extra planner round when
the LLM's draft cites a URL the agent never actually opened.

### 14.5b — Composite-body citation matching

A small but important matcher fix surfaced during live test 1: when
the LLM cites `[test:run_tests:bug_lab]` or `[shell:shell_exec:git:
porcelain]`, the body is a composite (multiple `:`-separated tokens)
and substring matching against the canonical source_id fails — the
literal string `run_tests:bug_lab` doesn't appear verbatim anywhere.

Two coordinated changes restore the match without weakening it:

  * **Richer source_id format** for `evidence_from_tool_result`:
    `test_result:run_tests:pytest:<paths>` instead of
    `test_result:<full-argv-with-python.exe-path>`, and
    `shell_output:shell_exec:<argv0-basename>:<short-cmd>` instead of
    `shell_output:<full-cmd>`. The keywords LLMs naturally use
    (`run_tests`, `pytest`, `shell_exec`, the bare program name) are
    now part of the source_id so direct substring matching works.
    The full path / full argv still lands in the excerpt for forensic
    context, just not in the citation surface.

  * **Token-overlap fallback** in `match_citation` for prefixes that
    aren't URL-bearing. After direct substring fails, the body is
    tokenised on structural separators only (`:`, `/`, `\`,
    whitespace, `,` — `_` and `-` stay as identifier characters), each
    token is filtered against a stopword list (`test`, `shell`,
    `https`, `web`, …) and a minimum length of 3 chars, and the
    candidate with the most distinct meaningful tokens present in its
    source_id wins (≥ 1 hit required). `web`, `search`, and `file`
    prefixes are explicitly **excluded** from the fallback — URL
    fragments and path components would otherwise let
    `[web:https://unknown.example]` match `web_page:https://known.example`
    through the shared `https` token. URL prefixes keep the strict
    substring-only rule and lean on the unresolved-citation re-plan
    loop instead.

Result: the live test 1 cite `[test:run_tests:bug_lab]` now resolves
to the right `test_result` record because the source_id contains
`run_tests` AND `bug_lab` as separate substrings; the regression case
`[web:https://unknown.example]` ↛ `web_page:https://known.example` is
pinned in the test suite so it cannot drift.

### Why MVP-14 before MVP-13.2

> Tests check **behaviour**. Evidence checks the **basis**: why does
> the agent believe it found the bug it's about to fix?

A self-repair controller without an evidence layer is dangerous —
LLM proposes a patch, `run_tests` happens to go green, and the
controller writes the change even though the diagnosis was wrong.
MVP-14 makes the diagnosis itself verifiable: the controller can
demand that a "this is the failing test" claim be tied to a
`kind=test_result` evidence record, not just an LLM assertion.

## MVP-13.1 — Self-repair diagnostic primitives

Before letting the agent modify its own code, it has to **see** its
own project. MVP-13.1 adds three new tools that form the diagnostic
surface for the upcoming self-repair controller (MVP-13.2). Each one
is intentionally narrow: they don't write files, don't widen the
network surface, and they all ship with the same compensation contract
the rest of the system uses (a `noop` plan for uniformity).

### The three primitives

**`run_tests(paths, pattern)` — sandboxed pytest runner**
([`tools/run_tests.py`](tools/run_tests.py))

Runs the project's pytest suite (or a `-k`-filtered subset) in a
subprocess with `shell=False`, `cwd=workspace_root`, a hard timeout
(default 90 s), and a 1 MiB output cap on each of stdout/stderr.
Returns a structured summary the planner can reason about:

```python
{
  "command": ["python", "-m", "pytest", "-q", "--tb=short", "tests"],
  "exit_code": 0, "timed_out": False, "duration_ms": 1843,
  "passed": 42, "failed": 0, "errors": 0, "skipped": 1, "total": 43,
  "failed_tests": [],                # populated on red
  "stdout_tail": "<last 4 KB>", "stderr_tail": "<last 4 KB>",
  "compensation_plan": {"id": "noop", ...},
}
```

Risk: **reversible** — no approval prompt by default. The full
self-repair controller (MVP-13.2) wraps an entire chain
(diff → write → tests) in a single approval; pestering on every
individual test run would shred the UX.

**`read_logs(last_n, event_filter, trace_id)` — JSONL audit reader**
([`tools/read_logs.py`](tools/read_logs.py))

Reads the agent's own JSONL audit log so the planner can answer
"what happened in the last run", "what error fired", "did the user
deny something". Default = last 50 events of the most-recently-modified
log; both arguments are bounded (`last_n ≤ 500`, `event_filter ≤ 20`
ASCII strings), the file path is contained to `<workspace>/logs/`,
and traversal-shaped `trace_id`s like `../../etc/passwd` are refused
before any filesystem lookup. Risk: **read_only**.

**`diff_file(path, proposed_content, context_lines)` — unified diff**
([`tools/diff_file.py`](tools/diff_file.py))

Shows what `file_write` *would* change without writing. Returns a
unified diff plus addition/deletion counts. Both the on-disk file and
the proposed content are byte-capped (1 MiB), the diff itself is
capped at 64 KiB and gets a truncation marker, and any secret-shaped
substring in the proposed content is redacted from the diff output
(defence-in-depth: `file_write` already scrubs secrets at write time,
but a preview can be requested by a tool registered later that's less
disciplined). Risk: **read_only**.

### Why this isn't MVP-13 in one shot

The full self-repair loop ("diagnose -> propose -> diff -> approval ->
write -> tests -> success or rollback") is the *user-visible feature*.
MVP-13.1 added the primitives first, then MVP-13.2 composed them into
the controller in [`core/self_repair.py`](core/self_repair.py). That
separation matters because:

- Every primitive ships fully tested in isolation (91 new unit tests
  + 37 planner sanitiser tests + 8 integration tests = 136 new cases)
  before any controller code is written.
- Each is independently useful — the user can already ask "show me
  the last error" or "what would change if you set `FOO = 42` in
  `config.py`?" today without any controller.
- The controller's design space stayed narrow once the primitives
  existed: there was no "what should run_tests return?" debate when
  MVP-13.2 started.

### Safety inheritance

All three tools inherit the agent's existing safety pipeline:

| Layer | Applied to MVP-13.1 tools |
| --- | --- |
| **PolicyGate** | `read_logs`/`diff_file` allowed with `read_only` audit; `run_tests` allowed with `reversible` audit |
| **ASCII-identifiers policy** | every path / trace_id / event_filter / pattern argument |
| **Sandbox** | every fs read goes through `workspace_root.resolve()`; subprocess for `run_tests` is `shell=False, env=minimal` |
| **Redaction** | tool output passes `redact_text`/`redact_payload` before crossing the boundary (defence-in-depth on top of the logger) |
| **Planner sanitiser** | explicit branch in `LLMPlanner._sanitize_step` for each tool — bad-shaped LLM proposals get dropped with a `step[N]: ... dropped` warning before reaching the PolicyGate |
| **Output validation** | each `validate_output` pins the result shape so a future change can't silently break the contract |

### Roadmap from here

- **MVP-13.2 — Self-repair controller (STARTED)**: a
  `RepairProposal` model plus an orchestrator that drives baseline
  tests -> diff -> approval -> file_write -> post-tests; on red tests
  it triggers `agent.rollback()` and reports the outcome. REPL command:
  `:repair <target> <proposal_file> [test_path...] [--pattern PAT]`.
- **MVP-13.3 — Repair proposal generator (STARTED)**: tests/logs/code
  feed an LLM strict-JSON prompt; the kernel validates target path,
  proposed replacement content, evidence, confidence, diff bounds and
  secret hygiene before returning a `RepairProposal`. REPL command:
  `:propose-repair <target> [test_path...] [--pattern PAT] [--trace TRACE]`.
- **MVP-13.4 — Self-repair E2E hardening (STARTED)**: the whole repair
  pipeline is pinned by an automated live-style audit: proposal dry-run
  leaves files untouched, approval denial prevents `file_write`, green
  tests leave a successful repair, red tests trigger rollback, and
  low-confidence proposals stop before approval/write.
- **MVP-13.5 — Learning loop**: agent reads a spec or docs page,
  produces a summary, finds where in the codebase it applies, then
  feeds the change into the same controller.

## Testing

A real safety net lives in [`tests/`](tests/) and runs via `pytest`:

```powershell
python -m pytest -v
```

What is covered today (**1533 tests, ≈ 30 s, zero network calls**):

| Layer | File | Cases |
| --- | --- | --- |
| Tool safety (`file_read`) | [`tests/test_file_read.py`](tests/test_file_read.py) | exists / Unicode workspace filename / missing / traversal / empty / whitespace / oversized / non-UTF-8 |
| Tool safety (`file_write`, §5 MVP-9) | [`tests/test_file_write.py`](tests/test_file_write.py) | create new file works (relative + nested + unicode) / overwrite creates timestamped `.bak.<ts>` with the original content / two overwrites leave at least one backup / parent-traversal rejected / absolute outside-workspace rejected / `sub/../../escape` rejected / non-string + empty path rejected / non-string content rejected / size-limit (over → ValueError, exactly → OK, default = 1 MiB) / **openai-key, aws-key in content → PermissionError, no file written, no backup** / **secret refused even on overwrite codepath (original untouched, no leak-side-effect backup)** / `risk_for`: new path → reversible, existing → irreversible, escape → irreversible, missing-arg → irreversible / static `risk = "irreversible"` fallback / `validate_output`: create + overwrite shapes pass, invalid mode flagged, overwrite-without-backup flagged, create-with-backup flagged, negative bytes flagged / description warns about approval + backup |
| Planner routing | [`tests/test_planner.py`](tests/test_planner.py) | file-hint + file question / no-hint drop / wrong-path remap / web-only / empty plan / malformed JSON / unknown tool / **`failure_context` empty → no `<replan_context>` in prompt** / **non-empty failure_context injected before question** / **secret in failure_context redacted before LLM** / **MVP-9: file_write sanitiser — well-formed pass, missing path/content dropped, label has no content (size + leak safe), `/abs`, `\\Windows`, `C:\\...`, `..`, `sub/../../...` all dropped at planner level** |
| Policy Gate — unit (§5 + §12.4) | [`tests/test_policy.py`](tests/test_policy.py) | read_only allow / reversible allow / irreversible escalate / external escalate / unknown deny / no-name deny / llm_synthesize allow / output allow / audit invariants / **MVP-9: `risk_for` consulted per call, dynamic downgrade to reversible when args allow, dynamic stay-irreversible otherwise, `risk_for({})` called when `Action.parameters` empty, decisions are not cached across calls** |
| Policy Gate — runtime safety | [`tests/test_policy.py`](tests/test_policy.py) | **escalated tool with no approval provider never runs in the Executor** (`error.code=approval_unavailable`) / **denied unknown tool never runs in the Executor** (no `tool_call` / `tool_result` / `verify` events) |
| Working Memory (§4) | [`tests/test_memory.py`](tests/test_memory.py) | record turn / recent N turns / `max_turns` retention / context formatting / `max_context_chars` cap / answer truncation / cache key stability / cache miss / cache hit / per-tool isolation / clear / summary |
| Full Control Loop | [`tests/test_integration.py`](tests/test_integration.py) | CLI → planner → policy → file_read → verify → response with `[file:...]` citation / no-tools / general-knowledge cycle |
| Memory in the Loop | [`tests/test_memory_integration.py`](tests/test_memory_integration.py) | `memory_inject` fires only after turn 1 / `memory_cache_hit` short-circuits policy + tool / `memory_clear` resets state / **after `:clear` no `memory_inject`** / **after `:clear` no cache hit (real tool runs again)** / `memory=None` build emits no `memory_*` events |
| Memory Write + Retrieval Policy (§4 + §12.4) | [`tests/test_memory_policy.py`](tests/test_memory_policy.py) | accept (user-explicit / consent tag / decision tag) / **secret regex reject** (OpenAI / Anthropic / GitHub / HF / AWS / PEM) / secret-keyword reject (`api_key`, `password`, `Authorization:`, …) / **PII rejected unless `sensitive-data-consent` is present** / empty / too short / too long / tool-dump heuristic / no-consent reject / blocked-tag reject / retrieval: empty store / blank question / keyword pick / `max_records` cap / recency tiebreak / stopword filter / tag-overlap scoring / prompt truncation |
| Persistent Store (JSONL) | [`tests/test_persistent_memory.py`](tests/test_persistent_memory.py) | empty-file load / save-then-load round trip / preserved insertion order / `save_many` / **new store instance sees previous records** / delete by id / unknown-id no-op / `delete_all` removes file / corrupted line skipped / blank lines skipped / `get` hit + miss |
| State Store Integrity | [`tests/test_state_integrity.py`](tests/test_state_integrity.py) | checksummed JSONL envelope round trip / legacy rows accepted and upgraded / corrupt JSON rows quarantined and removed from active state / quarantine redacts sensitive raw rows / checksum mismatch quarantined / atomic rewrite emits only checksummed rows |
| Persistent in the Loop | [`tests/test_persistent_integration.py`](tests/test_persistent_integration.py) | **`agent.remember()` writes to disk + emits `persistent_memory_write`** / **fresh AgentLoop sees previous session's records** / secret rejected, no disk write / no-consent rejected, no disk write / **PII without `sensitive-data-consent` rejected; with consent saved only as `[REDACTED:pii-*]`** / `list_persistent` returns saved / `forget(id)` removes one + emits delete event / `forget()` wipes all / unknown id reports `deleted=0` / retrieval fires `persistent_memory_inject` and injects `<long_term_memory>` block / no records → no inject event, no block / no keyword overlap → `records_selected=0`, no block / no store wired → zero `persistent_memory_*` events + `remember()` rejects cleanly |
| Approval Providers (§7) | [`tests/test_approval.py`](tests/test_approval.py) | `_classify` maps EN/RU yes-tokens → `approve`, no-tokens → `deny`, empty/garbage/None → `abort` / `CLIApprovalProvider`: yes / no / empty / EOF / KeyboardInterrupt / request_id round-trip / `AutoApprover`: parametrised default + records calls |
| Approval in the Loop (§7 acceptance) | [`tests/test_approval.py`](tests/test_approval.py) | read_only tool → no approval event, runs anyway / **irreversible + approve → tool runs, events ordered policy < request < decision < tool_call** / **irreversible + deny → tool MUST NOT run, `error.code=approval_deny`** / irreversible + abort → tool MUST NOT run, `error.code=approval_abort` / CLI provider with empty input → abort, responder=timeout / CLI provider with garbage input → abort / **no provider wired → tool MUST NOT run, `error.code=approval_unavailable`** / external risk also escalates |
| Secret Scanner (§7) | [`tests/test_secret_scanner.py`](tests/test_secret_scanner.py) | parametrised regex hits for OpenAI / Anthropic / GitHub / HuggingFace / AWS / Bearer / PEM / `KEY=VALUE` assignment / span consistency / Anthropic-vs-OpenAI disambiguation / case-insensitive keywords / **combined regex+keyword signals both surface in audit reasons** |
| DLP PII Scanner (§7) | [`tests/test_dlp.py`](tests/test_dlp.py) | detects email / SSN / international `+phone`, reports unique sorted markers, and stays silent on normal technical text |
| Redaction (§7) | [`tests/test_redaction.py`](tests/test_redaction.py) | clean text unchanged / known credential shapes masked with kind labels / credential-assignment masks whole `key=value` span / multiple secrets all masked / overlapping `sk-ant-...` ⊂ `sk-...` matches don't corrupt output / `redact_dlp_text` masks credentials + PII together / `redact_payload` deep-walks dicts + lists + tuples and masks PII values / scalars untouched / **dict keys are never modified, only values** |
| Data Classifier (§7) | [`tests/test_data_classifier.py`](tests/test_data_classifier.py) | secret signals win over PII / PII (email / SSN / international phone) → SENSITIVE with unique markers / source defaults (web→public, file/cli→private, unknown→private) / empty text falls back to source default / result carries source + reasons |
| Safety in the Loop — **hard invariants** | [`tests/test_safety_integration.py`](tests/test_safety_integration.py) | **secret in file → NEVER in JSONL, NEVER in any LLM call, NEVER in answer, cached artifact is `[REDACTED:openai-key]`** / **PII in user input or tool output → NEVER in JSONL/LLM/memory cache raw, emits `sensitive_detected`** / `data_classified` + `secret_detected(surface=tool_output)` events fired / **secret pasted into the user question → NEVER in JSONL or LLM, `secret_detected(surface=user_input)`** / **LLM hallucinating a credential or PII is scrubbed on output** / clean inputs emit zero `secret_detected` / **third-party owner without `cross-owner-consent` is rejected; with the tag is saved; first-party owners (self / user / session, case-insensitive) always pass; default owner is first-party (backward-compat)** |
| Release / Supply-chain guard (§audit) | [`tests/test_release_hygiene.py`](tests/test_release_hygiene.py), [`tests/test_supply_chain.py`](tests/test_supply_chain.py), [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | release manifest excludes `.env`, `.git`, `.venv`, caches, credential/token/key files, `logs/` and `data/`; supply-chain audit requires pinned direct dependencies, `requirements.in`, `requirements.lock` with `sha256` hashes, SBOM sync via `scripts/generate_sbom.py --check`, CI `pip install --require-hashes`, `pip check`, release audit, pytest and branch coverage gate at 85% |
| Re-planning (§3 / MVP-8) | [`tests/test_replan.py`](tests/test_replan.py) | clean first attempt fires zero `replan` events / 0-step plan is success / **`tool_error` → planner re-invoked → attempt 2 succeeds** / **`verify_failed` → planner re-invoked → attempt 2 succeeds** / **`approval_deny` → irreversible tool never runs, safer read-only tool runs on attempt 2** / **bounded: `max_replan_attempts=3` ⇒ exactly 2 `replan` events + 1 `replan_exhausted`** / `<replan_context>` block carries `attempt`, `code`, `tool`, `reason` for every prior failure / `planner` + `plan` events carry `attempt`, `respond` carries `attempts_used` + `replan_exhausted` / `max_replan_attempts=1` disables replan; `=0` rejected at construction |
| Re-planning policy (MVP-12 unit) | [`tests/test_replan_policy.py`](tests/test_replan_policy.py) | `FailureBudget`: zero / negative max rejected, default `requires_different_action` correct for every type / `DEFAULT_BUDGETS` covers every `FailureType`, advice non-empty, recoverable types have `>=2` retries / `ReplanPolicy` construction: missing budget rejected, custom budget replaces default / `decide()`: empty history → continue / one `tool_error` → continue with advice / two `tool_error` → `abort_no_retry` / two `web_empty` → exhaust / two `timeout` → exhaust / one `approval_deny` → continue but populates `forbidden_actions` / two `approval_deny` → exhaust / `approval_unavailable` exhausts immediately / `policy_blocked` allows one alternative / `unknown` exhausts immediately / global cap stops before per-type budgets / `completed_attempts=0` rejected / forbidden-action JSON canonicalised (sorted keys) / duplicate forbidden deduped / recoverable failures populate empty forbidden list / un-JSON-able args silently dropped from forbidden / `to_log_payload()` shape pinned / typo `code` → coerced to `unknown` |
| Re-planning policy (MVP-12 acceptance) | [`tests/test_replan_policy_integration.py`](tests/test_replan_policy_integration.py) | **acc#1 `web_empty` → planner advice contains REFORMULATE + second query runs** / **acc#2 two `tool_error` → bounded, exactly 2 tool invocations, `abort_no_retry`** / **acc#3 stubborn planner re-proposing denied step → `approval_request` fires exactly ONCE + dangerous tool never invoked** / **acc#4 `policy_blocked` → planner switches to registered safe tool** / **acc#5 `verify_failed` → second attempt uses different tool** / **acc#6 exhausted run still produces structured Output Contract answer with `respond.replan_exhausted=True`** / **acc#7 full audit trail: exactly 3 `planner`, 2 `replan`, 2 `replan_attempt`, 1 `replan_exhausted` events for a 3-attempt run** / **acc#8 no-infinite-loop: pathological `tool_error` + `verify_failed` patterns terminate with bounded tool invocations** |
| Cross-MVP integration (audit traps) | [`tests/test_cross_mvp_integration.py`](tests/test_cross_mvp_integration.py) | **compensation × replan: step1-success + step2-fail still registers compensation** / **failed mutation registers NO compensation** / **LIFO rollback across two `run()` calls** / **hygiene × compensation: fresh backup survives default `cleanup_backups`** / **old backup beyond `keep_last` is purged** / **classification priority: `web_search` returning a STRING is `verify_failed` NOT `web_empty`** / **redaction × replan: secret-shaped argument from failed step never appears verbatim in JSONL audit log** / **risk_for × compensation: new-file create is `reversible` AND still registers `delete_path_if_created`** / **approval × replan event ordering: `approval_decision` < `replan` < `tool_call`, danger ran 0× safe ran 1×** / **persistent memory × replan: failed retries write ZERO records to the store** |
| Re-planning audit (MVP-12 invisible-bug traps) | [`tests/test_replan_audit.py`](tests/test_replan_audit.py) | **taxonomy drift: `ReplanCode` Literal ≡ `FailureType` (set-equality + `ALL_FAILURE_TYPES` parity + `DEFAULT_BUDGETS` keyset parity)** / **no stray `code="..."` strings in `core/loop.py` outside the whitelist (typo guard)** / init: legacy-only `max_replan_attempts` synthesises default policy / policy-only construction / compatible pair OK / **conflicting caps rejected with `ValueError`** / `max_replan_attempts=0` rejected / classification false-positives: `shell_exec` normal output / `shell_exec` dict without `timed_out` key / `shell_exec` non-dict output / `web_search` non-empty list / **different tool returning `[]` is `verify_failed` NOT `web_empty`** / **different tool with `timed_out=True` is NOT `timeout`** / canonical JSON parity: 7 args shapes produce identical strings on both sides / **nested dict keys recursively sorted** / **end-to-end: same args in different key order → blocked once** / **different args → NOT blocked (gate not over-matching)** / multi-type: 2-step plan with `tool_error` + `web_empty` → both triggers surface / **forbidden_actions strips entire plan → success branch, NOT `replan_exhausted`, `attempts_used=2`** / `_count_failures` ≡ `decision.failure_counts` for 6 known-code patterns (incl. empty) / `_format_replan_context`: empty everything → "" / advice-only / forbidden-only / blank lines filtered / **history block carries `attempt`, `code`, `tool`, `arguments`, `reason`** / **stable advice-before-forbidden block ordering** / `ReplanPolicy` robustness: `None` tool_name / non-dict arguments / global-cap boundary at `completed_attempts == cap` / `completed_attempts == cap-1` continues / `DEFAULT_MAX_TOTAL_REPLANS == 3` pinned |
| Self-repair: `run_tests` unit (MVP-13.1) | [`tests/test_run_tests_tool.py`](tests/test_run_tests_tool.py) | **construction**: rejects nonexistent workspace / zero / negative timeout; default = 90 s — **risk**: `reversible` static + dynamic, never escalates static — **argv build**: defaults to `tests`, paths appended after flags, `-k pattern` injected; non-list paths / too-many paths / `..` / absolute / drive-letter / non-ASCII path / non-string pattern / pattern > 200 chars / non-ASCII pattern all rejected with the right exception — **subprocess contract**: `shell=False`, cwd=workspace, timeout passed through, `capture_output=True`, env carries `PYTHONIOENCODING=utf-8`, env excludes arbitrary user keys (`MY_API_KEY` filtered) — **parse counts**: passed-only / mixed `7 passed, 2 failed, 1 error, 3 skipped` / `FAILED ...` and `ERROR ...` lines extracted; no summary → zero counts — **timeout**: `TimeoutExpired` surfaces `timed_out=True, exit_code=None`, partial stdout/stderr captured — **redaction**: credential printed by a test never lands in the result — **validate_output**: well-formed passes; non-dict, missing keys, negative counts, `total < sum`, `timed_out + exit_code` set → flagged/warned — **compensation_plan** always `noop` |
| Self-repair: `read_logs` unit (MVP-13.1) | [`tests/test_read_logs_tool.py`](tests/test_read_logs_tool.py) | **construction**: rejects nonexistent workspace, `risk=read_only` static + dynamic — **args**: `last_n=0` / > 500 / non-int rejected; `event_filter` non-list / empty-string / non-ASCII / > 20 entries rejected; non-ASCII `trace_id` rejected — **empty state**: no `logs/` dir, empty dir, missing `trace_id` all return empty payload — **happy path**: default picks most-recently-modified log; explicit `trace_id` targets that file; `last_n` truncates chronologically (newest kept); `event_filter` keeps only listed events — **robustness**: malformed JSONL lines silently skipped; non-dict JSON lines silently skipped — **sandbox**: `trace_id="..\\..\\etc\\passwd"` → `PermissionError` (path separators / `..` rejected ahead of fs lookup) — **redaction**: a credential in a payload that somehow reached disk is redacted on the way out — **validate_output**: well-formed passes; missing keys / non-dict / `returned > total` when not filtered → fail; same numbers OK when filtered |
| Self-repair: `diff_file` unit (MVP-13.1) | [`tests/test_diff_file_tool.py`](tests/test_diff_file_tool.py) | **construction**: nonexistent workspace / non-positive `max_bytes` rejected; `risk=read_only` static + dynamic — **args**: empty / non-string `path`, non-ASCII `path`, non-string `proposed_content`, oversize `proposed_content`, negative / oversize `context_lines`, non-int `context_lines` all rejected — **sandbox**: absolute outside workspace + `..` traversal rejected with `PermissionError` — **new file**: missing file → `file_exists=False`, every proposed line counts as `addition`, no deletions — **existing file**: identical content → empty diff + zero counts; single-line change → 1 addition + 1 deletion + new line in diff; higher `context_lines` produces longer diff; current file > `max_bytes` refused — **truncation**: 10 k-line diff hits `MAX_DIFF_CHARS` cap with marker — **redaction**: secret in proposed content scrubbed from diff — **validate_output**: well-formed passes; missing keys / negative counts / non-bool `file_exists` / non-string `diff` flagged; constants pinned (`DEFAULT_CONTEXT_LINES=3`, `DEFAULT_MAX_BYTES=1 MiB`) |
| Self-repair: planner sanitiser (MVP-13.1) | [`tests/test_planner_self_repair_sanitizer.py`](tests/test_planner_self_repair_sanitizer.py) | **`run_tests`**: defaults pass (`paths=['tests']`), explicit paths + `pattern` pass, non-list paths / > 16 paths / non-string element / non-ASCII path / absolute path / drive letter / `..` / non-string pattern / pattern > 200 chars / non-ASCII pattern all dropped with explicit warning; label truncated to 60 chars — **`read_logs`**: defaults pass (`last_n=50`), explicit `last_n` pass, `last_n=0` / > 500 / non-int / non-list `event_filter` / > 20 entries / non-ASCII / empty-string filter / non-ASCII `trace_id` / empty `trace_id` all dropped — **`diff_file`**: well-formed pass, label does NOT echo `proposed_content` (size + leak safe), missing path / non-ASCII path / absolute / drive letter / `..` / non-string content / out-of-range / non-int `context_lines` all dropped with explicit warning |
| Self-repair: integration (MVP-13.1) | [`tests/test_self_repair_integration.py`](tests/test_self_repair_integration.py) | **`run_tests`**: `reversible` action runs WITHOUT approval prompt, `policy` event carries `reversible` reason, full result reaches `tool_result.output`; audit log carries the actual argv invoked (`-k memory` round-trips), failed tests round-trip with `failed_tests=['tests/test_x.py::test_one']` — **`read_logs`**: read_only action runs with ZERO `approval_*` events, returns pre-seeded events, `event_filter=['error']` selects only matching events — **`diff_file`**: read_only, ZERO approval events, additions/deletions counted, on-disk file content **NOT** modified after running, new file shows additions-only diff — **chained plan**: `diff_file → file_write → run_tests` composes in one plan, `tool_call` ordering preserved |
| Repair Proposal Generator (MVP-13.3) | [`tests/test_repair_proposal.py`](tests/test_repair_proposal.py) | valid LLM JSON -> `RepairProposal` with diff preview / green baseline tests stop before LLM / wrong `target_file` rejected / secret in `proposed_content` rejected / invalid JSON rejected / `AgentLoop.propose_repair()` leaves the target file untouched |
| Self-repair E2E hardening (MVP-13.4) | [`tests/test_self_repair_e2e.py`](tests/test_self_repair_e2e.py) | real pytest subprocess audit for success path / approval-deny no-write path / bad-patch auto-rollback path / low-confidence block before approval or write |
| Evidence model (MVP-14.1) | [`tests/test_evidence.py`](tests/test_evidence.py) | **taxonomy**: every `EvidenceKind` has a `DEFAULT_CONFIDENCE` entry; values in `[0, 1]`; pinned hierarchy `user_explicit > test_result ≥ file > log_event > shell_output > diff_preview > web_page > tool_output > memory > web_search_hit > llm_claim > unknown` — **content_hash**: stable across calls, UTF-8 + surrogate-safe (no crash on `\ud800`), known sha256 hex for "hello" — **make_evidence**: fills `id`/`hash`/`fetched_at`/baseline confidence; unknown kind / `confidence ∉ [0, 1]` rejected; oversize excerpt truncated with `…[truncated]`; frozen dataclass; `to_dict ↔ from_dict` roundtrip — **ProvenanceChain**: empty / add / extend / `by_source_id` returns FIRST match / `by_kind` / `highest_confidence` / `to_log_payload` drops excerpt + reports `excerpt_len` — **factory dispatch**: `file_read` (path/empty/non-string), `web_search` (≤ 10 hits collapsed, empty → None, all-malformed → None), `run_tests` (passed/failed/timeout), `read_logs` (events / empty = weak), `shell_exec` (argv + stdout), `diff_file` (additions/deletions), `web_fetch` (uses tool's own content_hash + fetched_at), `file_write` → **None on purpose** (action, not a source), unknown tool falls back to `tool_output`, factory NEVER crashes on a `repr()`-throwing output — **failure contracts**: `status="error"` → None, empty `tool_name` → None, `arguments=None` safe — **non-tool constructors**: `evidence_from_user_directive` (top confidence), `evidence_from_memory_record` reduces confidence when source missing (with floor `0.25`), `evidence_from_llm_claim` strictly below memory |
| Evidence loop integration (MVP-14.1) | [`tests/test_evidence_integration.py`](tests/test_evidence_integration.py) | **single step**: `file_read` adds `kind=file` evidence with right source_id / `diff_file` adds `kind=diff_preview` — **non-sources**: `file_write` produces ZERO evidence (action, not source) — **failed steps**: missing-file read produces ZERO evidence — **`evidence_collected` event**: emitted once per run with compact chain payload (no full excerpts); 0-step plan emits empty chain; multi-step preserves order — **memory → evidence**: persistent record retrieved via keyword overlap becomes `kind=memory` evidence with `mem_xxx` in source_id — **`agent.last_provenance`**: initialised empty, updated per `run()`, resets between runs — **robustness**: unknown tool falls back to `tool_output` kind; loop never crashes on factory edge cases |
| Ranker-to-Output Policy (MVP-14.3x) | [`tests/test_source_ranker.py`](tests/test_source_ranker.py), [`tests/test_output_policy.py`](tests/test_output_policy.py), [`tests/test_output_policy_integration.py`](tests/test_output_policy_integration.py) | realtime detector / ordinary web pages and search pointers capped as `insufficient_for_realtime` / English realtime terms require word boundaries (`knowledge` no longer triggers `now`) / market domains require a live value timestamp / structured market tool evidence can support realtime / final answer downgrades high confidence to low when no live source exists / verified web/search tags become `[unverified:insufficient_for_realtime]` / `replan_exhausted` warnings are merged into `Unverified` |
| Source Registry (MVP-14.3b) | [`tests/test_source_registry.py`](tests/test_source_registry.py) | manual book source stores page-level claim / core Evidence kinds map to source types (`file`, `test_result`, `log`, `memory`, `user`, docs, forum) / registry built from ProvenanceChain uses SourceRanker metadata and marks weak/realtime-unsafe claims unverified / `AgentLoop` logs `source_registry` and exposes `agent.last_source_registry` |
| Knowledge Pipeline (MVP-14.3c) | [`tests/test_knowledge_pipeline.py`](tests/test_knowledge_pipeline.py) | SourceRegistryStore roundtrip + duplicate suppression / ClaimExtractor extracts sentence claims and rejects secret-shaped text / ConflictResolver marks same-subject different-value claims conflicted / KnowledgeWritePolicy rejects unverified claims and accepts strong source-backed claims / AgentLoop E2E persists source catalog and writes approved knowledge to persistent memory when enabled |
| Controlled Ingestion (MVP-14.3d) | [`tests/test_cli.py`](tests/test_cli.py) | `:ingest-source` stores source-backed claims in SourceRegistry without memory writes by default / `--write-memory` saves approved verified claims through existing memory policy / `:ingest-project --dry-run` builds an in-memory registry report without persisting registry or memory |
| Online Source Library (MVP-14.3f) | [`tests/test_source_library.py`](tests/test_source_library.py), [`tests/test_cli.py`](tests/test_cli.py) | source groups (`wikis`, `books`, `science`, `docs`) / domain filtering / `:source-library` listing / `:ingest-web` search -> fetch -> Source Registry with no network in tests |
| RSS / Atom Source Ingestion (MVP-14.3g) | [`tests/test_rss_fetch.py`](tests/test_rss_fetch.py), [`tests/test_cli.py`](tests/test_cli.py) | RSS + Atom parsing / URL safety / gzip / schema validation / `:ingest-rss` entries -> Evidence -> Source Registry with no network in tests |
| Source Connector Registry (MVP-14.3h) | [`tests/test_source_connectors.py`](tests/test_source_connectors.py), [`tests/test_cli.py`](tests/test_cli.py) | connector inventory (`local`, `web`, `rss`, `openalex`, `arxiv`, `github_public`, `government_data`) / status + cost payloads / connector-plan recommendations / CLI JSON and text output |
| Role Router (MVP-14.3e) | [`tests/test_role_router.py`](tests/test_role_router.py) | repair / learning / default operator-chat routing; each route carries tone, output style, knowledge scopes and allowed memory tags |
| Knowledge Use Policy (MVP-14.3e) | [`tests/test_knowledge_use_policy.py`](tests/test_knowledge_use_policy.py) | role-tag filtering, question-overlap admission, quarantined/obsolete memory rejection before keyword retrieval |
| Learning Planner (MVP-14.3e) | [`tests/test_learning_planner.py`](tests/test_learning_planner.py) + [`tests/test_cli.py`](tests/test_cli.py) | README / architecture / core/test prioritisation, goal-specific self-repair source selection, workspace-escape rejection, `:learn-project` plan -> dry-run ingestion |
| Autonomous Runtime (MVP-16.1) | [`tests/test_autonomous_runtime.py`](tests/test_autonomous_runtime.py), [`tests/test_budget_governor.py`](tests/test_budget_governor.py), [`tests/test_circuit_breaker.py`](tests/test_circuit_breaker.py), [`tests/test_approval_inbox.py`](tests/test_approval_inbox.py), [`tests/test_cli.py`](tests/test_cli.py) | bounded dry-run health pass / status + learning + tests tasks / non-dry-run blocked into approval inbox / cycle budget denial opens circuit / CLI `:auto-run` and `:auto-status` |
| Persistent Task Queue + Scheduler (MVP-16.2) | [`tests/test_task_queue.py`](tests/test_task_queue.py), [`tests/test_scheduler.py`](tests/test_scheduler.py), [`tests/test_autonomous_runtime.py`](tests/test_autonomous_runtime.py), [`tests/test_cli.py`](tests/test_cli.py) | JSONL task persistence / pending due filtering / task state transitions / schedule persistence / scheduler tick enqueues due tasks / queued tasks run through `AutonomousRuntime` / CLI `:task-*` and `:schedule-*` |
| Model Usage Ledger (MVP-16.3) | [`tests/test_model_usage.py`](tests/test_model_usage.py), [`tests/test_cli.py`](tests/test_cli.py) | role/model token ledger / JSONL persistence / historical vs current-session totals / session call-budget block / estimated token fallback / CLI `:model-usage` and `:budget-status` integration |
| Persistent Budget Windows (MVP-16.5) | [`tests/test_budget_ledger.py`](tests/test_budget_ledger.py), [`tests/test_model_usage.py`](tests/test_model_usage.py), [`tests/test_cli.py`](tests/test_cli.py) | hour/day JSONL budget windows / malformed budget records skipped / model call blocking across sessions / model tokens + cost units recorded / CLI `:budget-window-status` |
| Model Registry Audit (MVP-16.4) | [`tests/test_model_registry_audit.py`](tests/test_model_registry_audit.py), [`tests/test_cli.py`](tests/test_cli.py) | active routes are explained separately from the wider local registry / unavailable models flagged by missing env / unsupported providers flagged / CLI `:model-registry-audit` |
| Release Hygiene Guard (P0 audit blocker) | [`tests/test_release_hygiene.py`](tests/test_release_hygiene.py), [`tests/test_cli.py`](tests/test_cli.py) | release manifest excludes `.env`, `.git`, `.venv`, caches, credential/token/key files, `logs/` and `data/` / forbidden local artifacts reported as present-but-excluded / CLI `:release-audit` |
| Team Plan (MVP-15.0 dry-run) | [`tests/test_team_plan.py`](tests/test_team_plan.py), [`tests/test_cli.py`](tests/test_cli.py) | simple tasks avoid subagents / multi-concern goals emit bounded contracts / `--limit` truncation warning / tool allow/deny validation / stable JSON shape / CLI `:team-plan` |
| Team Executor Dry-Run (MVP-15.1) | [`tests/test_team_executor.py`](tests/test_team_executor.py), [`tests/test_cli.py`](tests/test_cli.py) | contract order walk / budget aggregation / stops before budget overflow / approval-required contracts marked but not run / verifier handoffs / CLI `:team-run` |
| Architecture Audit (operator control plane) | [`tests/test_architecture_audit.py`](tests/test_architecture_audit.py), [`tests/test_cli.py`](tests/test_cli.py) | layer checklist / current multi-agent state / priority gaps before real subagent execution / CLI `:architecture-audit` |
| `web_fetch` unit (MVP-14.2) | [`tests/test_web_fetch.py`](tests/test_web_fetch.py) | **construction**: defaults pinned (1 MiB, 10 s, `risk=read_only`); zero / negative `max_bytes` and `timeout_seconds` rejected — **URL validation**: non-string / empty / > 2048 chars / non-ASCII (`https://пример.рф`) / `file://` / `data:` / `ftp://` / `javascript:` / `ws://` rejected; missing hostname rejected — **local-network block**: localhost, 127.0.0.1, 10.0.0.1, 192.168.1.1, 172.16.0.1, **169.254.169.254 (AWS metadata!)**, 0.0.0.0, `[::1]` IPv6 loopback all refused; public IP (1.1.1.1) passes — **happy path**: HTML stripped to text, `<script>` and `<style>` blocks removed (content too), HTML entities decoded (`&amp;`/`&lt;`/`&gt;`/`&quot;`/`&#39;`/`&apos;`/`&nbsp;`), plain text passes through, JSON passes through, User-Agent set, `fetched_at` ISO-8601 — **content-type policy**: `application/octet-stream` refused, `image/png` refused, all 6 allow-list types pass (`text/html`, `text/plain`, `text/xml`, `application/json`, `application/xml`, `application/xhtml+xml`), `text/html; charset=utf-8` accepted, empty content-type accepted — **truncation**: oversize body capped, exact-size NOT truncated — **gzip**: `Content-Encoding: gzip` decompressed transparently — **errors**: `HTTPError(404)` and `URLError("dns fail")` surface as clean `ValueError` — **charset**: UTF-8 default, cp1251 honoured from header — **redaction**: secret in body redacted before return — **content_hash**: same body → same hash, different body → different hash — **validate_output**: well-formed passes, non-dict / missing key / short hash / negative bytes rejected, empty text warns (not fails) |
| `web_fetch` planner sanitiser (MVP-14.2) | [`tests/test_web_fetch_planner.py`](tests/test_web_fetch_planner.py) | well-formed passes / missing url dropped / non-string url dropped / > 2048 chars dropped / non-ASCII dropped / all 5 disallowed schemes dropped (`file://`, `ftp://`, `data:`, `javascript:`, `ws://`) / all 7 SSRF targets dropped (localhost, 127.x, 10.x, 192.168.x, 169.254.x, 0.0.0.0, `[::1]`) / label truncated at 60 chars |
| Verifier (MVP-14.4) | [`tests/test_verifier.py`](tests/test_verifier.py) | **citation parsing**: file/web/user/all 9 prefixes, multiple per chunk, URL with `?q=1`, command with colons (`python -m pytest -k memory`), `[unknown:foo]` NOT parsed, nested brackets safe — **sentence splitting**: period / question / exclamation / newline / empty chunks dropped / markdown list of 3 items — **match_citation**: file→file evidence, wrong-kind no match (web_page with `foo.txt` in url DOESN'T match `[file:foo.txt]`), `[user]` no-body matches user_explicit, partial substring match, empty chain none, first match returned for empty body — **verify**: single verified claim (citation rewritten to `[verified:file:foo.txt]`, no disclaimer); no citations → `[unverified]` tag + `DISCLAIMER_FULLY_UNVERIFIED`; mixed verified+unverified → no disclaimer; cited-but-unmatched flagged separately (raw citation stays, no `[verified:]` prefix); empty chain + uncited → `DISCLAIMER_NO_CHAIN`; empty answer → 0 chunks + `fully_unverified=True` — **multi-citation chunk**: two valid citations → both rewritten, one matched + one unmatched → still verified verdict — **log_payload**: shape pinned, large strings excluded — **kind affinity**: `[search:python]` matches `web_search_hit` NOT `web_page`, `[web:python]` matches `web_page` NOT `web_search_hit` — **robustness**: 1000-sentence answer processed |
| Verifier integration (MVP-14.4) | [`tests/test_verifier_integration.py`](tests/test_verifier_integration.py) | **verified**: cited answer gets `[verified:file:doc.txt]`, no disclaimer, `agent.last_verification.verified_chunks ≥ 1` — **uncited**: chain non-empty + zero citations → `DISCLAIMER_FULLY_UNVERIFIED`, all chunks `[unverified]`; chain empty → `DISCLAIMER_NO_CHAIN` — **`verification` event**: emitted once per run with `verified_chunks` / `fully_unverified` / `verdicts` / `disclaimer_set` — **`verifier_enabled=False`**: draft returned unchanged, no `verification` event, `last_verification is None`, `[file:doc.txt]` NOT rewritten — **redaction ordering**: secret in LLM draft redacted on the way out even when Verifier processed the chunk as `[unverified]`; `secret_detected` event for `surface=user_output` |
| Verifier polish (MVP-14.4.x) | [`tests/test_verifier.py`](tests/test_verifier.py) (in same file) | **`is_structural_chunk`**: all 6 Output Contract headers (`Conclusion:`, `Facts:`, `Sources:`, `Confidence:`, `Unverified:`, `Safety:`) recognised case-insensitive, with optional `#`/`##`, markdown-bold (`**Conclusion:**`) prefix/suffix and trailing whitespace; markdown headings `# Title` through `###### h6` recognised; bare list markers `-`, `*`, `+`, `1.`, `2.`, `1)`, `2)` recognised; empty / whitespace-only recognised; **real claims NOT misclassified** (header WITH content stays a claim) — **section-aware metadata skip**: lines inside `Sources`, `Confidence`, `Unverified`, and `Safety` are not recursively tagged `[unverified]` — **`verify` pure-structural answer**: 5 structural chunks → `total_chunks=0`, `structural_chunks=5`, NO `[unverified]` pollution — **mixed**: structural headers preserved verbatim, `[unverified]` only on bare uncited claims — **`general-knowledge` prefix**: registered in `CITATION_PREFIXES` mapped to `llm_claim`, present in `SELF_DECLARED_PREFIXES`, parsed as Citation — **`self_declared` verdict**: rewritten to `[declared:general-knowledge]`, NOT counted as verified, NOT counted as unverified — **full self-declared answer**: `DISCLAIMER_ALL_SELF_DECLARED` fires, `fully_unverified=False` — **mixed verified+self_declared**: no disclaimer fires; both annotations present — **verified+self_declared in same chunk**: `verified` wins (precedence) — **disclaimer matrix**: 5 (chain × verdict) combinations pinned end-to-end |
| Planner self-doc allowlist (MVP-14.4.y) | [`tests/test_planner_self_documentation.py`](tests/test_planner_self_documentation.py) | **default**: `README.md` is allowlisted, passes WITHOUT hint, non-allowlisted path (`core/loop.py`) dropped with helpful warning listing the allowlist, `README.md` passes WITH matching hint, hint mismatch remaps to hint (allowlist does NOT override an explicit hint) — **custom allowlist**: constructor accepts custom tuple, `AGENTS.md` passes when allowlisted, `README.md` dropped when NOT in custom allowlist — **validator**: `../etc/passwd` filtered, `/etc/passwd` and `\Windows\System32` filtered, `C:\Windows\notepad.exe` filtered, non-ASCII `архитектура.txt` filtered, `""` / `"   "` / `None` / `42` filtered, all-invalid input yields empty allowlist (NOT silently restoring default) — **defence in depth**: non-ASCII path still dropped by ASCII check even if allowlist somehow let it through; empty path string still dropped by no-path check |
| `extract_unresolved_web_urls` helper (MVP-14.5) | [`tests/test_verifier_unresolved.py`](tests/test_verifier_unresolved.py) | **filters by verdict**: only `cited_but_unmatched` chunks contribute (verified, unverified, self-declared, structural all ignored) — **filters by prefix**: only `[web:URL]` extracted (`[file:...]`, `[search:...]`, `[test:...]`, `[log:...]`, `[general-knowledge]` all skipped) — **filters by scheme**: only http/https URLs; scheme-less bodies (`[web:Wikipedia]`, `[web:example.com]`) filtered out; uppercase `HTTPS://` allowed (planner sanitiser normalises later) — **path/query preserved**: `https://x/p?q=1&r=2#frag` kept verbatim — **order preserved + dedup**: cite-order respected, repeated URLs collapsed once (stable) — **empty / whitespace body filtered** — works on empty report (returns `[]`) |
| Composite-body citation matching (MVP-14.5b) | [`tests/test_verifier_token_match.py`](tests/test_verifier_token_match.py) | **tokeniser**: splits on `:`/`/`/`\`/whitespace/`,` ONLY (preserves `_` and `-` so `run_tests` and `bug_lab` stay whole), case-lowered, stopwords filtered (`test`/`web`/`shell`/`https`/`www`/all-prefixes/all-kind-names), `< 3` chars filtered (MIN_TOKEN_LEN=3 pinned), all-stopword body → `[]`, all-short body → `[]` — **direct substring still works**: `[test:pytest]` and `[test:tests/bug_lab]` and `[shell:python]` resolve via the existing path — **token fallback**: composite `[test:run_tests:bug_lab]` resolves to the right `test_result` record because both tokens substring-match the new source_id; multi-candidate picks the highest-score record (preserving chain order on ties); composite `[shell:shell_exec:git:porcelain]` resolves; body with only stopwords returns `None`; body with only short tokens returns `None` — **web/search/file excluded from fallback**: regression `[web:https://unknown.example]` does NOT match `web_page:https://known.example` (would otherwise hit via shared `https` token); `[file:src/tools/web_fetch.py]` does NOT match a record for `src/core/loop.py` despite shared `src`; `[search:java tutorial]` does NOT match a `web_search:python tutorial` record; `_NO_TOKEN_FALLBACK_PREFIXES` inventory pinned (`{web, search, file}`) so a future contributor adding a URL-ish prefix must make an explicit call — **richer source_ids**: `run_tests` source_id is `test_result:run_tests:pytest:<paths>` (default `tests`, explicit list comma-joined, string accepted, 60-char cap on target segment, full `C:\Python311\python.exe` path NEVER appears); `shell_exec` source_id is `shell_output:shell_exec:<argv0_basename>:<short_cmd>` (Windows `python.exe` → `python`, POSIX `/usr/bin/git` → `git`, bare `echo` preserved, empty argv0 falls back to `cmd`, 3-segment prefix pinned even when full argv has `C:\…`) — **end-to-end through verify()**: composite test + shell citations rewritten to `[verified:test:...]` / `[verified:shell:...]`, the URL regression stays `cited_but_unmatched` |
| Unresolved-citation re-plan loop (MVP-14.5) | [`tests/test_unresolved_citation_replan.py`](tests/test_unresolved_citation_replan.py) | **happy path**: 1st planner call = `web_search` only, draft cites `[web:URL]`, Verifier marks `cited_but_unmatched=1`; loop appends `unresolved_citation` trigger, policy says continue; 2nd planner call sees the URL in `failure_context` and returns `web_fetch(url=...)`; fetch runs against a stubbed opener; re-verify on enriched chain → citation rewritten to `[verified:web:URL]`, `cited_but_unmatched_chunks=0`, exactly 2 `verification` events (one synth + one verify-phase), exactly 1 `replan` event with `phase=verify` carrying the unresolved URLs, chain ends with `kind=web_page` evidence — **hard cap**: planner refuses to fetch (empty plans) → at most 3 total planner calls (1 initial + 2 verify-driven), citation stays unresolved (no false `[verified:]` marker), `verify_replan_noop` logged — **no-web-citations**: `[file:...]`-only draft NEVER triggers the loop (zero verify-phase events) — **verifier-disabled**: `verifier_enabled=False` returns the draft unchanged with `[web:URL]` intact and exactly 1 planner call (no `verification` event at all) — **multi-URL advice**: two unresolved URLs both surface in the planner's `failure_context` in cite-order, 2nd planner call returns 2 `web_fetch` steps, both resolved |
| Tool base + Registry (§5) | [`tests/test_tools_base.py`](tests/test_tools_base.py) | `Tool.invoke` wraps success / exceptions become `status=error` with type+message preserved / `KeyboardInterrupt` propagates, **NOT** swallowed / default `validate_output`: `None`/empty `str`/`list`/`dict` are hard fails, scalars / non-empty values pass / `ToolRegistry`: get unknown raises `KeyError`, double-register rejected, `list` / `describe` shape |
| Web Search (§5 — validate_output) | [`tests/test_web_search.py`](tests/test_web_search.py) | non-list output hard-fail / all-malformed rows hard-fail / non-dict rows flagged / **empty list is OK with warning (not a `verify_failed`)** / well-formed row passes clean / missing snippet → warning / missing url → per-row skip / missing title skipped / cap-exceeded → warning / **bad-query types rejected BEFORE network** / contract: name=`web_search`, risk=`read_only`, default_max_results within cap |
| LLM client + mock planner (§3) | [`tests/test_llm.py`](tests/test_llm.py) | construction with `mock` needs no API key / unknown provider rejected / explicit kwarg overrides env / `AGENT_MODEL` env overrides default / synthesis-mode echo shape (`[mock-llm response]`, `system_chars`, …) / user_preview truncated to 200 chars / **mock planner: every cue → right tool (file_read / web_search / both / empty plan), parametrised over EN+RU cues** / file-hint fallback for substantive questions / short questions don't trigger fallback / **scaffolding tokens (`current_date`, `registered tools`) do NOT leak into cue detection** (regression) / output is always valid JSON object |
| Model Router (§3) | [`tests/test_model_router.py`](tests/test_model_router.py), [`tests/test_cli.py`](tests/test_cli.py) | legacy single-LLM mode / default + role-specific ENV routing / identical route instance reuse / repair proposal uses repair route / `config/model_registry.json`, `AGENT_MODEL_REGISTRY_PATH`, or `AGENT_MODEL_REGISTRY_JSON` can introduce a new model without code changes / role ENV override wins over registry / model selection policies (`conservative`, `balanced`, `cost`, `offline`) / unavailable keys are not auto-selected / `:models` text + JSON output with policy + availability |
| TraceLogger (§8 audit) | [`tests/test_logger.py`](tests/test_logger.py) | one line per `log()` call / `ts`/`trace_id`/`event` always present / no payload → no `payload` field / `extra` kwargs land in record / Pydantic `BaseModel` → nested JSON dict (not string) / nested list of models walked / **secret in string payload → redacted on disk** / **secret in nested payload → redacted** / **secret in `extra` kwargs → redacted** / **stderr pretty-print also sees redacted view (no back door)** / event markers: known events get short marker, unknown fall back to `event.upper()[:4]` / `close()` releases handle (Windows-safe round-trip) |
| Core Data Models (§12.1) | [`tests/test_models.py`](tests/test_models.py) | every model auto-fills prefixed `id` / two instances get distinct ids / timestamps are timezone-aware / `status` Literals on Goal/PlanStep / `risk` Literal on ApprovalRequest / `decision` Literal on PolicyDecision (allow/deny/escalate) / `decision` Literal on ApprovalDecision (approve/deny/abort) / `status` Literal on ToolResult (success/error/timeout) + optional defaults / `type` + `side_effects` Literals on Action / MemoryRecord type Literal + defaults / ErrorObject severity Literal + recoverable default — every Literal rejects an unknown value with `ValidationError` |
| ID factory | [`tests/test_ids.py`](tests/test_ids.py) | format = `prefix_<16 hex chars>` / prefix preserved / 2000 ids in a row are unique / empty prefix doesn't crash |
| `file_write` end-to-end (§5 + §7, MVP-9) | [`tests/test_file_write_integration.py`](tests/test_file_write_integration.py) | (1) new file create runs without consulting approval (risk_for=reversible, policy=allow, no `approval_*` events) / (2) parent-traversal surfaces as `tool_result.status=error` "escapes workspace", nothing leaked outside / (3+5+6+8) **overwrite + approve → file changed, `.bak.<ts>` holds original content, `approval_request` + `approval_decision` + `tool_call` + `tool_result(mode=overwrite, backup_path)` all logged, AutoApprover called exactly once** / (4) **overwrite + deny → file untouched, no backup, NO `tool_call` / `tool_result` events** / (4) **overwrite + abort → file untouched, no backup, NO `tool_call` / `tool_result` events** / (7) **secret in content → `tool_result.status=error` with "credentials", file NOT created, raw secret NEVER on disk** / (9) **overwrite with no approval provider wired → `error.code=approval_unavailable`, file untouched, NO `tool_call` / `tool_result`** / new file create works even with no provider |
| Memory hygiene — unit (§4 MVP-10) | [`tests/test_hygiene.py`](tests/test_hygiene.py) | **backups**: empty workspace → empty report / keep_last=3 floor protects all 3 newest / 5 backups: 3 recent kept by floor + 1 inside cutoff kept by age + 2 over floor & over cutoff removed / **sole survivor never deleted regardless of age** / `keep_last=0` still protected by age cutoff / multi-target groups stay independent / `dry_run=True` reports but doesn't delete / non-backup files ignored / unparseable timestamps skipped / negative args rejected — **similarity**: identical=1.0 / case+whitespace insensitive / unrelated≈0 / containment boost lifts a near-prefix above threshold / empty inputs → 0 — **dedup**: empty + single-record no-ops / unique records unchanged / exact duplicates collapsed with **oldest = canonical** / near-duplicates collapsed / `dry_run` keeps disk + count unchanged / idempotent (second pass finds 0 new groups) / threshold ∉ (0, 1] rejected — **TTL**: `ttl_seconds=None` never expires / `ttl_seconds=0` treated as "no TTL" / within-TTL kept / past-TTL removed with audit ids / `dry_run` keeps disk / no-store empty-report fallback — **summarise**: 0 records / 1 record → skipped reasons / 2+ records → LLM called, originals removed, new record tagged with both source tag + `summarised` / already-summarised records excluded / refuses to summarise `SUMMARY_TAG` itself / **LLM exception → store untouched, `skipped_reason='llm_error:...'`** / empty LLM output → skip / `dry_run` calls LLM but doesn't rewrite / `max_records` caps input / empty tag + `max_records<2` rejected |
| Memory hygiene — loop integration (§4 MVP-10) | [`tests/test_hygiene_integration.py`](tests/test_hygiene_integration.py) | **write-time dedup**: second `:remember` of same fact rejected with `duplicate of mem_xxx` in audit, exactly 1 record on disk, two `persistent_memory_write` events with `decision=save` then `decision=reject` / **backup cleanup**: 4 backups (3 recent + 1 old) → `backup_cleanup` event with `deleted_count=1, scanned=4`, `dry_run=True` keeps the file but still fires event with `dry_run=True` / **expire**: stale + fresh record on disk → `persistent_memory_expire(expired_count=1, expired_ids=[stale.id])` event, only fresh survives, no-store agent still emits event with `expired_count=0` / **dedupe**: 2 disk-seeded duplicates → `persistent_memory_dedupe(deleted_count=1, groups=1)` event, canonical = oldest / **summarise**: 2 disk-seeded records + canned LLM → 1 merged record with both source-tag + `summarised`, `persistent_memory_summarise(summarised_count=2, new_record_id=...)` event, merged record stays findable via retrieval policy |
| Memory Write Policy — dedup gate (§4 MVP-10) | [`tests/test_memory_policy.py`](tests/test_memory_policy.py) | empty `existing=` keeps legacy behaviour / exact duplicate rejected with `duplicate of mem_xxx (similarity=1.00 ≥ 0.85)` / case + whitespace insensitive dedup / unrelated new record passes / **dedup gate runs AFTER secret check** — a near-duplicate that's also a credential is still rejected as a credential / empty-content existing record doesn't accidentally match everything |
| Compensation system — unit (§5 Undo MVP-11) | [`tests/test_compensation.py`](tests/test_compensation.py) | plan has unique `comp_*` id / `noop` helper / `to_dict` ↔ `from_dict` round trip / `delete_path_if_created` removes file + removes dir recursively + **idempotent when target already gone** / **path escape rejected even on rollback** — outside file untouched / `restore_from_backup` restores content + removes backup / idempotent when backup gone / backup escape rejected / **multi-action plan applied in REVERSE order (LIFO)** / one failing action does NOT abort the rest / unknown action kind returns `status='error'` not crash / `noop` action alone / summary counts ok / noop / error |
| `shell_exec` — unit (§5 MVP-11) | [`tests/test_shell_exec.py`](tests/test_shell_exec.py) | construction rejects missing workspace / zero timeout / zero output cap; defaults `timeout=5s, cap=64KiB` — **`risk_for`**: every read_only / mutating command classified correctly, unknown → `external`, empty argv → `external`, case insensitive — **argv validation**: empty / non-list / non-string / >16 elements rejected; **every shell metachar `; \| & < > \` $ ( ) [ ] { } \n \r \t` rejected**; `~` and `$` rejected; unknown command not in whitelist rejected — **path validation** for mutating: needs exactly one arg, absolute `/etc/foo` / `\Windows\foo` rejected, drive letter `C:\evil` rejected, `..` traversal rejected, `sub/../../escape` rejected — **read-only execution**: whoami / hostname run, cwd=workspace, `shell=False`, env stripped to `{PATH, SystemRoot}`, unknown binary surfaces `FileNotFoundError` cleanly — **mutating execution**: mkdir / touch create paths inside workspace with `delete_path_if_created` plan; mkdir on existing path refused; touch on existing path is noop with `noop` plan; failed mutation doesn't leak partial state — **timeout**: `TimeoutExpired` surfaces as `timed_out=True, exit_code=None`, partial stdout/stderr captured — **redaction**: huge stdout truncated to cap with `stdout_truncated=True`; credential in stdout replaced by `[REDACTED:openai-key]`; secret in stderr on mutating path also redacted — **validate_output**: well-formed passes, non-dict fails, missing keys fail, inconsistent `timed_out=True + exit_code=0` warns |
| `shell_exec` end-to-end (§5 + §7 MVP-11, 9 acceptance criteria) | [`tests/test_shell_exec_integration.py`](tests/test_shell_exec_integration.py) | **(1) safe**: `whoami` runs with zero `approval_*` events; `mkdir` creates inside workspace — **(2) dangerous blocked**: `rm -rf` dropped by planner sanitiser; hand-crafted `argv=['rm','-rf','x']` reaches the tool which fails with `whitelist`-mentioning error, target file untouched; metachar in argv blocked at tool layer — **(3) approval required**: `mkdir foo` order `approval_request → approval_decision → tool_call → tool_result`, `approval_request.risk == risk_for(args)` not static — **(4) deny / abort / no-provider**: each produces the matching `error.code` and ZERO `tool_call` / `tool_result` events; filesystem untouched — **(5) timeout**: mocked `TimeoutExpired` surfaces `timed_out=True, exit_code=None` in `tool_result.output` — **(6) redaction**: credential in subprocess stdout NEVER lands on disk; JSONL carries `[REDACTED:...]` — **(7) JSONL chain**: full `policy → approval_request → approval_decision → tool_call → tool_result → compensation_registered` ordered correctly, `tool_result.output.compensation_plan` carries the typed action — **(8) plan before execution**: `agent.compensation_log` populated from `tool_result.output`; failed mutation produces ZERO `compensation_registered` event — **(9) rollback**: `mkdir+rollback` removes dir, `touch+rollback` removes file, empty log is `skipped_reason='no plans registered'`, `rollback <plan_id>` targets one specific plan among multiple, unknown id is `skipped_reason`, no workspace_root is also a clean skip — **planner sanitiser**: real `LLMPlanner` with FakeLLM returning `rm -rf /etc` produces zero sources with `not in whitelist` warning; metachar in argv produces zero sources with `metachar` warning |

All non-unit tests use two helpers in [`tests/conftest.py`](tests/conftest.py):

- `FakeLLM` returns canned JSON responses — bypasses the network and makes the
  planner / synthesizer fully deterministic.
- `FakePlanner` emits arbitrary `sources` directly to the Executor — bypasses
  the LLMPlanner's sanitiser, so we can deliver plans the real planner would
  reject and prove the Executor's own safety net (policy gate enforcement).

Explicitly deferred until later MVPs: multi-file planner/action workflows, PDF/DOCX,
large-file chunking, multi-file search / indexing, embeddings / vector
retrieval (MVP-10's dedup is word-Jaccard + containment, no vectors),
richer approval UX (approval history view, per-tool default policies,
approval timeout policy), **selective per-step retries (re-run only the
failed step instead of re-planning the whole cycle), per-failure-type
strategies, exponential backoff / jitter**, regression / property
tests, secret-vault rotation, DLP-style egress blocking on
`web_search`, widening the `shell_exec` whitelist to additional binaries
(every new entry needs its own compensation contract), cross-session
compensation log persistence, multi-agent delegation (MVP-12).

## Safety notes

Layered defenses. Each one is independently tested.

- **`FileReadTool`** — sandboxed to the workspace; refuses paths outside it,
  files larger than 1 MB, and non-UTF-8 content (strict decoding, no silent
  garbling).
- **`WebSearchTool`** — read-only, no API key, results capped at 10, output
  schema-validated.
- **`Tool.validate_output()`** — every tool semantically verifies its own
  output (empty, malformed, partial). Failures surface as `verify_failed`
  in the trace.
- **`LLMPlanner`** — cannot call `file_read` without an explicit user hint,
  cannot use a path different from the hint, drops unknown tools, clamps
  `max_results`, recovers from malformed JSON.
- **`PolicyGate`** — denies unknown tools, escalates anything that is not
  `read_only` or `reversible`. **Proven by integration test:** when the gate
  refuses, no `tool_call` / `tool_result` / `verify` events occur — the
  Executor's `_execute_step` exits before the tool is even constructed.
- **Approval gate (`ApprovalProvider`)** — on `escalate`, the loop emits an
  `ApprovalRequest`, asks the provider, and only proceeds on `approve`.
  `deny` / `abort` / no-provider all collapse to refusal: zero `tool_call`
  events, errors logged with codes `approval_deny` / `approval_abort` /
  `approval_unavailable`. Empty input and EOF from the CLI become `abort` —
  no input is never an implicit yes.
- **Kernel-side safety layer (MVP-7)** — `SecretScanner` + universal
  redaction + `DataClassifier` co-operate so no raw credential survives a
  trip through the system. Proven by `test_safety_integration.py`:
  secrets in files, in user questions, and even in LLM-hallucinated
  responses are scrubbed from JSONL traces, LLM prompts, working memory
  cache, and the final answer. Persistent memory refuses to save
  anything matching a secret pattern OR keyword. Third-party data
  (`owner ≠ self/user/session`) requires an explicit `cross-owner-consent`
  tag before MemoryWritePolicy will save it.
- **Bounded re-planning (MVP-8)** — every failure path
  (`tool_error` / `verify_failed` / `approval_deny` /
  `approval_abort` / `approval_unavailable` / `policy_blocked`) produces
  a structured `ReplanTrigger` that the next attempt's planner sees
  inside a `<replan_context>` block. The loop is bounded by
  `max_replan_attempts` (default 3); `=0` is rejected at construction;
  after the budget is gone the kernel emits `error.code=replan_exhausted`
  and the synthesizer still produces a structured Output Contract
  response so the user never gets a bare stack-trace style failure.
  An approval-denied irreversible step provably never runs even when
  the planner is re-invoked.
- **`MemoryWritePolicy`** — gates every write to persistent memory. Refuses
  credential shapes (OpenAI / Anthropic / GitHub / HF / AWS / PEM), secret
  keywords (`api_key`, `password`, `Authorization:`, …), tool-result dumps,
  blocked tags (`transient`, `temporary`, `do-not-save`), records without a
  consent signal, and length extremes. The rejects are layered: pattern and
  keyword checks run even when the user gives explicit consent — you do not
  get to opt into saving a leaked key.
- **`.env`** — gitignored conventionally; do not commit secrets.

## File layout

```
agent/
├── .env                              # API keys (private)
├── архитектура автономного Агента.txt
├── requirements.in                     # direct dependency intent
├── requirements.txt                    # pinned direct dependencies
├── requirements.lock                   # transitive dependency lock with hashes
├── sbom.cdx.json                       # CycloneDX SBOM generated from the lock file
├── .github/workflows/ci.yml            # release/supply-chain/test/coverage gate
├── pytest.ini                        # pytest config (testpaths + pythonpath)
├── main.py                           # CLI entry point (+ :remember / :forget / :memory)
├── tests/                            # 1533 hermetic tests (FakeLLM + FakePlanner)
│   ├── conftest.py                   # FakeLLM, FakePlanner, workspace fixture
│   ├── test_ids.py                   # ID factory: 4 cases
│   ├── test_models.py                # Pydantic Literal guards + defaults: 48 cases
│   ├── test_llm.py                   # provider routing + mock planner: 23 cases
│   ├── test_logger.py                # JSONL + redaction in audit log: 15 cases
│   ├── test_tools_base.py            # Tool.invoke + Registry + default validate_output: 22 cases
│   ├── test_file_read.py             # tool safety: 7 cases
│   ├── test_file_write.py            # MVP-9 unit: sandbox + secret + backup + risk_for: 42 cases
│   ├── test_file_write_integration.py # MVP-9 acceptance: approve / deny / abort / no-provider: 8 cases
│   ├── test_hygiene.py               # MVP-10 unit: backups + dedup + TTL + summarise: 45 cases
│   ├── test_hygiene_integration.py   # MVP-10 acceptance: through AgentLoop + audit events: 11 cases
│   ├── test_compensation.py          # MVP-11 unit: CompensationPlan + apply (delete/restore/noop): 16 cases
│   ├── test_shell_exec.py            # MVP-11 unit: whitelist + sandbox + timeout + redaction + risk_for: 55 cases
│   ├── test_shell_exec_integration.py # MVP-11 acceptance: 9 criteria end-to-end via AgentLoop: 21 cases
│   ├── test_web_search.py            # validate_output branches (no network): 16 cases
│   ├── test_planner.py               # routing + sanitiser + replan + file_write: 20 cases
│   ├── test_policy.py                # gate unit + runtime safety + risk_for: 15 cases
│   ├── test_memory.py                # WorkingMemory + artifact cache: 14 cases
│   ├── test_integration.py           # full Control Loop: 2 cases
│   ├── test_memory_integration.py    # memory_inject / cache_hit / clear: 6 cases
│   ├── test_memory_policy.py         # write + retrieval policy + owner + DLP + MVP-10 dedup gate: 56 cases
│   ├── test_persistent_memory.py     # JSONL store save/load/delete: 12 cases
│   ├── test_state_integrity.py       # checksummed JSONL state stores + quarantine: 6 cases
│   ├── test_persistent_integration.py  # :remember/:forget/inject in the loop: 14 cases
│   ├── test_approval.py              # approval gate (unit + 7 acceptance): 43 cases
│   ├── test_secret_scanner.py        # regex + keyword detection: 17 cases
│   ├── test_dlp.py                   # PII detection and marker reporting: 4 cases
│   ├── test_redaction.py             # redact_text + redact_dlp_text + redact_payload: 17 cases
│   ├── test_data_classifier.py       # public / private / sensitive / secret: 14 cases
│   ├── test_safety_integration.py    # hard invariants: secret/PII NEVER leaks: 11 cases
│   └── test_replan.py                # bounded re-planning: 11 cases
├── core/
│   ├── __init__.py
│   ├── ids.py                        # short trace IDs
│   ├── models.py                     # Core Data Models (§12.1)
│   ├── llm.py                        # Multi-provider LLM wrapper + temperature + mock planner
│   ├── logger.py                     # JSONL TraceLogger (redacts every payload)
│   ├── memory.py                     # Working Memory + artifact cache (§4)
│   ├── memory_policy.py              # Memory Write + Retrieval Policies (§4 + §12.4); MVP-10 dedup gate
│   ├── persistent_memory.py          # Persistent Memory Record store (§4, JSONL)
│   ├── state_integrity.py            # shared locks + checksums + quarantine for JSONL state stores
│   ├── hygiene.py                    # Memory Hygiene (§4 MVP-10): backups + dedup + TTL + summarise
│   ├── compensation.py               # Compensation / Undo system (§5 MVP-11): plan + apply + sandbox
│   ├── policy.py                     # Policy Gate (§12.4)
│   ├── approval.py                   # Approval Providers (§7 Human Approval)
│   ├── secret_scanner.py             # Single source of truth — credential detection (§7)
│   ├── dlp.py                        # Sensitive PII detection (§7)
│   ├── redaction.py                  # Universal redactor: text + deep payload (§7)
│   ├── data_classifier.py            # public / private / sensitive / secret (§7)
│   ├── planner.py                    # LLM-driven Planner (§3 Cognitive Core) + replan context
│   └── loop.py                       # Control Loop (§3) + bounded re-planning + Output Contract + Safety pipeline
├── tools/
│   ├── __init__.py
│   ├── base.py                       # Tool ABC + Registry + validate_output + risk_for
│   ├── file_read.py                  # File Read tool (read_only, strict UTF-8)
│   ├── web_search.py                 # Web Search tool (read_only, DuckDuckGo)
│   ├── file_write.py                 # File Write tool (reversible/irreversible — MVP-9)
│   └── shell_exec.py                 # Sandboxed Shell Exec tool with compensation (MVP-11)
├── data/                             # persistent_memory.jsonl lives here (created on first :remember)
└── logs/                             # per-trace JSONL logs (created on first run)
```
