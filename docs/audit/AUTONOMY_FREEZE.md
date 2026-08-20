# Architectural freeze — the decision subject is wrong

**This is now the blocking architectural issue of the project. All other
development is subordinate to it.**

Declared by the operator on 2026-08-20. Nothing new is to be built — no new
features, subsystems, roles, agents, memory mechanisms, self-build capabilities,
planning mechanisms or organisational abstractions — until the architecture of
autonomy is corrected.

## Why

The system was designed as an autonomous agent. Over months of development a
large share of its **executive decisions** was written into the code by humans
and by LLM developers: which tasks to run, in what order, which roles exist,
what matters more than what, what to study first, when to improve itself. The
result is a mixture of autonomous decision-making and hidden scripted
behaviour.

Building further on that mixture only adds to what will have to be untangled.
A better memory would remember imposed decisions more accurately; better
subagents would execute an imposed organisation more efficiently; better
self-repair would more efficiently maintain a system still travelling an
imposed trajectory.

## The invariant this freeze exists to restore

> Hardcoded code may define safety constraints and generic capabilities, but
> must not prescribe the director's workforce, task agenda, organisational
> topology, or utility ranking. Every such choice must be traceable to the
> director's own deliberation, evidence and retained experience.

Three classes have been conflated in the code, and separating them is the work:

| Class | Who sets it | Example |
|---|---|---|
| **Constitution** | the human | kill switch, no self-issued credentials, budget ceiling, no widening its own rights |
| **Capability** | the human, in advance | a tool that can spawn a sandboxed worker, a memory API, a rollback mechanism |
| **Decision** | the agent | what matters now, which organisation to build, whom to hire or retire, what to study |

## Verified sites where code decides instead of the agent

Read from the code on 2026-08-20, not from a summary. Each line was confirmed
in the file named.

| Site | What the code decides for the agent |
|---|---|
| `core/best_next_action.py:36` | a utility function written as numbers — daemon down 100, tick error 90, failing tests 80, engineering task 59, doctrine document 58, external study 57, self-improvement failure 55, inbox debt 50. The largest weight wins |
| `core/autonomous_runtime.py:1400` | the autonomous queue always opens with two prescribed tasks — inspect state, then plan and dry-run ingest — before any judgement is made about what is worth doing |
| `core/learning_planner.py:181` | attention has a fixed price list (100 / 95 / 70 / 55 / 40) and `core/learning_planner.py:366` holds a hardcoded set of core files to study when no focus is given |
| `core/subagent_registry.py:50` | five roles exist by default. The registry recommends keep / watch / pause / retire and never hires or fires |
| `core/team_plan.py:233` | a second, separate predefined workforce built from keywords, in which one watcher is added whenever a team is deemed necessary at all |
| `core/autonomous_runtime.py:74` | `spawn_subagent` is blocked on the unattended goal path, alongside the network tools. Roles a human wrote in advance may run; a worker the agent decides it needs may not be created |
| `core/subagent_runner.py:380` | a subagent is a one-shot loop with `memory=None` and `persistent_store=None`. A temporary executor can be created; an employee who accumulates experience cannot |
| `core/charter_goal.py:14` | the agent picks its campaign goal itself, but the goal must anchor to a verbatim line of a document a human wrote. A one-time human push was replaced by a permanent human push stored in a file |
| `agent_tick.py:122` | on the unattended path `with_memory=False`, `episodic_replay=False`, and durable writes are restricted to a two-entry allowlist |

The pattern is not five imposed subagents. It is a layer of
developer-written heuristics sitting between the mission and the agent's
decisions, in at least nine separate places.

## What is permitted during the freeze

Only work directly needed to:

- find every place where code decides instead of the agent;
- classify each such mechanism as Constitution, Capability, or Agent Decision;
- remove or convert hidden executive decisions;
- restore one closed autonomous decision loop;
- wire memory and experience into subsequent decisions;
- prove that tasks, goals, roles and organisational structure are actually
  derived by the agent from observed state, mission and accumulated experience;
- preserve human sovereignty, safety boundaries, budget limits and the ban on
  self-widening authority;
- test and prove those properties.

Fixing an existing mechanism is allowed: it may be corrected, deleted,
simplified or rewired. Compensating for the problem by adding another planner,
manager, controller, memory layer, agent role or orchestration module is not.
A new abstraction is admissible only when a specific proven defect cannot be
fixed without it, and that must be argued separately.

## What lifts the freeze

Not "the code looks more autonomous", and not a green suite. Only an
end-to-end demonstration:

    observe state -> retrieve relevant experience -> identify gap or
    opportunity -> form candidate goals -> choose action -> choose or create
    the required capability or agent -> execute within authority -> verify
    outcome -> record experience -> the experience changes a later decision

And within that loop, the provenance of every significant decision must be
answerable: who decided this, and on what grounds? If the answer is a hidden
default, a hardcoded role, a fixed agenda, a fixed priority table, a charter
instruction or any other pre-written executive choice, that stretch is not yet
fixed.

One further condition, easy to lose: on a fresh state the agent is under no
obligation to do anything. It must be able to conclude **no justified action**
after observing, instead of performing activity that was arranged in advance
for the sake of activity.

Until these conditions are met the autonomous mode is not to be started as a
working mode, and nothing beyond this task is to be built.
