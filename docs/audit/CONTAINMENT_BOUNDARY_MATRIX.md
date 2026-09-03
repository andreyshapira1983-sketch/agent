# The containment-boundary matrix — 2026-08-21

> **⚠️ SNAPSHOT of 2026-08-21 (banner added 2026-09-03).** The classification
> method stands; the parameter list was read from that day's constructor and
> may have drifted. Re-verify against `AgentLoop.__init__` before relying on
> any specific row.

Every parameter of `AgentLoop.__init__`, classified so that a later
consolidation cannot silently open a hole. Read-only audit under the
architectural freeze; nothing here is built.

The question this answers came from one line the code writes about itself
(`core/loop_init.py:93`): memory permissions are instance-scoped, fixed for the
agent's life, "deliberately no per-run API yet". Three of them —
`durable_writes`, `experience_retrieval`, `episodic_replay` — were already
proven to be containment boundaries. The matrix finishes the enumeration.

## The count

| | |
|---|---|
| Constructor parameters | 36 |
| Containment boundaries — a different value makes something REFUSE | **26** |
| Must become run-scoped before organs share a host | **28** |
| Not boundaries | 10 |

By kind: 17 permission envelopes, 9 capabilities, 5 tuning knobs, 4
collaborators, 1 identity/state.

**The precondition is therefore much larger than it looked.** It is not three
memory flags. Twenty-six of thirty-six parameters carry a refusal, which means
twenty-six ways for an organ to gain authority it does not have today merely by
being handed a host's object.

## The structural defect underneath

Today's run-scoping, where it exists at all, is **save-and-restore mutation of
a shared object**. `core/autonomous_runtime.py:1007` does
`policy.blocked_tools = policy.blocked_tools | to_block` and restores it in a
`finally` at `:1036`; the same shape covers `planner.hidden_tools` and the
gateway attributes.

That is correct only while exactly one run holds the object. Consolidate organs
under one host and **one organ's `finally` lifts another organ's block
mid-run** — silent widening, with no error and no log line. Any per-run
envelope must therefore be a value the run carries, not a field the run
temporarily overwrites.

## A live inconsistency worth fixing before it is copied

The only child agent this codebase builds is already narrowed by exactly one
parameter and not the other. `core/subagent_runner.py:610` gives the child a
fresh registry containing only the safe tool set, but passes
`policy=self.policy` — the PARENT's gate, which still holds the PARENT's full
registry. So for a sub-agent the policy denies nothing extra and the whole
narrowing rests on `registry` alone.

Three unreconciled registry references exist per agent: `loop.registry`,
`loop.policy.registry` and `loop.planner.registry`. A consolidation that
narrows one and shares the others reproduces this exact divergence.

## The matrix

| Parameter | Kind | Boundary | Must become run-scoped | Where a different value refuses |
|---|---|---|---|---|
| `consolidation_store` | capability | **yes** | yes | cli/commands_memory.py:247 — `or agent.consolidation_store is None` → refusal printed at :249; it is the ONLY … |
| `episodic_store` | capability | **yes** | yes | core/self_build_memory.py:159 — `if store is None: return False`, the sole guard before `store.save(episode)` … |
| `memory` | capability | **yes** | yes | core/loop_step_execution.py:415 (`decision = self.policy.check(action)`) and its deny at :418; for effectful t… |
| `persistent_store` | capability | **yes** | yes | core/loop_memory_commands.py:69-74 — `if self.persistent_store is None: decision = MemoryWriteDecision("reject… |
| `procedural_store` | capability | **yes** | yes | cli/commands_memory.py:246 — `or agent.procedural_store is None` → printed refusal `(smart memory stores are n… |
| `source_registry_store` | capability | **yes** | yes | core/knowledge_pipeline.py:553 — `if source_store is not None:` gates `source_store.save_registry(registry)`; … |
| `planner` | collaborator | **yes** | yes | core/planner.py:490-497 — `try: self.registry.get(tool_name) / except KeyError: ... dropped; dropped_tools.app… |
| `role_router` | collaborator | **yes** | yes | Live one: core/low_evidence_policy.py:95 `if role in _GENERATIVE_ROLES:` -> `is_evidence_expected` returns Fal… |
| `pending_clarification_path` | identity or state | **yes** | yes | core/loop.py:322 — `_decided = None if _resumed else (self._prior_step_gate(...) or self._contract_ambiguity_g… |
| `approval_provider` | permission envelope | **yes** | yes | core/loop_step_execution.py:891 — `if self.approval_provider is None:` emits `code="approval_unavailable"` and… |
| `clarification_enabled` | permission envelope | **yes** | yes | core/loop_gates.py:105 `if self.clarification_enabled:` → returns `_clarif.question`; the caller core/loop.py:… |
| `durable_writes` | permission envelope | **yes** | yes | core/loop_memory_write.py:125 — `return sink not in allowlist`, the last rule of `_durable_learning_suppressed… |
| `episodic_replay` | permission envelope | **yes** | yes | core/loop_gates.py:191 — the `self.episodic_replay` conjunct of the guard at :190-196. False makes the conjunc… |
| `experience_retrieval` | permission envelope | **yes** | yes | core/loop_memory_read.py:225 — `if not getattr(self, "experience_retrieval", True):` → logs `rejected_by={"ret… |
| `gateway_dry_run` | permission envelope | **yes** | yes | core/actuation_gateway.py:148-155 `if self.dry_run: return GatewayDecision(outcome="simulate", ... reasons + (… |
| `gateway_path` | permission envelope | **yes** | yes | core/knowledge_pipeline.py:589 — `if require_verified and claim.status != "verified":` → the claim is skipped … |
| `knowledge_auto_write` | permission envelope | **yes** | yes | core/knowledge_pipeline.py:556 — `if not auto_write_memory or remember is None:` returns early; every claim ge… |
| `knowledge_pipeline` | permission envelope | **yes** | no | core/knowledge_pipeline.py:419 — `if _REDACTION_MARKER_RE.search(text): return KnowledgeWriteDecision("reject"… |
| `knowledge_use_policy` | permission envelope | **yes** | no | core/knowledge_use_policy.py:119 (quarantine reject) and :107-115 (role_scope reject), consumed at core/loop_m… |
| `memory_write_registry` | permission envelope | **yes** | yes | core/memory_policy.py:241-242 — `if echo.is_reject: return MemoryWriteDecision("reject", [echo.reason])`. Reac… |
| `odd_enabled` | permission envelope | **yes** | yes | core/loop_gates.py:54 `if self.odd_enabled:` -> :56 `if _odd.blocks:` -> :69 `return _odd.message`; consumed a… |
| `policy` | permission envelope | **yes** | yes | core/policy.py:71-85 — `if action.tool_name in self.blocked_tools: return PolicyDecision(..., decision="deny",… |
| `registry` | permission envelope | **yes** | yes | core/planner.py:490-497 — `try: self.registry.get(tool_name) / except KeyError: warnings.append(f"step[{idx}]:… |
| `replan_policy` | permission envelope | **yes** | yes | core/planner.py:508-513 — `if canonical_args and (tool_name, canonical_args) in forbidden_set:` the proposed s… |
| `verifier_enabled` | permission envelope | **yes** | yes | Two, in opposite directions. (1) Output: core/unsupported_claims.py:342 `if evidence_expected and fabricated >… |
| `write_policy` | permission envelope | **yes** | yes | core/memory_policy.py:148-155 — `if (source or "").strip().lower() in self.frozen_sources: return MemoryWriteD… |
| `assumption_store` | capability | no | yes | none keyed on this parameter — presence checks only: core/loop_run_tail.py:260 `and self.assumption_store is n… |
| `causal_store` | capability | no | no | none found. The nearest candidate, cli/commands_causal.py:21-23 (`store is None` → prints 'причинное хранилище… |
| `user_profile_store` | capability | no | yes | none keyed on this parameter — both uses are presence checks: core/loop_context.py:255 `if self.user_profile_s… |
| `llm` | collaborator | no | no | NONE. Searched every gate: core/policy.py:39-119, core/actuation_gateway.py:105-158, core/loop_step_execution.… |
| `logger` | collaborator | no | yes | NONE. core/logger.py:26-62 — `TraceLogger.log` builds a record, redacts it, writes and returns None; there is … |
| `cheap_path_enabled` | tuning | no | no | none — the only branch it selects is 'call the planner LLM or do not' |
| `clarification_gate_enabled` | tuning | no | no | none — no line refuses anything on either value |
| `max_replan_attempts` | tuning | no | yes | None of the authority kind. The nearest refusal-shaped line is core/replan.py:548 `if completed_attempts >= se… |
| `model_router` | tuning | no | no | NONE for authority — and the codebase says so in words at the exact place a refusal would belong. core/model_r… |
| `retrieval_policy` | tuning | no | no | none found |

## What this matrix does not settle

Which parameters should become run-scoped FIRST, and in what representation.
Nothing here proposes an API, and the run-scoped machinery that already exists
(`core/run_context.py`, entered per call at `core/loop.py:172`) was examined
only as evidence that the codebase already needed the distinction — not as the
place to put this.

Every verdict is from reading the code with a named refusal site. That is the
right standard for a classification and not enough for a migration: a parameter
marked "not a boundary" here means no refusal site was found, which is a
weaker claim than "widening it is safe".
