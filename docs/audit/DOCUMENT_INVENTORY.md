# Document Inventory — Deep Revision, Stage 1

Read-only inventory of the project's documentation, produced as **Stage 1** of a
deep revision. It classifies each document, records its cross-references and
unique content, and — where a document claims a *current implementation* — checks
that claim against code on `main`.

## Provenance & method

- **Audited commit:** `main` @ `f317c4c` (`f317c4c74956fca0e2d2a6b5200ce5b38f74a31d`).
- **Source-of-truth order (per operator rules):** current code on `main` →
  actually-wired execution paths → reproducible tests → canonical documentation.
- **Method this stage:** full read of each document + targeted **code reading**
  of the specific `file:line` anchors that each audit doc claims as "current
  implementation" + existence check of the named regression tests. Line anchors
  were re-located in current code (several have drifted since the docs were
  written).

### Verification limitations (explicit non-claims)

- I **read code and confirmed test files exist**; I did **not run the test suite**
  and did **not execute the live agent** this stage. "FIXED in code" below means
  *the code at the cited location now implements the fix and a named regression
  test file is present* — not that I re-ran it green.
- Only the anchors named by the audit docs were checked. Modules not referenced
  by any document were not inventoried here.
- A document's status is a judgement about the **document**, not a re-adjudication
  of the underlying defect. `duplicate_candidate` never means "safe to delete".

### Status vocabulary

`authoritative` · `active_working_document` · `historical` · `duplicate_candidate`
· `stale` · `completed` · `proposal` · `unknown_needs_verification`

A document may carry a **primary** status plus a **partial** qualifier (e.g.
"authoritative but partially stale") when only some of its content drifted.

---

## Headline finding (drives most contradictions below)

Three of the audit documents — `CORE_AUDIT_2026-07-18.md`,
`MEMORY_SYSTEM_AUDIT.md`, `LIVE_PROBE_FINDINGS.md` (Batch 2), and
`OPERATIONAL_FAILURE_MODES.md` — were written as **"nothing here is fixed yet"**
registers. **Since they were written, a substantial subset of their findings has
been fixed in code, each guarded by a named regression test.** The documents were
not updated to reflect that, so they now *over-state* the number of open defects.

**Verified FIXED in current code (comment in code cites the finding id; regression test present):**

| Finding id(s) | Location (current) | Evidence of fix | Test file present |
|---|---|---|---|
| CORE-01 / MGA-02 (episode success from chunk counts) | `core/smart_memory.py:541` | `elif unverified > verified` (the `and verified == 0` clause removed; comment cites CORE-01/MGA-02) | `tests/test_episode_outcome_majority_unverified.py` |
| CORE-04 / LPF-008 (fabricated citations +0.5) | `core/confidence_gate.py:57` | `cited * -0.25` (was `+0.5`; comment cites CORE-04) | `tests/test_confidence_gate.py` |
| CORE-05 / LPF-012 / A5 (experience retrieval unfiltered) | `core/loop_methods2.py:29-30` | search now filtered `outcome=="success" or "lesson" in tags` | `tests/test_experience_retrieval_outcome_filter.py` |
| CORE-07 / OFM-010 (retry no backoff) | `core/task_queue.py:294` `mark_failed` | exponential `run_after` backoff added (comment cites OFM-010/CORE-07) | `tests/test_task_queue_retry_backoff.py` |
| CORE-09 / MGA-06 / A1 (web_fetch classified private) | `core/loop.py:172` | `"web_fetch": "web"` added to `_TOOL_SOURCE_HINTS` | `tests/test_web_fetch_classification.py` |
| CORE-10 (tokenizer drops short/numeric) | `core/smart_memory.py:95` | keeps any digit-bearing token (comment cites CORE-10) | `tests/test_episodic_tokenizer_numeric.py` |
| LPF-001 (host_tools as fake evidence) | `core/loop.py:3718-3743`, `core/planner.py:350`, `core/loop_helpers.py:63` | `<host_environment>` non-citable block + `host_tools_relevant()` gate | `tests/test_host_tools_context.py` |
| #9 (injection scan scope) | `core/loop_helpers.py` `untrusted_scan_view` | scans untrusted payload only | `tests/test_injection_scan_scope.py` |

**Verified STILL OPEN in current code (anchor unchanged):**

| Finding id(s) | Location | Current state |
|---|---|---|
| CORE-06 / LPF-010 (command failure recorded as step success) | `tools/shell_exec.py:520-539` `validate_output` | only warns on timed_out/exit_code *consistency*; `exit_code!=0` still not mapped to failure. (Audit itself warns a naive fix is wrong — needs per-command semantics.) |
| CORE-08 / MGA-04 / A8 (suspicious injection enters evidence chain) | `core/loop.py:3181-3186` | `suspicious` verdict still only `annotate_suspicious()`-d and passed through as `Evidence`. |
| CORE-03 / LPF-011 / A3 (quality 1.0 on empty chain) | `core/smart_memory.py:51-52` | still `return 1.0` on `total==0`; docstring **reframed** as intentional "neutral, not penalised". Effectively a *won't-fix / re-scoped* item, not applied per MEMORY_FIX_PLAN A3. |

**Ambiguous — needs Stage-2 verification:**

- **CORE-02 / MGA-03 / A4** (procedure `active` from one success): the anchor
  `procedure_from_episode` (`core/smart_memory.py:578`) is **unchanged** (still
  born `status="active"`, no quality arg). **But** a separate distillation path
  now carries an `answer_quality_score < 0.5` gate (`core/smart_memory.py:409`).
  Whether that gate actually protects the CORE-02 scenario end-to-end is not yet
  confirmed → **unknown_needs_verification** for this specific finding.

---

## Per-document inventory

### 1. `docs/CORE_AUDIT_2026-07-18.md`
- **Purpose:** execution-verified core defect register (cognitive/memory/safety core).
- **Relates to commit/date:** created `19f327c` (2026-07-18); audited `main` snapshot of that day.
- **Problems / decisions it contains:** CORE-01…12 (self-reinforcing bad-learning loop CORE-01…05; safety CORE-06…09; new CORE-10; consistency CORE-11; architectural CORE-12) + a "checked & clean" honest-negatives section.
- **Referenced by:** none inbound in `docs/`. **References:** `MEMORY_SYSTEM_AUDIT.md`, `LIVE_PROBE_FINDINGS.md`, `OPERATIONAL_FAILURE_MODES.md`, `self-audit-lessons.md`, `MEMORY_FIX_PLAN.md`.
- **Unique info:** the "one loop" framing (CORE-01→05 chain); the honest-negatives (Section 4); new finding CORE-10.
- **Overlap:** high — CORE-01…09 are re-verifications of MGA-*/LPF-*/OFM-010. Largely a consolidation of the other three audits.
- **Contradiction with current code:** **YES (partial-stale).** Section 7 states "None is yet fixed in code." Verified false now: CORE-01, CORE-04, CORE-05, CORE-07, CORE-09, CORE-10 are fixed with tests. CORE-06, CORE-08 still open; CORE-03 reframed; CORE-02 ambiguous. Line anchors have drifted (e.g. `episode_from_agent_cycle` cited `:519`, now `:523`).
- **Proposed status:** `authoritative` (as an evidence method) **but partially stale** — the open/closed ledger must be refreshed against `f317c4c`.

### 2. `docs/MEMORY_SYSTEM_AUDIT.md`
- **Purpose:** read-only audit of memory & durable-learning governance (MGA-01…09).
- **Relates to commit/date:** created `accd746` (2026-07-17); explicitly audited `main` @ `3f4f8fa5…`.
- **Problems / decisions:** MGA-01 (mismatch observational), MGA-02/03 (learning-gate), MGA-04 (suspicious), MGA-05 (temporal truth), MGA-06 (web_fetch), MGA-07/08/09 (`needs-e2e`), plus corrected fix proposals (Section D) and required tests (Section E).
- **Referenced by:** `CORE_AUDIT`, `MEMORY_FIX_PLAN`, `LIVE_PROBE_FINDINGS`. **References:** `OPERATIONAL_FAILURE_MODES.md`, `self-audit-lessons.md`, `MULTI_AGENT_COORDINATION_LAYER.md`, `ROADMAP.md`.
- **Unique info:** the zero-write proof discipline; the careful `confirmed-by-code` vs `needs-e2e` split; Section D refined temporal-truth model.
- **Overlap:** MGA-02/03/04/06 == CORE-01/02, CORE-08, CORE-09.
- **Contradiction with current code:** **YES (partial-stale).** MGA-02 (partly) and MGA-06 fixed in code; MGA-04 still open; MGA-05 still absent. Audited commit `3f4f8fa` is older than HEAD.
- **Proposed status:** `authoritative` (memory-governance reference) **but partially stale**.

### 3. `docs/MEMORY_FIX_PLAN.md`
- **Purpose:** code-anchored implementation plan to repair memory (Parts A–F), incl. self-knowledge registration (Part B).
- **Relates to commit/date:** created `0d8b598` (2026-07-18).
- **Problems / decisions:** A1…A8 fix table with ordering; Part B (register each fix as `SelfImprovementIssue` + `lesson` episode); Part D (central-vs-subagent memory topology); Part E sequencing (P0…P7).
- **Referenced by:** `CORE_AUDIT`. **References:** `MEMORY_SYSTEM_AUDIT.md`, `self-audit-lessons.md`, `SUBAGENT_LIFECYCLE.md`, `MULTI_AGENT_COORDINATION_LAYER.md`, `CENTRAL_AGENT_GOVERNANCE.md`, `future/CORPORATE_MODEL.md`.
- **Unique info:** the A-fix ordering rationale; Part B autonomous self-repair loop; Part D "centre owns truth; sub-agents read narrow slices and propose."
- **Overlap:** A1–A8 map 1:1 onto MGA-*/LPF-*.
- **Contradiction with current code:** **partially superseded.** A1 (web_fetch), A2 (episode outcome), A5 (experience filter) are done; A3 was *not* applied as written (code kept `1.0`, reframed); A4/A6/A7/A8 not done. `data/self_improvement_issues.jsonl` exists with 40 entries (Part B mechanism is populated).
- **Proposed status:** `active_working_document` (a plan, partly executed) — **proposal + in-progress**, not a defect. Needs a "done/not-done" pass.

### 4. `docs/LIVE_PROBE_FINDINGS.md`
- **Purpose:** observation log from running the live agent on plain-language questions (LPF-001…020).
- **Relates to commit/date:** created `e1d8081` (2026-07-18 04:38); last update `036c370` (marks LPF-001 FIXED).
- **Problems / decisions:** LPF-001 (host_tools, FIXED), LPF-002 (confidence metric, observational), LPF-003 (trivial over-processing), LPF-004 (self-diagnostic strategy), LPF-005/006, Batch-2 LPF-007…018, and the CONFIRMED&FIXED injection-scan item (#9).
- **Referenced by:** `CORE_AUDIT`. **References:** `MEMORY_SYSTEM_AUDIT.md`, `self-audit-lessons.md`.
- **Unique info:** the only doc grounded in *live run* observations; the "one primary root" (host_tools / mandatory-citation) narrative; several low-severity UX findings (greeting over-processing, re-ask Jaccard, sticky expert profile) not in the other audits.
- **Overlap:** LPF-008/010/011/012/013 == CORE-04/06/03/05/02.
- **Contradiction with current code:** **partial.** LPF-001 and #9 correctly self-marked FIXED; LPF-008/011/012 now fixed/reframed in code but still listed "confirmed by code" as if live.
- **Proposed status:** `authoritative` (live-probe log) **but partially stale** — Batch-2 statuses need refresh.

### 5. `docs/OPERATIONAL_FAILURE_MODES.md`
- **Purpose:** external failure-mode catalogue (OFM-001…020) as a verification checklist; explicitly *not* a repo defect report.
- **Relates to commit/date:** created `6dc253e` (2026-07-17).
- **Problems / decisions:** 20 failure classes with `external-only`/`confirmed-gap`/`open-risk` statuses; Section 11 has exactly one `confirmed-gap`: OFM-010 (retry backoff).
- **Referenced by:** `ROADMAP.md`, `MEMORY_SYSTEM_AUDIT.md`, `MULTI_AGENT_COORDINATION_LAYER.md`, `CORE_AUDIT`. **References:** `self-audit-lessons.md`.
- **Unique info:** the whole external-operator failure taxonomy (rate limits, false external success, draft≠sent, queue-without-consumer, stale-state force-push, proxy metrics); the "no path to delivery" / "stale world-view" meta-causes.
- **Overlap:** OFM-010 == CORE-07.
- **Contradiction with current code:** **YES (one item).** Its sole `confirmed-gap` OFM-010 is now **fixed** (`task_queue.py:294` backoff + test). The rest remain `external-only`/`open-risk` (unchanged, legitimately open as unverified).
- **Proposed status:** `authoritative` (failure-mode checklist) **but Section 11 stale** (OFM-010 closed).

### 6. `docs/ROADMAP.md`
- **Purpose:** intended order + current state of capabilities (Tracks A–G). Source-of-truth entry #3 per README.
- **Relates to commit/date:** created `13a4900` (2026-07-11); last touched `6dc253e` (2026-07-17).
- **Problems / decisions:** per-track IMPLEMENTED / PARTIAL / PLANNED status; "what is deliberately NOT here yet" (full multi-agent isolation, unattended self-mod, real Windows service, corporate model).
- **Referenced by:** `README.md`, `MEMORY_SYSTEM_AUDIT.md`. **References:** `daemon-progress.md`, `OPERATIONAL_FAILURE_MODES.md`, `CENTRAL_AGENT_GOVERNANCE.md`, `AGENT_ANATOMY.md`, `future/CORPORATE_MODEL.md`.
- **Unique info:** the track-by-track capability map; the honest PARTIAL labels on Tracks E/F.
- **Contradiction with current code:** **minor/stale.** Track G lists `operator_intent` NL-routing but does **not** mention the newer `core/intent_understanding` module (model-based intent translator, commits `9005f6b`/`564e0d8`, 2026-07-18). Track B's parenthetical "a single success is not treated as certainty" is the same wording `MEMORY_SYSTEM_AUDIT` MGA-03 flagged as incomplete.
- **Proposed status:** `authoritative` **but slightly stale** (missing the intent-understanding capability).

### 7. `docs/daemon-progress.md`
- **Purpose:** per-sub-item tracker for the incremental asyncio-daemon plan. Source-of-truth entry #4 per README.
- **Relates to commit/date:** created `db35726` (2026-07-10); last touched `6c4e92a` (2026-07-17).
- **Problems / decisions:** four-field status (implementation/main_pr/hotfix/acceptance) per sub-item 1.1…4.1; final table of items 1.1…7.4; every merged item is "acceptance pending."
- **Referenced by:** `README.md`, `ROADMAP.md`. **References:** none in `docs/`.
- **Unique info:** the only record of daemon PR history (#35–#89), the 1.3 release()-race hotfix rationale, item 2.4 "skipped (deferred)", and 4.2–7.4 "not started."
- **Contradiction with current code:** **not checked this stage** (would require verifying each PR merge state and that `app/daemon.py` etc. match). Item 4.1 shows `main_pr: open` — needs a check of whether it has since merged.
- **Proposed status:** `active_working_document` — **partially unknown_needs_verification** (per-PR state not re-verified).

### 8. `docs/MULTI_AGENT_COORDINATION_LAYER.md`
- **Purpose:** architectural **proposal** for a coordination layer (Agent Mail, Decision Log, Shared Backlog, Evidence links, Human Feedback) with trust classes and acceptance criteria.
- **Relates to commit/date:** created `87861e2` (2026-07-17).
- **Problems / decisions:** header explicitly "not yet implemented"; 15 acceptance criteria; trust-class ladder; phased rollout (Phase 0–5).
- **Referenced by:** `MEMORY_SYSTEM_AUDIT.md`, `MEMORY_FIX_PLAN.md` (Part D). **References:** `SUBAGENT_LIFECYCLE.md`, `OPERATIONAL_FAILURE_MODES.md`.
- **Unique info:** the coordination-ledger design and trust-class model; "shared context is not automatically shared truth"; Agent Mesh relationship (Section 16).
- **Contradiction with current code:** **none** — it is a self-declared proposal; nothing claims current implementation.
- **Proposed status:** `proposal` (not a defect).

### 9. `docs/SUBAGENT_LIFECYCLE.md`
- **Purpose:** normative spec for how the central agent proposes/bounds/trusts/evaluates/quarantines/retires sub-agents. Subordinate to `CENTRAL_AGENT_GOVERNANCE.md`.
- **Relates to commit/date:** created `34b4938` (2026-07-11); last touched `9d3a87d` (2026-07-12).
- **Problems / decisions:** current-vs-target split; §4 lifecycle table (target); §8 quarantine/retire (all PLANNED); honest "auto-pause/auto-retire do not exist."
- **Referenced by:** `AGENT_ANATOMY.md`, `MEMORY_FIX_PLAN.md`, `MULTI_AGENT_COORDINATION_LAYER.md`; wired into `core.planner._SUBAGENT_GOVERNANCE_DOC_PATHS`; guarded by `tests/test_doctrine_docs_exist.py`. **References:** `CENTRAL_AGENT_GOVERNANCE.md`.
- **Unique info:** the two-contract reality (`SubagentProposal` vs `team_plan.SubagentContract`); the "same event means opposite things by role" evaluation model; the document-lifecycle self-knowledge section.
- **Contradiction with current code:** **needs targeted verification** — it makes many precise "verified today" claims (e.g. `memory=None`, `VALID_STATUSES = active/paused/retired`, `_MIN_JUDGED_FOR_RECOMMENDATION=5`). These were self-consistent when written; not re-checked against `f317c4c` this stage.
- **Proposed status:** `authoritative` (normative spec) with a **`unknown_needs_verification`** flag on its "verified today" claims.

### 10. `docs/CENTRAL_AGENT_GOVERNANCE.md`
- **Purpose:** authoritative contract for the central agent's authority and limits (Policy Gate, governance modes, approval, budget, human-reserved rights).
- **Relates to commit/date:** created `13a4900` (2026-07-11).
- **Problems / decisions:** §10 honest PLANNED/NOT-DONE limits; §9 human-reserved rights.
- **Referenced by:** `ROADMAP.md`, `SUBAGENT_LIFECYCLE.md`, `MEMORY_FIX_PLAN.md`, `future/CORPORATE_MODEL.md`. **References:** `future/CORPORATE_MODEL.md`.
- **Unique info:** the consolidated authority contract (modes × operations × verdicts); the deep/Opus escalation rule.
- **Contradiction with current code:** **not fully checked** — makes many "IMPLEMENTED" claims tied to `core/policy`, `core/governance`, `core/approval*`. Plausible but not re-verified this stage.
- **Proposed status:** `authoritative` with `unknown_needs_verification` on the IMPLEMENTED claims.

### 11. `docs/AGENT_ANATOMY.md`
- **Purpose:** grouped module index of `core/` (134 modules, 12 groups); kept in sync by `scripts/agent_anatomy_check.py`.
- **Relates to commit/date:** created `0bf4c22` (2026-07-09); last touched `9005f6b` (2026-07-18) — updated to include `core/intent_understanding`.
- **Referenced by:** `ROADMAP.md`. **References:** `SUBAGENT_LIFECYCLE.md`.
- **Unique info:** the only logical grouping of the flat `core/` package; includes the newest module `intent_understanding`.
- **Contradiction with current code:** **low risk** — it has a drift-check script (`scripts/agent_anatomy_check.py`) and was updated in the latest feature commit. The "134 modules" count needs a quick re-count to confirm.
- **Proposed status:** `authoritative` (script-guarded index); `unknown_needs_verification` only on the exact module count.

### 12. `docs/COMMANDS_MAP.md`
- **Purpose:** authoritative map of the real REPL/CLI command surface + the NL intent-router parity table.
- **Relates to commit/date:** created `13a4900` (2026-07-11); last touched `b26a2f4` (2026-07-11).
- **Referenced by:** `ROADMAP.md` (Track G). **References:** `main.py`, `cli/commands_*.py`, `core/operator_intent*.py`.
- **Unique info:** the full `:command` table and the NL-routing safety-guarantees section.
- **Contradiction with current code:** **possible/stale** — built from `main.py` on 2026-07-11; `main.py` was last modified 2026-07-18 and the newer `core/intent_understanding` layer (a *different* router than `operator_intent`) is **not reflected** in the NL-routing section. Command list not re-diffed against current `main.py` this stage.
- **Proposed status:** `authoritative` **but needs re-diff** against current `main.py` (`unknown_needs_verification` on completeness).

### 13. `docs/future/CORPORATE_MODEL.md`
- **Purpose:** explicitly FUTURE/aspirational target ("Level 5" multi-agent org).
- **Relates to commit/date:** created `13a4900` (2026-07-11).
- **Referenced by:** `ROADMAP.md`, `CENTRAL_AGENT_GOVERNANCE.md`, `MEMORY_FIX_PLAN.md`, `SUBAGENT_LIFECYCLE.md`. **References:** `ROADMAP.md`, `CENTRAL_AGENT_GOVERNANCE.md`.
- **Unique info:** the target per-agent identity/memory/budget model and its "hard invariants any future version must preserve."
- **Contradiction with current code:** **none** — self-declared future; its "today" baseline matches ROADMAP Track F.
- **Proposed status:** `proposal` (future/aspirational).

### 14. `docs/self-audit-lessons.md`
- **Purpose:** durable record of a prior 13-issue multi-model re-audit (all fixed + regression-tested), plus recurring anti-patterns and the next-audit procedure.
- **Relates to commit/date:** created `c5553a4` (2026-07-17).
- **Referenced by:** `CORE_AUDIT`, `MEMORY_SYSTEM_AUDIT`, `MEMORY_FIX_PLAN`, `LIVE_PROBE_FINDINGS`, `OPERATIONAL_FAILURE_MODES`. **References:** none in `docs/`.
- **Unique info:** the 6 recurring anti-patterns; the 13-finding fix table with guard tests; the 4-step next-audit procedure. **This is a history-of-regressions document — must not be treated as deletable because its list is "completed."**
- **Contradiction with current code:** none claimed as open; it is a *completed* fix log. (Individual guard tests not re-run this stage.)
- **Proposed status:** `historical` (completed fix log) + `authoritative` for the anti-pattern/process guidance. **Do not retire.**

### 15. `README.md` (root)
- **Purpose:** entry point + source-of-truth hierarchy; owns no doctrine itself.
- **Relates to commit/date:** created `affb2b8` (2026-07-09); last touched `13a4900` (2026-07-11).
- **Unique info:** the 5-level source-of-truth order. **References:** `AGENT_DOCTRINE.md`, target-architecture, `ROADMAP.md`, `daemon-progress.md`.
- **Contradiction with current code:** none (navigation only).
- **Proposed status:** `authoritative` (navigation).

### 16. `AGENT_DOCTRINE.md` (root)
- **Purpose:** top of the source-of-truth hierarchy — behavioural doctrine (correctness-first, minimal-change, priority order).
- **Relates to commit/date:** created `9606f4f` (2026-07-09).
- **Contradiction with current code:** none (behavioural rules).
- **Proposed status:** `authoritative`.

### 17. `AGENTS.md` (root)
- **Purpose:** repository guidelines for agent behaviour (safety, dev workflow, test discipline).
- **Relates to commit/date:** created `81f63f9` (2026-07-15).
- **Unique info:** the git-safety rules (e.g. "Do not run `git log`/`git show`") and the change-discipline workflow. **Note:** these are constraints on the *in-repo agent*, and one (no `git log`) is in tension with this human-directed audit's use of git history for provenance — flagged, not acted on.
- **Contradiction with current code:** n/a (process doc).
- **Proposed status:** `authoritative` (process doc).

### 18. `архитектура автономного Агента.txt` (root, canonical architecture)
- **Purpose:** the "target architecture" — source-of-truth entry #2 per README ("a map of direction, not an implementation status").
- **Relates to commit/date:** last modified 2026-07-10 (not re-committed recently).
- **Contradiction with current code:** **not assessed** — only the first lines were read this stage (24 KB target-architecture narrative).
- **Proposed status:** `unknown_needs_verification` (canonical but not yet read in full this pass) — likely `authoritative`/`proposal` mix once read.

---

## Cross-reference map (inbound → who cites this doc)

- `self-audit-lessons.md` ← CORE_AUDIT, MEMORY_SYSTEM_AUDIT, MEMORY_FIX_PLAN, LIVE_PROBE_FINDINGS, OPERATIONAL_FAILURE_MODES (most-cited).
- `OPERATIONAL_FAILURE_MODES.md` ← ROADMAP, MEMORY_SYSTEM_AUDIT, MULTI_AGENT_COORDINATION_LAYER, CORE_AUDIT.
- `MEMORY_SYSTEM_AUDIT.md` ← CORE_AUDIT, MEMORY_FIX_PLAN, LIVE_PROBE_FINDINGS.
- `CENTRAL_AGENT_GOVERNANCE.md` ← ROADMAP, SUBAGENT_LIFECYCLE, MEMORY_FIX_PLAN, CORPORATE_MODEL.
- `SUBAGENT_LIFECYCLE.md` ← AGENT_ANATOMY, MEMORY_FIX_PLAN, MULTI_AGENT_COORDINATION_LAYER.
- `future/CORPORATE_MODEL.md` ← ROADMAP, CENTRAL_AGENT_GOVERNANCE, MEMORY_FIX_PLAN, SUBAGENT_LIFECYCLE.
- `ROADMAP.md`, `daemon-progress.md`, `AGENT_DOCTRINE.md` ← README.

## Overlap clusters (duplicate_candidate — NOT deletion candidates)

1. **Core-defect cluster:** `CORE_AUDIT_2026-07-18` ⊃ (re-verifies) `MEMORY_SYSTEM_AUDIT` (MGA-*) + `LIVE_PROBE_FINDINGS` (LPF-*) + `OPERATIONAL_FAILURE_MODES` (OFM-010). CORE_AUDIT is a **consolidation**; the three sources each retain unique method/context (static audit / live probe / external taxonomy). Consolidate the *status ledger*, keep all four.
2. **Memory-plan cluster:** `MEMORY_FIX_PLAN` (A1–A8) restates `MEMORY_SYSTEM_AUDIT` §D/E as an executable plan. Complementary, not redundant.
3. **Subagent/governance cluster:** `SUBAGENT_LIFECYCLE` + `CENTRAL_AGENT_GOVERNANCE` + `future/CORPORATE_MODEL` form a present→target ladder. No true duplication.

---

## Contradictions with current code (summary, Stage-1)

1. **CORE_AUDIT §7 "None is yet fixed in code" is now false** — CORE-01/04/05/07/09/10 fixed with tests (see headline table).
2. **OPERATIONAL_FAILURE_MODES §11 sole `confirmed-gap` OFM-010 is now fixed** (`task_queue.py:294` backoff + `test_task_queue_retry_backoff.py`).
3. **MEMORY_SYSTEM_AUDIT MGA-06 fixed** (`web_fetch:web`), MGA-02 partly fixed.
4. **MEMORY_FIX_PLAN A3 not applied as written** — code kept `_compute_quality_score → 1.0` on empty chain and **reframed** it as intended-neutral, contradicting the plan's "floor 0.3."
5. **ROADMAP + COMMANDS_MAP omit `core/intent_understanding`** — a new NL-intent layer (commits `9005f6b`/`564e0d8`) landed 2026-07-18 but is absent from the roadmap and the NL-routing table.
6. **Line anchors have drifted** across the memory audits (`smart_memory.py`, `loop.py`) — several cited `file:line` are off by a few lines at `f317c4c`.

## Documents requiring additional verification (Stage-2 candidates)

- `daemon-progress.md` — re-verify each PR merge state; is item **4.1** still `open`? are 4.2–7.4 still untouched?
- `SUBAGENT_LIFECYCLE.md` — re-check its "verified today" claims (`memory=None`, `VALID_STATUSES`, `_MIN_JUDGED_FOR_RECOMMENDATION=5`) against `core/subagent_*`.
- `CENTRAL_AGENT_GOVERNANCE.md` — confirm each "IMPLEMENTED" against `core/policy`, `core/governance`, `core/approval*`.
- `COMMANDS_MAP.md` — re-diff the `:command` table + NL-routing table against current `main.py` / `core/operator_intent*` / `core/intent_understanding`.
- `AGENT_ANATOMY.md` — re-count modules (claims 134) and confirm `intent_understanding` grouping via `scripts/agent_anatomy_check.py`.
- `CORE_AUDIT` **CORE-02** — confirm whether the `answer_quality_score < 0.5` gate at `smart_memory.py:409` closes the "procedure active from one low-quality success" scenario.
- `архитектура автономного Агента.txt` — full read pending; classify authoritative vs proposal sections.

---

*Provenance: verified against code on `main` @ `f317c4c`. Verification was code-reading
+ test-file existence, NOT test execution or live runs (see limitations). Code > this
document; where they disagree, code wins and this inventory must be corrected.*
