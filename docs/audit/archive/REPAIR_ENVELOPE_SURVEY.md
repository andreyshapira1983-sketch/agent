# What already exists of an autonomous repair envelope — survey of 2026-08-20

A read-only survey, run before anything is built, so that nobody builds a
second copy of a mechanism that is already there. Eight elements, 146
components, each classified by how alive it is rather than by whether a class
with the right name exists.

| Level | Components | Meaning |
|---|---|---|
| `wired_live` | 92 | a real runtime path reaches it |
| `manual_only` | 16 | it works, but only when a human runs a command |
| `absent` | 14 | it does not exist in any form |
| `constructed_not_reached` | 13 | built and instantiated; nothing calls it on a live path |
| `described_only` | 11 | a document describes it; there is no code |

The headline is not the 92. It is that the three lower rows are 38 components
that a reader of the doctrine would assume are working.

## What is genuinely alive

Evidence typing is the strongest area: a 14-value evidence taxonomy with
per-kind confidence, a second coarser class taxonomy that constrains which
verdict a claim may receive, twelve separate per-claim counters each of which
exists because collapsing it into a neighbour produced a named live defect, a
structured reason for refusal rather than a sentence, a three-valued arithmetic
judge whose SILENT value means "this instrument cannot discriminate on this
claim shape", and provenance rules that refuse to let the agent's own earlier
output count as independent support. Fifteen of nineteen components on a live
path.

Authority boundaries are the next strongest: eighteen of twenty-five live.

## The gaps that matter

**Human sovereignty is one-directional.** `core/budget_kill_switch.py` exposes
`clear`, `status` and `engage_if_needed`. There is no `engage`. The switch can
only be turned ON by day-budget exhaustion, and the operator can only turn it
OFF. A running process can still be stopped — `app/daemon.py` has a cooperative
stop and signal handling — but the durable "no effects until I say otherwise"
state that the gateway checks has no operator control. The right is in the
doctrine; the mechanism is asymmetric.

**No before-state in the self-apply lane.** `core/self_apply_lane.py` runs
tests after writing files, and nothing runs them before. There is no by-name
comparison of which test was green before and is red now — the only thing that
distinguishes a regression this change caused from a failure that was already
there. `core/self_build_producer.py` never runs tests at all; it only chooses
which test paths the lane should run afterwards.

**No execution-based proof that an authored test is red before the fix.** The
doctrine names the criterion — a controlled break of this link makes the test
red — in `docs/NERVE_PROTOCOL.ru.md` and in the "a diagnosis earns a test, not
a patch" section of `docs/CODE_NOTES.md`. No code executes it.

**A reflection lesson never reaches the repair lane.** A `focus_area` has no
reader outside `core/reflection.py`. The organ that notices weak spots and the
organ that repairs them are not connected.

**The failure half of a repair outcome never becomes a lesson.** `_finish` in
`core/self_repair.py` logs every result, but rolled_back, low_confidence,
no_changes, blocked and failed do not reach memory. The agent can learn from
its successful repairs and not from its failed ones.

**The daemon's auto-repair does not invoke the repair controller.** On a red
test run `agent_tick.py` files a proposal in the approval inbox; the controller
that would actually run the lane is not called on that path.

**Dead governance surface.** Seven of the twelve `GovernedOperation` members are
unused in production code, and the `tests_passed` argument the governor reads
is never set on the live path — it arrives at its default. `CHANGE_POLICY`,
`ADD_TOOL` and `ENABLE_EXTERNAL_CHANNEL`, the three operations the charter calls
highest-scrutiny, exist as enum members that nothing raises.

**What is absent and should stay absent.** No mechanism mints, issues, rotates
or grants credentials. No outbound write capability exists in any tool. Those
two blanks are the boundary working as intended, and they are recorded here so
that a later reader does not mistake the absence for an oversight.

## What this survey did not look at

Whether any of the live paths is CORRECT — only whether it is reached. Whether
the agent's decisions on those paths are its own rather than prescribed; that is
the separate and larger question of `AUTONOMY_FREEZE.md`. And the survey read
the repair envelope only: planning, memory retrieval and the organisational
machinery were out of scope.
