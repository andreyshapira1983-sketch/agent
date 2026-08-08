# What a `.qm` artifact is about — working note, Step 1

**Not a schema. Not adopted.** This records the candidate subject models, the
evidence that forced each, and the measurement that killed each. Every candidate is
kept, per the lab's rule that failed representations are results. The ledger at the
bottom is where status is *intended* to live.

> **Superseded, recorded as specimen S4.** This paragraph used to end: `The ledger
> at the bottom is the authoritative status; nothing above it states one`. Plainly
> false — candidates 0, 1 and 2 each assert a status above it. The coherence probe
> stayed green because those duplicates agree, which is exactly the S1 limit; and
> the sentence is S3-class as well, since no rule of the probe can see that a
> universal claim about a document is contradicted by the document.

**Candidate identity.** Candidates are numbered **0** (file-centric), **1**
(single junction), **2** (boundary-and-carriers). No candidate ever existed between
1 and 2. A spoken report once called the last one "candidate 3" by counting the
dead ones and continuing from the wrong index; that was an off-by-one in the
telling, not a lost candidate. Recorded rather than silently corrected, because if
the identity of the thing under evaluation is ambiguous, so is every verdict about
it.

The labels above are **descriptions, not statuses** — deliberately. The roster
previously read `**2** (current)`, and when candidate 2 died the copy did not. The
fix is not to update the duplicate but to stop duplicating a status that can move.
`scripts/qm_doc_coherence.py` catches duplicates only when they CONTRADICT — see
specimen S1 — so consistent restatements elsewhere in this note are unguarded.

## Why the question exists

`main.qm` is named after `main.py`. Of its 81 non-prose facts, **two** are about
`main.py`'s bytes (`subject.path`, `subject.sha256_prefix`). The rest describe
outcome states, an observer, and the internal structure of a different file in a
different language. The name and the content are about different things.

## Candidate 0 — file-centric: `filename ↔ semantic subject`

**FALSIFIED** before this step, by probe QT1: editing `cli/app.py` made a claim in
`main.qm` false while `main.py` stayed byte-identical (`def397b4c33e51d3` before
and after) and all eight cited tests stayed green. A subject whose truth is
controlled by files it does not name is not that file.

## Candidate 1 — the single junction: one carrier + the roles bound to it

Proposed in this step: the subject is one **carrier** (a channel holding
distinguishable states) crossing one **execution boundary**, with producer,
consumers, observers, consequences, validity and unproven scope attached as roles.

Motivation was real. Classifying all 81 facts against it left only 12 unslotted,
and the entry-point experiment had behaved exactly as it predicts: `main.py` is
neither carrier nor producer, and removing it destroyed no state and no observer —
all four baseline behaviours reproduced through `cli.app.run_cli`.

**FALSIFIED, in this step, by measurement.** Two runs were held equal on the
exit-code carrier and compared elsewhere:

| run | exit code | first line of stdout |
| --- | --- | --- |
| `python main.py --help` | 0 | `usage: main.py [-h] …` |
| `argv[0]='main.qm'`, `run_cli()` | 0 | `usage: main.qm [-h] …` |

One class on the exit-code carrier — indistinguishable to *every* observer of that
carrier, including `selfcheck.ps1`. Two classes on the stdout carrier: 2 of 33
lines differ. A subject with one carrier cannot hold the result of the very
experiment that motivated it.

## Specimen: this note contradicted itself, and a probe found it

Recorded before correcting, because the document became an instance of the disease
it is investigating.

**Two contradictions, verbatim as they stood.**

1. The opening roster read `**2** (current)` while the body read
   `## Candidate 2 — STRUCTURALLY INSUFFICIENT` and the ledger read *there is no
   surviving candidate*. One semantic fact, three assertions, one of them stale.
2. `Seven constraints (E1, E3–E7)` — that set has six members. The count is derived
   from the list, duplicated in prose, and has no authoritative source.

Both are the same shape: **one semantic fact → duplicated derived assertions →
the authoritative state changes → the dependents do not.**

**The detector, and its own failures.** `scripts/qm_doc_coherence.py` is told
nothing about which sentence is stale and has no reference copy. It looks for one
subject carrying both an alive-class and a dead-class status word, and for a
spelled-out count whose own enumeration expands to a different size.

It was wrong five times, each differently, and every correction is a finding:

1. **Dead on arrival.** Line-scoped, it found nothing: the count `Seven
   constraints` ended one line and its enumeration `(E1, E3–E7)` began the next.
2. **Still blind to the named specimen.** Raised to sentences, it caught a
   contradiction — but by a different route, missing the roster, because
   `There are exactly three candidates:` and the bolded numerals fell into
   different sentences. Paragraph granularity fixed it.
3. **It lit up on its own evidence.** The specimen above quotes both
   contradictions verbatim, and the probe cannot tell **use from mention**. The
   document now marks quotations with backticks and the probe blanks code spans —
   blanking, not deleting, so line numbers survive.
4. **A recall hole the bite test exposed.** Injecting `Candidate 1 is the current
   surviving model` raised nothing, because candidate 1's death is asserted in a
   sentence that does not repeat its name. Status is inherited from headings, so
   the probe now scopes a heading's subject over its section.
5. **The use/mention fix survived one round and then broke.** Writing up failure 3
   produced quotations that *wrapped across lines*, and the span pattern forbade
   newlines inside a quotation, so the probe fired on its own evidence again. The
   fix had assumed quotations do not reflow.

Every one of those five is the same failure as the disease itself, one level down:
**the detector's scope was narrower than the fact it had to see** — and failure 5
adds that a fix can carry the same assumption it was written to remove.

**Bite test.** Clean document: `COHERENT`, exit 0. Inject two synthetic
contradictions: both rules fire, exit 1. Restore: exit 0 again. Run over
`docs/PROJECT_MAP.ru.md` as well: clean, so the disease was not everywhere — it
was where derived statements had drifted from a status that moved.

**Its limits, stated so its silence is not overread.** It detects *divergence
between duplicated assertions*; it has no notion of derivation and cannot say which
side is authoritative. It depends on the document marking quotations. Heading-scoped
inheritance is a heuristic, not a parse. Its vocabulary is fixed and small, so a
status word outside it is invisible.

### Three more specimens, recorded before correction

Writing the section above produced three fresh instances of the same disease. Each
was probed rather than assumed.

**S1 — the prose claimed an enforcement the probe does not implement.** The note
said status is asserted in exactly one place and that the probe fails if a second
appears. Neither half is true. Candidate 0 states `FALSIFIED` in its own section,
candidate 1 states `FALSIFIED` in its own section, candidate 2 carries
`STRUCTURALLY INSUFFICIENT` in its heading, and the ledger states that all are
dead — four consistent assertions, and the probe is green. Adding a fifth
consistent one (`Candidate 0 is falsified, as established above`) kept it green.

So the supported claim is only this: **the probe detects duplicate *contradictory*
status. It permits duplicate *consistent* status, and therefore does not enforce a
single authoritative source.** The prose is corrected to that, rather than the
probe being enlarged to fit the prose — a single-source rule would need its own
bite test, and inventing it here to rescue a sentence is how the sentence got wrong
in the first place.

**S2 — the write-up immediately drifted its own count.** The introduction said the
probe `was wrong four times` above a list of five. Same shape as the `Seven
constraints` specimen, one paragraph after describing it.

And it was **invisible to R2**: a probe with `The probe failed seventeen times:`
above a three-item numbered list returns green. R2 only matches a count against an
*inline parenthetical* enumeration. A THIRD instance appeared while correcting the
second: the same paragraph introduced the list as `its own two failures`. Three
count drifts in one document, two of them created by the act of documenting the
first — the derived count has no source, so every restatement is a new place to
rot. So R2 biting on one form is not evidence that
derived counts are protected — it protects one syntax, and the live specimen in
this very document used the other.

**S3 — a stale assertion no rule can see.** The A2 section still read
`No probe has been run against this` while the same document contained the A2
experiment and measured verdicts for H1a and H1b.

This one matters more than the other two. It is not a status contradiction: no
alive/dead vocabulary appears. It is not a count. The two statements share **no
fixed vocabulary at all** — one is a negated-existence claim about an unnamed
"this", the other is a section of results. Detecting it needs to know what "this"
refers to and that the later section is an instance of it.

**The conclusion this forces, at the scope it was measured:** *shared-form textual
contradiction detection is insufficient for general semantic freshness.* It catches
divergence between assertions that happen to share a form, and S3 shares none. What
is NOT shown is that no text-operating mechanism could — coreference-aware parsing,
structured claims and generated derivations were never attacked, and the universal
version of this sentence would be an impossibility claim without evidence. Document
coherence and semantic validity are, at minimum, not the same problem.

**What this does and does not license.** It shows the requirement *can* be made to
bite. It does not establish the requirement as stated: the probe detects
**divergence between duplicated assertions**, and cannot tell which one is
authoritative — it has no notion of derivation at all. So what is earned is the
weaker claim that duplicated derived assertions can be made mechanically
detectable when they diverge. Generation-from-an-authoritative-state remains
**untested**, and is not adopted.

## Candidate 2 — STRUCTURALLY INSUFFICIENT

> **Killed by the projection measurement below.** It attached partitions and
> domains to *observation surfaces*. Measurement shows a partition is not a
> property of a surface: two projections on one surface induce different
> partitions, and one projection reaches several surfaces carrying one partition.
> A model whose ontology puts the partition in the wrong place cannot be repaired
> by wording. Preserved whole, as a failed representation.
>
> Its previous status line, `blocked on A2`, is withdrawn — it did not last long
> enough to be blocked. The authoritative status for every candidate is the ledger
> at the end of this note, and this block deliberately states none.

Its constraints are kept, because most of them outlived it. Scope is now separated
from ontology, which the first version of this table did not do:

| # | constraint | forced by | scope |
| --- | --- | --- | --- |
| E1 | the subject is not a file | QT1; 2 of 81 facts concern the named file | ontology |
| E2 | ~~the invoked file is a transit, replaceable without semantic loss~~ | — | **RESCOPED, see below** |
| E3 | a subject spans **several carriers**, each with its own state space | the `--help` probe | ontology |
| E4 | facts about **instantiation** fit no carrier or observer slot | the 12-fact residue | ontology |
| E5 | one consumer binds **several carriers** | `selfcheck.ps1:23–29` | ontology |
| E6 | the producer of record is a **symbol plus a termination contract**, not a file | the launch worked from `cli.app.run_cli` with `main.py` gone | ontology |
| E7 | a behavioural partition has **no structural home**: not the surface, not the projection, not the decision | four probes, each falsifying one candidate home | ontology |

> **Superseded, recorded as specimen S5.** This row used to read `a behavioural
> partition is induced at the decision`. The fourth probe, further down this same
> note, falsifies precisely that. That table is a derived
> restatement of results proved elsewhere, and it rotted the moment the result
> moved — the same shape as S1 and S2, in the one table meant to be authoritative.
>
> Writing this note also produced the probe's **first false positive**: the phrase
> `the surviving-constraints table` put an alive-class word inside candidate 2's
> section, and the probe read it as a status claim. Its vocabulary is not only
> small but *unsensed* — it cannot tell a status word from the same word used
> otherwise. Recorded as a second limit beside the known one.

### E2, rescoped

The old wording made a general claim out of one scenario. Split in two:

- **Measured, in one scenario, over two carriers:** with `argv[0]` held fixed,
  removing `main.py` from the execution path left the exit-code carrier and the
  stdout carrier byte-identical, and the `SystemExit` transit intact.
- **Ontology:** *transit* names a role that is invoked but is neither producer,
  carrier, consumer nor observer. **Whether that role is semantically empty is
  UNPROVEN.** Nothing licenses "replaceable without semantic loss" as a definition;
  the one law that said so is dead, and no carrier beyond those two was checked.

### E7 — projection is a distinct level, and it bites

The operator supplied two discriminators. Both were run, and both bite.

**(a) Two projections, one surface, different partitions.** `run_journal` returns
one dict through one call — a single output surface. Mutating only `ts` and
`trace_id`: `events_matched` stays 1 → 1 (**one class**), while `events` changes
(**two classes**). Same destination, same call, same keys. The surface cannot be
what carries the partition.

**(b) One projection, several surfaces, one partition.** In `selfcheck.ps1` the
projection `$code -eq 0` reaches three destinations — `return $ok`, the colour
choice `if ($ok)`, and the summary via `$results` — without its partition
changing.

So surfaces and projections are many-to-many. **But that is as far as those two
discriminators reach**, and the first wording — "the partition belongs to the
projection" — went further than they license.

**Third probe: one projection, two real consumers, incomparable partitions.** The
projection is *read the process exit code as an integer*. Two consumers already in
the repository take exactly that and nothing else:

| consumer | decision | partition over {0,1,2,3} |
| --- | --- | --- |
| `scripts/selfcheck.ps1:26` | `$ok = ($code -eq 0)` | `[[0], [1,2,3]]` |
| `tests/characterization/test_main_public_surface.py:154` | `assert result.returncode == 2` | `[[0,1,3], [2]]` |

Same projection, and **neither partition refines the other** — one merges 2 with 1
and 3, the other merges 0 with 1 and 3. A partition is therefore not determined by
the projection any more than by the surface.

What the three probes together establish: a projection is a *function from carrier
state to a value*, and it does not determine the partition. **Projection
equivalence** (two runs giving the same projected value) and **behavioural observer
equivalence** (two runs producing the same consequence) are different relations, and
the first does not determine the second.

### Fourth probe: the decision does not determine it either

The wording "the behavioural partition is induced at the decision" was the next
thing to outrun its evidence. Discriminator (b) — *different decisions reconverging
on one consequence* — bites in production code, without any construction:

| decision site in `core/loop_step_execution.py` | |
| --- | --- |
| `gw.outcome == "deny"` (:348) | effectful path, gateway refusal |
| `gw.outcome == "block"` (:371) | effectful path, gateway block |
| `decision.decision == "deny"` (:402) | non-effectful path, policy check |

Those distinct decisions reach **five** emission sites (:351, :363, :386, :405,
:417) that all write the same `code="policy_blocked"`, and the downstream consumer
in `core/loop_run_tail.py:302` tests **only** `code == "policy_blocked"`. So the
consequence partition is strictly coarser than the decision partition: two runs
that differ in decision are the *same* run under the consequence observer.

So the partition belongs to no single arrow in

> carrier state → projection → decision → consequence

It is finer at the decision boundary and coarser at the consequence boundary, and
neither is privileged. **Decision equivalence** and **consequence-relative
behavioural equivalence** are separate relations, added to the projection
equivalence already separated above.

What survives is the lab's older and plainer principle, which none of these probes
has dented: **equivalence is relative to a named observer and a named observation
boundary.** Three attempts to locate the partition in a structural place — the
surface, the projection, the decision — have each been falsified by measurement.
That is now the strongest statement the evidence supports, and it is weaker than
any of the three it replaced.

### The terminology contradiction, resolved

The note said both *a partition is induced by an observer* and *a partition belongs
to a projection*. The two-consumer probe decides it: the first survives, the second
is **obsolete**. `projection` and `observer` are not interchangeable, and the
discriminator is exactly the probe above — hold the projection fixed, vary the
observer, and the partition changes.

### The law this model proposed — DEAD

> ~~Replacing a transit preserves every carrier's state space and every observer's
> partition.~~

**KILLED, twice over.** The first draft of this note recorded it as surviving
"except in one localised place". That is not what universal quantification means:
one counterexample on one carrier is a falsification, and the `usage:` line was
that counterexample. Attaching the exception afterwards was rescuing the law, not
testing it.

The second kill is worse for the law and better for the model. The entry
experiment **varied two factors at once** — it replaced the transit *and* changed
`argv[0]` — so it never tested the law at all. A 2×2 separating them:

| transit | argv[0] | stdout sha256 | first line |
| --- | --- | --- | --- |
| `main.py` executes | `main.py` | `7b32cf76287b` | `usage: main.py …` |
| `main.py` executes | `ZZZ` | `9fe0544188bb` | `usage: ZZZ …` |
| never touched | `main.py` | `7b32cf76287b` | `usage: main.py …` |
| never touched | `main.qm` | `fe1ceb4afe04` | `usage: main.qm …` |

Hold the transit and vary `argv[0]`: **different**. Hold `argv[0]` and remove the
transit entirely: **byte-identical**. So `argv[0]` is contributed by the
invocation, not by the transit; the transit's name appears there only because the
invocation names it. Of the operator's candidate readings, this measures one —
*`argv[0]` belongs to the binding* — and disconfirms *identity-bearing transit*:
the transit does not bear the identity, the binding supplies it.

**What is NOT thereby established.** That the transit is transparent in general.
Two carriers were compared in one scenario. The dead law is not resurrected in a
narrower form; there is currently no law, only one measured pair of runs.

**Vacuous cell, on record.** The first version of this 2×2 used
`runpy.run_path('main.py')` for the "vary argv[0]" cell. `run_path` replaces
`sys.argv[0]` with the path it is running for the duration of the call and
restores it afterwards, so the cell silently re-tied the two factors it existed to
separate, and returned the baseline hash. It was caught because a cell designed to
differ came back identical.

## Attacks

**A1 — is "boundary" just the file under another name?**
Named without reference to `main.py`: *one process invocation whose producer is
`cli.app.run_cli`*. The entry experiment proves this identifier is the load-bearing
one — the boundary survived the file's deletion. **SURVIVES**, with a recorded
consequence: boundary identity is parasitic on producer identity, so validity must
bind the producer's source. The current artifact binds `main.py` and
`scripts/selfcheck.ps1` and **does not bind `cli/app.py`** — which is precisely the
file QT1 used to falsify it. A demonstrated gap, not a hypothetical one.

**A2 — is the carrier set closed?** **UNRESOLVED — this is what blocks Step 1.**
The boundary emits at least: exit code, stdout, stderr, log files, memory writes,
and network calls to the model. If the subject is "all carriers", it can never be
completed, and certifying it is meaningless. A consumer-relative closure ("all
carriers with a named consumer") matches the lab's existing INSTANTIATED /
HYPOTHETICAL discipline, but nothing yet proves that closure is well-founded — a
new consumer can appear at any time and silently widen the subject. The A2
experiment below attacks exactly this; H1a and H1b now carry measured verdicts,
and H1c does not.

**A3 — does the residue really need its own part, or is it a producer fact?**
P4 asserts six call sites, four passing `main.py`. That is neither producer nor
consumer alone; it is the consumer's invocation of the producer, and it names a
**transit** — an object E2 says carries no semantics but which must still be
nameable. **SURVIVES**, and it is what promoted "transit" from a description to a
part of the model.

**A4 — does the model earn its keep on the fact the old one could not express?**
The unexpressible fact was one consumer occupying two observer classes. Under
candidate 2 it becomes ordinary: one consumer, one carrier, two surfaces — plus a
second carrier the old model had no room for at all. **This attack was repelled at
the time, and its verdict is now superseded:** E7 shows the two things are two
projections, and that the partition was put on the surface. A4 is the attack that
was passed while the defect that later proved fatal was already present — kept as
a reminder that passing the attacks you thought to write is not evidence of
soundness.

## The A2 experiment — closure

**Scope, fixed before results.** Layer 1 is the **CPython audit-event frontier**,
not a kernel frontier: no kernel-level instrument was used, so anything a C
extension does without passing through CPython's hooks is invisible. Layer 2 is a
**hand-picked** set of in-process effects — environment mutation, module-table
growth, the outcome, the two text streams — and is explicitly *not* an enumeration
of in-process carriers: object mutation, queues, obligation state and planner
state were not observed. Layer 3 is a workspace filesystem diff. Nothing below
licenses "these are all the carriers of the boundary".

**Two paths through one boundary, three repeats each.**

| | channels | module reads | data reads | writes | stderr | outcome |
| --- | --- | --- | --- | --- | --- | --- |
| S1 `--file missing.txt` | `FS_READ` | 214 | 1 | 0 | 217 ch | RETURNED 2 |
| S2 `--ask :budget-status` | `FS_READ`, `FS_WRITE` | 220 | 2 | 1 | 6194 ch | RETURNED 0 |

Across three identical repeats the frontier was byte-stable in both scenarios —
which proves determinism, not closure. The result that matters is the comparison
*between* paths: **S2 has an entire channel S1 does not have**, and writes
`logs/run_<trace>.jsonl` — an effect this note first recorded as unconsumed and
later found to have had a consumer all along.

### H1 — split into three, because the first verdict was broader than the experiment

The original H1 wording covered two claims and the verdict prose drifted into a
third. Separated:

- **H1a — one path-independent active carrier set `C(B)`. REFUTED.** The active
  carrier set is a function of the path: S1 has `FS_READ` only, S2 adds an entire
  `FS_WRITE` channel. This is what the experiment actually measured.
- **H1b — closure derived by observing executions. REFUTED.** `FS_WRITE` was found
  only because S2 was chosen. A discovery procedure that finds what you already
  thought to look for is not a closure rule, and no number of additional runs
  changes that.
- **H1c — *any* mechanically checkable closed-world closure rule. UNPROVEN, NOT
  ATTACKED.** A union over paths, or a static, compositional, registry-based or
  formal derivation might yield one. Nothing here tests those, and the earlier
  phrasing "closing over all paths would require enumerating all paths" asserted
  their impossibility without evidence. **Withdrawn.**

### H2 — open-world with closed certification scope: UNTESTED

The decisive experiment was step 7: introduce one new consumer of a
previously-ignored effect and see whether the old certification becomes FALSE or
merely SCOPE_EXPANDED. **It was not run**, because the effect chosen for it turned
out to have a consumer already — see below. The result reached instead is
narrower, and is recorded at the end of this section.

### The partition belongs to the observer, not to the carrier

A first draft of this section said "stderr has the discrete partition". That is
wrong and the correction matters more than the observation. A carrier has a state
space; a **partition is induced on it by an observer**. The exact-byte observer
induces an almost-discrete partition over `stderr`, because nondeterministic
dimensions — `trace_id`, `ts` — put nominally identical runs in different raw byte
states. So:

> carrier state space ≠ observer partition
> raw physical distinguishability ≠ useful semantic distinguishability

Another observer over the same carrier may project those dimensions away and
induce a far coarser partition. That is not a workaround; it is what an observer
*is*.

**Both explanations for "identical input, different bytes" were attacked, and both
turned out to be real, in decomposable parts.** The first pair of runs was not
actually given identical input — they had different `--workspace` values, which
was my error, not a property of the system. Re-run with the workspace genuinely
held equal, the raw bytes still differ, and masking **the trace ids alone** makes
the two streams identical (timestamps and durations needed no masking in this
scenario). So: part of the difference was an omitted real input; the entire
residual is one nondeterministic dimension that byte-equality refuses to project
out.

### The journal already had a consumer — an unsearched absence, corrected

The draft above recorded "no named consumer reads it". **FALSE.**
`tools/agent_state_view.py:217` `run_journal()` reads the newest run log, filters
by event name, and returns a bounded result through the MCP server. It predates
this experiment by a long way — its own docstring cites MIR-077 and a review of
#317. The claim was an unsearched absence of exactly the kind the negative-claims
register exists to catch, and it was made in the same session that created that
register.

This settles one of the two questions asked of step 7: the effect did **not**
become semantic because a consumer appeared. It was already semantic and merely
**undiscovered by me**.

### The existing consumer, tested against the six criteria

Rather than invent a consumer, the real one was put through the test. `_LOGS` was
pointed at a laboratory copy of one run's journal; no production file was changed.

*Preconditions, proven before mutating:* the consumer really read the carrier
(`log_file` returned, `error` None), `rows_in_log=1`, `events_matched=1` for
`event_filter="session_start"` — so the thing to be observed demonstrably existed
in that run.

*Two opposite mutations, opposite predictions:*

| mutation | `events_matched` | `events` list |
| --- | --- | --- |
| only `ts` and `trace_id` changed | **1 → 1, unchanged** | **changed** |
| the `event` name changed | **1 → 0, RED as predicted** | changed |
| restored | **back to 1, GREEN** | restored |

The row count `rows_in_log` stayed 1 throughout the second mutation: the record
never left the carrier, only its class changed.

**The structural result: one consumer, one carrier, two projections of radically
different coarseness.** `events_matched` is blind to the nondeterministic
dimensions and bites on the distinction it claims to observe. The `events` list,
which carries `ts` and `payload`, is near-discrete on the same carrier for the same
consumer.

> **Superseded sentence, kept as evidence.** This paragraph used to end:
> `Observer status is therefore a property of a projection, not of a consumer and
> not of a carrier`. **Classified: stale prose, and a second meaning of "observer"
> smuggled in.** It was written when `observer` still meant *whatever induces the
> partition* — the loose sense. The two-consumer probe separates the words: a
> projection extracts a value, an observer is a projection plus a decision, and the
> fourth probe then showed the decision does not fix the partition either. So the
> ontology did briefly carry two incompatible meanings of `observer`; they are now
> distinct, and the claim that either one *owns* the partition is withdrawn.

*What the consequence actually is, stated honestly:* the projected state changes
an authoritative **report** returned through a tool boundary. No in-process control
branch was shown to depend on it. That is weaker than `selfcheck.ps1`'s `$ok`,
which drives its own control flow, and the difference is recorded rather than
smoothed over.

### What this does and does not settle for H2

H2 remains **UNTESTED**. The experiment that would test it — a genuinely new
consumer appearing and the old certification being re-graded — did not happen,
because the consumer turned out to predate the experiment. What was measured
instead is narrower and still useful: a subject can be **incomplete without being
false**. Nothing `main.qm` asserts became untrue when the journal consumer was
found; the artifact simply never had a field able to say whether its carrier list
was complete or merely as far as anyone had looked.

### What H2 must actually test — and the one piece now built

A new consumer is **necessary but not sufficient**. The question H2 asks is whether
a validator can *detect* that the certification frontier moved, without a human
telling it. If a new consumer appears and the certification stays green because
nothing mechanically notices, then open-world certification is operationally
useless — the status would be produced by retrospective interpretation, which is
the thing this lab exists to avoid.

So H2 needs, in order: a **declared frontier** with checkable evidence; a **scoped
negative** for the chosen effect; a **genuinely new consumer**; and a **mechanism**
that emits FALSE / STALE / SCOPE_EXPANDED / still-valid-under-narrower-scope by
itself.

**The scoped negative is checkable — built and positively controlled.** The claim
form is *no consumer of effect E exists within declared universe U at snapshot S
according to procedure D*, not a metaphysical absence. Instantiated:

- **U** — 278 files: repository `.py` outside `tests/`, plus every `.ps1`.
- **D** — textual reference to the effect's store or its reader.
- **Positive control** — run against the journal effect, D returns 46 candidate
  files and **does find `tools/agent_state_view.py`**, the consumer this note
  previously missed. A search whose silence is to mean anything must first be shown
  capable of finding a known consumer; this one is.

**What the positive control does and does not license.** For a negative claim
**recall** is load-bearing and **precision** only costs review effort — 46 hits
needing a second, precise pass is expensive, not invalid. Recall itself is proven
for exactly one consumer: a reader that builds its path dynamically, lives outside
U, or is another process entirely would escape D. The negative is therefore valid
strictly at the scope declared, which is what makes it usable and what forbids
reading it as "nothing consumes E".

### Three vacuous probes, preserved

1. `runpy.run_path` cell in the 2×2 — silently re-tied the two factors it existed
   to separate. Caught because a cell designed to differ returned the baseline hash.
2. "the journal contains the `--reason` value" — the substring matched the word
   `reason` inside `model_routes`, not the value passed. `--reason` without
   `--expect` never reaches that record at all.
3. `payload.reason` as the new consumer's field — does not exist; the consumer read
   `None` in all four runs, so its "one class" verdict established nothing.
4. `model_routes` as the semantic field — stable under repetition, but blind to the
   escalation input (`--reason` + `--expect`), because routes are fixed at session
   start and escalation happens per call. A field that never moves proves nothing
   about the observer that reads it.
5. "two runs with identical input" — the two runs had different `--workspace`
   values. The control that was supposed to isolate nondeterminism was itself
   varying a real input.

Each was caught by the rule that a probe returning the convenient answer must
prove its subject existed in that run. Three of the five were caught only because
the answer came back *too clean*.

## Status

**No candidate is alive.** Candidates 0, 1 and 2 are all dead, each by a different
measurement, and all three are preserved above. Six constraints — E1, E3, E4, E5,
E6, E7 — outlived candidate 2 and are what any successor must satisfy; E2 is
rescoped to a measurement and is no longer a constraint.

**Step 1 is not complete. Step 2 does not begin.**

Ledger:

| item | status |
| --- | --- |
| H1a — path-independent active carrier set | REFUTED |
| H1b — closure by observing executions | REFUTED |
| H1c — any checkable closed-world rule | UNPROVEN, not attacked; earlier impossibility claim withdrawn |
| H2 — open world with closed certification scope | UNTESTED; the detection mechanism does not exist |
| scoped negative (universe U, snapshot S, procedure D) | **CHECKABLE**, positively controlled, recall proven for one consumer |
| projection as its own level | FORCED, both discriminators bite |
| transit — is the role semantically empty? | UNPROVEN; the law that asserted it is dead |
| producer-binding debt | **OPEN, and not a hashing problem** — QT2 and QT3 below |
| dependency frontier of a claim | **OPEN** — QT3 shows it leaves the repository; QT5 shows it is evaluation-relative, not static |
| validity: artifact-level or claim-level | **SEPARATED** — QT4: one claim false while another stays true, both directions |
| duplicated derived assertions detectable when they diverge | bites for a shared form only; semantic staleness out of reach (S3) |
| single authoritative status source | **NOT enforced** — the probe permits consistent duplicates (S1) |
| partition has a structural home | REFUTED three times — surface, projection, decision |
| observer = projection + decision | **NOT frozen** — the five concepts stay separately attackable |

### QT2 — the validity subject is not the producer's file either

QT1 proved that `cli/app.py` controls the truth of `main.qm`'s claims, so the
current binding is insufficient. It did **not** prove that binding `cli/app.py`'s
bytes would be sufficient. Separating experiment:

- precondition: `cli/app.py` sha `a39e32aae8070fbc`, baseline exit code 0;
- mutate **one downstream dependency**, `cli/one_shot.py`, one `return 0` → `return 3`;
- result: exit code **3**, so the claim *RETURNED payload ∈ {0,2}* is now **FALSE**;
- `cli/app.py` sha after: `a39e32aae8070fbc` — **unchanged**;
- therefore a `cli/app.py`-only binding would report **FRESH: falsely green**;
- restored, exit code back to 0.

So `cli/app.py` alone is not the validity subject. **The conclusion first drawn
here — that the subject is "the set of code capable of producing the quantified
values" — outran the experiment**, which only showed the dependency set is larger
than one file. The licensed statement is weaker:

> A claim's validity depends on whatever can change its truth value within the
> declared evaluation domain. QT2 proves that this dependency set extends beyond
> the producer source file.

### QT3 — and it extends beyond code entirely

Attacking the dependency *class* rather than widening the file list. Claim under
test: *`session_start` records `llm_model = gpt-4o-mini`*. The lever is the
workspace's own `.env`, which `cli/app.py` loads by design — not code, not
`config/`, not even a repository file.

- precondition: no `AGENT_MODEL` in the workspace `.env`; combined sha of every
  `.py` in the repository plus every `config/*.json` = `f4701e3349379e22`;
- add one line, `AGENT_MODEL=some-other-model`, to the workspace `.env`;
- the recorded model becomes `some-other-model` — **the claim is FALSE**;
- combined sha after: `f4701e3349379e22` — **unchanged**;
- so a binding over **the repository's `.py` files and `config/*.json`** would
  report **FRESH: falsely green**. Stated as measured: that set was held constant,
  not "all code" and not "all configuration";
- remove the line: back to `gpt-4o-mini`.

The validity question is therefore no longer *which files must be hashed* — no set
of repository files answers it. It becomes: **what is the dependency frontier of a
semantic claim?** Configuration, environment, persisted state, external data and
model responses are all in the candidate class, and none of them is a file this
repository owns.

### QT4 — validity separates by claim, not by artifact

Two claims, one declared evaluation domain (`main.py --ask :budget-status`, fresh
workspace, `AGENT_MODEL` cleared from the process environment):

- **C1** — `RETURNED payload ∈ {0,2}`
- **C2** — `session_start records llm_model = gpt-4o-mini`

Both true at baseline. Two separating mutations, each restored:

| | C1 | C2 |
| --- | --- | --- |
| baseline | true (exit 0) | true (`gpt-4o-mini`) |
| **M1** — `cli/one_shot.py`, one `return 0` → `return 3` | **FALSE** (exit 3) | **still true** |
| **M2** — one line in the workspace `.env` | **still true** (exit 0) | **FALSE** (`some-other-model`) |

**One claim can be invalid while another in the same artifact is valid, in both
directions.** An artifact-wide binary freshness state is therefore strictly coarser
than the measured validity structure.

*What this does not settle.* It does not show claim-level validity is the right
architecture. A coarse artifact-level invalidation may be a deliberate
conservatism — invalidating everything when anything moves is sound, merely
imprecise. The experiment forces only that the two are **not the same relation**,
and that the artifact-level one loses information the claim-level one has.

**Vacuous mutation, on record.** M1's first run reported C1 still true. The
mutation had matched nothing: the file on disk carries CRLF endings — a residue of
an earlier text-mode restore — and the needle used a bare `\n`. It was caught only
because a mutation designed to bite came back green, and the rerun asserts that the
replacement changed the bytes before trusting the result.

### QT5 — a dependency can be present and inert

QT3 showed the workspace `.env` can control C2. It does not follow that `.env` is a
dependency of C2 in every evaluation. `cli/app.py` calls `load_dotenv` without
`override`, so a variable already in the process environment wins. Measured:

- workspace `.env` says `AGENT_MODEL=from-dotenv`;
- process environment says `AGENT_MODEL=from-process-env`;
- recorded model: **`from-process-env`** — the `.env` line is **inert**.

So the same physical dependency is load-bearing in one evaluation and irrelevant in
another, decided by precedence rather than by presence. That distinguishes a
**potential** dependency frontier from the **active** frontier of a given
evaluation. Recorded as a measured distinction only — not proposed as fields, since
one precedence rule in one loader is thin ground for a structure.

### Three problems that looked like one

The coarse representation made these look like a single question. They have now
come apart, and collapsing them again would undo the evidence:

- **document coherence** — do duplicated assertions in a record agree? Mechanically
  detectable for contradictions of a shared form; provably out of reach for
  semantic staleness (specimen S3).
- **semantic validity** — can this claim still be true? Depends on a dependency
  frontier that QT2 showed is wider than one file and QT3 showed is wider than the
  repository.
- **behavioural equivalence** — are two runs the same? Relative to a named observer
  and boundary, and three attempts to give it a structural home have failed.

The goal is no longer to rescue a candidate. The question QT4 sharpens is whether
semantic validity is a property of an artifact, of a boundary, or of an individual
claim relative to an evaluation domain and a dependency frontier. QT4 shows the
artifact-level relation is strictly coarser than the claim-level one; it does not
show which belongs in a representation.

If that separation holds under further attack, **H2 itself needs restating** — from
*did the artifact frontier move?* to *which claims lost or expanded their certified
frontier, and why?* That reformulation is **not adopted**: one experiment with two
claims is not enough to redefine the next hypothesis.

Also not frozen: `observer = projection + decision`. Projection, decision,
consequence, observer and observation boundary remain five separately attackable
concepts, because every structural relation proposed between them so far has been
falsified within one or two probes.

Nothing here is adopted, and no field names are proposed. `main.qm` is unchanged,
and no production file was modified by any experiment in this note.
