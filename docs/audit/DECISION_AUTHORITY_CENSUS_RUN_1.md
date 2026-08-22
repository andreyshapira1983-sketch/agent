# Decision-authority census, run 1 — family A — 2026-08-22

The instrument in `DECISION_AUTHORITY_CENSUS_METHOD.md`, finally run on the
operator's word. **Family A only** (literals participating in a selection).
Family B (provenance and repertoire closure) was NOT run — it needs a per-site
graph walk and is a separate exercise.

Read the numbers by level. Nothing here extrapolates between them.

## The rule was falsified before it was trusted

    POSITIVE control 1  _build_queue                    FOUND   (tasks = <2 items>, line 1402)
    POSITIVE control 2  best_next_action ranking table  FOUND   (_P_DAEMON_DOWN = 100, ...)
    NEGATIVE control 1  policy / thresholds             FIRED   (as designed: high recall)
    NEGATIVE control 2  model_router                    FIRED   (as designed)
    KNOWN-MISS control  the closed candidate repertoire MISSED  (0 hits — correct)

The known-miss control is the important one: family A found **zero** hits on the
mere existence of the `_candidate_*` producers, exactly as predicted. A decision
encoded as the *absence* of an alternative carries no literal, so this census is
**provably incomplete for that whole class** — not "incomplete pending proof".

## The four levels, with their four different numbers

| Level | Count | How obtained |
|---|---|---|
| files scanned | 276 | mechanical |
| raw detector hits | 1168 | mechanical |
| **sites** (file::function) | **412** | mechanical |
| candidate boundaries, high-precision | **~19 distinct** | hand-judged after sampling |
| **measured** boundaries | **2** | differential experiments, recorded earlier |
| classified boundaries | 19 provisional | hand, awaiting causal verification |

## The hand-sampling that had to come before the number

Per shape, 8 sampled at random before any figure was published:

- **`c_compare` — 901 hits, and overwhelmingly FALSE POSITIVES.** The sample was
  `tool_name == 'run_tests'`, `p.suffix == '.jsonl'`, `head == ':ingest-source'`,
  `token == '--max-cost-units'`. That is dispatch and argument parsing — an
  identity test, not a choice among admissible alternatives. **The entire shape
  is excluded from the candidate set**, with the reason recorded rather than the
  hits quietly dropped. Had this been published as "901 decision sites", it would
  have been a beautiful lie of exactly the kind the method exists to prevent.
- **`b_const` — 188 hits, ~33 judgement-named, most of those initialisers.**
  `score = 0.0`, `best_score = 0`, `trust_score = 0.0` are zero-initialisation,
  not judgement. The genuine ones are few and named below.
- **`b_table` (12) and `d_worklist` (9) — high precision.** Every one was read,
  not sampled.
- **`a_kwarg` — 58, a distinct and interesting class**: literal `confidence=`
  values authored into the code (`confidence=0.85`, `0.68`, `0.45`, `0.4`).
  How sure the system claims to be is, at these sites, a developer's number.
  Not classified here; recorded as a class worth its own pass.

## The high-precision candidate set, classified by hand

| Site | What the literals decide | Provisional class |
|---|---|---|
| `core/instruction_conflict_gate.py:56` `AUTHORITY_RANK` | whose instruction outranks whose — **`operator: 1` at the top**, advisor last | **Constitution** — and correctly so: this is the §9 sovereignty machinery in a table. Found by the detector exactly as a high-recall detector should, and refused by the classifier. |
| `core/instruction_conflict_gate.py:201` `RESOLUTION_STEPS` | the conflict-resolution procedure | Constitution |
| `core/budget_kill_switch.py:38` `CONSERVATIVE_DAY_LIMITS` | daily spend ceilings | Constitution (sovereignty: money) |
| `core/source_ranker.py:53` `_TIER_SCORES` | epistemic worth per source kind — authoritative 1.00, reputable 0.82, blog 0.50, **llm 0.20**, unknown 0.10 | **VACANCY CANDIDATE under ratified C0** — see below |
| `core/source_ranker.py:65` `_FRESHNESS_SCORES` | worth by age — fresh 1.00, stale 0.45, undated 0.35 | same |
| `core/backlog_selector.py:43` `_SOURCE_BASE_SCORE` | which backlog source ranks higher | vacancy candidate, same family |
| `core/best_next_action.py:36-53` `_P_*` (11 constants) | which of two admissible actions wins | **Decision — MEASURED** (perturbation changes the winner; recorded earlier) |
| `core/autonomous_runtime.py:1402` `tasks` | which work opens every run | **Decision — MEASURED** (zero references to `self`) |
| `core/model_router.py:113/122/130` `_QUALITY_SCORE`, `_COST_SCORE`, `_COST_RANK` | quality-versus-cost trade-off per model | **Mixed** — part capability (what exists), part a value judgement about what matters now; needs a split hypothesis |
| `core/model_router.py:758` `_PROVIDER_FALLBACK_ORDER` | fallback order between providers | Capability (candidate) |
| `core/confidence_vector.py:46` `_SEVERITY_WEIGHT` | how severity weighs | Mixed |
| `core/clarification_gate.py:142` `_QUESTION_ORDER` | in what order the human is asked | Mixed — interface procedure with a judgement inside |
| `core/causal_lesson.py:61` `_ORDER` | ordering of lesson kinds | Observation/presentation |
| `core/source_connectors.py:361` `status_weight` | source status weighting | vacancy candidate, same family |
| `core/subagent_registry.py:105` `_TECHNICAL_SUCCESS_WEIGHT` | how a helper's success is scored | Mixed |
| `core/role_router.py:143` `_SCORE_MIN` | routing threshold | Mixed |
| `core/operator_intent_patterns.py:461`, `core/plan_parsing.py:269`, `core/smart_memory.py:1509` | parsing vocabularies and fixed step lists | Observation / procedure |

## The census's own finding

**A second concrete vacancy candidate under a ratified criterion.** The quoted,
operator-ratified half of C0 says hardcoded code must not prescribe *utility
ranking*. `core/source_ranker.py` is a utility ranking in the plainest possible
form: a literal table assigning epistemic worth — an authoritative source is
worth 1.00, an LLM's own claim 0.20, a blog 0.50 — and freshness likewise. Under
ratified **C0.P** ("the agent decides what to attend to and what to study"), the
worth of a source *for the agent's own learning* sits in its interior territory,
while these numbers are a developer's. MIR-119 found the missing post-change
verdict; this is its upstream twin — the missing *pre*-study valuation.

**And a counter-finding that matters just as much.** `AUTHORITY_RANK` puts
`operator: 1` above everything else. The detector fired on it — correctly, being
high-recall — and the classifier refused it: this is Constitution, the machinery
that keeps the human's word above the agent's. If this census were run by
someone hunting for things to remove, that table is exactly what a careless pass
would strip. It stays, and the reason it stays is written down.

## What this run does NOT establish

- **Completeness: UNPROVEN**, and provably incomplete for decisions-by-absence
  (the known-miss control fired correctly at zero).
- **Family B was not run.** Repertoire and provenance closure remain unmeasured.
- **Only 2 of the 19 candidates are causally measured.** The rest are hand
  classifications from reading, and a classification is a hypothesis until a
  perturbation confirms it.
- **`c_compare` was excluded as a class after sampling, not after exhaustive
  review.** A genuine decision boundary expressed as a literal comparison would
  have been dropped with the noise. The exclusion is a stated trade, not a proof
  of absence.
- **The `confidence=` class (58 sites) is untouched** — authored certainty is a
  distinct question and deserves its own pass.

Nothing here is repaired, and no boundary is removed. Under the standing rule a
vacancy is recorded as a vacancy; what to do with `source_ranker` — and whether
the `Mixed` rows deserve split hypotheses — belongs to the operator.
