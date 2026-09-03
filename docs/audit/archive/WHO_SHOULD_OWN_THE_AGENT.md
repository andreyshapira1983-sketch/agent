# Organ, door, or a separate agent — classification of 2026-08-21

Seven places construct an agent. Under the freeze, before anything is built,
each was asked one question: does it need a cognitive instance OF ITS OWN, or a
capability that one agent could provide?

| Site | Verdict |
|---|---|
| queue worker — `agent_tick.py:946` | organ of one agent |
| self-build producer — `agent_tick.py:680` | organ of one agent |
| hygiene — `agent_tick.py:1165` | organ of one agent |
| campaign lane — `agent_tick.py:1352`, `core/campaign.py` | organ of one agent |
| HTTP API — `api/server.py:84` | interface to one agent |
| interactive shell — `cli/app.py:184` | interface to one agent |
| one-shot — `cli/one_shot.py:81` | interface to one agent |

**Genuinely separate agents: none. Diagnostic harnesses: none.** Four organs
and three doors. Consolidation is therefore not a redesign; it is
subordination.

## The mechanism that produced four instances

Every classification arrived at the same counter-argument independently, and it
is the useful finding of this pass. `core/loop_init.py:93` states it in the
code:

> Memory permissions (INSTANCE-scoped, fixed for this agent's life) … set once
> at construction and there is deliberately no per-run API yet.

`core/loop_memory_write.py:109` repeats it for `durable_writes`. So
`with_memory`, `experience_retrieval`, `episodic_replay` and `durable_writes`
can only be expressed by building a different agent. **In this codebase the
instance IS the unit of permission.**

That is why one tick builds three of them. Not because there are three minds —
because there are three permission envelopes, and a constructor call is the
only way the code can say so. The hygiene pass deletes, so it is built narrow.
The self-build producer touches the agent's own source, so it is built narrow.
The unattended drain runs with nobody watching, so it is built narrow. Each is
a containment boundary expressed as a separate object.

The consequence for any future consolidation is precise, and it is a
precondition rather than a design: **subordinating an organ to one host would
silently widen that organ's permissions**, because the host's envelope is
whatever it was constructed with. Two of the classifications named exactly this
as the concrete breakage. A per-run permission envelope has to exist before one
agent can carry organs that hold different rights. Nothing here builds it.

## The counter-arguments, kept because they are load-bearing

Each site was asked for the strongest case against its own verdict. They are
one shape:

- **API** — the only site whose value is being continuously reachable; an
  interface that answers only when a host is up is a worse interface.
- **shell** — the only site that wires a live human into the escalation path,
  so it is the strongest agent in the repo, not the weakest.
- **campaign** — the only lane that owns TIME: it sleeps between cycles and
  carries a wall-clock ceiling.
- **hygiene** — this pass DELETES, and building it narrow is containment by
  construction rather than by policy.
- **self-build producer** — the one production path allowed to propose changes
  to the agent's own source; a deliberately narrow fresh instance is a
  containment boundary.
- **one-shot** — its amnesia is itself a permission envelope.

Every one of them is the same argument: *this instance is how the code
expresses a right*. That is the thing to fix first, and it is why the answer to
"who should own the agent" cannot be settled by choosing a host.

## What this classification does not settle

Which host. `agent_tick.py` is nearest today only because it already owns the
queue, the lock, the heartbeat and the stores — not because its shape is right.
Nothing here says the doors should be deleted rather than subordinated. And no
site was measured against a running system; every verdict is from reading the
code with file and line evidence, which is the appropriate standard for a
classification and not enough for a migration.
