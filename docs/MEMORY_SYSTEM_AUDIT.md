# Memory System Audit — governance, learning gates, temporal truth

Independent, read-only audit of the agent's memory and durable-learning
governance. Findings are separated by **evidence class**: what is proven by
reading current code versus what still needs an end-to-end run to confirm.

This document is the corrected, authoritative version of the audit. It exists
separately from `docs/OPERATIONAL_FAILURE_MODES.md` (external operational failure
classes) and from `docs/self-audit-lessons.md` (already-fixed repo defects with
passing regression tests).

---

## Provenance

- **Audited commit:** `main` at `3f4f8fa5bab4bb20497d0248f1825274bbbb67af`
  (code of `main` is the source of truth; docs/logs/Figma rank below it).
- **Method:** static reading of current code + isolated `pytest` (tmp_path) +
  in-memory probes of the real retrieval path. The **live agent was not run**
  against the real `data/` stores.
- **Zero-write proof:** all durable stores had identical line count and SHA-256
  before and after the audit (`persistent_memory`, `.archive`, `episodic`,
  `procedural`, `consolidation`, `user_profile`, `assumptions`,
  `source_registry`, `memory_writes`). The audit itself wrote nothing.

### Evidence classes

- `confirmed-by-code` — proven by reading current code (file:line cited). A
  static-analysis conclusion, **not** an executed end-to-end experiment.
- `needs-e2e` — the code strongly indicates the behaviour, but the full-cycle
  consequence was not reproduced against a live run.
- `verified-control` — a control that is present and correct (not a defect).
- `open-risk` — a forward-looking risk, not a failure.

> Discipline: a `confirmed-by-code` finding is a claim about the code, not a
> claim that the full runtime consequence was observed live. The two are kept
> apart on purpose (see the audit's own correction #11).

---

## A. Confirmed by code (present behaviour)

### MGA-01 — reasoning/action mismatch is observational only
- **Code:** `core/loop.py:1082–1095`. On `has_mismatch` it only logs
  `reasoning_action_mismatch`; the surrounding `try/except` is commented
  "Observational only — must never abort the loop." Execution continues.
- **Status:** `confirmed-by-code`.
- **Note on fix:** `MismatchReport` (`core/reasoning_action_check.py`) has **no
  `severity` field** — only `unjustified_actions`, `mentioned_but_not_planned`,
  `matched_tools`. So "add a severity threshold" cannot be applied directly. A
  deterministic classification must be defined **first**, e.g.:
  `mentioned_but_not_planned` → warning; unjustified *read-only* action →
  warning/replan; unjustified *external/write* action → hard stop; several
  mismatches → replan; a mismatch caused only by weak lexical matching →
  log-only. Otherwise enforcement will block normal plans on heuristic false
  positives.

### MGA-02 — episode `outcome=success` ignores relevance and the confidence gate
- **Code:** `core/smart_memory.py` `episode_from_agent_cycle`. Outcome is decided
  solely from chunk counts: `replan_exhausted → failed`;
  `elif unverified > verified and verified == 0 → partial`;
  `elif weak > 0 and weak >= verified → partial`; `else → success`. Because of
  the `verified == 0` clause, `verified=1, unverified=10, weak=0` is **not**
  `partial` → it lands as `success` even though answer quality is ≈0.09.
  `relevance_score` and the confidence-gate verdict are never inputs.
- **Status:** `confirmed-by-code`.

### MGA-03 — one "success" makes an *active* procedure, with no quality gate
- **Code:** `procedure_from_episode` gates only on
  `episode.outcome == "success" and episode.tools_used` — it does **not** check
  `answer_quality_score`. Then `_smoothed_confidence` (Beta(1,1)) makes one
  success `(1+1)/(1+2) = 0.667`, and `status = "active" if confidence >= 0.6`
  → **active immediately** (`core/smart_memory.py:60–62, 221–222`).
- **Doc/code nuance:** `docs/ROADMAP.md` Track B says smoothing means "a single
  success is not treated as certainty." True for *certainty* (never reaches
  1.0) — but a single success **does** reach `active` (0.667 ≥ 0.6). The
  reassurance is incomplete.
- **Status:** `confirmed-by-code`. Combined with MGA-02: an episode with one
  verified and many unverified parts can be `success`, then a procedure is
  created without a minimum `answer_quality_score` and becomes `active` after
  one case.

### MGA-04 — `suspicious` injection content still enters the evidence chain
- **Code:** `core/loop.py:3110–3139`. A `malicious` verdict (`is_blocked`) drops
  the output and triggers a replan. A `suspicious` verdict is only **annotated**
  (`annotate_suspicious`) and passed through (`flat_output = annotated`); the
  successful step then becomes `Evidence` in the provenance chain.
- **Status:** `confirmed-by-code`.
- **Note on fix:** "always fully exclude suspicious" is too blunt. Better:
  do not use `suspicious` as confirmation of a fact; allow it only as a
  quarantined observation; keep it out of semantic auto-write; surface it as
  conflicting/untrusted material; a `clean` source keeps the normal path.

### MGA-05 — no semantic temporal-truth model
- **Code:** `MemoryRecord` (`core/models.py`) has only
  `id, type, content, tags, owner, ttl_seconds, created_at, access_count,
  last_accessed_at, importance, archived`. No lifecycle `status`
  (active/proposed/superseded/invalidated/conflicting/historical/exception),
  no inter-record relations, no `valid_from/valid_until`, no
  "confirmed by human/test/code/decision". Retrieval
  (`MemoryRetrievalPolicy.select`) ranks purely by token/tag overlap, tie-broken
  by `created_at`. An in-memory replay of the JWT→server-sessions history
  returned current-truth, an exception, and a legacy note as **equal facts**.
- **Precise wording:** a full semantic temporal-truth model is **absent**. Only
  *partial* mechanisms exist: TTL expiry, archiving (out of hot retrieval),
  recency in ranking, manual `QUARANTINE_TAGS` (incl. `superseded`), and an
  advisory `[historical README/reference]` note. Do **not** say "nothing exists".
- **Cross-reference:** `docs/MULTI_AGENT_COORDINATION_LAYER.md` §5/§7 already
  *proposes* trust classes and a `superseded` status — but that document is an
  explicit proposal; the current `MemoryRecord` implements none of it.
- **Status:** `confirmed-by-code`.

### MGA-06 — `web_fetch` output is classified `private` by default
- **Code:** `core/loop.py:165` `_TOOL_SOURCE_HINTS = {"file_read": "file",
  "web_search": "web"}` — **`web_fetch` is absent**. At `loop.py:3074`,
  `_TOOL_SOURCE_HINTS.get(tool_name, "tool_output")` → `web_fetch` falls back to
  `tool_output`, and `core/data_classifier.py:53` maps `tool_output →
  DataClass.PRIVATE` (while `web → PUBLIC`). So a public page fetched via
  `web_fetch` is classified `private` unless a stronger signal escalates it to
  sensitive/secret. This is current code behaviour, not a historical incident.
- **Status:** `confirmed-by-code`.
- **Fix (cleanest of the set):** add `"web_fetch": "web"` to `_TOOL_SOURCE_HINTS`
  + a regression test asserting a fetched public page classifies `public`.
  (`core/evidence.py:330` already special-cases `web_fetch` into a `web_page`
  evidence kind — a separate path; confirm no double handling.)

---

## B. Needs end-to-end reproduction

The code strongly indicates these, but the full-cycle consequence was not run
live. Do not report them as executed experiments.

### MGA-07 — a natural-language "do not learn" request does not engage any brake
- **Code (partial):** the only things that set a durable-learning brake are the
  explicit `:audit` command (`main.py:1344` → `set_audit_read_only`), autonomous
  dry-run (`autonomous_runtime.py:798`), and env `AGENT_FREEZE_AUTO_MEMORY`.
  `OperatorIntentKind` has **no** memory-freeze intent; `route_operator_intent`
  returns `None` for such phrases. So a free-text prohibition in an ordinary
  turn should run with `durable_learning_writes=True`.
- **Why `needs-e2e`:** the *no-route* fact is code-confirmed, but the full
  consequence (the run actually learning after a text prohibition) was not
  reproduced live.
- **Status:** `needs-e2e` (code-confirmed absence of route).

### MGA-08 — self-diagnostic is not recognised as a distinct strategy
- **Code (partial):** `route_operator_intent("Сделай проверку себя …")` and the
  English equivalent both return `None` → they fall into `general_question`.
  This part is code-confirmed.
- **Why `needs-e2e`:** the downstream consequence from the incident (running web
  search/fetch instead of a strictly-local audit) was not reproduced live.
- **Status:** `needs-e2e` (code-confirmed missing route).

### MGA-09 — exact zero-delta of *every* store under *every* mode
- **Evidence:** `tests/test_audit_read_only.py` proves the `:audit` path in
  isolation (episodic/procedural/consolidation/access-count/user-profile all
  frozen; `user-explicit` still allowed). There is **no** dedicated
  `source_registry Δ=0 under :audit` regression, and no per-mode/per-store live
  matrix.
- **Status:** `needs-e2e`.

---

## C. Verified controls (present — not defects)

These correct two overstatements in the earlier draft.

### The durable-learning brake is comprehensive **for agent-initiated writes** — but not for `user-explicit`
- `_durable_learning_suppressed()` (`core/loop_methods2.py:38`) = dry-run brake
  OR `audit_read_only`. When engaged it gates: semantic auto-write, Source
  Registry + Knowledge Pipeline (`loop.py:1392,1825`), episodic/procedural/
  consolidation (`_record_experience_memory`), user profile, assumptions, and
  **access-counter mutation** (`loop_methods2.py:143`).
- **Correction:** `set_audit_read_only` **deliberately keeps `user-explicit`
  `:remember` allowed** (documented; proven by
  `test_audit_read_only_freezes_agent_auto_but_allows_user_explicit`). So the
  correct claim is "covers all *verified agent-auto* durable sinks of the main
  `AgentLoop`", **not** "all sinks". "All sinks" ≠ "all agent-initiated sinks".

### Test coverage is not "all sinks proven"
- **Correction:** the Source Registry structural gate is visible in code, but
  there is no `source_registry Δ=0 under :audit` delta test (see MGA-09).
  Reading code is not a delta test — full test coverage of all sinks is **not**
  yet proven.

### Retrieval excludes archived and quarantined records
- `persistent_store.load()` reads only the active file (archive is a separate
  sidecar); `KnowledgeUsePolicy.filter` rejects `QUARANTINE_TAGS`. Confirmed.

### Historical fixes still holding (regression-tested)
- Project-ingestion pollution (evidence-only + fragment/mojibake rejection),
  curated-memory archive protection, episodic-hygiene wiring — all pass their
  regression tests on this commit (see `docs/self-audit-lessons.md`).

---

## D. Corrected fix proposals

### D.1 Natural-language no-learning → a run-scoped `LearningMode` (not `audit_read_only`)
A text prohibition ("do not learn / do not save anything durable from this run")
is **not** the same intent as "put the agent into operator audit mode".
`audit_read_only` is an operator mode that can persist across cycles and
deliberately leaves `user-explicit` allowed. The right primitive is a
**run-scoped** directive:

```
LearningMode = NORMAL | NO_DURABLE_LEARNING | AUDIT_READ_ONLY
```

`NO_DURABLE_LEARNING` must: apply to the **current run only**; block semantic
auto-write, episodic, procedural, consolidation, Source Registry durable write,
user profile, assumptions, and access-counter mutation; and reliably restore
`NORMAL` afterwards. Do not disguise a user prohibition as an audit mode.

### D.2 Minimal temporal-truth model (refined)
Add opt-in fields to `MemoryRecord`, but with these corrections:

- **Default `lifecycle_status = unclassified`**, *not* `active`. Legacy records
  whose lifecycle is unknown must not be silently asserted as current truth. In
  a shadow phase, retrieval stays unchanged for `unclassified`.
- **One canonical relation direction** stored (e.g. `superseded_by`), with the
  inverse computed via an index — storing both `supersedes` and `superseded_by`
  invites desync.
- **`confirmation_refs`**, not a single `confirmed_by` string: who (person),
  which test, which commit, which decision id, which source, and when.
- **A validity period:** `valid_from` / `valid_until` (or `effective_at` /
  `observed_at`). `created_at` is not always when a claim became true.
- **Intent-aware retrieval order**, not a fixed one. `current truth → exceptions
  → conflicts → history` fits "how does X work now?" but not "why did we drop
  JWT?" or "when did the migration happen?". Retrieval must branch on intent:
  current-state / historical / change-explanation / conflict-investigation /
  exception-lookup.

### D.3 Safe rollout for any automatic lifecycle decision
advisory/shadow (log proposed lifecycle changes, retrieval unchanged) →
counterfactual (answer before/after) → offline replay on labelled histories
(incl. JWT/server-sessions) → human-approved apply for supersession/
invalidation/conflict-resolution → limited production. Never silently hide or
override a record on one model inference.

---

## E. Required regression / integration tests

One test per defect; each must fail on the broken behaviour and pass on the fix;
do not bundle unrelated defects.

1. **MGA-06** (cleanest first): a public page via `web_fetch` classifies
   `public`, not `private`.
2. **MGA-02/03:** `verified=1, unverified=10` → episode is **not** `success`;
   and a low-quality success does **not** create an `active` procedure.
3. **MGA-01:** an unjustified *write/external* action stops/replans; a weak
   lexical mismatch only logs (requires the deterministic classification first).
4. **MGA-04:** `suspicious` content is admitted only as a quarantined
   observation and never becomes a verified claim or semantic write.
5. **MGA-07:** a run-scoped `NO_DURABLE_LEARNING` directive yields zero delta on
   every agent-auto store; `user-explicit :remember` behaviour is separately
   specified.
6. **MGA-09:** `source_registry Δ=0 under :audit` (and the per-mode matrix).
7. **MGA-05:** retrieval does not present superseded/proposed/historical records
   as equal current facts once lifecycle fields exist.

---

## F. Scope limits (explicit non-claims)

- The live agent was not run against real stores; MGA-07/08/09 rest on code
  reading, not observed full-cycle runs.
- Numbers like a "3565-claim Source Registry" are a **local snapshot**, not an
  architectural constant; they change as the agent runs. Do not cite them as
  fixed facts without an attached snapshot/log.
- Nothing here is declared "fixed". Confirmed defects are `confirmed-by-code`;
  fixing them requires the tests in Section E.
