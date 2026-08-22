# The three Saturday decisions — prepared 2026-08-22

WHO NEEDS THIS. The operator, before the week-long unattended run planned for
Saturday 2026-08-23 evening. Each of the three is a decision only he can make
— none is a code repair, and the code side of each is already done or ready.
Facts below are measured on this workspace today, not recalled.

**A deadline discovered while writing this:** the standing grant that powered
the 2026-08-16 run **expires 2026-08-23 at 16:34 UTC** — during launch day.
Whatever is decided in §3, a fresh grant must be issued at launch, or the
autonomous runs stop that afternoon regardless of everything else.

---

## Decision 1 — the launch mechanism (nothing starts the agent today)

**Measured state.** Of 201 scheduled tasks on this host, none runs the agent:
`AutonomousAgentTick` was disabled by its own kill-switch task on 2026-07-06
and no longer exists; `AgentCharterCampaign` exists and is Disabled. The
half-hourly life described in the docs is the *designed* one, not the current
one.

**The instrument is ready.** `scripts/install_daemon.ps1` registers
`AutonomousAgentTick`:

    # dry-run (reads, plans, proposes; no file effects), every 30 min:
    .\scripts\install_daemon.ps1

    # real effects, every 30 min:
    .\scripts\install_daemon.ps1 -AllowEffects

    # remove:
    .\scripts\install_daemon.ps1 -Uninstall

**The sub-choices inside this decision:**

| choice | default | note |
|---|---|---|
| interval | 30 min | 48 ticks/day; the standing grant separately caps LLM-bearing runs (12/day on the old grant), so most ticks are cheap queue/hygiene passes |
| dry-run vs effects | dry-run | `-AllowEffects` is what makes the week real; without it the run produces proposals and reports only |
| a stop-switch | none | the 07-06 pattern (a second task that disables the first after N hours) is available; the week already has three independent brakes — grant expiry, daily budget caps, `:budget-kill-switch` |

**What the repairs of 2026-08-22 changed here:** a budget-exhausted task now
returns to the queue by itself (3 per tick, oldest first), the tick sweeps its
own duplicate episodes, and the router skips a provider whose balance is
empty. The failure mode that ended the 08-16 run on day three is closed.

**Only the operator can do this:** registering the task is an action on his
machine. The agent side is complete.

---

## Decision 2 — a money wall outside the process

**Why it is a decision and not a repair.** Every wall in this repository is
in-process Python (MIR-120): the budget windows, the cost envelope, the kill
switch all run in the same interpreter as the agent, under the same OS user,
with the API key in the same environment. For a week alone with a funded key,
the money wall must stand where the agent cannot reach it — on the provider's
side.

**Measured state of the inner walls (they exist and are on):**

    hour window: 400 LLM calls / 4M tokens / 3000 cost units
    day window: 2000 LLM calls / 20M tokens / 15000 cost units
    kill switch: engages when the autonomous day budget is exhausted
    scale: the full working day of 2026-08-15 cost 158 calls / 618k tokens

**What only the operator can do:** set a **Hard Spend Limit** in the OpenAI
dashboard (platform.openai.com → Settings → Limits; per-project limits exist,
so the agent's key can be capped separately from any other use). At the cap
the API returns 429 and everything stops — enforcement is the provider's, not
ours. Two cautions from the external check (MIR-120/124): enforcement is not
instantaneous, so the cap is a ceiling, not a meter; and the cap should be a
number he is prepared to lose entirely to a defect, not a number he hopes is
enough.

**The other two providers need nothing:** Anthropic and DeepSeek are unfunded,
which is itself a provider-side wall; the router now treats an empty balance
as unhealthy instead of retrying it 391 times. The standing instruction —
DeepSeek stays off until the operator says the balance is topped up — is
unchanged.

---

## Decision 3 — what the agent may do with approvals for a week

**Measured mechanics.** There is no outbound channel (no Telegram), so the
agent cannot ask mid-week. An approval item self-aborts after 24 hours
(`expire_stale`, by design, so the queue does not rot). The standing-grant
mechanism is built and proven: per-day run cap, expiry, usage journal,
consumed one run at a time; §9 rights sit above it and the kill switch beats
it. The 2026-08-16 grant read: «до 12 прогонов в сутки, 7 дней, только
чтение+генерация+предложения в очередь».

**The three shapes on offer:**

**(a) Observe-and-propose — the 08-16 shape, renewed.** Runs are allowed;
self-build files proposals; nothing is ever applied. Cost: the week produces
reports, memory, and a queue of expired proposals to review on return (about
one per day — the measured throughput ceiling). Risk: lowest. This is the
shape the previous grant chose.

**(b) The same, plus effects for non-code work.** `-AllowEffects` with the
grant worded to cover memory, hygiene, reports and queue work — but any change
to `core/`, `tools/`, `cli/`, `app/` still only queues a proposal. Cost: the
run can actually maintain itself for a week. Risk: moderate; the effects path
is bounded by the existing tool walls.

**(c) A named class of self-apply.** A grant covering, e.g., low-risk splits
that pass the full battery, with automatic rollback. Risk: highest, and two
open findings argue against it for the FIRST week: MIR-119 (no independent
judge of «better» — the critic and the builder share a brain) and MIR-120
(the walls are in-process, and a self-editing agent is the exact case they do
not hold). Recommendation, stated once and not pressed: not this week; let
the first week measure.

**Whichever shape is chosen, two acts are needed at launch:** issue the fresh
grant (the old one dies Saturday afternoon), and word it to the chosen shape.
The grant text is the decision; the mechanism is ready.

---

## The launch-evening checklist, in order

1. OpenAI dashboard: Hard Spend Limit set (Decision 2).
2. Fresh standing grant issued, worded to the chosen shape (Decision 3).
3. `.\scripts\install_daemon.ps1` with or without `-AllowEffects` (Decision 1).
4. `python agent_tick.py --status` once, to see the first tick's world.
5. Walk away.

What the week will and will not prove is already banked: the only prior
unattended evidence is a two-day run that died of a now-repaired cause.
Whether a different limit appears on day four is unknown, because nothing here
has ever run that long — that is what the week is for.
