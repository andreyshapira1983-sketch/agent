# Live-Probe Findings — plain-language questions to the running agent

Findings observed by running the **live agent** with ordinary human questions
(no `:commands`), in Russian and English, to see how it actually behaves. Each
finding records the **root location in code**, not just the surface symptom, and
whether that root spans multiple files.

This is an *observation log from live runs*, distinct from
`docs/MEMORY_SYSTEM_AUDIT.md` (static code audit) and
`docs/self-audit-lessons.md` (already-fixed defects).

---

## Provenance

- **How:** `python main.py --ask "<question>" --workspace <temp> --auto-approve deny`,
  each in an **isolated temp workspace** (live memory untouched), real LLM
  (`openai / gpt-5.6-terra` per the router), on `main` code.
- **Questions asked:** "What can you do?", "Кто ты?", "How much is 17 times 23?",
  "Какие у тебя баги?", "What is the capital of France?", "привет".
- **Status legend:** `confirmed-defect` (real, reproduces on any workspace) ·
  `observational` (misleading signal, does not gate output) ·
  `test-artifact` (partly caused by the empty temp workspace) ·
  `to-verify` (needs a focused check before it is called a defect).

---

## LPF-001 — `host_tools` injected as fake `<evidence>` defeats the general-knowledge path *(confirmed-defect)*

**Symptom.** "What can you do?" → the agent answers with **Blender / OpenSCAD /
ADB / Python** (paths from `.env`), not its real tools
(`file_read/list_dir/web_search/shell_exec/…`), citing a non-existent
`[tool:host_tools]`. "17 × 23" → the agent **refuses to compute**, saying "the
provided data only lists installed tools `[tool:host_tools]`". Verifier:
`chain_was_empty=True`, all such chunks `cited_but_unmatched`, `fully_unverified=True`
— yet the answer is delivered.

**Root (spans 3 files, interacting):**
- `core/planner.py:317` `_build_host_tools_block()` — reads `BLENDER_PATH`,
  `OPENSCAD_PATH`, `ADB_PATH`, `PYTHON_PATH`, … from `.env` into a text block.
- `core/loop.py:3674` — injects that block into the synthesizer prompt as
  `<evidence source="host_tools">` for every non-local-critique turn (comment:
  "so the model treats the paths as verified context").
- `core/loop_helpers.py:29` (synthesizer system prompt) — *"If the user message
  contains `<evidence>` blocks, answer STRICTLY from them"*; the general-knowledge
  branch only fires when there are **no** evidence blocks.

**Why it breaks.** The prompt has a correct carve-out ("no evidence → answer from
general knowledge, cite `[general-knowledge]`"). But the unconditional
`host_tools` injection means **every** turn contains an `<evidence>` block, so the
synthesizer switches to strict-evidence mode even for self-contained questions
(arithmetic, capabilities), anchors to the irrelevant host-tool paths, and refuses
general knowledge. Because `host_tools` is never added to the real
`ProvenanceChain`, the verifier can never match it → guaranteed `cited_but_unmatched`.
This is a design contradiction: "synthetic verified evidence" that the verifier
structurally cannot verify.

**Fix direction.** Do not present `host_tools` as an `<evidence>` block. Either
put it in a separate, explicitly non-citable context section, or only inject it
when a host-tool task is actually planned — so the general-knowledge path is not
silently disabled.

**Required test.** "17 × 23" with no tools planned returns the computed answer via
`[general-knowledge]`, and a capability question lists the agent's registered
tools, not `.env` host paths. (Negative control: a real host-tool task still gets
the run command.)

---

## LPF-002 — Confidence metric punishes universally-true general knowledge *(observational)*

**Symptom.** "What is the capital of France?" → correct answer "Paris", but
`overall_confidence = 0.032`. "привет" → `overall_confidence = 0.032`. A perfectly
coherent, on-topic, true answer scores near-zero confidence purely because there
is no external source.

**Root.** `core/confidence_vector.py` — `overall_confidence` is a weighted
geometric mean of `evidence_score`, `coherence_score`, `relevance_score` with
weights `0.5 / 0.3 / 0.2`. For general knowledge `evidence_score = 0.0`, and a
zero factor collapses the geometric mean to ≈0.03 regardless of perfect coherence
(1.0) and relevance (1.0).

**Scope note.** This module is **observational only** (its own header says so); it
logs alongside the scalar `compute_confidence` gate and does not directly gate the
user-facing answer. So this is a misleading *signal*, not (yet) a wrong *gate* —
but it makes any downstream consumer that reads `overall_confidence` distrust true
facts.

**Fix direction.** Give a self-evident / general-knowledge answer a non-zero
evidence floor, or exclude the evidence axis when the turn is legitimately
evidence-free, so "Paris is the capital of France" does not read as 0.03.

---

## LPF-003 — Trivial input is over-processed *(confirmed-defect, low severity)*

**Symptom.** "привет" (a greeting) runs the full Observe→Plan→Synthesize→Verify
pipeline and appends an `Unverified:` disclaimer ("user intent beyond the greeting
is not stated"). A greeting does not need planning, verification, or a disclaimer.

**Root.** The strategy classifier routes "привет" to `strategy=general_question`
(full pipeline) with no LIGHT / greeting short-circuit
(`core/strategy_router` / adaptive routing). The Output Contract + verifier then
attach a disclaimer to a greeting.

**Fix direction.** A LIGHT fast-path for greetings / trivial social turns that
skips the verifier and the disclaimer.

---

## LPF-004 — Self-diagnostic is not recognised as a distinct strategy *(confirmed-defect — also in the static audit)*

**Symptom.** "Какие у тебя баги?" → `strategy=general_question`; the planner then
reaches for `list_dir` / `file_read` on `core/*.py` instead of a purpose-built
local self-check.

**Root.** `core/operator_intent.py` — `OperatorIntentKind` has no self-diagnostic
kind; `route_operator_intent("check yourself and find problems")` returns `None`
(confirmed in `docs/MEMORY_SYSTEM_AUDIT.md`, MGA-08).

**Fix direction.** A self-diagnostic intent + a locally-scoped strategy (read own
code/logs/tests, no web), not the generic question path.

---

## LPF-005 — Planner reaches for file tools on no-file questions *(confirmed behaviour; the crash is a test-artifact)*

**Symptom.** "Кто ты?" and "Какие у тебя баги?" made the planner call
`file_read` / `list_dir` (e.g. `README`, `core/loop.py`, `core/smart_memory.py`).
In my empty temp workspace those files do not exist → `FileNotFoundError` ×N →
`replan_exhausted` after 1 attempt.

**Honesty about the crash.** The `FileNotFoundError` itself is an **artifact of the
isolated empty workspace**. On the real workspace those files exist and the reads
would succeed, giving a partially-grounded answer. So the *error* is not a real
defect; the real observation is that the planner defaults to reading project files
for identity/meta questions.

**Note.** Error handling here worked correctly: the loop aborted cleanly
(`replan_exhausted` → episode would be `outcome=failed`) and returned an honest
"could not confirm" answer rather than looping or fabricating.

**Root.** Planner LLM behaviour guided by the `core/planner.py` prompt (not a
single code line). Overlaps with LPF-004 (a proper self-diagnostic strategy would
route these correctly).

---

## LPF-006 — Verifier reported `verified_chunks > 0` with an empty provenance chain *(to-verify)*

**Symptom.** On "Какие у тебя баги?" the verifier logged
`verified_chunks=4` while `chain_was_empty=True`. How can chunks be "verified"
against an empty evidence chain? Possibly the file-not-found tool errors were
counted as evidence, or the counter is computed before the chain check.

**Status.** `to-verify` — flagged, not yet called a defect. Needs a focused read of
`core/verifier*` to see how `verified_chunks` relates to `chain_was_empty`.

---

## Cross-cutting note

The dominant root of the campaign is **LPF-001** (`host_tools`). It alone explains
the refusal to do arithmetic, the misrepresented capabilities, and the guaranteed
`fully_unverified`. It is genuinely **cross-file** (planner.py + loop.py +
loop_helpers.py), which answers the operator's question directly: the root of a
given error is not "one place for everything" — it must be traced per error, and
some roots span several files while others (LPF-002) live in one.
