# Who owns the agent's life — map of 2026-08-20

A read-only map of the four places that construct an agent, made under the
architectural freeze so that a later decision can pick which existing mechanism
should host one logical subject. Nothing here proposes a runtime.

## The short answer

| Site | Restart recovers the same subject? | Role it could play |
|---|---|---|
| `agent_tick.py` | partially | closest to a host |
| `api/server.py` | partially | could be subordinated |
| `cli/app.py` | partially | could be subordinated |
| `cli/one_shot.py` | **starts fresh** | could be subordinated |

## There is no canonical agent identity at all

**Corrected 2026-08-21.** An earlier version of this section said identity is
"minted, never loaded" and that the subject's name is a random hex string. That
phrasing invites exactly the wrong fix — persisting the hex — so it is replaced
by the accurate statement.

Only TRANSIENT identifiers exist. `app/bootstrap.py:103` calls
`new_trace_id()` on every build and `core/ids.py:18` makes it from
`secrets.token_hex(16)` with no disk read; working memory mints a session id
independently. **That is correct behaviour**: a trace, a session, a run and a
cycle each name one episode of activity and must keep changing, or events of
one life stop being distinguishable.

The defect is the layer ABOVE them. Nothing on disk says which agent those
traces belonged to, because no such notion exists — no agent name, no
persistent agent id, no role record. So a build has nothing to continue, not
because the wrong thing is being minted but because the thing that would be
continuous was never defined.

    Agent ID   — WHO lived.  Absent.
    trace / session / run / cycle — WHAT it did at a moment. Present, and
                                    rightly regenerated every time.

The `session_id` logged at session start on the unattended path is literally
`None`, because working memory is off there — a second symptom of the same
absence, not a second identity to preserve.

## It is not one life per thirty minutes

That was the earlier reading and it was too generous. **One tick constructs up
to three separate agents** — the task-drain agent (`agent_tick.py:946`), the
self-build producer agent (`agent_tick.py:680`), and the hygiene agent
(`agent_tick.py:1165`) — each with its own trace id and its own independent
load of every store. The campaign lane builds a fourth (`agent_tick.py:1352`).
The comment at `agent_tick.py:100` says the memory profile is defined once
"because all three build sites below must stay identical", which is an
admission that identity here is a *shape* that must be kept consistent, not a
subject that exists.

## The same agent remembers differently depending on which door was opened

`cli/app.py` and `api/server.py` may write all nine memory sinks. The
unattended path may write two — `agent_tick.py:125`. So what the agent
accumulates is a function of the entrance used, not of what happened to it.

Working memory is per-process everywhere it exists at all, and nothing
serialises it. Two shells, or a shell and the API, hold different conversations
with no shared history; there is no cross-process view of turns.

## Concurrency: what is guarded and what is not

Two of any of these can run at once. Only one overlap is guarded, and only
partly: `SingleInstanceLock` is instantiated in exactly one production place,
`agent_tick.py:903`, and it protects the queue drain. `cli/app.py`,
`cli/one_shot.py` and `api/server.py` never take it.

**A finding that had to be corrected.** The map first recorded that the
scheduler fires at `agent_tick.py:891`, before the lock is taken at `:903`, and
concluded that two concurrent ticks would both enqueue due schedules. The
ordering is real; the consequence is not. `SchedulerStore.tick` does its whole
load-enqueue-advance-save under its own `exclusive_file_lock`
(`core/scheduler.py:225`), so a second tick blocks, then finds nothing due. The
scheduler defends itself one layer below the daemon lock. Recorded here because
an unverified consequence would have become a phantom defect.

## What survives a process, and what does not

Survives: the tick narrative log, the heartbeat file, the self-build cooldown
timestamp, the task queue and schedules, the approval inbox, incidents, the
budget ledger and kill-switch latch, model usage, and quarantined episodes. All
of it is read back by the next process.

Does not survive: the identity, and therefore the correlation between
everything one process did. All working memory and the whole dialogue. Most of
the tick's own `summary` — `error`, `schedules_due`, `tasks_enqueued`,
`clarification` and `repair_proposed` reach the append-only log but not the
heartbeat, and the heartbeat is what the next process actually reads at
decision time. The campaign's set of already-attempted signatures
(`core/campaign.py:85`), so a restarted campaign re-attempts what the previous
one tried.

## What this map does not settle

Which mechanism *should* host the subject. `agent_tick.py` is nearest today
only in the sense that it already owns the queue, the lock, the heartbeat and
the durable stores — not because its shape is right. Nothing here says the
others should be deleted rather than subordinated or demoted to diagnostic
harnesses; that is the decision the freeze exists to make deliberately.
