# Autonomy audit — 2026-09-03: what earlier passes missed, verified

> **What this is.** Six independent read-only reviewers, one axis each, were
> asked for defects NOT already in `MASTER_ISSUE_REGISTRY.md` (they grepped it
> first). Every finding below was then re-verified by hand against the code or
> re-run against the live stores before it was written here. Verdict words:
> **CONFIRMED** (I reproduced it), **PLAUSIBLE** (evidence quoted, not re-run).
> Statuses live only in the registry; this file is a dated snapshot.
>
> **Count.** 41 CONFIRMED, 14 PLAUSIBLE, 0 rejected outright. Of the 36
> registry entries none covered these mechanisms; several findings show a
> *fixed* registry entry re-opened one layer away from its fix.

## 0. The one sentence

The agent's cognition is measurably better than its plumbing: it chooses its
own defects, verifies with line numbers, admits fabrications when confronted.
The plumbing then (a) counts every run as work, (b) hands the worker the wrong
goal after a switch, (c) has no second step for any action but four, (d)
cannot record that a lesson was ever used, and (e) is described by doctrine
that stopped being true three weeks ago. None of that is about the model.

## 1. Accounting truth — the fabricated-success chain (axis 2)

| # | Site | What is minted | False for | Verdict |
|---|---|---|---|---|
| A1 | `core/autonomous_runtime.py:1062` + `:663` + `autonomous_runtime_types.py:137` | every run has a `status` task, always `done`; `semantic_result` counts any `done` | every run — a clarify or failed goal still reads `completed/work_done=True`; **overrides the Д1 fix landed today** | CONFIRMED critical |
| A3 | `core/campaign_io.py:746` | `proposal="approvals_pending=N"` from an absolute inbox count | any cycle while old items sit in the inbox → `did_work=True`, unproductive streak reset | CONFIRMED critical |
| A4 | `core/campaign_io.py:749-757` | `artifact="reasoning: …"` from any non-empty answer | the gate's question back → artifact → `did_work` | CONFIRMED high |
| A5 | `core/campaign_io.py:310,478` | dedup collision returns a non-None string | second proposal for the same target within TTL counts as a proposal | CONFIRMED high |
| A7 | `core/self_apply_bridge.py:42,440` | `rolled_back` is terminal → `mark_executed`; value review calls it "applied" | every lane rollback | CONFIRMED high |
| A8 | `core/autonomous_runtime.py:534` | `clarify` → `circuit.record_success()` | a run of nothing but questions never trips the breaker | CONFIRMED high |
| A9 | `core/autonomous_runtime.py:1018` | empty answer → `done` | `answer=""` | CONFIRMED medium-high |
| A2 | `core/autonomous_runtime.py:538` + `core/task_lifecycle.py:75` | queue row `done` from circuit verdict, bypassing `semantic_result` | a failed goal below the breaker threshold | PLAUSIBLE critical |
| A6 | `core/campaign_io.py:668-674` | liveness probe always `completed/work_done` | heartbeat missing; crash-looping daemon reads "alive" | PLAUSIBLE high |
| A10 | `core/self_build_memory.py:48-63` | `proposed → success` episode with `lesson` tag, never revised on denial | 55–60 % of self-chosen proposals are denied | PLAUSIBLE medium-high |
| A11 | `tools/run_tests.py` | never overrides `execution_status`; a red suite is `success` | collection error → 0 failures | PLAUSIBLE medium |
| A12 | `core/smart_memory.py:1464-1477` | empty/clarify answer → episode `outcome=success` | record pollution | PLAUSIBLE medium |

Common shape: the honest value is computed (`_pending_before`, `_dedup_verdict`,
`run_report.semantic_result()`) and the caller uses the dishonest neighbour.

## 2. Orchestration liveness — why a run lives four minutes (axis 6)

| # | Site | Mechanism | Verdict |
|---|---|---|---|
| L1 | `core/campaign_io.py:720,772,781,798` | after a goal switch the executor still receives the frozen `config.goal`; the switch changes who chooses, not who works | CONFIRMED critical |
| L2 | `core/campaign.py:140-145,295-299` | `action_steps` written only for the four causal actions → no other action can ever be reported exhausted; `_candidate_open_self_improvement_issue` re-elects `dominant[0]` forever | CONFIRMED critical |
| L3 | `core/campaign_io.py:227` | diagnosis→repair bridge only for action name `improve_failure_to_idea_pipeline`; every per-issue action has no second step | CONFIRMED critical |
| L4 | `core/best_next_action.py` (`if improvement is None and not registry_available`) | fresh-failure candidate (60) unreachable whenever the registry has any row | CONFIRMED critical |
| L5 | `core/campaign.py:360` | goal switch reachable only from the REPEAT exit; idle streak → `healthy_idle` without switch | CONFIRMED high |
| L6 | `core/campaign.py` / `agent_tick.py:89` | one `next_goal` attempt mid-run vs three at start | CONFIRMED high |
| L7 | `core/charter_goal.py:229-236` | file-identity rule short-circuits Jaccard: "Implement X.md" is a repeat of "draft X.md" — the ONE PAPER RULE's own next step is unreachable (1 live decision) | CONFIRMED high |
| L9 | `core/self_build_producer.py:1425` | any pending/approved self-apply item blocks the engineering hand for every goal; refusal stored as a proposal string → useful cycle | CONFIRMED high |
| L10 | `core/approval_inbox.py:171-174` | dedup consults pending only → a denied proposal re-files byte-identically; `approval_outcomes.jsonl` carries no dedup key | CONFIRMED high |
| L12 | `core/charter_goal.py:83-86,180` | `_recent_goals` reads `work_done` that the ledger never writes (0 of 479 rows); `blocked/empty/failed` count as work — **my own defect of 2026-09-02** | CONFIRMED medium |
| L8 | `core/charter_goal.py:239-241` | Jaccard over the union lets a shorter restatement through | PLAUSIBLE high |
| L11 | `core/charter_goal.py:457-469` | 8 bare goals "do not repeat" next to a menu recommending the same subjects; window ordered by first appearance | PLAUSIBLE medium-high |

## 3. Memory and learning — five cuts in one loop (axis 5)

All numbers re-run against the live stores on 2026-09-03.

| # | Fact | Verdict |
|---|---|---|
| M1 | `data/causal_claims.jsonl`: 25 REFUTED / 15 EXPLAINED / 10 OBSERVED / 1 LESSON, 0 ATTRIBUTED ever; all 15 EXPLAINED blocked by «не назван нарушенный инвариант» — a prose field no machine writes (`core/causal_lesson.py:185-188`); slice 2 (`chosen`) disqualifies slice 3 (`experimentable_claims`) | CONFIRMED fatal |
| M2 | the only lesson has `machine_action=''`; the one site where a lesson can change a decision (`core/self_task_producer.py:226`) is dead on live data; `lesson_block_for_prompt` drops `machine_action` | CONFIRMED fatal |
| M3 | 359 injections, **one** `lesson_key`, 347 into `planner.plan` on every turn regardless of scope (`core/planner.py:220`) | CONFIRMED high |
| M6 | 200 episodes; 86 usage-eligible, **84 of them self-build bookkeeping**; two ordinary episodes may steer a run; the four lost conclusions of 09-01 are all ineligible | CONFIRMED high |
| M7 | 837 persistent records; **635 owned by `reflection_engine`**, 625 written on one day; reflection bypasses the durable-writes allowlist (`core/reflection.py:545-568`); `lesson`+`reflection` tags make them unarchivable | CONFIRMED high |
| M9 | 200/200, 84 protected, 116 evictable spanning 2 days 9 hours; no eviction is ever logged; the one lesson's source episode already evicted | CONFIRMED high |
| M11 | 49 procedures: 42 `candidate` from one success and **retrievable** despite the docstring; the best-evidenced one (24 successes) stuck `needs_review` by a stale stored byte | CONFIRMED medium-high |
| M4 | `data/lesson_measurements.jsonl` has a writer, reachable only for one signal class and one CLI command; none on the planner path | PLAUSIBLE high |
| M5 | injection receipts carry no `run_id`; the provenance meter grades "measured" by existence, ignoring `defect_recurred` outcomes | PLAUSIBLE high |
| M8 | consulting a lesson stamps `memory:` on the run → the resulting episode is disqualified from becoming one | PLAUSIBLE high |
| M10 | unattended profile denies `access_stats` (evidence of use) and grants `hygiene` (destroys unused) — safe only because hygiene is in shadow mode | PLAUSIBLE medium-high |
| M12 | persistent retrieval: 3 slots, ties to newest; 590–622 relevant records cut by the cap; experience/lesson blocks never reach the synthesizer | PLAUSIBLE medium |

## 4. Dead wiring (axis 1)

| # | Fact | Verdict |
|---|---|---|
| W1 | `data/incidents.jsonl` write-only: `needing_human/summary/update` have no production caller; file absent; dedup on an open incident suppresses later ones | CONFIRMED high |
| W2 | `tools/journal_append.py`: `workspace_root` stored and never read; boundary is a string prefix; `data/../../x.jsonl` passes; path relative to CWD | CONFIRMED high |
| W3 | persistent budget ledger charges only `llm_calls/model_tokens/model_cost_units`; `web_fetches`, `cycles`, `team_*` limits and the kill-switch on them are inert; health prints `web_fetches=0/300` as if metered | CONFIRMED high |
| W4 | `core/persistent_memory.py:330` calls `policy.decide` without `recent_writes` → the memory door bypasses the echo antibody and never feeds `memory_writes.jsonl` | CONFIRMED medium |
| W5 | `AGENT_FETCH_ALLOW_HOSTS/DENY_HOSTS` read (`tools/web_fetch.py`, `rss_fetch.py`), set nowhere, documented nowhere — egress host lists permanently empty while unattended web is open | CONFIRMED medium |
| W6–W10 | prompt registry read for one of five prompts; `AGENT_DEEP_MAX_CALLS_PER_SESSION` undocumented (deep tier permanently off); `data/conflict_episodes.jsonl` unread; duplicated suppressible-severity constant; `AGENT_MODEL_POLICY` default drift (code `conservative`, docs `balanced`) | PLAUSIBLE |

## 5. Doctrine vs code (axis 3)

| # | Contradiction | Verdict |
|---|---|---|
| D1 | `CENTRAL_AGENT_GOVERNANCE.md:136` "No unattended self-modification" vs `core/rule_approved_apply.py:77` `inbox.approve(actor="rule:documents_only")` + apply, live since 08-27 | CONFIRMED high |
| D2 | `AUTONOMY_FREEZE.md:100` "network tools blocked unattended" (a *binding* doc per INDEX) vs web open since 09-01 | CONFIRMED high |
| D3 | freeze ledger row for `self_stop_record`: operator quote «ДА» and suffix "BLOCKED for the unsupervised path" in one cell | CONFIRMED high |
| D4 | governance modes "IMPLEMENTED": `core/governance` consumed by `self_repair` (one mode) and the architecture audit only; no production gate on `write_memory/run_shell/add_tool/change_policy` | CONFIRMED (partial wording) high |
| D6 | `AGENT_ENFORCE_UNSUPPORTED_CLAIMS` default `off`, absent from `.env.example` and `CONFIGURATION.md`; doctrine calls the layer "Enforcing" | CONFIRMED medium-high |
| D7 | `ROADMAP.md` Track H describes `project_intelligence/`, deleted 08-06 | CONFIRMED medium |
| D5, D8–D12 | OPERATIONS "all modes honour the same memory controls" (false by design); CONFIGURATION omits both safety brakes and the planner/synthesizer roles; INDEX calls the freeze absolute while the freeze says it is not; SELF_REPAIR §13 "PLANNED" for what the daemon now does; OPERATIONS lacks the paced-campaign mode; SUBAGENT_LIFECYCLE names the wrong module | PLAUSIBLE |

Root: three of four doctrine files last edited 2026-08-07; nothing measures
doc-vs-code disagreement (existence and filename tests only).

## 6. Guards that do not guard (axis 4)

| # | Fact | Verdict |
|---|---|---|
| G1 | `tests/test_no_test_pins_a_production_path.py` detector matches a spelling the repo never uses: finds 0, independent AST count finds 34; no negative control | CONFIRMED high |
| G2 | `ruff` in no requirements file; CI installs the lock only → `test_ruff_config` skips in CI; the lint-debt brake is enforced on one workstation | CONFIRMED high |
| G3 | `test_an_expired_effects_grant_does_not_authorise.py:109` asserts `"expires" in src and "continue" in src` — a substring for an irreversible-effects deadline check | CONFIRMED medium-high |
| G4 | `main.py` ceiling 2000 vs measured 27: 1 973 lines of regrowth slack | CONFIRMED medium |
| G5 | `test_a_suppression_names_its_removal_condition.py`: non-recursive glob (12 `tests/characterization` modules unseen); markers counted against prose, not per marker | CONFIRMED medium |
| G6 | `test_injection_guard.py`: `_suspicious` helper never called; five tests accept `("suspicious","blocked")` | CONFIRMED medium |
| G7 | `data/` gitignored → backup restore drill collects 0 cases in CI | CONFIRMED medium |
| G10 | ratchets raised on nearly every touch (agent_tick 16 raises since 08-20); four files at 0 slack | CONFIRMED process |
| G8, G9, G11–G15 | substring invariants in the replan-loop test; MCP read-only enforced by a 20-name denylist; 13 checker scripts run by nothing; dead constants | PLAUSIBLE |

## 7. What is missing for autonomy, in order

1. **Truthful accounting** (A1–A9). Without it nothing else can be measured;
   the 59 % "productive cycles" figure is unfounded.
2. **A second step** (L1–L4): the worker receives the current goal; every
   action reports exhaustion; a per-issue action has a bridge to a product;
   the fresh-failure signal is reachable.
3. **A commitments registry** (WAITING ≠ EXHAUSTED; denied ≠ never seen —
   L9, L10, A3), which is the roadmap's own next organ.
4. **A learning loop that can close** (M1–M3, M6–M7): a machine-writable
   exit from EXPLAINED, a lesson that can change a decision, a lesson block
   scoped to the question, a store not 76 % self-talk.
5. **Doctrine re-verified against code** (D1–D7) and **guards that can go
   red** (G1–G7), or the next audit finds this list again.

## 8. Who builds what

- **By hand (Fable), because the agent cannot repair the organ it needs to
  repair with:** 1, 2, the CI/guard wiring of 5, and the rule-side fixes of
  4 (M1 exit field, M2 machine_action transport).
- **The agent's own, once 1 and 2 hold:** choosing among its registry entries,
  causal investigation (its strongest organ), closing issues with witnesses,
  the three module-split slices, the doctrine text corrections (D1–D7 are
  reading-and-writing work with named lines).
- **Operator decisions, not code:** the priority table (habit 55 vs causal
  organ 45), `AGENT_AUTO_HYGIENE`, egress host lists, the deep-tier budget.
