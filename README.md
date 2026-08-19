# Agent

An autonomous, self-diagnosing research agent that answers with **verified
evidence**, records its own defects, and — under explicit human approval —
proposes repairs to its own code.

The design bet of this repository: an agent should become *harder to deceive*,
not merely more confident — including deception by its own past conclusions.
Every mechanism below exists because a live failure demanded it; the history of
those failures and their measurements lives in [docs/CODE_NOTES.md](docs/CODE_NOTES.md).

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env      # ships AGENT_PROVIDER=mock — runs offline, no keys
python main.py            # interactive REPL (type :help for commands)
python agent_tick.py --status    # is the daemon alive, what awaits approval
python agent_tick.py             # one bounded autonomous tick (dry-run)
```

Without a `.env` the code's own default provider is `anthropic`, so the first
command needs a key; the template above keeps a fresh clone offline until you
choose otherwise.

Requires Python 3.11+. Model keys via `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`;
the router fails over between credentialed providers, keeps the capability
tier across the switch, prefers models by **measured outcome** on this agent's
own runs, and every answer produced by a substitute model says so in its tail.

## The loop

```
Observe → Interpret → Plan → Act → Verify → Respond
```

Answers cite their sources inline; a verifier checks every claim against the
evidence chain actually collected that turn. Unverified claims are labelled,
fabricated citations kill the answer, and a claim of *absence* can be refuted
by evidence but never certified by a truncated excerpt. Detectors
(`reasoning_action_mismatch`, `citation_fabricated`, `self_contradiction`, …)
feed causal observations, reflection lessons, and a durable issue registry —
the raw material for self-repair.

Memory is three-fold and file-based (`data/*.jsonl`): episodic (what
happened), procedural (what worked), persistent (what to keep). Procedures are
offered to the planner and earn promotion only through causally credited use.

## Autonomy, gated

Everything effectful is closed by default and opens one gate at a time:

* **Dry-run first.** The autonomous runtime simulates effects until a human
  approves a *single-use, per-goal* grant through the approval inbox
  (`:approval-list`, `:approval-approve`). Consumed grants are marked executed;
  yesterday's "yes" never covers today's goal.
* **Policy by call, not by tool.** `file_write` to a new path is reversible;
  over an existing file it is irreversible and escalates to a human. The
  unattended path additionally blocks network egress and subagent spawning.
* **VCS cannot push.** `core/safe_vcs.py` has no push/fetch/remote at all.
  The self-apply lane works on a temp branch, runs the full test suite, rolls
  back on red, and commits locally on green — merging stays human.
* **Budgets and the kill switch.** Hour/day windows in
  `config/budget_limits.json`; `:budget-status` shows spend;
  `:budget-kill-switch` is the human's hard stop and latches.

Four rights are reserved to the human operator and never delegated: merging,
the budget kill switch, approval of irreversible or external actions, and deep
escalation.

## Self-repair ladder

A defect the agent finds in itself travels:

```
detector signal → causal observation → verified diagnosis (every claim checked)
   → red test exists?  → repair proposal (guarded patch, human approval, rollback)
   → no red test yet?  → Stage A: a FAILING acceptance test is written first
                          and a human blesses the test before any implementation
```

The failing-test-first rule is the anti-cheating guarantee: the agent never
grades its own homework, because the yardstick is frozen by a human before the
code exists. Campaigns (`agent_tick.py --campaign --goal "…"`) drive this loop
unattended within cycle/spend ceilings and stop themselves on idle or stall.

## Repository map

| Where | What |
|---|---|
| `core/` | ~190 modules; grouped index in [knowledge/generated/AGENT_ANATOMY.md](knowledge/generated/AGENT_ANATOMY.md) |
| `tools/` | the agent's tool surface (file I/O, shell allowlist, logs, tests, web) |
| `cli/`, `app/` | REPL commands and runtime entry points |
| `docs/INDEX.md` | documentation index; doctrine in [docs/AGENT_DOCTRINE.md](docs/AGENT_DOCTRINE.md) and [docs/COGNITIVE_CORE.md](docs/COGNITIVE_CORE.md) |
| `docs/CODE_NOTES.md` | the book of *why*: every fix with its live measurement |
| `data/` | memories, registries, ledgers, approval inbox (JSONL, integrity-hashed) |
| `logs/` | per-session audit traces — the agent's own diagnostic surface |

## Development

```bash
python -m pytest -q
```

7,900+ tests, all green, plus ratchets that only tighten: file size, function
length, lint debt, docs↔code conformance (every path in the docs must exist or
be declared historical), and a census of the control-loop's nervous system.
Growth past a ceiling must be banked with a written reason. Convention: code
carries a 1–3 line contract comment; the narrative goes to
[docs/CODE_NOTES.md](docs/CODE_NOTES.md).

## Honest status

The diagnostic loop closes: the agent finds real defects in its own traces,
verifies the diagnosis claim-by-claim, and routes it toward repair. The
*repair* loop has not yet closed end-to-end without a human: every campaign
run still starts from an explicit operator grant, and no scheduler is enabled
by default (`scripts/install_daemon.ps1` exists for when it is wanted). That
is a design posture, not a limitation to hide.
