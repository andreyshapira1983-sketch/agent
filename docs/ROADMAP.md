# Roadmap — order in which capabilities are developed

> **Status of this document:** authoritative for *intended order and current
> state*. It is the source-of-truth entry #3 named by `README.md`.
> **Facts are grounded in code** (`core/*.py`, `app/*.py`, `cli/*.py`,
> `main.py`) and in `docs/daemon-progress.md`. A capability marked
> **IMPLEMENTED** here means the code exists and is tested; it does **not**
> claim the behaviour is production-hardened. **PLANNED** means the contract is
> declared (module, doc, or test scaffold) but the full behaviour is not built.

The existence of a module is **not** proof that a capability is complete. Read
each track's status line, and cross-check `docs/daemon-progress.md` for the
per-PR merge/acceptance state of the async-daemon work.

---

## Track A — Single-shot agent cycle (foundation)

**Status: IMPLEMENTED.**
Observe → Interpret → Plan → Act → Verify → Respond, driven by an LLM planner.

- Control loop: `core/loop` (+ `loop_helpers`, `loop_methods`).
- Planner: `core/planner`; deliberation kernel before the LLM: `core/strategy_router`.
- Verifier + confidence gating: `core/verifier*`, `core/confidence_gate`,
  `core/confidence_vector`, `core/low_evidence_policy`.
- Evidence / provenance: `core/evidence`, `core/source_registry*`,
  `core/source_ranker`, `core/evidence_budget`.
- Entry point: `main.py --ask` (one-shot) or interactive REPL.

## Track B — Memory & knowledge governance

**Status: IMPLEMENTED (working + persistent + episodic/procedural).**

- Working memory: `core/memory`; persistent records: `core/persistent_memory`.
- Episodic / procedural / consolidation: `core/smart_memory`
  (procedure confidence is Beta(1,1)-smoothed — a single success is **not**
  treated as certainty).
- Write / retrieval policy + hygiene: `core/memory_policy`, `core/hygiene`,
  `core/episodic_hygiene`, `core/memory_echo_antibody`.
- Controlled ingestion: `core/ingestion*`, `core/knowledge_pipeline`.

## Track C — Safety, policy & autonomy governance

**Status: IMPLEMENTED (gates in place); autonomy stays human-gated.**

- Policy Gate — pre-execution checkpoint for **every** Action: `core/policy`.
- Governance modes (`diagnostic`, `learning`, `repair`, `improvement`,
  `governance`) + governed-operation verdicts (`allow` / `require_approval` /
  `deny`): `core/governance`.
- Human approval path: `core/approval`, `core/approval_inbox`,
  `core/approval_triage` (REPL: `:inbox`, `:approve`, `:deny`, `:approval-run`).
- Deep/Opus escalation is reason-gated — the agent never opens Opus for itself:
  `core/deep_escalation` (`main.py --reason`).
- See `docs/CENTRAL_AGENT_GOVERNANCE.md` for the full authority contract.

## Track D — Budgets, durability & long-running work

**Status: IMPLEMENTED (bounded runs); daemon is incremental — see progress doc.**

- Budget governor / ledger / **persistent kill-switch**: `core/budget_governor`,
  `core/budget_ledger`, `core/budget_kill_switch`, `core/model_usage`.
- Durability: `core/checkpoint` (resume mid-run), `core/state_integrity`,
  `core/file_lock`, `core/circuit_breaker`, `core/termination_guard`.
- Autonomous runtime / scheduling / campaigns: `core/autonomous_runtime`,
  `core/scheduler`, `core/task_queue`, `core/campaign*`, `core/work_session`.
- **Async daemon** (`app/daemon.py`): incremental, tracked per sub-item in
  `docs/daemon-progress.md`. `agent_tick.py` remains the single-shot fallback.

## Track E — Learning & self-improvement (self-build)

**Status: PARTIALLY IMPLEMENTED — every applied change stays human-approved.**

- Self-repair loop: `core/self_repair` (+ `repair_proposal`); REPL `:repair`.
- Coding-skill ladder **Ступень 1**:
  - Stage A — turn a code TODO/FIXME into a task + *failing* acceptance test:
    `core/self_task_producer` (`:self-task-propose`).
  - Stage B — implement one **approved** task until its frozen test passes:
    `core/self_task_builder` (`:self-task-build`).
- Self-build / self-apply lane (TD-023/024/025): `core/self_apply_lane`,
  `core/self_apply_bridge`, `core/self_build_producer`,
  `core/proposal_value_gate`, `core/incremental_splitter` (no-LLM split).
- **Human gate is mandatory:** applied steps go through `:approval-approve` +
  `:self-apply-run`. There is no unattended self-modification.

## Track F — Multi-agent / subagents

**Status: PARTIALLY IMPLEMENTED — real bounded child loops, NOT full isolation.**

What exists (`core/subagent_runner`, `core/team_executor`,
`core/subagent_registry`, `core/subagent_memory_scope`, `core/team_plan`):

- A subagent runs as a **real** child `AgentLoop` with its own trace and a
  restricted tool set.
- **Shared, not isolated:** the child reuses the parent's Policy Gate and
  Model Router/budget (`policy=self.policy`, `model_router=self.model_router`).
- **No persistent identity or memory:** `memory=None`, `persistent_store=None`.
- **Bounded:** `max_replan_attempts=1`, `verifier_enabled=False` — the parent
  reviews the child's answer (a subagent claim is a witness, not a verified
  source).

Not yet built: per-agent persistent memory, independent identity, independent
budget, and self-directed multi-agent coordination. See
`docs/future/CORPORATE_MODEL.md` for that target (explicitly future).

## Track G — Operator surface & observability

**Status: IMPLEMENTED.**

- REPL `:command` surface — see `docs/COMMANDS_MAP.md` (built from `main.py`).
- Structured JSONL logging: `core/logger`; trace ids: `core/ids`.
- Read-only audits: `core/architecture_audit` (`:architecture-audit`),
  `core/model_registry_audit`, `core/release_hygiene`, `core/supply_chain`.

---

## What is deliberately NOT here yet

- Full multi-agent isolation (own memory/identity/budget per agent).
- Unattended self-modification (kept behind human approval on purpose).
- A real installed Windows service (only the shell contract exists —
  `app/windows_service.py`, every `*_implemented` flag is `False`).
- The corporate/organisational model — future only, see `docs/future/`.

_Source of facts: repository code as of the referencing commit + module index
in `docs/AGENT_ANATOMY.md`. When this file and code disagree, code wins and this
file should be corrected._
