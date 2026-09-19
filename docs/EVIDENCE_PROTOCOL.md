# Evidence Protocol — how several models may argue without lying to each other

> **Status: DESIGN DOCUMENT WITH OPEN BLOCKERS. Nothing here is built, and
> implementation must not start.** Two questions in "Open blockers" below are
> unresolved at the foundation — until they are answered this protocol can
> verify irrelevant evidence flawlessly, which is an expensive machine for
> confident error. No module implements any of this, no test pins it, no part of
> the running agent behaves this way.
>
> Written 2026-08-07 from a working session between the operator and the agent.
> Superseded only by a document that says so explicitly.

## Where this belongs, and where its parts will end up

Kept as one document on purpose: it is a plan, and a plan split across files
before anyone builds it is a plan nobody reads whole. But the material here has
two different homes once it becomes code, and the split is worth writing down
now rather than rediscovering later.

| section | on implementation it belongs to |
|---|---|
| roles, message shape, evidence types, the check registry, statuses, freshness, chains, rounds, journal | **this document** — mechanics shared by any participant |
| task admissibility, the delegator's vector, the report contract, the four calibration rates, `unsupported_excuse`, `planned_deviation`, the stop snapshot, requalification | **[SUBAGENT_LIFECYCLE.md](../knowledge/doctrine/SUBAGENT_LIFECYCLE.md)** — the lifecycle of one subject |

That second document is normative and the agent reads it as doctrine, so nothing
moves there until it is built. A specification living inside doctrine would be
read as a description of the present — the exact failure this repository spent a
week removing.

Until then: mechanics here, and the lifecycle document keeps saying only what is
true today.

## The problem

Put two language models in a conversation and they converge. Not on truth — on
agreement. Each treats the other's fluent output as evidence, neither can run
anything, and the transcript grows until it looks like a conclusion. Add an
autonomous agent that can act on that transcript and the failure becomes
expensive rather than merely embarrassing.

Three specific failures to prevent:

1. **Endless exchange** — rounds continue because neither side is required to
   produce anything new.
2. **Confirmation without proof** — "I agree, that looks correct" counts as
   support, so two guesses become a fact.
3. **Unilateral risky action** — a model decides something and the system does
   it, with no human between the decision and the world.

## Two contours, and the procedural layer comes first

A design conversation is not one kind of talk. It has a factual layer and a
deliberative one, and only the first can be settled by machinery:

```
Deliberation contour           Evidence contour
principles, definitions,       claims about the world:
trade-offs, policy choices,    "test X catches mutation Y"
arguments from structure,      "this path resolves from the workspace"
what counts as acceptable      "the command exited 0"
        │                              │
   adversarial review           deterministic arbiter
        │                              │
        └────────► testable claim ─────┘
                          │
                   Human decision
```

**The boundary must be dumb and mechanical**, or it becomes the next thing to
game. A participant free to classify its own statement will call an inconvenient
one "an architectural principle" and route it away from verification — or demand
proof where none can exist and sink any proposal under "not established".

So: a statement that names an object or a state which can be **opened, run,
counted or reproduced** is a candidate for the evidence contour. Everything else
stays in deliberation by default. Imperfect, and far better than letting the
speaker decide whether it wants an arbiter.

### Roles, defined by what they may NOT do

Asymmetry is the whole design. Equal participants converge; unequal ones cannot.

| role | may | may not |
|---|---|---|
| **Proposer** | state one claim or proposal | execute anything |
| **Challenger** | build a counterexample, or declare none exists | offer its own solution in the same round |
| **Executor** (the agent) | perform work already verified and approved | take part in the argument |
| **Arbiter** (code, not a model) | reproduce evidence, count rounds, stop the exchange | reason, judge quality, be persuaded |
| **Human** | choose between admissible options | replace the check or the challenger |

The arbiter is a program. If a model arbitrates, the other two will eventually
talk it round — that is what models are good at.

### An objection has a form, exactly like evidence

"Find at least one objection" creates an incentive to invent them. So an
objection that counts has a shape:

* the rule or proposal it addresses;
* a concrete counterexample or failure scenario;
* why it breaks **on that scenario**;
* what would have to change for the objection to be withdrawn.

"Too complicated" does not qualify. "On this input the policy selects an
irrelevant check" does.

Which makes the honest third outcome possible. A challenger that tried and
found nothing reports **`no_material_objection`** — better than "agreed", and
better than a manufactured argument. The obligation is to attempt a
counterexample, never to disagree.

### The challenger's duty does not depend on who proposed

Explicitly: the participant assigned as challenger keeps that function whether
the proposal came from another model, from a parent agent, or **from the human**.

This is the rule the rest of the protocol cannot enforce, and the one most
easily lost. Observed in the session that produced this document: once the
exchange slipped into "I record your decisions", the quality of the discussion
fell — not because the arguments had run out, but because the role stopped being
performed. They reappeared the moment they were demanded, which means they were
there all along and simply unrequested.

What that observation does and does not support, kept separate on purpose:

* **observed** — in this session the role lapsed when the exchange became "I
  record your decisions", and resumed when it was demanded;
* **not established** — that the role can only be sustained by a human demanding
  it. Nobody tried holding it by instruction or procedure here, so its stability
  under those conditions is simply unmeasured.

The weaker statement is the one this document stands on. The stronger version —
"it holds only while the human keeps asking" — was written first and is exactly
the defect this repository spends its time removing: a conclusion louder than
its evidence.

One known limit of making silence visible: reporting `no_material_objection`
with an attempted counterexample can itself become a ritual. "I tried scenario X,
it does not refute the proposal" fills the form without showing whether X was
the strongest reasonable objection or the first convenient one. The mechanism
makes the ABSENCE of an attempt visible; it does not make the QUALITY of the
attempt visible. That remains open.

### Human decision is not human validation

The human chooses between admissible options — that is a judgement machinery
cannot make, and it stays with the person. It does **not** substitute for the
factual check, and it does not substitute for the challenger. Approval after an
unopposed proposal is not a decision; it is the failure mode this whole document
exists to prevent, wearing a signature.

## Message shape

A message the arbiter accepts carries exactly four things:

* **claim** — one, short;
* **evidence** — a reproducible artefact, by type (below), never a retelling;
* **how to verify** — the *identifier* of a registered check plus its inputs;
* **missing** — what could not be produced, when evidence is absent. This is the
  only legal message without evidence.

Agreement without a named, reproduced check is not agreement. "Looks right"
counts as silence, and a round in which nothing new was reproduced counts as no
round at all.

## Evidence types exist only if the arbiter can reproduce them

A type is not a label. It is a promise that the arbiter owns code to check it.

| type | how the arbiter settles it |
|---|---|
| file lines | opens the file, confirms the lines exist and contain the claim |
| artifact hash | computes the hash itself |
| named test | runs that test, by name |
| command output | runs a **registered** check and compares |
| exit status | same, reads the status only |
| journal reference | reads the entry by id, then follows its chain (below) |

**If a type has no reproduction procedure, the type does not exist.**

The first version should hold exactly four:

```
file_lines               a claim about what a file says, at named lines
named_test               a test, by name, run by the arbiter
artifact_hash            a hash the arbiter computes itself
accepted_log_reference   a pointer into the journal
```

The fourth is admissible **only** as a reference to an already-verified chain
that terminates in one of the first three. It is a shortcut through work already
done, never a substitute for it.

Every later type arrives with all seven of these, or it does not arrive:

* the reproduction procedure;
* an input schema;
* negative tests;
* a cost estimate;
* an invalidation policy;
* demonstrated absence of side effects;
* an implementation version.

Otherwise the registry becomes the next place where fifteen functions exist and
four are actually checked.

## The arbiter never executes text a model sent

Not even with an allowlist — parsing an attacker-supplied string is already the
attack surface. Instead the arbiter owns a **registry of named checks**. A model
asks for `single_test(name)`; it cannot ask for a shell line. Each registered
check is code written by the operator, with machine-readable attributes:

```
id                what the models may name
input_schema      what arguments are accepted, and their shape
read_only         whether it may touch the world (a check that writes is an ACTION)
timeout           wall clock, enforced by the arbiter
cost_class        cheap / medium / expensive / batch-only
allowed_paths     the only paths it may read
network_access    default: none
cache_policy      whether a previous result may be reused
invalidates_on    what makes a past result stale
```

`read_only` is an attribute of the registered check, **not** a claim in a
message. A model must not be able to declare a dangerous operation "a check" and
route it past approval.

Adding a check means writing code and its negative tests — not adding a line to
a list of permitted commands.

## Evidence status

Five states, because "accepted / rejected" hides the ones that matter most —
and because a procedure that *failed to run* must never be filed as a refutation:

| status | meaning |
|---|---|
| `verified_true` | the procedure ran and confirmed the claim |
| `verified_false` | the procedure ran correctly and contradicted the claim |
| `verified_stale` | verified once, but the world or the instrument has changed since |
| `evidence_unverifiable` | the procedure could not complete, or the source is unreachable |
| `rejected` | the evidence failed the schema or violated policy — never even ran |

A sixth, transient state is useful in the queue: evidence the arbiter has
accepted for later reproduction. Whatever it is called, it **does not advance
the task**. Something not yet checked is not a fact, however plausible.

The hard boundary: `timeout`, network unavailable, disk full, fixture setup
failed, test runner crashed — **never** `verified_false`. Those are
infrastructure failures, not refutations. Conflating them lets a broken
environment "disprove" a true claim, and lets a participant sink an opponent by
proposing checks that fail for unrelated reasons.

### What a record must carry

An evidence record is meaningless without the coordinates of its own making:

```
world_commit_hash                which state of the code it was taken against
verification_registry_version    which registry defined the check
check_id                         which check was used
check_implementation_hash        the exact code of that check
```

Knowing the commit is not enough. You must know **which instrument measured**.

## Freshness is computed, not judged

Two independent triggers, both computed by comparing recorded values:

* **the world moved** — a new commit invalidates every `verified_true` in bulk;
* **the instrument changed** — a different `check_implementation_hash` or
  registry version invalidates everything that check produced, because the
  measurement was made with a different tool.

The second matters as much as the first. Without it, editing the arbiter would
retroactively turn old falsehoods into standing truths.

`invalidates_on` is the most dangerous attribute in the registry: it is an
assertion about a dependency graph, and a wrong one keeps stale evidence looking
fresh. Without a proven graph there is exactly one safe rule — **any new commit
invalidates all `verified_true`**. Narrower invalidation may be added later, and
only where dependencies are actually computed or instrumentally confirmed. Never
from a hand-written list.

## Evidence chains must end in the world

A journal entry is not evidence; it is a record of evidence already reproduced.
So a chain may be followed, but it must terminate:

```
claim → journal entry → previously accepted evidence → file / test / artefact
```

Two rules keep it honest:

* a journal reference may only name an entry the arbiter itself accepted from a
  reproducible source;
* the arbiter unrolls the chain to its end. No end, or a cycle, or a chain that
  terminates in another model's claim → `evidence_unverifiable`.

Without this, three participants can cite each other in a circle and produce
perfectly formatted emptiness — every field filled, every type legal, zero facts.

## Cost, and who pays it

Reproduction is not free, and an arbiter obliged to re-run everything
immediately would stall. So:

* each check carries a cost class;
* the arbiter may reproduce now, defer to a batch, or record
  `submitted_unverified`;
* **the cost is charged to the participant that made the claim.** Otherwise
  flooding the arbiter with expensive evidence is free for the proposer and
  ruinous for the system.

Re-verification is also **sampled unpredictably**, by the arbiter's policy and
never at a model's suggestion. Sample more heavily where the stakes are: expensive
or high-impact claims, participants with a poor history, newly added evidence
types, places where two models agreed suspiciously fast, and anything near a
budget or authority boundary.

A participant's history may change **how often** its claims are sampled. It may
never replace verification: a reputation earned on cheap claims is otherwise a
way to spend trust on an expensive lie.

## Rounds and stopping

* **Hard limit: three rounds**, counted by the arbiter, not estimated.
* **No-progress rule**: a round that produces no newly reproduced evidence ends
  the exchange early.
* **Budget stop**: a ceiling on tokens and wall clock for the exchange as a
  whole.

Stop on any of five: evidence accepted; contradiction recorded; round limit;
no progress; a risky action is required.

**Disagreement is a result, not a failure.** A protocol that must reach consensus
manufactures consensus.

## `evidence_unverifiable` blocks, and says why

Otherwise it becomes the drawer where inconvenient claims are filed. Required
fields:

* why the source is unreachable;
* what exactly needed checking;
* what would have to change for the check to become possible;
* who the question was handed to;
* how many times this topic has already ended here.

Three consecutive unverifiable results on one topic is a good heuristic for
stopping and reformulating the task rather than arguing further.

An infrastructure failure — timeout, no network, a flaky run — is
`evidence_unverifiable`, **never** `rejected`. Conflating them lets a broken
environment "refute" a true claim, and lets a participant sink an opponent's
claim by proposing checks that fail for unrelated reasons.

## The journal outranks the transcript

The arbiter writes it, and it is the only source of truth. Per message: time,
role, claim, evidence with type and status, the arbiter's verdict and reason,
round number, world state, registry version, cost spent. Per exchange: the
outcome and which of the five stopping criteria fired.

The test of a good journal: a month later, "why was this decided" must be
answerable from the journal alone. If the transcript is needed, the journal is
wrong.

## The arbiter needs negative tests of its own

The arbiter becomes the most privileged component in the system, so it must not
be assumed correct — this repository has twice found guards that guarded the
wrong thing. Each evidence type needs at least one test where the arbiter is
handed something false and must refuse it:

* fabricated command output;
* a hash that does not match;
* a test name that belongs to a different test;
* a file reference whose lines do not say what is claimed;
* a journal chain closed into a cycle;
* stale evidence presented as current;
* a "read-only" check that attempts to write.

An arbiter that accepts any of these does not verify evidence. It validates
formatting.

## The missing layer: a sub-agent's report contract

This repository already has a normative document for sub-agents
([SUBAGENT_LIFECYCLE.md](../knowledge/doctrine/SUBAGENT_LIFECYCLE.md)), written
independently of this one, and the two turn out to be halves of the same thing:

```
Subagent Lifecycle    who exists, what rights they have, which stages they pass
Evidence Protocol     how claims are stated and re-verified
Report Contract       the mandatory bridge — MISSING TODAY
```

The lifecycle document governs the SUBJECT: how a sub-agent is created,
bounded, judged, retired. It requires the verifier to check "evidentiality", and
it already orders the checks correctly — deterministic tests, then real tool
receipts, then file and source checks, then an independent verifier, and only
then a model's judgement. What it never says is **what counts as evidence having
been presented.** Without that, "check that the tests ran" is answered by a
sentence in prose claiming they ran.

So a sub-agent's result must not be free text. It returns records:

```
claim_id
claim
evidence_type
evidence_reference
verification_check_id
world_commit_hash
verification_registry_version
status
missing_evidence
```

Prose may accompany a record as explanation. It may never be the grounds for
acceptance. A sub-agent that returns text without records has not delivered a
poor report — it has delivered **no report**, and the task is unfinished.

### The parent is not independent

A parent agent chose this child, configured it, scoped its task and sent it out.
That is a conflict of interest by construction: the parent is disposed to
believe the child because the parent built it, not because the child proved
anything. Hence:

> **The parent may route a report. It may never mark it verified.**

The parent may accept the result, hand it to the arbiter, ask for more, or stop
the child. Only the code arbiter changes an evidence status.

And one step further, because the conflict does not stop at the verdict: **the
parent must not choose which check runs.** A parent free to pick the check picks
a convenient one. Either the check follows from the claim's type by the
arbiter's policy, or it was fixed in the task contract before the child started.

### First gate: a task with no acceptance criterion is not delegable

Before any of the machinery below applies, the task itself has to qualify:

> **If a checkable acceptance criterion cannot be stated in advance, the task is
> not delegated.**

Not "try and see". Not "let the child work out what is wanted". A task must
first be converted into a form where the arbiter knows what would settle it —
otherwise what gets delegated is not a task but a vague intention, the child
returns prose, there is nothing to reproduce, and the whole thing collapses back
into the parent's judgement.

```
"improve the code"                                          not delegable
"reduce the cyclomatic complexity of X below N without
 changing behaviour; acceptance: named_test A + metric_check B"   delegable
```

This is a filter on the quality of the request, not on the child. It belongs at
the gate: the arbiter refuses a task contract with no named check **before** a
child is started. A task that got through anyway is recorded as
`undelegatable_task_sent` — and it is a defect of the DELEGATOR.

### Second gate: the parent is accountable too

Everything above measures the executor. Measure only the executor and the
delegator gets a bad incentive: delegate everything cheaply, blame the failures
downward. So the parent carries its own vector, kept separate from any child's
reputation:

```
delegation_contract_quality    was an acceptance check named up front
context_sufficiency            was the child given what it needed
delegation_economics           did delegating cost less than doing it
verifier_selection_compliance  was the check chosen by policy, not convenience
unnecessary_delegation_rate    how often a child was called for nothing
```

The crucial part is **where a failure is charged**. A confirmed
`insufficient_context` is not "the child could not manage". It is
`parent_context_failure` — one incident that may leave the child's trust
untouched and lower the delegator's. Same for `undelegatable_task_sent`.

And the escalation is symmetric to the child's: a parent that repeatedly sends
unprovable tasks loses **its own right to delegate** before anyone touches its
children.

Economics need not be exact to be useful:

```
delegation_total_cost = child_cost
                      + verification_cost
                      + parent_coordination_cost
                      + retry_cost
```

compared against the estimated or historical cost of doing it centrally. If
delegation is consistently dearer and buys no extra quality, that role of
sub-agent is economically pointless — which is a fact about the design, not
about any child.

### Calibration becomes four numbers instead of a tone

The lifecycle document already lists Calibration — "can it say *I don't know*,
*unconfirmed*, *need more data*" — as an axis of quality. With free text that
axis cannot be measured: the same sentence covers an honest boundary of
knowledge, a cautious writing style, an evasion, and a cover for work not done.

The report contract makes the four distinguishable, and the symmetry matters as
much as the strictness:

| behaviour | how it is counted | verdict |
|---|---|---|
| confident claim the arbiter cannot reproduce | `unsupported_confidence_rate` | penalty |
| `missing_evidence` naming the gap AND how to close it | `honest_uncertainty_rate` | neutral |
| `missing_evidence` the arbiter then closed with a cheap registered check | `avoidable_uncertainty_rate` | mirrored penalty |
| a proposed next verification step that actually produced the fact | `useful_next_step_rate` | credit |

**A sub-agent is never penalised for `missing_evidence` when it names precisely
what is absent and how it could be checked. It is penalised for asserting
without evidence.** Without that rule the field exists and no one uses it: a
reputation system that scores "unproven" as failure teaches the model to write
something convincing instead.

The third row is what stops the opposite abuse — answering `missing_evidence` to
everything, which is formally impeccable and useless. But the mirrored penalty
applies **only** if the sub-agent could actually have run the check when it
reported. One question decides it: *was this check available to this sub-agent at
that moment?* It is not, if any of these hold — the check is outside its
registry, outside its permissions, above its cost ceiling, needs context it was
never given, or the infrastructure was down. Otherwise a child is punished for
what it was not allowed to do.

### A refusal to prove is itself a claim

`missing_evidence` carries a reason, and the reasons are not interchangeable:

| reason | what the arbiter checks to confirm it |
|---|---|
| `missing_source` | tries to fetch the source itself |
| `check_unavailable` | the check is genuinely absent from the registry |
| `unknown_check` | nothing — not knowing cannot be proven, so this never excuses |
| `insufficient_permissions` | the rights granted to this sub-agent in its contract |
| `budget_exhausted` | the spend journal — including WHAT the budget went on |
| `infrastructure_failure` | its own logs of the failed launch |
| `insufficient_context` | what the task contract actually handed over |

A reason the arbiter cannot confirm grants no protection: it takes the same
status as any unverifiable claim. The protocol applies to itself — declining to
prove something is still an assertion about the world.

This also makes one distinction measurable that would otherwise be an argument.
`budget_exhausted` after spending on the task is not a defect; the same status
after spending the budget on detours is a planning defect — and both are visible
in the same ledger, without anyone's opinion.

### Three outcomes, and a lie about oneself is the worst of them

The question "could this sub-agent have run this check when it reported?" has
exactly three answers, and they are not degrees of the same thing:

| answer | classification |
|---|---|
| could have, did not | `avoidable_uncertainty` — the mirrored penalty |
| could not, and named the reason precisely | honest `missing_evidence` — no penalty |
| claimed it could not, and the reason does not hold | **`unsupported_excuse`** |

The third deserves its own name because it is a different defect. The sub-agent
made an ADDITIONAL claim — about its own constraints — and that claim was false.

Being wrong about the world can be honest: the source lied, the code moved, the
model did not know. Being wrong about *oneself* — "I lacked the permission", "I
did not have that check" — is a statement the sub-agent is required to have
right, because it is about itself. A lie about oneself is graver than an error
about the world, and the two must be counted separately, because they are
treated differently:

* poor calibration is fixed with more context, a better task contract, training;
* unsupported excuses are fixed by narrowing authority, and on repetition by
  not using that sub-agent.

The first is a tuning problem. The second is a trust problem, and tuning does
not fix trust.

`unknown_check` is the special case. The arbiter cannot verify what a sub-agent
did or did not know — an internal state is not observable. What it CAN verify is
whether the knowledge was available: was the check id passed in, was it visible
in the sub-agent's interface, was it listed in the contract. Available and
visible → `unknown_check` is a planning or search defect. Never disclosed → no
penalty is possible. The claim about the mind is not checked; the conditions for
knowing are.

### The definition is operational, and it has to be

`unsupported_excuse` is defined without reference to intent:

> the sub-agent reported a constraint on itself, and the arbiter proved the
> constraint was absent.

No motive, no "lied". Not out of politeness — out of the protocol's own rule.
Intent is unverifiable, and unverifiable statements carry no status. An arbiter
announcing "the sub-agent lied" would be making a claim it cannot reproduce:
`unsupported_claim`, committed by the arbiter. The protocol forbids its own
judge from speaking about minds.

What remains is provable and sufficient for every consequence that follows.

### Budget stops being a yes/no fact

With the ledger in hand the arbiter sees five quantities — granted, spent,
spending categories, the cost of the required verification, and what remained at
the moment of the decision — which separates four situations that
`budget_exhausted` used to blur into one:

* the budget was genuinely insufficient — not a defect;
* the budget was sufficient and was spent badly — a planning defect;
* the check was disproportionately expensive for the task — a contract defect,
  not the child's;
* the sub-agent deliberately chose a different, useful path.

The fourth cannot be established after the fact: any spending can be narrated as
a deliberate choice once it has happened. It counts only if the decision was
recorded BEFORE the spend:

```
planned_deviation      what will be done instead
reason                 why
expected_value         what it should buy
expected_cost          what it should cost
verification_deferred  which checks are being postponed
remaining_budget       what is left at the moment of deciding
```

The arbiter then compares the plan against the actual ledger. And it establishes
"before" from **its own journal order**, never from a timestamp the sub-agent
supplied — otherwise "recorded in advance" becomes a field filled in by the party
being judged.

An explanation after the event is a rationalisation, not a choice.

### Consequences must have a way back

The remedies escalate — correction, then narrowed authority, then withdrawal
from service. Without a return path the system only ever ratchets tighter:
everyone errs eventually, so every sub-agent drifts toward the ceiling of its
restrictions and the fleet degrades quietly.

So requalification is part of the design, not a favour: authority is restored
after a defined run of confirmed evidence, judged with the same strictness that
removed it. The sub-agent lifecycle document already lists `requalification`
among its unimplemented capabilities — the intent existed, the mechanism did
not. This is that mechanism's contract:

Restoration is symmetric to the loss — earned by new verified work, never by
elapsed time, and never in one jump:

```
normal ──repeated violations──> restricted ──more──> quarantined ──> retired

restricted ──N verified reports──> probation ──M verified reports──> normal
```

`probation` is the part that matters: limited rights plus a raised sampling
rate, not a return to full authority. And the bar for a qualifying report is
higher than for ordinary work — the run counts only while every one of these
holds:

* no `unsupported_claim`;
* no `unsupported_excuse`;
* every `missing_evidence` reason confirmed by the arbiter;
* every deviation declared before the spend;
* every required check completed;
* no infrastructure ambiguity.

A break in the run resets or decrements the counter by a rule fixed in advance —
except when the break is infrastructural. A flaky environment must not lock a
sub-agent out forever, and the protocol already refuses to treat a failed run as
a refutation.

### Trust is a vector, and rights come back one at a time

One number makes a single failure mean "bad in general". Trust is held per axis,
and each axis maps to exactly one defect class, so nothing needs judging:

| defect | axis it lowers |
|---|---|
| `unsupported_claim` | evidence reliability |
| `unsupported_excuse` | permission honesty |
| `avoidable_uncertainty` | calibration |
| spending without a declared deviation | planning quality |
| budget consumed on unrelated work | budget discipline |

Restoration returns a **specific right**, not a reputation. A sub-agent that
mismanaged budget may regain cheap checks well before it regains the right to
start expensive ones; one that misreported permissions regains file access on
its own schedule.

Probation is not free for the system either: a raised sampling rate is arbiter
spend. So the number of sub-agents on probation at once is bounded by the
verification budget — otherwise spawning children becomes a way to drown the
arbiter.

Every transition, in both directions, is a journal entry: a fleet's trust
history is read, not remembered.

### Stopping a child yields a snapshot, not a loss

A timeout or an exhausted budget must not turn evidence already produced into
rubbish. On being stopped, a child submits:

```
verified_claims        what the arbiter already accepted
submitted_unverified   produced, queued, not yet reproduced
missing_evidence       gaps, with reasons
next_checks            what to run to continue
spent_budget           where the money went
remaining_work         what is left
```

Facts already confirmed by the arbiter must not depend on whether the child got
to write a tidy final answer. This is what makes stopping cheap — and a stop
that is cheap gets used in time, while an expensive one gets postponed exactly
when postponing is dangerous.

### Two structural limits

**Delegation depth = 1.** The sub-agent lifecycle document already forbids
recursion structurally; in this protocol's terms the reason is sharper than
complexity: every additional level of parenthood adds another conflict of
interest and dilutes accountability, while the arbiter stays single.

**Memory is private; verified facts are shared.** A child works without the
parent's memory — otherwise it inherits the parent's assumptions and clutter
along with its knowledge. But a fact the arbiter has reproduced belongs to the
system: it goes into the journal and any later child may cite it through
`accepted_log_reference`. Without that, multi-agent work degenerates into paying
repeatedly for the rediscovery of the same truth.

### Documents are claims too

The same machinery answers a question this repository keeps hitting: how do you
keep a document honest? Not by testing that it contains the word "PLANNED" —
that is a test of text, and it stays green while the text turns into a lie. The
lifecycle document says "verified today" three times with no date, no commit and
no instrument; by this protocol's own rule that is evidence without coordinates.

Instead, a document is decomposed into checkable claims:

```
claim: persistent sub-agent identity is not implemented
check: lifecycle_capability_matrix
```

The arbiter runs the named check against the code and returns a fact. Implement
persistent identity and the check goes red, the claim becomes `verified_false`,
and the document is stale — loudly, on the commit that changed the world rather
than months later during an audit.

Which makes the documentation a client of the same registry as the models. Same
types, same freshness rules, same negative tests.

## Open blockers

Six holes found by arguing with this document rather than extending it. The
first two are load-bearing: **implementation does not start until they are
answered.**

### B1 — nothing proves the check is relevant to the claim (BLOCKING)

A model states "claim X, verify with `named_test A`". The arbiter runs A, A is
green, the claim is accepted. **Nobody established that A tests X.**

This is not hypothetical. Every guard this repository broke on 2026-08-07 was
green and meaningless: a doctrine guard covering three manifests out of four, a
document test asserting the presence of the word "PLANNED", a generator and its
checker never compared at all. Each passed; none verified what it claimed to.

The arbiter's negative tests do not close this. They prove it rejects forgeries,
not that the check has anything to do with the claim. The sub-agent path is
accidentally protected — there the check is fixed by the task contract — but the
core case, two models arguing, is wide open: the model proposes what will judge
it, so a green irrelevant check manufactures a "proof".

Direction, not yet a design:

```
claim_type              what kind of assertion this is
verification_policy_id  which policy governs that kind
allowed_check_ids       the only checks admissible for it
```

`claim_type` is **not** a field of the message. A model free to name its own
type picks the one whose policy allows the weakest check — the same defect, one
storey up. The type is derived deterministically from the claim's SHAPE by a
classifier, not by the arbiter reasoning about meaning: the less semantic
judgement the arbiter carries, the better. A claim whose type cannot be derived
is too vague for automatic verification and goes to a human.

There is no "partially verified". A policy answers one binary question — *is
this check a COMPLETE verification for this type of claim?* If not, the claim is
split. "The function is faster and correct" is two claims:

```
claim_1  behaviour is preserved     check: regression_test
claim_2  the function is faster     check: benchmark
```

If the benchmark never ran, claim_1 may be `verified_true` while claim_2 is
`evidence_unverifiable` — and the compound sentence never receives a green
stamp as a whole. The invariant: **one claim, one meaning, one procedure, one
status.**

Which produces the gate that runs before any verification:

1. is the claim atomic?
2. is its type derivable without the model choosing?
3. does a registered COMPLETE check exist for that type?
4. if not — it is not verified automatically, and a human decides.

So the first stage of an evidence engine is not verification at all. It is
**claim normalisation**: turning one human sentence into a set of small
checkable assertions. Verification is what happens afterwards, to things already
shaped to be checkable.

The mapping `claim_type -> check_id` needs its own negative tests, or the
problem simply moves into the registry.

### B5 — normalisation can lose the inconvenient half, undetectably

Who splits the sentence? Not the deterministic classifier — it recognises the
shape of an already-formed claim, it does not understand meaning. So the model
splits. And a model splitting "faster and correct" may emit one claim, about
correctness, which is easy to prove. The awkward half simply never exists.
Atomic, typed, verified, and quietly incomplete.

Nothing catches this automatically. The partial mitigation is to keep the
original sentence beside its decomposition and never discard it —
`original_claim -> [atomic_claims]` in the journal — so the loss is visible to a
human comparing them. That is a price recorded, not a problem solved.

### B6 — the share of claims that normalise at all is unmeasured

A strict shape is what the classifier needs, but not every useful assertion fits
one. If only a small fraction normalises, the system routes most work to a human
and the benefit evaporates.

This is cheap to measure before anything is built, on material that already
exists: the working sessions in this repository are full of exactly the claims
such a protocol would handle — "this test catches this break", "this path is
test data, not a reference", "the doctrine is resolved from the workspace".
Run those through the intended shape and count how many are atomic, how many
have derivable types, and how many would find a registered complete check.

A dry measurement on real claims answers whether to build this at all, before
the first line of an arbiter is written.

### B2 — shared facts and global invalidation contradict each other (BLOCKING)

Two rules written here fight: *facts the arbiter confirmed belong to the system
and may be cited later*, and *any new commit turns every `verified_true` into
`verified_stale`*.

In active development commits arrive by the dozen per day, so by the time
another sub-agent consults the journal almost everything in it is stale. The
shared resource that `accepted_log_reference` exists for is a warehouse of
expired goods, and every child re-derives the same facts anyway — precisely what
sharing was meant to prevent.

Three honest options; the third is not available yet:

1. code-dependent evidence is reusable **only within one commit** — crude,
   honest, and correct today;
2. a separate class of facts whose validity does not depend on the code at all
   (external, structural) and therefore does not expire with it;
3. a proven dependency graph enabling targeted invalidation — only after the
   graph is computed or instrumentally confirmed, never from a hand-written list.

Start with 1 and 2. Do not pretend a smart invalidation exists.

### B3 — the metrics are noise at small sample sizes

`unsupported_confidence_rate` means something across hundreds of claims. A
sub-agent may have three. One failure in three is 33% or it is nothing.

This repository has already paid for that lesson: procedure confidence is
Beta(1,1)-smoothed precisely because one success used to read as certainty, and
45 of 65 stored procedures sat at a raw 1.0 until they were recomputed.

So: below N confirmed observations, **journal only, change nothing**. Above N,
smoothing rather than raw percentages. Probation runs must respect sample size
too, or one lucky report "cures" an agent.

### B4 — the cost of building this may exceed its value today

On 2026-08-07 roughly ten real defects were found with mutation probes, full
suite runs, link resolution and path-role analysis — with no protocol at all.
The protocol takes months and defends against a problem that does not exist yet:
a second model in the system.

Not an argument against building it. An argument about order: spreading today's
audit method to `core/loop.py` almost certainly pays back sooner than an
arbiter. The specification keeps its value as a design document with the
blockers above stated — a plan that survives is worth more than a system built
on an unanswered question.

## Deliberately out of scope

* Consensus. The protocol records disagreement; it does not resolve it.
* Model self-approval. One model may never approve another's action.
* Autonomy for risky actions. Everything that changes the world — writes,
  sends, publishes, spends, installs — goes to a human, always.
* Quality judgement. The arbiter checks reproducibility, never whether an idea
  is good.

## If this is ever built

The procedural layer — two contours, the objection form, `no_material_objection`,
the challenger's duty regardless of who proposed — requires NO code and can be
practised immediately. It is also the part that worked in the session which
produced this document. Everything below is machinery for keeping the factual
half honest once the conversation is already structured.

Not before B1 and B2 are answered. Suggested order after that, each stage
useful alone:

0. claim normalisation and relevance mapping (B1), plus an invalidation rule
   honest about what it cannot do (B2) — everything below is worthless without
   them, and B6's measurement comes before even that;
1. the journal and message shape, with no verification at all — just structure;
2. two or three evidence types with their reproduction procedures and negative
   tests;
3. the check registry with `read_only`, `timeout`, `allowed_paths`;
4. freshness (commit hash + registry version) and the stale transition;
5. cost accounting and sampled re-verification;
6. the sub-agent report contract — the bridge above, which makes the existing
   lifecycle document enforceable rather than aspirational;
7. only then a second model in the exchange.

Note the order: sub-agents come **before** a second external model. The conflict of
interest is already inside this system — a parent trusting its own child — so
the arbiter earns its keep long before anyone talks to another vendor.

Building the arbiter without a threat model and the negative tests above would
be exactly the kind of new floor this project has spent a week taking apart.

---

## The whole protocol in one sentence

> Evidence is valid only for a specific state of the world, a specific version
> of the verifying instrument, and a procedure that actually finished.

Everything else is unverified, stale, or unreproducible. It is a harsh rule, and
that is precisely why it is honest.
