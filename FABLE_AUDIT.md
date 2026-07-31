# FABLE_AUDIT — forensic audit of recurring systemic defects

Status: PHASE 1 complete (causal graph saved), PHASE 2 cluster 1 selected.
Branch: `audit/fable-forensic` @ `d622d5e` (origin/main).
Worktree: `copilot-worktrees/agent/andreyshapira1983-sketch-fantastic-memory`.
Live data read (READ-ONLY) from the operator's checkout: `data/*.jsonl`, `logs/run_*.jsonl`.

Method: every symptom below was checked against implementation paths and real
traces. Documentation was treated as claim, not proof. "Confirmed" means the
code path AND a durable trace/store artifact both show the behaviour.

---

## 1. Verified symptoms

### S1 — the agent re-calls the LLM to answer why it called the LLM  — CONFIRMED
- Trace: `logs/run_ab3f4bb6724763987b4c4331fcb220a3.jsonl` (2026-07-31 01:04–01:08).
  - ev[19] `interpret`: goal = «Почему ты вызываешь LLM? …» (mojibake in trace, cp1251 console).
  - ev[7], ev[25] `model_call_start` role=planner; ev[51] role=synthesizer — 3 LLM calls total.
- Code: `core/loop.py:874-882` classifies the turn (`is_self_analysis_turn`,
  `core/evidence_classes.py:175`) — but the ONLY effects are: log an event,
  admit the last 3 dialogue turns as evidence (`loop.py:1585-1614`), and suppress
  nothing. There is no introspective path that answers from the agent's own
  trace/log without a fresh planner+synthesizer round-trip.
- Note: this is a design gap, not a crash. The loop has exactly one shape:
  plan(LLM) → tools → synthesize(LLM).

### S2 — current trace loses to old logs and persistent memory — CONFIRMED
- Code: `core/loop.py:4077-4100` — only `artifacts` (fresh tool outputs) pass
  through `apply_total_budget` (`core/evidence_budget.py:216-273`), which trims
  the LARGEST block first. Freshly read source files are almost always the
  largest block.
- Meanwhile `persistent_block` / `experience_block` are injected OUTSIDE the
  budget (`loop.py:915-916`, `991-993`, `3932-3966` synthesizer path) — memory
  is structurally untrimmable, fresh evidence is structurally first to go.
- Trace: ev[48] `evidence_budget_trim` labels=["read_logs:latest"],
  total 31 972 chars — the fresh read was cut; memory blocks were not.
- Consequence observed 2026-07-30 (user-documented): agent quoted memory record
  `mem_528f46c99825fb423c16396077d866fe` ("Bug fixed in core/loop_methods2.py…")
  while the fixed code itself did not survive the trim → reported an already
  -fixed bug as current.

### S3 — reasoning_action_mismatch is logged, action executes anyway — CONFIRMED
- Code: `core/loop.py:1281-1298`. `check_reasoning_actions(...)` → if mismatch:
  `self.log.log("reasoning_action_mismatch", ...)`. Then execution proceeds
  unconditionally to `_build_plan` (`:1298`). The module itself declares
  "observational only" (`core/reasoning_action_check.py:13-14`).
- Trace: ev[30] mismatch `unjustified_actions=["read_logs"]` → ev[37] `tool_call`
  read_logs executed anyway.
- Assessment: by design (MAST FM-2.6 shadow mode). It is a *sensor without an
  actuator*. Not a bug per se; a gap only if a stricter mode was claimed.

### S4 — diagnostic queries ARE written into durable memory — CONFIRMED
- Same diagnostic run (`run_ab3f4bb…`, a "why did you call the LLM" question)
  produced 5 × `persistent_memory_write` (ev[41..45]): contents are literally
  log lines re-stated as facts, e.g. «strategy_classified: 2026-07-31T01:04:43…
  Source: log:log_event:run_ab3f4bb…:36», tags `["fact","knowledge",
  "source-backed","log", claim:*]`, Confidence 0.85.
- Store check: `data/persistent_memory.jsonl` now holds 10 records with
  `Source: log:` (scan 2026-07-31).
- Gate analysis (`core/knowledge_pipeline.py`):
  - `KnowledgeWritePolicy.decide` (`:390-432`) rejects source types
    `{"forum","unknown","tool_output"}` (`:427`) — but ACCEPTS `log`,
    `test_result`, `memory`, `file`.
  - The SAME module already encodes the doctrine that log/tool_output/
    test_result/memory are "observations of one moment, not standing claims"
    (`_NON_ASSERTING_SOURCE_TYPES`, `:269`, MIR-054) — but that set is consulted
    ONLY by conflict detection (`:300`), not by the write gate.
  - Episodic banking (`loop.py:2592`) additionally banks EVERY completed cycle
    (episode sink), including diagnostic ones; only usage_eligibility is
    restricted (`usage_eligible=False` observed in the trace, ev[~134 of the
    user's larger session]). Episodic banking is by-design (records "what
    happened"), so the defect is specifically the *semantic knowledge* writes.

### S5 — architecture readiness > coding readiness — CONFIRMED (two meanings of "ready")
- Trace `logs/run_80f543a85ad2a6eeaae79614eff3420a.jsonl` (2026-07-31 11:28):
  - `architecture_audit`: ready_for_multi_agent_execution=True, 18/18 present,
    priority_gaps=[].
  - `operator_programming_readiness`: status="ready_for_read_only_programming_planning"
    (i.e. NOT ready to actually code).
- Code: `core/architecture_audit.py:81-98` — "ready" = all checks "present",
  and checks are file-existence + test-file-existence probes (`_build_checks`).
  `app/operator_status.py:410-460` — coding readiness = tool registry probes.
  Neither measures demonstrated capability (e.g. a passed end-to-end repair
  drill); they measure artifact presence. Architecture says "everything built",
  coding says "may only plan read-only" — both true, but they use one word
  "ready" for two different modalities, which reads as a contradiction and
  inflates perceived readiness.

### S6 — persistent memory contains legacy code/test/log fragments — CONFIRMED
- Store scan (`data/persistent_memory.jsonl`, 814 records):
  - 800/814 sourced `file:*`; 776 of them created 2026-07-25 in one ingestion
    wave; examples are raw `assert …` lines from tests stored as
    "fact/knowledge/source-backed", Confidence 0.85 (26 records match
    code/test/log regex).
  - 0/814 records carry any machine-readable status field.
  - 2 records state "Bug fixed …" in prose (incl. `mem_528f46c…`); nothing marks
    them as historical-fix records, so retrieval can quote them as current.
- Code: the writer was already hardened AFTER the wave — `ClaimExtractor.
  _accept_sentence` now rejects code fragments (`knowledge_pipeline.py:183`,
  commit `e1232e0` "prevent-project-ingest-memory-pollution"), and docstring
  admits these were "file chunks that flooded memory as distractors".
  But the store was never migrated: pre-filter records persist and keep feeding
  retrieval (S2 makes them beat fresh evidence).

### S7 — self-repair fails mid full programming cycle — CONFIRMED (structural)
- `core/repair_proposal.py`:
  - `:249-254` refuses when baseline tests are green (`no_failing_tests`) —
    so "write a fail-before test, then fix" is impossible for the agent itself;
    it can only repair what already breaks the suite.
  - `:303-321` refuses any target whose content exceeds one context window —
    `core/loop.py` (194 KB) and every large module are structurally unrepairable;
    proposal must contain the WHOLE replacement file.
- `core/self_repair.py:1-12`: controller "deliberately does not invent patches";
  envelope = diagnose→diff→approval→write→tests→rollback. Safe, but the
  generative half (produce a correct proposal for a real bug in a big codebase)
  is the part that fails mid-cycle. Analysis/proposal for small files: works.
  Full programming cycle (new test + multi-file change): not implemented.
- This matches the runtime evidence the operator observed: investigation runs
  produce diagnosis + honest unverified-markers, then stop short of repair.

### S8 — model audit shows default routes, not actual per-run models — CONFIRMED
- Trace `run_ab3f4bb…`: planner first call `gpt-5.4-mini`
  (route_reason=`policy:balanced:my-current-planner`), then `gpt-5.6-terra`
  (route_reason=`complexity:standard:openai`), synthesizer `gpt-5.6-terra`.
- Audit `run_80f543a…` `model_registry_audit`: `unique_route_models=
  ["openai:gpt-5.4-mini"]`, `catalog_mode=local_config`, `live_provider_catalog=false`.
  → the audit reports CONFIGURED routes; actual usage diverged (complexity
  escalation) and the audit cannot see it.
- Schema: `ModelUsageRecord` (`core/model_usage.py:83-118`) has NO run_id/
  trace_id field → the usage ledger cannot be joined to a specific run even
  though per-run `model_call_start/end` events exist in traces.

---

## 2. Causal graph

```
ROOT A. "Knowledge" admission trusts source *type*, not assertion class
        (write gate ignores the module's own non-asserting doctrine)
   ├─ immediate: KnowledgeWritePolicy accepts `log` and `test_result` sources   (S4)
   ├─ immediate: pre-e1232e0 ingestion stored raw file lines as facts           (S6)
   ├─ immediate: no machine-readable status on records → "Bug fixed" prose
   │             is retrievable as a current-state claim                        (S6→S2 incident)
   └─ shared consequence: polluted store amplifies ROOT B at retrieval time

ROOT B. Evidence budget is one-sided: fresh artifacts are trimmable,
        memory blocks are untrimmable and outside the budget
   ├─ immediate: apply_total_budget trims largest (= fresh file/log read) first (S2)
   └─ consequence: stale memory (incl. ROOT-A pollution) outweighs current code (S2 incident)

ROOT C. Sensors without actuators / probes without capability measurement
   ├─ reasoning_action check is observational by design                          (S3)
   ├─ architecture "ready" = files present; coding "ready" = tools registered —
   │  same word, no demonstrated-capability check behind either                  (S5)
   └─ model_registry_audit reads config, not the usage ledger; ledger schema
      lacks run_id so per-run truth is unjoinable                                (S8)

ROOT D. The cognitive loop has exactly one shape (plan→tools→synthesize)
   ├─ self-analysis questions re-enter the LLM loop                              (S1)
   └─ repair generator: whole-file, single-shot, only-on-red — no incremental
      cycle (test-first, multi-file, chunked)                                    (S7)
```

Confirmed roots: A, B, C, D (all evidenced above by code path + trace/store).
Rejected hypotheses:
- "S3 is a silent bug" — rejected: explicitly documented shadow mode; the
  defect class is C (sensor w/o actuator), not an accidental swallow.
- "S6 is an active writer bug" — rejected: writer already filters code
  fragments (`e1232e0`); the live defect is the unmigrated store + missing
  status/typing (ROOT A residue), not the current writer.
- "S8 models are unlogged" — rejected: traces DO log actual models
  (`model_call_start/end`); the audit simply doesn't read them and the ledger
  can't be joined per-run.

## 3. Falsifying experiments (per root)

- A: construct `ClaimRecord(source.type="log")` with high confidence → today
  `decide()=="save"`. Fix must flip to reject. Negative control: type
  "documentation" stays "save".
- B: build artifacts where fresh `file_read` block > memory block; run trim;
  today fresh block is cut first. Fix: memory-typed blocks enter the same
  budget with priority for fresh reads of the same path.
- C: compare `model_registry_audit.unique_route_models` with the set of models
  in `model_call_start` events of a run where complexity escalation fired —
  today they diverge silently; fixed audit must surface actual-vs-configured.
- D: ask repair generator for a fix with green baseline / oversized file —
  today `no_failing_tests` / `rejected`. (Repairing D is a feature build, not
  a patch; out of scope for cluster 1.)

## 4. Repair plan — one causal cluster per branch/PR

Cluster 1 (CHOSEN): ROOT A write-gate slice — the smallest provable invariant
fix with the highest downstream damage reduction:
  1. fail-before test: `KnowledgeWritePolicy` accepts a log-sourced claim.
  2. patch: `decide()` consults `_NON_ASSERTING_SOURCE_TYPES` (single source of
     truth, minus `memory`? — no: memory/log/tool_output/test_result all
     non-asserting per MIR-054; `file` stays writable but is already filtered
     by `_accept_sentence` + `_is_meaningful_claim`).
  3. negative tests: documentation/web still save; file behaviour unchanged.
  4. targeted + full pytest.
Store migration (the 776+ legacy rows) is Cluster 1b — a separate dry-run
migration script with backup + report, only after gate is fixed (fail-closed
order: stop the inflow, then drain the pool).

Cluster 2: ROOT B — budget symmetry (fresh-read-of-path evicts memory-of-path,
not vice versa). Cluster 3: ROOT C — model audit reads actual usage; add
run_id to ModelUsageRecord. Cluster 4: ROOT D — design work (introspective
answer path; incremental repair cycle), needs operator decisions.

## 5. Tests required (cluster 1)

- test_knowledge_write_policy_rejects_log_sourced_claim         (fail-before)
- test_knowledge_write_policy_rejects_test_result_sourced_claim (fail-before)
- test_knowledge_write_policy_rejects_memory_sourced_claim
- test_knowledge_write_policy_still_saves_documentation_claim   (negative ctl)
- test_non_asserting_types_single_source_of_truth (gate and conflict resolver
  consult the same frozenset)

## 6. Unresolved risks

- Store migration (1b) touches 814-row live JSONL with integrity hashes —
  must reuse the existing state-layer writer, never hand-edit; backup +
  dry-run + idempotence required (per docs/SELF_REPAIR_DOCTRINE.md).
- `role_router.py` tags suggest some flows intentionally store repair lessons
  with `log`-ish provenance — verify no production path legitimately writes
  log-sourced semantic facts before enforcing (checked: the 10 live log-sourced
  rows are all timestamp-restatements, zero information; but re-verify in tests).
- Operator's main checkout is dirty with tooling files; never commit from there.

## 7. Current state / next action

- Worktree branch `audit/fable-forensic` @ d622d5e, clean.
- NEXT ACTION (exact): create branch `fix/knowledge-gate-non-asserting-sources`
  from `origin/main`; add failing tests from §5 to
  `tests/test_knowledge_pipeline.py`; run
  `python -m pytest tests/test_knowledge_pipeline.py -q` expecting the two
  fail-before tests to FAIL; then patch `KnowledgeWritePolicy.decide` to
  reject `_NON_ASSERTING_SOURCE_TYPES | {"forum","unknown"}`; rerun targeted,
  then full `python -m pytest -q` (expect 6016+new passed, 3 skipped).
