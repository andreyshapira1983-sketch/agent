# Evidence Protocol — how several models may argue without lying to each other

> **Status: SPECIFICATION. Nothing here is built.** No module implements it, no
> test pins it, no part of the running agent behaves this way. It exists so the
> design is not re-derived from scratch later. Read every sentence below as
> "this is what we would build", never as "this is what happens".
>
> Written 2026-08-07 from a working session between the operator and the agent.
> Superseded only by a document that says so explicitly.

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

## Roles, defined by what they may NOT do

Asymmetry is the whole design. Equal participants converge; unequal ones cannot.

| role | may | may not |
|---|---|---|
| **Proposer** (model) | state one claim with evidence | execute anything |
| **Challenger** (model) | confirm, refute, or name what is missing | offer its own solution in the same round |
| **Executor** (the agent) | perform work that is already verified and approved | take part in the argument |
| **Arbiter** (code, not a model) | reproduce evidence, count rounds, stop the exchange | reason, judge quality, be persuaded |

The arbiter is a program. If a model arbitrates, the other two will eventually
talk it round — that is what models are good at.

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
recorded BEFORE the spend — the same discipline that fixes checks in the task
contract in advance. An explanation after the event is a rationalisation, not a
choice.

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

## Deliberately out of scope

* Consensus. The protocol records disagreement; it does not resolve it.
* Model self-approval. One model may never approve another's action.
* Autonomy for risky actions. Everything that changes the world — writes,
  sends, publishes, spends, installs — goes to a human, always.
* Quality judgement. The arbiter checks reproducibility, never whether an idea
  is good.

## If this is ever built

Suggested order, each stage useful alone:

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
