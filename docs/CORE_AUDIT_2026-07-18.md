# Core Hardcore Audit — 2026-07-18

A deep, **execution-verified** defect register for the agent's cognitive /
memory / safety core. Every item below is proven against code — by running the
function or by citing the exact line — and each carries a concrete failure
scenario. **Nothing here is invented or assumed.** Where I checked something and
found it *clean*, that is recorded too (Section 4) — the negatives are part of
the honesty of this audit.

> **What "100%" means here.** 100% confidence that *each listed defect is real*
> (verified, reproducible), **not** a claim of 100% coverage of all 134 modules.
> Scope audited deeply: `smart_memory`, `confidence_gate`, `confidence_vector`,
> `task_queue`, `loop` (safety pipeline + verify path), `memory_policy`,
> `injection_guard`, `lang_match`, `step_repetition`, `state_integrity`,
> `shell_exec`, `planner` (doctrine wiring). Modules outside this set were not
> exhaustively verified and are out of scope for this pass — stated plainly so
> the coverage is not overclaimed.

## Evidence classes

- **execution-verified** — I ran the function and observed the wrong output
  (reproduction printed in this session).
- **code-verified** — proven by reading the exact line(s); behaviour is
  unambiguous from the code.
- **consistency-concern** — a real inconsistency, but I did not prove a concrete
  wrong output; labelled low-confidence on purpose.

---

## 1. The systemic defect: a self-reinforcing bad-learning loop (CRITICAL)

CORE-01…05 are not five independent bugs — they chain into one loop where a
weakly-supported answer is banked as a success, promoted to a reusable skill,
and fed back as "experience". This is the highest-severity finding.

### CORE-01 — episode `outcome=success` decided from raw chunk counts *(MGA-02)*
- **Where:** `core/smart_memory.py:519` `episode_from_agent_cycle` (the
  `elif unverified > verified and verified == 0` clause).
- **execution-verified:** `verified=1, unverified=10, weak=0` →
  `outcome='success'`, `answer_quality_score=0.091`. The `verified == 0` guard
  means one lucky verified chunk lets a 9%-quality answer bank as success.
  `relevance_score` and the confidence-gate verdict are never inputs.

### CORE-02 — one dubious success mints an `active` procedure, no quality gate *(MGA-03)*
- **Where:** `core/smart_memory.py:567` `procedure_from_episode` (gate is only
  `outcome=="success" and tools_used`).
- **execution-verified:** feeding the CORE-01 episode →
  `procedure.status='active'`. No `answer_quality_score` check; Beta(1,1)
  smoothing puts one success at 0.667 ≥ 0.6 = `active` immediately.

### CORE-03 — `_compute_quality_score` returns 1.0 on an empty chain *(LPF-011)*
- **Where:** `core/smart_memory.py:41` (`if total == 0: return 1.0`).
- **execution-verified:** `_compute_quality_score(0,0,0) = 1.0`. A purely
  self-declared / general-knowledge answer with zero evidence banks **max**
  quality — directly contradicting a `fully_unverified` verifier report.

### CORE-04 — fabricated citations receive positive evidence credit *(LPF-008)*
- **Where:** `core/confidence_gate.py:52`
  `raw = verified*1.0 + cited*0.5 + unverified*-0.25`.
- **execution-verified:** a report with `cited_but_unmatched=2, total=2,
  verified=0` → `compute_confidence = 0.5`. Citations that matched **nothing**
  in the provenance chain (i.e. fabricated `[tool:…]` markers) each earn +0.5.
  An unmatched citation should be a red flag, not half credit.

### CORE-05 — experience retrieval has no outcome/quality filter *(LPF-012)*
- **Where:** `core/loop_methods2.py:159` `_retrieve_experience_memory` →
  `episodic_store.search(question, limit=3)`.
- **code-verified:** the search is unfiltered — a `partial` / `quality≈0`
  episode (produced by CORE-01/03) can be retrieved and injected as prior
  experience, closing the loop.

**Chain:** CORE-03 → CORE-01 (bad answer banks as success) → CORE-02 (becomes an
active procedure) → CORE-05 (fed back as experience). CORE-04 inflates the
confidence signal along the way. Fix these together, quality-gated, or the loop
persists.

---

## 2. Safety / execution-correctness defects (HIGH–MEDIUM)

### CORE-06 — command failure recorded as step success *(LPF-010)*
- **Where:** `core/loop.py:2985` gates on `result.status != "success"`;
  `tools/shell_exec.py` sets `status="success"` whenever the subprocess **ran**,
  putting the command's `exit_code` inside `output` where no post-condition
  reads it. `validate_output` (shell_exec.py:520+) only warns on
  timed_out/exit_code *consistency*, never maps `exit_code!=0` to failure.
- **code + live-verified:** observed live this session — `where ffmpeg` /
  `where python` returned `exit_code=1` with `tool_result.status="success"` and
  `[VER] ok=True` (isolated-workspace traces). Transport execution ≠ command
  success.
- **Nuance (do not "fix" naively):** for `where`/`grep`/`test`, `exit_code=1` is
  a legitimate "not found", so a blanket `exit!=0 → failure` would break valid
  cases. The correct fix is per-command exit-code semantics, not a global rule.

### CORE-07 — retry with no backoff *(OFM-010)*
- **Where:** `core/task_queue.py:289` `mark_failed` sets a non-exhausted task
  back to `pending` **without changing `run_after`**; `pending()`
  (task_queue.py:237) serves any task whose `run_after <= now`, so it is
  immediately eligible on the next tick.
- **code-verified:** the retry **cap** works (`attempts` bumped in
  `mark_running:271`, terminal at `attempts >= max_attempts`), but there is **no
  backoff**. Impact is bounded to tasks created with `max_attempts > 1`
  (default is 1); those hot-retry every tick.

### CORE-08 — `suspicious` injection content still enters the evidence chain *(MGA-04)*
- **Where:** `core/loop.py:3110–3139`. A `blocked` verdict drops the output and
  replans; a `suspicious` verdict is only **annotated** (`annotate_suspicious`)
  and passed through, so the successful step becomes `Evidence` in the
  provenance chain and can later reach semantic memory.
- **code-verified.** (Related: the injection-scan **scope** bug #9, fixed this
  week on `fix/injection-scan-scope`, is a different layer — what gets scanned;
  CORE-08 is what happens to a suspicious verdict once raised.)

### CORE-09 — `web_fetch` output classified `private` by default *(MGA-06)*
- **Where:** `core/loop.py:165` `_TOOL_SOURCE_HINTS` omits `web_fetch` → falls
  back to `tool_output` → `core/data_classifier.py` maps `tool_output → PRIVATE`
  (while `web → PUBLIC`). A public page fetched via `web_fetch` loses `public`
  classification.
- **code-verified.** Cleanest fix in the set: add `"web_fetch":"web"`.

---

## 3. Newly found this session

### CORE-10 — episodic tokenizer silently drops short / numeric / acronym tokens *(NEW)*
- **Where:** `core/smart_memory.py:91` `_tokens` filters `len(token.strip()) > 2`.
- **execution-verified:**
  - `_tokens('AI ML 3D PR os')` → **`[]`** (every token dropped).
  - `_tokens('How much is 17 times 23')` → `['how','much','times']` — the
    numbers `17`/`23` vanish.
- **Failure scenario:** episodic memory keys/matches on these tokens. Two
  arithmetic questions ("17 times 23" vs "500 times 900") reduce to the same
  token set `{times}` + stopwords, and a technical query like "AI ML 3D"
  produces an **empty** token set → zero episodic-match signal. Short but
  meaningful terms (acronyms `AI/ML/os/io/v2`, 2-digit numbers) are invisible to
  episodic retrieval. This is a real retrieval-quality defect, distinct from the
  known memory findings.

---

## 4. Checked and found CLEAN (honest negatives — not defects)

Recorded so this audit cannot be accused of only listing suspicions.

- **`overall_confidence` is NOT consumed by any gate.** Its two non-definition
  occurrences (`core/planner.py:294`, `core/learning_planner.py:84`) are
  **keyword-list strings**, not decision inputs. So the geometric-mean collapse
  (LPF-002: "Paris" → 0.032, floor `eps=1e-3` at `confidence_vector.py:224`) is a
  misleading *signal*, **not** a wrong *gate*. I did not escalate it.
- **`task_queue.recover_stuck` date parse is equivalent to `_parse_iso`.** A
  differential test over 7 timestamp formats showed identical accept/reject —
  no "fresh task treated as stale" bug. Not a defect.
- **`core/lang_match.py` is correct.** The documented Russian bug (`"сравни с"`
  matching `"сравни своё"`) is genuinely fixed by the `STEM_MIN=4` whole-token /
  stem-prefix rule. Verified by reading.
- **`core/step_repetition.py`, `core/state_integrity.py`** — reviewed, no defect
  found (integrity envelope hashes payload canonically and verifies on decode;
  repetition counter is correct).
- **No classic Python footguns in `core/`:** no mutable default arguments, no
  `== None`, no bare `except:` swallowing the primary operation (the many
  `except Exception:` blocks are documented best-effort boundaries).

---

## 5. Low-confidence observation (consistency-concern, not a proven defect)

### CORE-11 — two different tokenizers across the memory subsystems
- `core/smart_memory.py:79` `_tokens` uses `len > 2`, **no** stopwords;
  `core/memory_policy.py:360` uses `_TOKEN_RE` with `len > 1` **and** stopwords.
  Episodic and persistent stores therefore tokenize the same text differently
  (e.g. `"17"` is dropped by the former, kept by the latter).
- **Status:** consistency-concern. They serve *different* stores, so this is not
  proven to cause a concrete wrong retrieval — flagged for review, **not** claimed
  as a defect. (Ties to CORE-10.)

### CORE-12 — no semantic temporal-truth model *(MGA-05, architectural)*
- `core/models.py:150` `MemoryRecord` has only `ttl_seconds / importance /
  archived`; ranking is token-overlap + recency (`core/memory_policy.py`). No
  `lifecycle_status`, `valid_from/until`, `superseded_by`, `confirmation_refs`.
  Current truth, an exception, and a legacy note can rank as equal facts.
- **code-verified**, but it is a *missing capability*, not a wrong computation —
  classified architectural, addressed by `docs/MEMORY_FIX_PLAN.md` A6.

---

## 6. Severity summary

| ID | Defect | Where | Class | Severity |
|---|---|---|---|---|
| CORE-01 | episode success from chunk counts | `smart_memory.py:519` | execution-verified | **High** |
| CORE-02 | procedure active on one success | `smart_memory.py:567` | execution-verified | **High** |
| CORE-03 | quality 1.0 on empty chain | `smart_memory.py:41` | execution-verified | **High** |
| CORE-04 | fabricated citations +0.5 credit | `confidence_gate.py:52` | execution-verified | **High** |
| CORE-05 | experience retrieval unfiltered | `loop_methods2.py:159` | code-verified | **High** |
| CORE-06 | command failure = step success | `loop.py:2985` + `shell_exec` | code+live | Medium-High |
| CORE-07 | retry with no backoff | `task_queue.py:289` | code-verified | Medium |
| CORE-08 | suspicious injection enters chain | `loop.py:3110` | code-verified | Medium |
| CORE-09 | web_fetch classified private | `loop.py:165` | code-verified | Medium |
| CORE-10 | tokenizer drops short/numeric tokens | `smart_memory.py:91` | execution-verified (NEW) | Medium |
| CORE-11 | dual tokenizer across memory subsystems | `smart_memory.py:79` / `memory_policy.py:360` | consistency-concern | Low |
| CORE-12 | no temporal-truth model | `models.py:150` | code-verified (architectural) | Low-arch |

---

## 7. Relationship to existing audits & next step

Most CORE-01…09 correspond to findings the repo's own audits already recorded
(`MEMORY_SYSTEM_AUDIT.md` MGA-*, `LIVE_PROBE_FINDINGS.md` LPF-*,
`OPERATIONAL_FAILURE_MODES.md` OFM-010). This pass **independently re-verified
them by execution/reading** (not by citation), added the new CORE-10, and
recorded the honest negatives (Section 4). None is yet fixed in code.

Per repo discipline (`self-audit-lessons.md`): each fix ships with a regression
test that fails on the old code, and — per `docs/MEMORY_FIX_PLAN.md` Part B —
each is registered as a `SelfImprovementIssue` so the agent knows the problem
existed and can re-verify it with `:self-issue-verify`.

*Provenance: verified against code on `main`. Reproductions were run this session
in an isolated process; durable stores were not mutated. Code > this document.*
