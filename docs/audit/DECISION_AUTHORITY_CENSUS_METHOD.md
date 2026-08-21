# How to count decision authority — the instrument, before the number

Designed 2026-08-21, in a three-way exchange between the operator, this agent
and a second model. **Nothing here is a plan of work and nothing is
authorised.** It is the instrument, written down before it runs, so that the
first number it produces is not another beautiful lie.

Two measurements already exist and prompted it: `_build_queue` admits the
`learn` task with no reference to `self` at all, and the ranking table decides
which of two admissible actions matters more. Two causally measured sites do
not license a statement about the system. That gap — the denominator — is what
the census is for.

## The one rule that shapes everything else

> The audit may prove **who authored a choice**. It may not supply the choice
> that should stand in its place.

A removed developer decision leaves a hole, and a hole asks to be filled —
"temporarily, until the agent can". Every scripted decision in this project was
once exactly such a stopgap. So a hole is recorded as a hole. `cannot decide` is
a more honest state than `pretended to decide, because we quietly decided for
it` — in an experiment whose whole purpose is to tell those apart.

## What counts as a boundary, and the four classes

A decision boundary is a site where developer-written code fixes a choice among
alternatives that are all permitted by the constitution, all within capability,
and all consistent with what has been observed — so that changing it changes
what the agent **does**, without changing what it may do, can do, or can know.

| Class | Changing the value changes | |
|---|---|---|
| Constitution | what is **permitted** | a refusal appears or disappears |
| Capability | what is **possible** | an operation succeeds or fails technically |
| Observation | what is **known** | the picture of the world differs |
| Decision | what is **chosen** | among equally permitted, possible and known |
| Mixed | more than one of the above | requires a split hypothesis |
| Unknown | not yet measured | |

The class is **assigned** by asking which question the site answers, and
**checked** by the causal response. A disagreement between the two is not the
verdict — it is a detector of misclassification. A budget ceiling that leads to
a different choice stays Constitution; it gets flagged as "behaves like a
Decision — check whether a judgement about value is baked into the ceiling".

`Mixed` must never be a polite word for "we did not work it out". It carries an
obligatory split hypothesis: *this boundary conflates observation interpretation
with executive policy, and here is how to separate them.*

## Four counting levels, four different numbers

| Level | What is known |
|---|---|
| site / detector hit | a place was found mechanically |
| candidate boundary | a hypothesis that one effect is separately changeable |
| measured boundary | a differential test proved that separate causal effect |
| classified boundary | after measurement: which of the classes above |

No extrapolation between levels. `120 detector hits` is not `120 decisions`;
`40 candidate boundaries` is not `40 measured`; `20 measured` is not
`20 Agent Decisions` until each is classified. A candidate census is the
denominator **of what the detectors found**, not of how widespread developer
authority is in the system.

Three further columns, because a mechanism's design space and its behaviour are
different facts: **specified** (written in the code), **reachable** (attainable
on this production path), **observed** (actually occurred in a measured window).

## Detector families

**Family A — literals in a selection.** Ordered work lists built from literals,
comparisons against literal numbers that rank, sort keys with literal
tie-breaks, ordered dispatch, literal candidate priorities. Then a reachability
filter: keep what flows into naming, producing or ordering an action.

Family A is deliberately **high-recall and over-inclusive**. It fires on
constitutional ceilings and capability gates too. That is not a fault in the
detector: **specificity belongs to the classifier, not to the detector.**
Narrowing family A so that it stops hitting Constitution would trade real recall
for an imaginary problem.

**Family B — provenance and repertoire closure.** Family A cannot see a decision
encoded as the *absence* of an alternative, and the closed candidate repertoire
is exactly that: which kinds of candidate may exist at all is authored, with no
literal anywhere. Absence is only well defined over a **closed enumerable**
choice space — the walls have to be visible — otherwise the missing actions are
infinite.

Family B splits by axis, and each axis gets its own verdict:

| Axis | Question |
|---|---|
| B1a | which producer channels exist inside the selector at all |
| B1b | which of them a given production caller can reach |
| B1c | who produces the values that caller activates them with |
| B2 | can a value naming the concrete action arise outside the authored set |
| B3 | can persisted or external state introduce a value outside it |
| B4 | can anything register a new producer or action family at runtime |
| B5 | can a value today's code can no longer produce still enter from storage |

**The axes are conjunctive for the global verdict and non-short-circuit for
diagnosis.** One `OPEN` makes "the space is closed" false; it does not make the
lower axes worthless, and they keep being measured to localise the cause.

**Closure is a property of the provenance graph, not of the consumer's shape.**
Reading the consuming module gives the wrong answer in both directions: it can
look open while being closed one module upstream, and look closed while being
fed from an extension. The unit is a flow — from every construction site of the
value, through transformation and persistence, to the choice.

## Falsifying the detector before trusting its number

Positive controls it must find: `_build_queue`, and the ranking table.

**A known-positive it must miss:** the closed candidate repertoire. Testing a
detector only against what it should find is a test that cannot fail. The miss
measures the blind class instead of asserting it.

**Negative controls, for specificity:** a constitutional site whose containment
was causally measured independently and earlier — `policy.blocked_tools`, where
the transition `allow → deny → allow` is on record. The provider-admission gate
in the router is a **candidate** capability control only, until availability and
developer routing policy are separated there. Gold controls must be boring; a
mechanism with a known semantic crack is not a control. The campaign cost cap is
therefore excluded: MIR-116 showed it gates the next cycle rather than the next
spend.

Two traps from earlier instruments here, both paid for: a scanner's false
positives must be sampled by hand **before** the number is published, and the
discovery rule must not be "the first N in source order" — a mutation sweep once
measured a 1776-line module across lines 32–125 and still called it measured.

## Vacancy is a function of a criterion, never a property of the code

An `authority vacancy` is a boundary that is developer-owned where a criterion
says the agent should decide, and no agent-owned mechanism has been shown to
take it. It is not a TODO, not a heuristic to install, not a recommendation.

The census does **not** get to say "this should belong to the agent". It says
"developer-owned; **under criterion C**, vacant". The criterion is an input
supplied by the human, so the same measurement under a different C yields a
different list, and C itself stays explicit and contestable.

Hence every vacancy row carries its criterion, and every criterion carries:

    criterion_id
    criterion_text
    normative_authority   = the operator
    ratified              = yes / no        <- ONLY yes licenses a vacancy
    provenance_note       = where the text came from

`ratified` is deliberately binary. A three-valued `explicit / inferred /
unknown` invites the middle value to license a computation, and `inferred` is
the name of the defect itself, not a weaker form of consent. This is the third
time in one design session that a scale of three smuggled softness into a binary
semantics; the others were the closure axes and the counting levels.

**Textual producer is not normative authority.** A sentence drafted by a model
and then ratified by the operator is his rule in someone else's words; a
sentence he once said in passing is not automatically a constitutional
criterion. Two separate columns — which is `producer != owner`, the lesson this
audit is chasing through the code, applied to the audit itself.

### The current state of C0

`docs/audit/AUTONOMY_FREEZE.md` holds two halves with different provenance.

Inside the block quote, and therefore the operator's own wording: hardcoded code
must not prescribe **workforce, task agenda, organisational topology, or utility
ranking**. Under this half the ranking table is covered directly — utility
ranking is named.

Below it, in ordinary prose, a table elaborates Decision as *what matters now,
which organisation to build, whom to hire or retire, what to study*. **That
table was written by this agent, not quoted**, and its items were put to the
operator one at a time. The register of his answers:

| # | criterion_text | ratified | provenance_note |
|---|---|---|---|
| C0.1 | what matters now — the ordinary executive choice of priority, inside permitted bounds, belongs to the agent | **yes** | operator's own word, 2026-08-22: "первый пункт да". Item wording drafted by the agent; the ratification is the operator's |
| C0.P | see below — interior ownership with two boundary classes | **yes** | operator's own word, 2026-08-22: "Да, подтверждаю", given against the exact text quoted below. Wording drafted by the second model from the operator's explanation; the norm is the operator's |
| C0.2 | what to attend to / what to study, as a separate permission category | superseded | never ratified as a category; replaced by C0.P before an answer — see why below |
| C0.3 | how to organise the work | open | not answered; likely redundant under C0.P, but its formal status is the operator's to set, not derived |
| C0.4 | whether to create and retire sub-agents | open | same |

**Why C0.2 died as a category.** Taking the items one at a time exposed that the
item list itself was quietly rebuilding the defect under audit: a register of
*permitted kinds of activity* — studying here, editing files there — is a
developer-authored corridor with the operator's signature on it. The operator's
actual position replaces the enumeration with a default and two boundary
classes. C0.P, ratified verbatim:

> Агент сам решает, нужно ли ему учиться, чему уделить внимание и что менять в
> собственной рабочей среде. Человек не задаёт для этого исполнительную
> политику. Ограничения существуют только двух видов: сохранение
> жизнеспособности агента и суверенитет человека.

That is: inside its own working space the agent decides — whether to study,
what to attend to, what to rewrite, whether to do nothing, which path to try.
No executive policy is set for this by the human. Constraints exist in exactly
two classes, and both are boundaries rather than permission lists:

    viability      it may change itself, but must not destroy its ability to
                   keep existing or to be restored
    sovereignty    money beyond the granted envelope, rights, accounts, legal
                   acts, other people's resources, irreversible external
                   commitments — the human's territory

A boundary is not a permission list: *do not destroy your ability to recover*
is a boundary; *studying is allowed* need not exist as a grant at all.

**Consequence for the vacancy computation.** Under C0.P vacancy is no longer
computed by matching a boundary against a ratified category. Any
developer-owned executive boundary **interior** to the two boundary classes is
a vacancy candidate — unless measurement shows the site is in fact Constitution
(viability or sovereignty) or Capability. The criterion got shorter and
stricter, and the classifier carries more of the weight: misclassifying a
constitutional gate as interior would now manufacture a vacancy, which is one
more reason the gold controls exist.

Only `ratified = yes` rows parameterise anything. Under the combined criterion
the ranking table is covered twice over — *utility ranking* from the quote and
C0.1 — and the `_build_queue` admission stays covered by *task agenda* from the
quote; under C0.P the subject-of-study sites become computable as candidates,
though whether any is a vacancy still waits on the census procedure itself.

## The order of proof

    A  EXISTENCE       is there a candidate boundary at all
    B  CAUSALITY       does perturbing it change the result
    C  CLASSIFICATION  Constitution / Capability / Observation / Decision / Mixed
    D  OWNERSHIP       if Decision: the agent, or a developer-authored policy
    E  EXPERIENCE      can retained experience change this agent-owned boundary
    F  END-TO-END      does that happen on the production path

`E` is only meaningful after `D`. Otherwise the criterion in the freeze —
*experience changes a later decision* — is satisfied by
`if memory_contains(x): action = y`, which is a developer's mapping from history
to action wearing the costume of learning.

### `E` needs no archaeology, and is blocked by `D` rather than by the record

The missing provenance tempts a much stronger conclusion than it supports, and
this document held it for a while: that `E` cannot be proven at all, because the
history cannot say whose decisions it contains. That is wrong, and the shape of
the error is the same one recorded throughout — *this corpus cannot answer it*
turned into *the question is unanswerable*.

A controlled experiment replaces the archaeology:

    t1   measure that the choice at this boundary is the agent's
         -> retain its result as experience
    t2   present the same choice, with that experience and without it
         -> the decisions differ

Authorship is **established by measurement at t1**, not recovered from a log, so
no old record is consulted and none needs to be built first. `E` therefore does
not wait on a new provenance mechanism. It waits on `D`: the experiment needs a
boundary already shown to be the agent's, and that set currently has no members.
The moment the census finds one, `E` becomes runnable.

What does survive from the provenance gap, and only this: the existing episode
corpus cannot serve as evidence of **whose** experience it holds. So an
experiment must **generate its own experience under measured authorship** rather
than draw on the store.

### Three claims this document previously overstated

**"Autonomy cannot be audited after the fact."** Too broad. What is proven is
that *this* history lacks the record. A system that records decision authorship
from the start is retrospectively auditable. The defect is that the trace was
never written, not that it cannot be.

**"WHO LIVES is meaningless without WHO DECIDES."** Too broad. Whether the same
subject, memory and obligations persist across a restart is separately
investigable and does not depend on decision provenance. Only the narrower claim
does: *it remembers its own biography*. Two questions, kept apart —
`WHO LIVES` and `WHOSE EXPERIENCE IS THIS`.

**A caution in the other direction, for whenever a provenance record is built.**
Such a record recovers **authorship**, never **latitude**. An entry reading
"the agent decided" is fully compatible with the line having been written by
`priority_table.pick()`. The counterfactual — could it have chosen otherwise —
does not fit in a log at all; it needs perturbation. So an honest-looking
authorship journal would close the first question and leave the second exactly
where it is.

### Three ways to get a false green on learning

    1  developer semantics    memory X -> hardcoded action Y
    2  developer arbitration  state -> fixed priority table -> action
    3  human-deferred authorship
       human chooses at t1 -> episode stored WITHOUT decision-author provenance
       -> memory changes the output at t2 -> the test reads "experience changed
       a later decision", while the causal chain contains a human choice

The third is the worst, because the deliberation at `t2` may be genuinely the
agent's; it is the *experience* it reasons over that carries an unlabelled
foreign executive decision. Kin to MIR-115, with missing provenance in place of
laundering through metadata.

Hence a further instrument invariant:

> A historical episode can prove that memory **influenced** an outcome. By
> itself it cannot prove **whose** experience influenced it.

This does not contaminate levels A–D, which prove ownership by perturbing the
boundary itself. It bites `E`, retrospective attribution, and every claim of the
form "this is the agent's own experience".

## Provenance is five axes, not one word

"Who decided" hid five separate facts. The inspected surfaces on 2026-08-21
were `EpisodeRecord`, the approval inbox row, the review-outcome row and the
approval receipt. **The trace and log graph was not walked**, so every "not
recoverable" below is scoped to those surfaces and never to the system.

**Every status cell carries its scope.** A bare yes/no column has nowhere to put
"not checked at this scope", and the first version of this table duly turned
*absent on three surfaces* into *absent*. The format produced the overclaim, so
the format carries the fix.

| Axis | Question | Recoverable, and where |
|---|---|---|
| request origin | which subsystem formed the request | yes on the inbox row — but a code-fixed constant, see below |
| executive authorship | who judged this move worth making | **no on the inspected surfaces**; system-wide UNKNOWN |
| evidence origin | who supplied the grounds it was judged on | **no on the inspected surfaces**; system-wide UNKNOWN |
| review actor | who actually approved or denied this request | **no on the inspected surfaces**; system-wide UNKNOWN |
| execution | whether the move actually ran | **no — see below**; the inbox row carries a lifecycle token, not an execution proof |

`review actor` is deliberately not called `review authority`. Authority is who
is *entitled* to permit a crossing, and that may be settled by the constitution
whether or not anything records the actor. What is missing is the actor. In an
audit about authority the wrong word here would manufacture a false finding.

### `executed` is four different facts wearing one word

`mark_executed()` is `set_status(item_id, "executed")` and verifies nothing. Its
four production callers each reach it after something different:

| Call site | What precedes it | What the token then means |
|---|---|---|
| `core/autonomous_runtime.py:524` | the config is flipped to `effects_approved` and nothing has run | **a one-shot permission was consumed** |
| `cli/commands_approval.py:502` | `report.status == "completed"` | the runtime run finished |
| `cli/commands_approval.py:579` | a subagent result with an execution receipt | the subagent finished |
| `core/self_apply_bridge.py:401` | a terminal lane status | the lane ended — and `rolled_back` is terminal, so **the work was undone** |

None of this is a bug: the one-shot grant is deliberately consumed on use, while
the standing grant is deliberately not marked at all, and transient lane
refusals deliberately leave the item retryable. The defect is in reading the
token, not in writing it.

`executed` is one token over four distinct facts, and it establishes only the
first:

    approval lifecycle    was the approval consumed or closed   RECOVERABLE
    actuation             did execution actually begin          producer-specific
    effect outcome        did the effect persist, or roll back  producer-specific
    goal completion       did the wider work succeed            producer-specific

`rolled_back` is a counterexample to *the effect persisted*, not to *execution
occurred* — the lane ran and was then undone. The counterexample to actuation is
`autonomous_runtime.py:524`, where the token is written before the queue is even
built.

**And the same operation carries both semantics.** `cli/commands_approval.py`
refuses anything but `autonomous_runtime.allow_effects` on its path and marks
the item executed only after `report.status == "completed"`; the runtime marks
the identical operation executed before any work starts. So `operation + status`
does not recover one causal meaning either — knowing *what* was requested and
*what state it reached* is still not enough.

This is MIR-116 one level over: there a mechanism named `ceiling` did not mean
"do not exceed the ceiling"; here a status named `executed` does not, by
itself, prove downstream actuation, a persisting effect, or goal completion.

**So the instrument needs three things, and each was found by the previous one
failing.** Every verdict carries its **scope**. Every shared enum read as
evidence carries its **meaning per producer**. And — because one operation was
found reaching the same write by two paths with opposite temporal semantics —
the unit of meaning is sometimes neither the producer nor the operation but the
**causal write path**: not only who wrote the token, but which event licensed
that particular write.

A run can therefore read `request origin = autonomous_runtime`,
`verdict = approved`, `status = executed` and still answer none of the four
questions underneath it: who judged the move worth making, on what grounds, who
permitted it, and whether it ran. `autonomous_runtime requested it` does not
mean the agent chose it; `approved` does not name who approved; and `executed`
does not by itself prove actuation, a persisting effect, or completion.

**Request origin is fixed by code, not merely constant in the data.**
`ApprovalInboxItem.requested_by` carries the default `"autonomous_runtime"` and
`ApprovalInbox.add()` has no parameter for it, so every item written on that path
takes the default. `from_dict()` accepts an arbitrary persisted string, so the
persisted surface is wider than the current producer — the familiar shape:
current producer repertoire of one, persistent acceptance unbounded. Measured:
137 of 137 rows in the live inbox, `observed = one`.

**Reviewer identity is absent on all three durable surfaces, proven from code.**
`ApprovalInboxItem` has no reviewer field; `approve()` and `deny()` take a
`reason` and no actor; the separate review channel written by `_record_outcome()`
to `data/approval_outcomes.jsonl` carries ts, id, operation, summary, verdict and
reason — no actor; the approval receipt carries trace, path, operation,
fingerprints and approval id — no actor. Other CLI and log surfaces were not
walked, so system-wide recoverability stays UNKNOWN.

Two properties of that review channel matter for reading it later. It is
deliberately **best-effort** — the write is wrapped and a failure never blocks
the verdict — so a row proves a review happened while a missing row proves
nothing. And lifecycle transitions are deliberately excluded as plumbing, so it
holds verdicts only, not executions.

**The evidence axis is the one this audit added.** The intervention rule below
turns on the difference between a human supplying a fact and a human supplying
the answer. Both look identical from outside — *after human input the agent did
X* — while the authority differs completely. On the surfaces inspected here,
nothing records where the grounds came from, so the rule cannot be audited on
them even when it is followed perfectly. Whether some other surface preserves
the distinction is UNKNOWN and was not checked.

**On the episode window.** `max_episodes = 200` is a nominal target, not a hard
cap: episodes carrying a protected tag are never evicted, so a store can stay
above 200 if protected history exceeds it. The live count of 200 is a local
measurement and must not be read as the whole history, nor as a ceiling the code
guarantees.

## Human intervention has kinds, and only one of them takes authority

Supplying a resource, a permission, an inaccessible fact or a clarification of
an ambiguous goal leaves the executive choice with the agent. Being told which
ordinary next move to make does not — that is a human executive intervention,
legitimate but to be named as one rather than counted as autonomous work.

> At an impasse the human should remove the cause of the impasse, not substitute
> for the decision.

Money is an envelope, not a veto per transaction: the human sets the ceiling,
the agent spends inside it. The same shape covers concurrency. Worker count is
**not** authority scope — four workers with identical rights do not widen the
permitted action set; they amplify throughput, resource draw and blast radius
over time. Blast radius is a safety property, so the bound stays constitutional,
but for that reason and not because a helper grants a new right.

## What has been measured so far, with its status

| Fact | Status |
|---|---|
| `_build_queue` admits `learn` with zero references to `self` | measured, committed |
| the ranking table decides between two admissible actions | measured, committed |
| `LLMPlanner` exists and produces reasoning plus steps | candidate deliberative capability; ownership not measured |
| durable issue action repertoire: 2 authored, 1 realized | code plus local measurement |
| persisted-state ingress for that action is structurally open | proven from code |
| no value outside the current repertoire in the live store | local measurement only; the store is not in the repository |
| the episode record has no decision-author field | proven from code |
| authorship recoverable from an episode row | no — proven |
| `requested_by` is fixed by the current producer path (dataclass default, no `add()` parameter) | proven from code |
| the live inbox carries that one value in 137 of 137 rows | local measurement |
| authorship recoverable elsewhere in the trace graph | UNKNOWN — the graph was not walked |
| the runtime was active during the freeze | local measurement; **not** continuity of one subject |
| known proven Agent Decision boundaries | 0 |
| system-wide Agent Decision cardinality | UNKNOWN |

`known proven = 0` is not `deliberative capability = 0`. These are different
sets, and the second may well be non-empty while no boundary hands it authority.
Possibly the central diagnosis of the project: not *a dumb script that now needs
a brain*, but *a brain that was there, with rails in front of it and the points
kept by the human* — if measurement confirms that picture.

Nothing above licenses a repair. If the census returns zero, the number does not
instruct anyone to create one; what to do then belongs to the operator.

## The cost of the freeze, kept as its own number

The freeze reduces the risk of building further on a false architecture. It also
costs. The runtime still ticks roughly once a day in dry-run with effects
disabled, stopping after one cycle, so what does not accumulate is the sequence
*decision → real effect → changed world → observed consequence → adaptation*.
Some outcomes remain genuine — a plan parses or fails, a verifier accepts or
rejects, a gate stops a cycle. What is missing is the consequence of effectful
action, and causal learning about one's own effects cannot be grounded on that
material.

The decline in campaign activity began before the freeze was declared, so it is
not attributed to it.

Neither number cancels the other, and neither sets a date. The subject of the
research is a working autonomous agent, not a classifier with forty-seven
columns.
