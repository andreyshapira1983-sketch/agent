# Why does self-build keep failing? — measured, 2026-08-22

Assignment: «разберись почему self-build постоянно падает», prompted by the side
finding in `MEMORY_CONSOLIDATION_MEASUREMENT.md` (83 rows, 65 `partial`).

Input: `data/episodic_memory.jsonl`, `data/approval_inbox.jsonl`, and the code
paths named below. Measurement only; nothing changed.

**The premise does not survive the measurement.** Self-build is not mostly
failing. It is mostly *blocked*, and its blocking is what fills memory.

## 1. What the 83 events actually are

    43  approval_wait      blocked: an approval was already pending
    16  dirty_tree_wait    blocked: the git working tree was not clean
    10  critic_veto        GENUINE failure
     8  proposed           success
     6  no_grounded_target the size gate correctly refusing an impossible job

Fifty-nine of 83 (71%) are not failures. They are gates reporting that the
agent was not allowed to start. Only the ten `critic_veto` rows are the loop
actually trying and getting it wrong.

## 2. The day-by-day story

    2026-07-30 .. 08-05   the real failure period: 9 critic_veto, 6 no_grounded_target
    2026-08-13 .. 08-14   only dirty_tree_wait (7) — the tree was busy
    2026-08-15            5 proposals in one day. It works.
    2026-08-16            1 proposal, then 32 approval_wait
    2026-08-17            9 approval_wait
    2026-08-19            1 proposal
    2026-08-20            1 dirty_tree_wait

Since 2026-08-15 the loop produced **8 proposals against 1 genuine veto**. The
"constant falling" is concentrated in late July / early August and has largely
stopped. What replaced it is waiting.

## 3. The cost of one unanswered approval: 41 episodes

On 2026-08-16 — the unattended day run — self-build produced a proposal, filed
an approval item, and then hit gate 3 on every subsequent tick. 32 times that
day, 9 more the next. The inbox holds **no pending `self_apply_lane.run` item
today**, so the block eventually cleared; the record of it did not.

**Not a money problem, and this should be said plainly.** All four gates run
before any LLM call — `core/self_build_producer.py:1429` carries the comment
"before ANY LLM-heavy work", and kill-switch / budget / approval / dirty-tree
are checked in that order ahead of the Manager. The 41 waits cost nothing but
disk. The gate ordering is correct and is not the defect.

## 4. The defect: a blocked gate is banked as a permanent lesson

Each wait writes a full episode tagged `lesson`. Measured on the live store:

    wait-episodes                       67
    of them usage_eligible=True         64
    protected (unevictable) rows        127 of 200  (63%)
    of that protected set, wait rows     64  (half)

So half of the agent's permanent, un-evictable, retrievable "experience" is the
sentence *an approval is already pending* and *the working tree is not clean*.
Those are status lines, not lessons, and retrieval is entitled to serve them.

The store is simultaneously at its 200-row ceiling — so genuine lessons are the
records being evicted to keep these.

### Why the existing fix does not catch it

`core/episodic_hygiene.py` has `select_duplicate_episodes` /
`collapse_duplicate_episodes`, built for exactly this under MIR-090 (measured
then at 14 identical `dirty_tree_wait` rows). It does not fire here, for two
independent reasons:

1. **It exempts protected tags** — a deliberate choice, recorded in MIR-090 as
   "matching `select_for_pruning`". What that choice did not anticipate:
   `core/self_build_memory.py` tags *every* self-build episode `lesson`
   **specifically to prevent eviction** — its own docstring says "Episodes are
   tagged ``lesson`` so the episodic store never evicts them." The exemption is
   therefore not a rare hand-marked case; it swallows the entire population the
   fix was written for. Measured: **43 of 43** `approval_wait` rows are
   `lesson`-tagged, and they form **exactly one duplicate group of 43** — the
   collapser would reduce them 43 → 1 if it were allowed to look.
2. **It is reachable only from a manual command.** The only production caller is
   `core/memory_hygiene_commands.py:240`, i.e. the `:memory hygiene` sweep.
   Nobody types a command during an unattended run, which is precisely when the
   43 accumulated.

MIR-090 called this: it is marked `fixed` **for the accumulation** and states
"Deliberately still open: the daemon keeps banking the repeat", plus the missing
test "a producer that hits the same gate twice does not bank a second identical
episode." That clause came true two days later at 43×. The entry was honest;
the measurement is its follow-up, not its refutation.

## 5. The genuine failures, and what happened to them

Of the 10 `critic_veto` rows:

- **3 unparsable builder replies** (2026-08-04, within two hours, `raw_chars`
  46057 / 47830 / 46501, JSON invalid mid-reply). Cause named in code: the
  Builder rewrites the shrunk target **plus** the new sibling modules in a
  single model reply, bounded by the output-token ceiling; past a certain size
  the reply truncates and parses to nothing.
- **4 builder-reported `confidence 0.00`**, 1 at `0.02`, 1 "empty generated
  content". The provider was alive on those days (51 and 129 model calls on
  08-04 / 08-05), so this is not a dead key.
- **1 real quality catch** (2026-08-15): the proposed split dropped top-level
  names other modules import. The critic doing its job.

The truncation class was **fixed by a gate rather than by a bigger reply**:
`SPLIT_ONE_SHOT_MAX_LINES = 1500` in `core/backlog_target_mapper.py:35` now
refuses oversized targets up front. That is why the 3 parse failures on 08-04
became 6 cheap `no_grounded_target` refusals on 08-04/05 and never recurred. A
correct trade: an expensive broken call replaced by a free honest refusal.

## 6. The consequence nobody chose: three files self-build can never touch

The 1500-line ceiling is a real capability boundary, and today exactly three
modules in `core/` + `tools/` (225 modules) sit above it:

    1776  core/model_router.py
    1742  core/self_build_producer.py
    1663  core/smart_memory.py

The self-improvement loop cannot split **the provider router, its own engine,
or the memory store**. The three files most entangled in its own behaviour are
the three it is structurally unable to work on, and the refusal is silent —
`no_grounded_target` reads like "nothing to do", not "I am permanently unable
to do this one". This is why `core/autonomous_runtime.py` had to be split by
hand. The gate's own comment names the missing piece: "needs an incremental
splitter".

## 7. What this does NOT establish

- Not that the `lesson` tag should be removed from self-build episodes. It was
  put there to stop eviction of genuine build lessons, and that need is real;
  what is measured here is that the tag cannot serve as both "this is a lesson"
  and "do not evict this" without polluting the first meaning.
- Not that the daemon should stop retrying while blocked. Retrying a gate is
  cheap and correct; only the *banking* of each retry was measured as harmful.
- Nothing about whether an incremental splitter is worth building.
- The `confidence 0.00` cluster is described, not explained. Why the Builder
  returned near-zero-confidence content four times was not traced.

---

# Addendum: why the last unattended run really stopped — 2026-08-22

Asked while the above was being written: how many defects must be closed before
a **week-long** unattended run. The registry count turned out to be the wrong
instrument, so the question was taken to the one real trial we have.

## There has already been a 7-day grant. It lasted two days.

`data/approval_inbox.jsonl` holds an approved standing grant, 2026-08-16:
*«до 12 прогонов в сутки, 7 дней, только чтение+генерация+предложения в
очередь»*. So the authority for a week existed. Activity across four
independent logs:

    day     budget_ledger  tool_receipts  campaign  episodes
    08-15        943            662          133       —
    08-16        448            304           57       73
    08-17        295             99           18       50
    08-18          9              5            1        1
    08-19         42             45            4       12
    08-20          6             11            2        1

Monotone decay to a near-stop on the third day. Not a crash — a starvation.

## The cause, pinned in code

`data/runtime_tasks.jsonl` collapses to 21 distinct tasks:

    resume_checkpoint  14   ALL paused
    auto_run            7   3 done, 4 cancelled

Every one of the 14 carries `attempts=0/1` and `last_error=budget_exhausted`.
They were parked **before a single attempt**, the oldest on 2026-07-30.

The mechanism is a sensor with no actuator:

- `core/task_queue.py:263` `add_paused_checkpoint()` converts a run stopped by
  budget exhaustion into a task with `kind="resume_checkpoint"`,
  `status="paused"`.
- `core/task_queue.py:335` `pending()` returns only `task.status == "pending"`.
  A paused task is therefore **invisible to the scheduler forever**.
- `core/task_queue.py:551` `summary()` lists those same tasks under a
  `"resumable"` key — it *reports* them to a human and resumes nothing.

So: the hour budget runs out, the task in flight is checkpointed, and the
checkpoint is never picked up again by any automatic path. On 2026-08-15 the
agent ran hot (943 ledger rows), exhausted the window, and everything
interrupted from then on went to the paused graveyard. By 08-18 the queue held
nothing runnable and the agent had no work.

**The queue today contains zero runnable tasks.** A launch from this state
depends entirely on what the runtime generates for itself.

## What this means for a week-long run

The blocker is not a count of open registry entries. It is one missing
transition — nothing moves `paused` back to `pending` when the budget window
refills — plus two decisions that are not code at all:

1. **Resume after budget exhaustion.** Code, small, and strictly necessary:
   without it the first exhausted budget window ends the useful part of the
   run, exactly as it did on 08-18.
2. **A money wall outside the process.** Every wall here is in-process
   (MIR-120), and an unattended week with a funded key is precisely the
   scenario the in-process class does not cover. Provider-side Hard Spend Limit
   is an account action, not a repository change.
3. **What happens when the agent wants approval.** With no outbound channel
   there is no way to ask. Either a standing grant covers a named class of
   self-apply, or self-build halts at its first proposal — which is what the
   08-16 grant already implied by limiting the agent to «предложения в очередь»,
   and what §3 above measured as 41 wait-records from one unanswered item.

**Not claimed:** that closing these three delivers a week. The only evidence is
a two-day run that died of one identified cause. Removing that cause is
necessary; whether a different limit appears on day four is unknown, because
nothing here has ever run that long.
