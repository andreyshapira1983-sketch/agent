# Sensor signal measurement — accuracy, uniqueness, effect

> **What this file is:** the measurement the operator asked for before any of the
> five observational sensors is connected to policy, left as an observer, or
> removed. It reports **numbers only**. It deliberately makes **no
> recommendation** and classifies nothing — that decision is the operator's, and
> `docs/COGNITIVE_CORE.md` §12 already records it as one of his.
>
> **Standing instruction this measurement serves (2026-07-27):** do not delete
> sensors; do not grant them autonomous power; keep observation; measure
> accuracy, signal uniqueness and possible effect; only then classify —
> useful+accurate → connect to policy, useful but risky → keep observing,
> noisy+duplicating → remove.

## The five — *as they were at the measured commit*

This table is part of the dated record: it names the modules and events that
existed at `main` @ `388fac1`, which is what was measured. **Two of the five have
since been rebuilt** — see the addendum for the current module and event names,
and `docs/COGNITIVE_CORE.md` §10 for their current wiring. Do not read this
table as current architecture.

| | Sensor | Module *(at `388fac1`)* | Event *(at `388fac1`)* |
|---|---|---|---|
| S1 | Confidence gate | `core/confidence_gate.py` <!-- historical-ref: measured before the rename --> | `low_confidence_gate` |
| S2 | Stagnation | `core/termination_guard.py` | `stagnation_detected` |
| S3 | Premature completion | `core/termination_guard.py` | `premature_completion_risk` |
| S4 | Reasoning ↔ action mismatch | `core/reasoning_action_check.py` | `reasoning_action_mismatch` |
| S5 | Subsystem disagreement | `core/subsystem_disagreement.py` | `subsystem_disagreement` |

## Method and its limits — read before the numbers

Three independent measurements, because no single one is sufficient:

1. **Real traffic** — 35 trace files in `logs/`, of which **14 complete cycles**.
   Real, and small. The sample is also skewed: **11 of 14 turns are tool-free**
   operator chat and **0 turns replanned**.
2. **Contract accuracy** — constructed positive and negative cases per sensor.
   Ground truth is each sensor's **own documented contract**, so this measures
   *"does it detect what it says it detects"*, **not** *"is that the right thing
   to detect"*. The second question is a judgement, not a measurement.
3. **Effect** — for each sensor, the most plausible connection named in its own
   docstring, applied to the 14 real cycles, counted in turns changed and model
   calls added.

**A zero firing rate in §1 is not a verdict.** Where the sample contains no
instance of what a sensor detects, that is stated as *no opportunity* rather
than reported as silence.

Reproduce: `scratchpad/probe/sensors_traces.py`, `sensors_accuracy.py`,
`sensors_effect.py` (read-only, temp dirs only).

---

## 1. Firing rate on real traffic (14 cycles)

| Sensor | Fired | Rate | Note |
|---|---|---|---|
| S1 confidence gate | 12/14 | **85.7 %** | |
| S2 stagnation | 0/14 | 0 % | **no opportunity** — 0 cycles replanned |
| S3 premature completion | 1/14 | 7.1 % | |
| S4 reasoning ↔ action | 3/14 | 21.4 % | |
| S5 subsystem disagreement | 0/14 | 0 % | **no opportunity** — its cases need a plan with steps; 11/14 turns were tool-free |

S2 and S5 were never in a position to fire. Their zeros carry no information
about their quality.

## 2. Contract accuracy

| Sensor | Positives caught | Negatives quiet |
|---|---|---|
| S2 stagnation | 1/1 | 4/4 — including *3rd identical attempt does not re-fire* |
| S3 premature completion | 4/4 | **3/4** |
| S4 reasoning ↔ action | 2/2 | 3/3 |
| S5 subsystem disagreement | 3/3 | 3/3 (incl. the by-design suppression of pure general knowledge) |

S1 is definitional — it fires iff `confidence < 0.45`, so "accuracy" for it means
*what does the scalar actually measure*. Mapped over the verdict space
(total = 8 chunks):

| verdict mix | confidence | fires |
|---|---|---|
| 8 verified | 1.000 | — |
| 6 verified / 2 unverified | 0.688 | — |
| 4 verified / 4 unverified | 0.375 | **X** |
| 2 verified / 6 unverified | 0.062 | **X** |
| 0 verified / 8 unverified | 0.000 | **X** |
| 0 verified / 8 **fabricated** citations | 0.000 | **X** |
| 8 self_declared (honest general knowledge) | **0.000** | **X** |
| 4 verified / 4 self_declared | 0.500 | — |
| 2 verified / 6 self_declared | 0.250 | **X** |

**Measured property:** `self_declared` counts as neither support nor penalty, so
a purely general-knowledge answer scores **exactly 0.0** — the same score as an
answer whose every citation was fabricated. The scalar cannot separate those two.

On real traffic that is not a corner case: **11 of S1's 12 firings** were turns
with 0 verified and 0 unverified chunks, i.e. honest general knowledge. And on
**6 of the 12**, the enforcing layer's own recorded reason for doing nothing was
`no_evidence_expected` — the system had already decided evidence was not expected
on that turn, and S1 fired anyway.

### S3 — two measured precision/recall defects

**Precision.** It fired on `объясни разницу между REST и GraphQL`. Cause: the
keyword `"разниц"` exists for the *diff* tool and matches any question about a
conceptual difference. Same shape: `что измен` matches
`что изменилось в python 3.12`.

**Recall.** Over 12 phrasings that unambiguously demand a tool but are not in the
keyword list, it caught **1 of 12** (`посмотри, что вернул последний прогон`, via
`"прогон"`). Missed, among others: `сколько строк в core/loop.py`,
`какие тесты сейчас падают`, `проверь, что лежит в data/`,
`list the files under core`, `grep for TODO in the repo`,
`check the current git branch`.

This is the same failure shape as the routing defect fixed in #172 (a literal
keyword list that misses natural phrasings) — measured here, not assumed.

### S4 — false-positive probe

On 10 correct, ordinary planner rationales covering nine different tools, S4
fired **0 times**. The historical over-firing noted in its own source comments
(the old "≥ 6 characters" rule) does not reproduce.

## 3. Signal uniqueness

Across 8 constructed turn-shapes, computing all sensors on the same input:

| turn shape | S1 | S3 | S4 | S5 | enforcing layer acts |
|---|---|---|---|---|---|
| honest general-knowledge, no tools | X | · | · | · | · |
| fabricated citations, tools ran | X | · | · | X | X |
| long unsupported answer, tools ran | X | · | · | X | X |
| well-verified answer | · | · | · | · | · |
| half verified | X | · | · | · | · |
| every tool failed | X | X | · | X | · |
| tool used without being argued for | · | · | X | · | · |
| empty chain on a tool-demanding question | X | X | · | · | · |

Implications measured (a ⊆ b means every firing of *a* was also a firing of *b*):

- **S3 ⊆ S1** (2/2), **S5 ⊆ S1** (3/3), and the **enforcing layer ⊆ S1** (2/2).
- **S4 is independent** — it fired on a shape no other sensor flagged, and it is
  the only sensor that reads the planner's *reasoning* rather than the verifier's
  counters.
- S1 fires on 6 of 8 shapes. A signal present on three quarters of turns
  separates little by construction, whatever its individual verdicts.

On the real traces the same pattern holds: S3's single firing and 2 of S4's 3
firings coincided with S1.

**S2 reads a dimension nothing else reads** (repetition across attempts). It has
no per-shape column because it needs two attempts, and no real-traffic evidence
because nothing replanned.

## 4. Possible effect, on the 14 real cycles

Baseline: 14 cycles, **28 model calls** (2.0 per cycle).

| Sensor | Connected as (its own docstring's suggestion) | Turns affected | Model-call cost |
|---|---|---|---|
| S1 | replan on low confidence | **12/14** | **+24 (+86 %)** |
| S2 | stop the run early | 0/14 (no opportunity) | saves work rather than adding it |
| S3 | force a tool call before answering | 1/14 | +2 (+7 %) |
| S4 | replan the step | 3/14 | +6 (+21 %) |
| S5 | replan or escalate | 0/14 (no opportunity) | +0 |

For S1, of the 12 turns it would have replanned: **11 were honest
general-knowledge answers**, and **6 were turns the enforcing layer had already
ruled `no_evidence_expected`** — connecting it would spend a second model round
on turns where the system has explicitly decided no evidence was owed.

## 5. What this measurement does not establish

- **No independent ground truth for answer quality.** S1 is computed from the
  verifier's counters, so "was the answer actually bad" cannot be judged from the
  same counters without circularity. Everything above measures behaviour and
  cost, not correctness of the underlying judgement.
- **The sample is 14 cycles**, skewed toward tool-free chat. S2 and S5 are
  effectively unmeasured on real traffic.
- **Contract accuracy is self-referential** by design (§Method). A sensor can
  implement its contract perfectly and still be detecting the wrong thing.
- **The "connected as" column is an interpretation** of each docstring's own
  suggestion, not an approved design. A different connection would give
  different numbers.

## 6. Open questions for the operator

These are the judgement calls the numbers cannot make:

1. Should an honest general-knowledge answer score 0.0 confidence? If not, S1's
   scalar needs `self_declared` handled explicitly — that changes the meaning of
   the metric, not just a threshold.
2. Is S3's keyword list worth repairing (a 1/12 recall and a named
   false-positive cause), or is the premature-completion question better answered
   from the evidence chain than from question wording?
3. S2 and S5 cannot be judged on this sample. Is it worth collecting traffic that
   exercises them (a run with real replans and real tool failures) before
   deciding anything about them?

_Measured 2026-07-27 against `main` @ `388fac1`. Sensors read at that commit;
traces are whatever was in `logs/` at the time of measurement._

---

## Addendum — operator decision, 2026-07-27

The measurement above was delivered without a recommendation, as required. The
operator's ruling on it, recorded here so the numbers and what was done with
them stay together:

| Sensor | Decision |
|---|---|
| **S1** | Do **not** connect to replan. Stop calling it "confidence". Keep the computation as evidence-support telemetry and change its **semantics**: `no_evidence_expected` → `applicable=False, score=None`; expected-and-absent → `0.0`; fabricated citations → `0.0` **plus** an integrity flag. |
| **S2** | Do not delete. Build targeted replan scenarios, then shadow-log `would_stop` / `would_save_attempts` / `would_change_result` before any real stop. |
| **S3** | Keep the requirement, replace the detector. A keyword list over the user's wording is not the architecture; the question is whether a **tool/evidence obligation** was incurred and left unsatisfied. Keywords may survive only as a high-precision fallback. |
| **S4** | Keep as an observer, keep measuring. Later, tie the reaction to the **risk of the action**: read-only mismatch → telemetry; unjustified effectful/irreversible action → replan or approval escalation. Not a universal blocker. |
| **S5** | Do not delete. Build targeted scenarios for each declared conflict kind plus negatives, shadow-policy first. |

Operator's summary: sensors may not be removed in bulk, but **S1 in its current
form is not a confidence gate and S3 in its current form is not a reliable
premature-completion detector** — one needs redefining, the other replacing.
S2 and S5 simply have not had a chance to be judged.

**Implemented so far:**

* **S1** — `core/evidence_support.py`, event `evidence_support`.
* **S3** — `core/completion_obligation.py`, event `completion_obligation`.
  Three sources wired (`intent`, `plan`, `freshness`); `acceptance_criteria` is
  reported through `unavailable_sources` and draws no conclusion. The keyword
  detector is retained **only** as a shadow verdict inside the new event
  (`shadow_keyword_detector`), so the disagreement between old and new is a
  number on real traffic rather than a claim.

  `intent` is read structurally — from the **object** the question names (a
  workspace path, or a turn-scoped `file_hint`) — not from a verb list. That is
  what makes «объясни разницу…» a non-event while «сколько строк в
  core/loop.py» is one. The limit is stated rather than hidden: a verb-only
  demand with no named object and no admitted plan step (*«запусти тесты»* where
  the planner planned nothing) is **not** caught by `intent`; it is caught by
  `plan` whenever the step is admitted, and when the planner admits nothing that
  is a planner-recall problem, not something this sensor should paper over with
  a keyword list.

  **Update 2026-08-02 (PRs #216/#227, operator ruling):** the requirement now
  carries authority **at banking time**. The verdict is stored with the
  episode (`EpisodeRecord.defect_signals`), and an unmet obligation on a run
  that declared `achieved` lowers the completion verdict to
  `partially_achieved` (`completion_override` names the displacing fact; the
  declaration is never edited) and withholds procedure credit. Mid-run the
  sensor still stops and replans nothing — the promotion is to the verdict a
  run banks under, exactly the shape this ruling asked for: the requirement
  has the authority, not the retired keyword detector.

* **S2** — the five ruled scenarios exist as tests
  (`tests/test_sensor_shadow_scenarios.py`), and the loop now emits
  `stagnation_shadow` at the END of a run: `would_stop`,
  `would_save_attempts`, and `would_change_result` — the last one answered
  honestly, by whether any artifact actually arrived after the detection point.
  It stops nothing.
* **S5** — each declared conflict kind and its negatives exist as tests, and the
  loop emits `subsystem_disagreement_shadow` with `would_replan` /
  `would_escalate` counts (severity `high` → escalate, otherwise replan). It
  replans nothing.

Both shadow events are emitted at the end of the cycle on purpose: *"would
stopping there have changed anything?"* can only be answered once it is known
what the remaining attempts produced.

S4 unchanged by decision — still an observer, still measured. **Since
2026-08-02 its firings are additionally *banked* with the episode
(`EpisodeRecord.defect_signals`, PR #216), so a repeated mismatch is visible
in the agent's own memory rather than only in the per-run journal. Recording
grants no power — pinned by test — so the ruling above stands unchanged.**
