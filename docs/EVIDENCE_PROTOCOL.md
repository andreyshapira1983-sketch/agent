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
6. only then a second model in the exchange.

Building the arbiter without a threat model and the negative tests above would
be exactly the kind of new floor this project has spent a week taking apart.

---

## The whole protocol in one sentence

> Evidence is valid only for a specific state of the world, a specific version
> of the verifying instrument, and a procedure that actually finished.

Everything else is unverified, stale, or unreproducible. It is a harsh rule, and
that is precisely why it is honest.
