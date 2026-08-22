# Techniques from the field, mapped against what we already have — 2026-08-22

Searched on the operator's instruction, with his framing: not "what resembles
us" but "what can we take and use", and his warning that the internet is a
source of guesses, never of truth. Every guess below is checked against our own
code before it is called a gap.

The result was not what I expected: two of the three techniques the field is
excited about are **already here**, one in a stronger form than the papers
describe. The genuinely missing one is a concept we had never named.

## 1. Reasoning monitoring — WE ALREADY HAVE IT, and better

**The field's finding.** OpenAI compared an *action monitor* (sees only the
agent's commands and patches) against a *chain-of-thought monitor* (sees the
reasoning too). The CoT monitor has "very high, but not perfect, recall" on
reward hacking and "far outperforms action-only monitors" — the intent to cheat
is detectable in the reasoning before it reaches the action. DeepMind runs an
asynchronous monitor over coding-agent trajectories that escalates for human
review.

**What we have:** `core/reasoning_action_check.py`, comparing the planner's
free-text `reasoning` against the chosen `steps`, surfacing two shapes —
`unjustified_action` (a step uses a tool the reasoning never argued for) and
`mentioned_but_not_planned` (the reasoning argues for a tool the plan omits).

**And the part that is stronger than the papers.** Our detector is deliberately
**observational, and the reason is a measurement**: over 268 real turns
carrying both a reasoning text and a plan, it fires on 190. A monitor that
fires on 71% of ordinary turns cannot be allowed to block, and we knew that
because we counted. The field's write-ups describe monitors as if precision
were a detail.

**The one caution to carry forward**, from OpenAI's own paper: optimising the
agent *against* a reasoning monitor makes the reasoning opaque while the
behaviour persists. Any future move from observation to enforcement has to
answer that, not just the false-positive rate.

## 2. Stuck detection — WE HAVE THE CORE, and one granularity gap

**The field's practice.** A hard step ceiling plus no-progress detection;
action de-duplication by hashing each `(tool, args)` pair; oscillation
detection when state has not changed in k steps. OpenHands ships a
`StuckDetector` with five distinct stagnation patterns. Documented cost of
getting it wrong: one agent called the same broken tool 400 times in five
minutes; $50–500 burned before anyone notices.

**What we have:** cycle ceilings, wall-clock ceiling, `max_idle_streak`,
`unproductive_streak`, a circuit breaker, repeat-skip via
`attempted_signatures`, and since 2026-08-22 a cost envelope that refuses at
the moment of spend.

**The gap, and it is real but narrow:** our repeat-skip keys on the ACTION NAME
(`signature = action.action`), not on `(tool, arguments)`. Two genuinely
different pieces of work that share an action name collapse into one "repeat";
two identical tool calls under different action names do not. The field keys on
the argument tuple. Not fixed here — recorded so the next loop change knows the
axis exists.

## 3. Agentic abstention — GENUINELY MISSING, and we had never named it

This is the one worth the search.

**The concept.** Deciding when an agent should **stop instead of act** under
uncertainty. Unlike ordinary "I don't know" abstention, this is a *sequential*
problem: at each turn the agent may answer, abstain, or gather more
information, and the need to abstain often becomes clear only after it has
started.

**The measurements, from `AgentAbstain` (arXiv 2607.10059), 17 frontier models
across 4 agent frameworks:**

- best result, Gemini 3.1 Pro: **59.5% paired accuracy** on act/abstain pairs;
- **abstention capability is largely independent of general task-solving
  capability** — a stronger model does not abstain better, so this cannot be
  waited out;
- the named failure mode is **post-hoc abstention**: the agent executes an
  irreversible action and only then recognises it should have abstained.

**Why it lands on us specifically.** Our whole approval architecture answers
"may this action cross a boundary". Abstention answers a different question:
"should this action happen at all, given what I do not know". We have gates for
permission and nothing for uncertainty. The field's own recommendation —
"abstention should trigger retrieval, tool use, or escalation rather than a
forced best guess" — is a behaviour our agent has no way to express: it can
succeed, fail, or be blocked, but it cannot say *I should not proceed on what I
know, and here is what I need*.

The nearest thing we do have is the clarification gate (`clarification_for_
replan_exhausted`), which fires when replanning is exhausted — that is
abstention by exhaustion, arrived at after the attempts, not before them.

**Not proposed as work.** Under the standing rule this is a hole, recorded as a
hole: a mechanism for "stop and ask because of what I do not know" would be a
new capability, and whether the agent should have it — and who decides when it
fires — is the operator's call, not an inference from a benchmark.

## 4. Recovery differentiated by stuck type — missing, and probably minor

The field suggests reacting differently by stagnation shape: change approach
for a repeater, reassess the goal for a wanderer, escalate to a human after N
failed recoveries. We stop the campaign and report; we do not differentiate.
Recorded, but low value against the others: our stop is cheap and honest, and a
differentiated recovery is exactly the kind of developer-authored policy the
freeze is suspicious of.

## What the search did NOT change

The four repairs already made — credentials out of reach, drained ≠ done,
lesson waives failure not provenance, the preview admits what it hid — remain
the right four, and nothing in this sweep contradicts them. The techniques above
are additions to consider, not corrections.

And the operator's caution held: three of the four items here were already
solved in this repository, in one case with a number the field's own write-ups
do not bother to measure. Reading the internet told us what to look for; only
reading our own code told us what was true.
