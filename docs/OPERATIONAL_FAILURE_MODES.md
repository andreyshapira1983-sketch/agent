# Operational Failure Modes (OFM)

A catalogue of **operational failure classes observed in a real third-party
autonomous system**, recorded so we can test each one against *our* code and
tests. This is a checklist to drive verification — not a report of confirmed
defects in this repository.

---

## 1. Status and evidence rules

Read these rules before reading anything below.

1. **External, not ours.** Sections 3–9 describe classes of failure reported by
   an outside operator about *their* system. They are **not** confirmed defects
   in this repository. Nothing here is "our bug" until Section 10/11 cites our
   code or a test proves it.
2. **No number here measures this repo.** Every figure in Sections 3–9
   (e.g. "≈15× token cost", "481 follows", "294 planned / 31 published",
   "31 approved tasks", "35 minutes") belongs to the **author's system**. Do not
   quote any of them as a measurement of our agent.
3. **Three honest statuses only.** Each item and each gap carries exactly one:
   - `external-only` — observed elsewhere; not yet checked against our code.
   - `confirmed-gap` — verified absent/broken in our code (with a file/line).
   - `open-risk` — a forward-looking risk the author flags, not a failure.
4. **Absence of proof is not a fix.** "A module with the right name exists" does
   not close an item. Closing requires a regression or integration test that
   fails on the broken behaviour and passes on the fix (repo convention — see
   `docs/self-audit-lessons.md`).
5. **Stable IDs.** Every failure class has a stable `OFM-NNN` id so tests,
   commits, and cross-references can point at it without ambiguity.

### Per-entry template

```
OFM-NNN — <name>
- Observation:     one-line description of the external failure
- Failure factors: the contributing conditions the author listed
- Maps to control: <module/function> (verified) | partial | none-found
- Status:          external-only | confirmed-gap | open-risk
- Required test:   the integration/regression test that would settle it
```

---

## 2. External observations — not confirmed repo defects

Everything in Sections 3–9 is an external observation. It earns a status of
`confirmed-gap` **only** where Section 11 cites our code. Until then it is
`external-only`, regardless of how plausible it sounds.

---

## 3. Context and routing failures

### OFM-001 — Context overload
- **Observation:** A whole company was moved into one repository; over time
  folders grew too large and recall quality degraded.
- **Failure factors:** too many files; loading the whole company into one
  context window; retrieval degrading as context grows; no selective loading of
  only the relevant material.
- **Maps to control:** `MemoryRetrievalPolicy.select` + `KnowledgeUsePolicy.filter`
  bound and filter what reaches a prompt (partial — this is retrieval scoping,
  not repository-size scoping).
- **Status:** `external-only`
- **Required test:** retrieval stays bounded and relevant as the store grows to
  N× its current size (context-budget regression).

### OFM-002 — Generalist overload
- **Observation:** One generalist agent handled most tasks but stopped coping
  with complex, parallel, multi-step work.
- **Failure factors:** a task too large for one context; parallel dependencies;
  several specialisations at once; wrong choice between the central agent and
  sub-agents; high multi-agent cost (author estimates ≈15× tokens — *their
  system, not a measurement of ours*).
- **Maps to control:** sub-agent runner exists as a bounded executor
  (`core/subagent_runner.py`); central agent owns memory/policy/budget.
- **Status:** `open-risk` (see OFM-020)
- **Required test:** none yet — this is a scaling risk, not a failure.

---

## 4. Platform and external-action failures

### OFM-003 — Platform rate violation
- **Observation:** The agent performed social-network actions in fast bursts;
  the account was blocked.
- **Failure factors:** no hard daily caps; machine speed instead of human pace;
  bulk identical external actions; limits not enforced in code; the platform
  read the behaviour as abuse.
- **Maps to control:** `core/budget_governor.py` / `core/budget_ledger.py` +
  `core/rate_limiter.py` exist (partial — general budget/rate primitives, not a
  per-platform hard cap on external actions).
- **Status:** `external-only`
- **Required test:** every external-action adapter has a hard per-window cap that
  refuses the (N+1)th action, proven by test.

### OFM-004 — False external success
- **Observation:** A platform's send UI changed twice; the tool kept reporting
  success while the message was never actually sent.
- **Failure factors:** trusting the tool's own success report; no check of the
  real external state change; DOM/UI change; no post-condition; conflating
  "command ran" with "goal achieved".
- **Maps to control:** `core/tool_receipts.py` is append-only and records the
  tool result — but a receipt proves the *tool call*, not that an external
  platform reached the business outcome. No per-adapter post-condition found.
- **Status:** `external-only` (candidate `confirmed-gap` — see Section 11)
- **Required test:** each external-action adapter verifies the actual external
  state change (post-condition), and a stale/changed UI makes the action report
  failure, not success.

### OFM-005 — Proxy-metric failure (wrong lead source)
- **Observation:** The system collected people engaging with business content;
  most turned out to be other sellers, not buyers.
- **Failure factors:** wrong sampling; engagement mistaken for buying intent;
  lead *count* used instead of lead *quality*; a technically-working pipeline
  with near-zero business value; optimising a proxy metric instead of the real
  goal.
- **Maps to control:** `core/proposal_value_gate.py` + `core/value_review.py`
  separate "change applied" from "change was valuable" (partial — value gate is
  for self-changes, not for lead-quality of an external funnel).
- **Status:** `external-only`
- **Required test:** an output pipeline is scored on the real target, not a
  convenient proxy; a high-proxy / zero-value result is flagged.

---

## 5. Approval and queue failures

### OFM-006 — Approval-queue bypass
- **Observation:** Some actions ran around the single approval queue; each bypass
  was, in the author's words, a loss of control.
- **Failure factors:** several send paths; a direct effect call without approval;
  no single mandatory application point; incomplete auditability; some actions
  never reached the shared log.
- **Maps to control:** `core/approval_inbox.py` (single inbox) +
  `core/actuation_gateway.py` (effectful-action gate). Whether **every** effect
  path is forced through one point is not proven repo-wide.
- **Status:** `external-only`
- **Required test:** no effectful action can reach an external adapter without
  passing the single approval/actuation gate (proven by attempting a bypass).

### OFM-007 — Draft treated as a completed action
- **Observation:** A customer reply was drafted in 35 minutes but sat in Gmail
  Drafts for three days.
- **Failure factors:** `drafted` treated as `sent`; the draft placed in a store
  nobody reads; no next-step executor; no processing deadline; no stale-draft
  alarm.
- **Maps to control:** none-found for external draft stores.
- **Status:** `external-only`
- **Required test:** a `drafted` artifact is a distinct state from `sent`, has a
  consumer and a deadline, and raises an alarm if it stalls.

### OFM-008 — Queue without a consumer
- **Observation:** In another queue, 31 approved tasks accumulated but no process
  executed them.
- **Failure factors:** queue created separately from its consumer; no automatic
  drain; no queue-depth monitoring; no backlog alarm; a dashboard giving the
  illusion of work; `approved` mistaken for `executed`.
- **Maps to control:** `AutonomousRuntime.run_task_queue()`
  (`core/autonomous_runtime.py:313`) IS a real consumer (pulls `pending`,
  `mark_running` → execute → `mark_done`/`mark_failed`); `RuntimeTaskStore`
  distinguishes `pending/running/done/failed/cancelled/paused`;
  `recover_stuck()` resets crashed `running` tasks; queue-depth summary exists.
  Approval inbox separates `approved` from `executed`
  (`core/approval_inbox.py`). **Verified control present.**
- **Status:** `external-only` for the "no consumer at all" case; the *residual*
  gap is whether the consumer is actually running in the production loop
  (Section 11).
- **Required test:** an approved task with no running consumer raises a backlog
  alarm; and an integration test proves the consumer drains the queue end-to-end.

---

## 6. Runner, heartbeat and retry failures

### OFM-009 — Dead runner unnoticed
- **Observation:** The execution process stopped and went unnoticed for days.
- **Failure factors:** no heartbeat or no monitoring of it; no health surface; no
  stale-heartbeat alarm; no distinction between "no tasks" and "runner died";
  the failure was silent.
- **Maps to control:** heartbeat file (`data/daemon_heartbeat.json`) + staleness
  reasoning (`core/incident.py`) surfaced via `cli/commands_health.py`
  (advisory: it recommends, it does not self-heal).
- **Status:** `external-only` (partial control present)
- **Required test:** a stale heartbeat produces a visible operator warning and is
  reported distinctly from "no work due".

### OFM-010 — Infinite or excessive retries
- **Observation:** A weekly task failed deterministically but retried on every
  tick, burning 100+ agent sessions per day.
- **Failure factors:** a deterministic error treated as transient; retry on every
  tick; no backoff; no final failure marker; no cap / no correct exhaustion; no
  notification; the error kept spending budget.
- **Maps to control:** `RuntimeTaskStore` has `attempts` / `max_attempts`
  (a retry cap) and `last_error`. **But** `mark_failed()` re-queues to `pending`
  when `attempts < max_attempts` **without bumping `run_after`** — so retries are
  immediately eligible again with **no backoff**. Default `max_attempts=1` makes
  most tasks fail once (no re-pend); the gap bites tasks created with
  `max_attempts > 1`.
- **Status:** **`confirmed-gap`** — `core/task_queue.py` `mark_failed()` has no
  `run_after` backoff (see Section 11).
- **Required test:** a task with `max_attempts>1` that fails deterministically is
  re-scheduled with an increasing `run_after` delay (backoff), and a final
  failure marker + notification appears when attempts are exhausted.

---

## 7. Stale-state and reversibility failures

### OFM-011 — Working on stale repository state (force-push)
- **Observation:** A bot ran on an old checkout, hit a conflict, and force-pushed
  the deploy branch backwards. Result: prices rolled back, checkout broke.
- **Failure factors:** stale checkout; no mandatory refresh before change; no
  base-SHA freshness check; wrong reaction to a rejected push; force-push rights;
  an irreversible external action without confirmation; the current production
  version replaced by an older one.
- **Maps to control:** `core/safe_vcs.py` has **no `push` / `fetch` / `pull` /
  `remote` method by design** — the self-apply lane physically cannot force-push
  through this interface. **Verified strong control** for the git path.
- **Status:** `external-only` overall; the *residual* gap is a base-SHA freshness
  check before acting on any *other* stale external state.
- **Required test:** any action gated on external state re-reads that state and
  refuses to act on a stale base (freshness precondition).

### OFM-012 — One-way automation without a reverse process
- **Observation:** The system auto-followed accounts but had no real cleanup.
- **Failure factors:** automation can only add; no prune; no periodic quality
  check; no blacklist; accumulated error grows over time; each action "succeeds"
  while slowly degrading the system.
- **Maps to control:** memory hygiene has a reverse process for *internal* state
  (`archive_low_value_memory`, `prune_episodic`); no reverse process for
  *external* add-only actions.
- **Status:** `external-only`
- **Required test:** every add-only external automation has a paired prune /
  blacklist and a periodic quality check.

---

## 8. Signal-consumer and feedback-loop failures

### OFM-013 — Wrong quality criterion
- **Observation:** Auto-follow scored the *content quality* of a person, not the
  probability that the person was a customer. Author reports 481 follows,
  ≈330 off-target (*their system*).
- **Failure factors:** wrong objective function; good content mistaken for a good
  customer; optimising a convenient measurable metric; the real business goal not
  checked.
- **Maps to control:** none-found (external-targeting objective is out of repo
  scope today).
- **Status:** `external-only`
- **Required test:** targeting is scored against the real objective, and a
  proxy-vs-goal mismatch is flagged.

### OFM-014 — A recorded signal that nobody reads
- **Observation:** Four warm replies arrived in a week; the system proposed zero
  follow-ups.
- **Failure factors:** the event is logged; the log has no consumer; no
  `signal → task` route; the data exists but changes no action; recording data
  mistaken for using data.
- **Maps to control:** partial — the runtime task queue is the `signal → task`
  substrate, but no wiring from inbound external signals to task creation is
  proven.
- **Status:** `external-only`
- **Required test:** an inbound signal deterministically produces a task (or an
  explicit "no action" decision), proven end-to-end.

### OFM-015 — Feedback stored but possibly not applied
- **Observation:** Rejection/edit reasons were written to a playbook. The author
  warns: a signal that is stored but never read by the future system changes
  nothing.
- **Failure factors:** feedback written to the wrong place; the future agent does
  not load the relevant playbook; no proof the next decision used the feedback;
  no measurement of behaviour change.
- **Maps to control:** partial — persistent memory + retrieval exist, but there
  is no proof a specific stored lesson influenced a later decision.
- **Status:** `external-only`
- **Required test:** a stored lesson demonstrably changes a later decision
  (behaviour-change measurement, not just a write).

---

## 9. Value, shipping and human-bottleneck failures

### OFM-016 — Production exceeds human decision capacity
- **Observation:** The system created too much material (author: 54 drafts / 1
  published; 294 planned / 31 published — *their system*).
- **Failure factors:** optimising volume created; no work-in-progress limit; the
  human bottleneck ignored; the review queue growing faster than the human clears
  it; new ideas created instead of finishing old ones; `created` conflated with
  `shipped`.
- **Maps to control:** `proposal_value_gate` + human `value_review` gate value;
  no explicit WIP limit found.
- **Status:** `external-only`
- **Required test:** a WIP cap refuses new creation while the review backlog is
  above a threshold; `created` and `shipped` are distinct counters.

### OFM-017 — Software makes unwanted work more visible
- **Observation:** The author built a priority display; nine goals never moved.
- **Failure factors:** wrong diagnosis (treated as a visibility problem); the real
  cause was no desire to do those tasks; software amplified unnecessary work;
  automation built before checking the real constraint.
- **Maps to control:** none-found (this is a product/diagnosis discipline, not a
  code control).
- **Status:** `open-risk`
- **Required test:** n/a — process discipline; verify the real constraint before
  automating.

### OFM-018 — No path to actual delivery (meta-cause)
- **Observation:** The author reduces most incidents to this: an action or output
  had no complete path to delivery. One link was missing from:
  `proposal → approval → consumer → execution → external verification → recorded outcome`.
- **Failure factors:** any single missing link in that chain.
- **Maps to control:** partial — approval inbox (`approved`≠`executed`) + task
  queue consumer + tool receipts cover several links; per-adapter external
  verification is the weakest link.
- **Status:** `external-only` (structural checklist)
- **Required test:** for each effectful capability, an integration test walks the
  full chain and fails if any link is absent.

### OFM-019 — Stale world-view (meta-cause)
- **Observation:** The author's second general cause: the agent acted on stale
  state — not only Git, but a changed platform UI, old customer data, a stale
  queue, an old external-system state, or a decision made before a newer event.
- **Failure factors:** acting without re-reading the current state of the world.
- **Maps to control:** partial — `recover_stuck` (stale `running`), `SafeVCS`
  local-only (git); no general "re-read before act" precondition across adapters.
- **Status:** `external-only` (structural)
- **Required test:** any effectful action re-reads the relevant external state
  immediately before acting and refuses on staleness.

### OFM-020 — Unverified architectural risks (open risks)
- **Observation:** The author does **not** claim these as incidents. Two open
  questions: whether one generalist agent stays effective as the company grows;
  whether the feedback/taste loop keeps improving or plateaus.
- **Status:** `open-risk` (both). Track, do not "fix".

---

## 10. Mapping to current repository controls

Verified against code on the audited `main` (source of truth):

| OFM | Repo control (verified) | Coverage |
|---|---|---|
| 008 | `AutonomousRuntime.run_task_queue()` (`core/autonomous_runtime.py:313`), `RuntimeTaskStore` statuses, `recover_stuck()` | consumer + recovery present; production-loop wiring unproven |
| 006 | `core/approval_inbox.py` (`pending/approved/denied/aborted/executed`, TTL 24h, `expire_stale`), `core/actuation_gateway.py` | single inbox present; repo-wide single-choke-point unproven |
| 009 | `data/daemon_heartbeat.json` + `core/incident.py` staleness + `cli/commands_health.py` | detection present, advisory only (no self-heal) |
| 010 | `RuntimeTaskStore.attempts/max_attempts` (`core/task_queue.py`) | retry **cap** present; **backoff absent** → see Section 11 |
| 011 | `core/safe_vcs.py` — no `push/fetch/pull/remote` by design | strong: self-apply cannot force-push |
| 004 | `core/tool_receipts.py` (append-only) | proves tool call, **not** external business outcome |
| 005/016 | `core/proposal_value_gate.py`, `core/value_review.py` | separate "applied" from "valuable" (self-change scope) |
| 012 | `archive_low_value_memory`, `prune_episodic` | reverse process for **internal** state only |

---

## 11. Confirmed current gaps

> **⚠️ SUPERSEDED (2026-07-20).** The single `confirmed-gap` recorded below —
> OFM-010, retry without backoff — **is fixed**: it is MIR-017 in
> [`audit/MASTER_ISSUE_REGISTRY.md`](audit/MASTER_ISSUE_REGISTRY.md), closed
> with a runtime-reproduced regression test. Read no open/closed status from
> this section; the registry owns them.
>
> The rest of this document is **not** affected, because it never claimed to
> describe this repository: sections 3–9 are failure modes observed in someone
> else's system, kept as a verification checklist. That checklist is still
> useful and still largely unexercised — see §12.

Only gaps verified against our code appear here.

- **OFM-010 — no retry backoff.** `core/task_queue.py` `mark_failed()` sets a
  non-exhausted task back to `pending` **without changing `run_after`**, so it is
  immediately eligible on the next tick. There is a retry *cap*
  (`attempts >= max_attempts` → `failed`) but **no backoff** in this path.
  Impact is bounded to tasks with `max_attempts > 1` (default is 1). This is a
  confirmed gap, not a hypothesis.

Everything else below is **not** yet a confirmed gap — it needs a test.

---

## 12. Unverified risks requiring tests

Without dedicated integration tests we cannot honestly claim:

- every queue has a working, running consumer (production loop);
- every retry path has backoff (only OFM-010 is confirmed *absent* so far);
- backlog always raises a visible alarm;
- every external action verifies the actual external state change (OFM-004);
- feedback is actually read by the next agent (OFM-015);
- stale external state is checked before acting (OFM-019);
- total work-in-progress is bounded (OFM-016);
- `done` means delivered value, not just a completed call (OFM-018);
- every external platform has hard caps (OFM-003);
- every add-only process has prune + blacklist (OFM-012).

---

## 13. Required regression and integration scenarios

One test per gap, each failing on the broken behaviour and passing on the fix
(repo convention). Do **not** bundle unrelated gaps into one patch.

1. **OFM-010 backoff:** `max_attempts>1` deterministic failure → `run_after`
   grows per attempt; final failure marker + notification on exhaustion.
   *(Confirmed gap — fix candidate first.)*
2. **OFM-008 consumer:** approved task with no consumer → backlog alarm;
   end-to-end drain test for the real consumer.
3. **OFM-004 post-condition:** an adapter whose external UI "changed" must report
   failure, not success.
4. **OFM-006 single gate:** an attempted effect bypass is refused by the single
   approval/actuation point.
5. **OFM-019 freshness:** an effectful action on stale external state is refused.
6. **OFM-016 WIP cap:** new creation refused while review backlog is over
   threshold; `created` vs `shipped` counters distinct.
7. **OFM-014 signal→task:** an inbound signal deterministically yields a task or
   an explicit no-action decision.

---

*Provenance: Sections 3–9 are external observations supplied by an operator of a
different system. Section 10/11 statements are verified against code on `main`.
No figure in Sections 3–9 measures this repository. This document confirms no
defect except the one explicitly marked `confirmed-gap` in Section 11.*
