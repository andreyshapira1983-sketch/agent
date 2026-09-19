# Prospective autonomy hazard audit

> **⚠️ PREDICTIONS OF 2026-08-27 (banner added 2026-09-03).** Written before
> the autonomy push; several predicted hazards have since been observed live
> (fabricated completions, guard clinches). Treat each entry as a prediction
> with a date, not a current status — statuses live only in
> `MASTER_ISSUE_REGISTRY.md`.

**Question this pass asks.** Not «is there a bug». For every historical failure
class already collected in `HISTORICAL_FAILURE_LEDGER.md` (H-01…H-51): could an
unattended agent built from THIS repository develop the same causal mechanism as
its capabilities expand?

**Rule.** A historical incident generates a hypothesis and nothing more. Every
class ends in exactly one state, and the states are not collapsed:

`NOT_APPLICABLE` · `ALREADY_PROTECTED` · `FUTURE_PLAUSIBLE_UNPROVEN` ·
`LOCALLY_REACHABLE` · `CURRENT_DEFECT_REPRODUCED` · `LIVE_OBSERVED`

No production code is changed on the strength of an analogy. A failed hypothesis
is a successful result. Hypotheses are recorded HERE, never as registry defects.

Started 2026-08-27, on the operator's instruction, after the agent's first
unattended run.

---

## 1. Capability inventory — the denominator

Every class reduces to «which prerequisites does it need, and does the agent have
them». So the prerequisites were measured first, against the tree at `b0cbee2`.

| capability | today | evidence |
|---|---|---|
| persistent state | **yes** | 24 append-only stores under `data/` |
| long-lived memory | **yes** | episodic / procedural / persistent, read into planning |
| background daemon operation | **yes**, since 2026-08-26 | scheduled task enabled; one tick observed 2026-08-27T00:31Z |
| paid actions (model calls) | **yes**, bounded | `BudgetGovernor`; observed 2 calls / 72 of 200 cost units in the live tick |
| external credentials | **yes** | openai funded; anthropic and deepseek keys present, unfunded |
| delegated tools | **yes**, 19 | `tools/` |
| automatic recovery | **yes** | `reactivate_paused_checkpoints`, lane rollback |
| autonomous file **creation** | **yes** | `file_write` on a non-existent path → `reversible` → allowed |
| autonomous file **overwrite** | **NO** | `file_write.risk_for` → `irreversible` → policy `escalate` (I-1) |
| autonomous shell beyond an allowlist | **NO** | unrecognised argv → `external`/`irreversible` → gate (I-2) |
| self-modification (own code applied) | **NO** | `run_approved_self_apply` has exactly one caller: `cli/commands_self_apply.py`. `agent_tick.py:747` only COUNTS such items |
| code promotion (push / merge) | **NO** | `core/safe_vcs.py` defines zero `push`/`fetch`/`pull`/`remote` methods; the lane commits to a temp branch only |
| multiple agents | **NO** autonomously | `launch_subagent` is created in `cli/commands_proposals.py` and executed in `cli/commands_approval.py`; no autonomous caller |
| autonomous application of its OWN proposals | **yes, since 2026-08-27**, narrowly | only proposals that CREATE documents: no overwrite, no code, no tests, and none of the five documents code reads as authority. Two gates: effects on AND an active standing grant. See MIR-173 |
| model-generated configuration | **partial** | the charter `knowledge/doctrine/future/CORPORATE_MODEL.md` steers goal choice and is an `.md` the agent may PROPOSE; it may not overwrite it (I-1) |

**The one-sentence denominator.** The agent's autonomous effect surface today is
*creating new files inside the workspace* — nothing else. Since 2026-08-27 it may
also APPLY its own proposals of that same shape (new documents only) through the
lane, which adds targeted tests, the full battery and automatic rollback to an
effect it could already perform bare. The single genuinely new capability is a
commit to a temporary branch without a human; it never touches the base branch
and is undone by deleting that branch. Everything wider is
either gated to a human or absent from the code.

---

## 2. Load-bearing invariants

These three carry most of the 51 classes. Each is stated, proved to bite, and
paired with the way it could disappear.

### I-1 — an overwrite is irreversible, and irreversible escalates
`tools/file_write.py::risk_for`: target exists → `irreversible`; unresolvable or
sandbox-escaping → `irreversible` (conservative); otherwise `reversible`.
`core/policy.py`: `irreversible` → escalate (human approval), `external` →
escalate, unknown tool → deny.
**Proof it bites:** `tests/test_actuation_gateway.py::test_evaluate_escalate_on_overwrite`
runs the whole composition with effects enabled and asserts `escalate`.
**How it disappears:** anything that makes an overwrite look reversible (a
delete-then-create path, a tool that truncates rather than replaces, a backup
step reclassifying the write), or a policy edit granting `file_write` a standing
allowance.

### I-2 — the shell is an allowlist, and the default is not «allow»
`tools/shell_exec.py::risk_for`: `argv[0]` in `READ_ONLY_COMMANDS` (or a
read-only git subcommand) → `read_only`; a write subcommand (`git add/commit/
checkout`) → `irreversible`; **everything else → `external`**. Both non-read
verdicts reach the gate. A separate rule forbids committing onto a branch the
agent did not create.
**Why it matters here:** it is what stops «an action inside your own authority
removes the path that repairs you» — the agent cannot disable its own scheduled
task, kill switch, or credentials, because none of those commands are on the
allowlist.
**How it disappears:** growth of the allowlist by convenience; a wrapper command
that is read-only in name and mutating in `argv[1]` beyond the recorded pairs.

### I-3 — one chokepoint, and it fails closed
`gateway.evaluate` is called from exactly one place: `core/loop_step_execution.py:344`.
Direct registry access elsewhere fetches only reading tools (`file_read`,
`web_search`, `web_fetch`, `rss_fetch`) and `run_tests`. A missing policy raises
rather than passes: `if self.policy is None: raise ValueError`.
**How it disappears:** a second execution path added for convenience — the
bypass would be by omission, not by failure, and nothing currently detects a
tool call that skipped the gateway.

---

## 3. Class table

`historical case | causal mechanism | prerequisite for autonomy | exists now? | local analogue | blocking invariant | evidence | classification | next evidence needed | action`

| # | mechanism (stripped of brand) | prerequisite | exists now | local analogue | blocking invariant | evidence | class | next evidence | action |
|---|---|---|---|---|---|---|---|---|---|
| H-16 | what RUNS differs from what was reviewed | a build/release pipeline the agent feeds | **no** | none: no artifact build, no publish step | — (absent by construction) | `safe_vcs` has no push/fetch/pull/remote | NOT_APPLICABLE | would change if a release path is added | none |
| H-31 | old code meets new data and acts on it | a fleet, or two versions live at once | **no** | single process per tick, `MultipleInstances=IgnoreNew` | scheduler setting + single process | task settings read 2026-08-27 | NOT_APPLICABLE | — | none |
| H-45 | two nodes each believe they are the only one | concurrent agents sharing state | **no** | `IgnoreNew` on the task; subagents human-launched | I-2 + scheduler | task settings; `launch_subagent` callers | NOT_APPLICABLE **today** | recheck the day subagents get an autonomous caller | none |
| H-17 | an action inside your own authority removes the path that repairs you | ability to disable its own scheduler / kill switch / credentials | **no** | `schtasks`, `sc`, credential edits are all off the allowlist | I-2 | `READ_ONLY_COMMANDS` contents | ALREADY_PROTECTED | — | keep I-2 pinned |
| H-33 | the protective mechanism removes protection when IT fails | a gate that degrades open | **n/a** | gateway raises when policy is missing | I-3 | `raise ValueError` in `evaluate` | ALREADY_PROTECTED | a probe for «tool ran without a gateway decision» | consider a tripwire, not a fix |
| H-36 | a destructive command aimed at the wrong target | delete/truncate authority | **no** | no delete tool; overwrite escalates | I-1 + I-2 | `risk_for` table | ALREADY_PROTECTED | — | none |
| H-11 | old state read by new code, a default asserting a past that never held | persistent state **and** code change | state yes, autonomous code change no | reproduced historically (ledger H-11); MIR-168 is the same family | I-1 (agent cannot rewrite code) | ledger H-11; MIR-168 | FUTURE_PLAUSIBLE_UNPROVEN | an executable path where the agent both changes code and reads its own older state | record only |
| H-35 | data read as instruction | untrusted text enters · is persisted · is read back through a channel that does not re-check it · that reader feeds a decision-maker | **all four exist** | see §5 | **none on this path** — the scan is skipped by tool NAME | 397 live `injection_*` events carrying `excerpt`; `read_logs` in `_INJECTION_SCAN_EXEMPT`; registered at `app/bootstrap.py:118` | **LOCALLY_REACHABLE** | does a real planner call to `read_logs` deliver the excerpt into the prompt unscanned? | witness first, no code change yet |
| H-01 | a diagnostic payload consumed as valid data | an evidence pipeline that banks verified claims, fed by autonomous fetching | **yes** — web tools are live and the daemon runs | reproduced and fixed historically; the compounding path is MIR-140, where the verify-replan loop FETCHES the cited URL, so a page that has since become a login wall resolves the citation and RAISES acceptance | the H-01 fix bites; MIR-140 does not, and is open | ledger H-01 + MIR-140 | FUTURE_PLAUSIBLE_UNPROVEN | an autonomous run where an error page resolves a citation and lifts acceptance | record; MIR-140 already owns the repair |
| H-30 | identifiers unique only in appearance | an id generator whose entropy can be silently weakened | n/a | `core/ids.py::new_id` | `secrets.token_hex(16)` — 128 bits from the CSPRNG, matching W3C TraceContext | source read 2026-08-27 | ALREADY_PROTECTED | — | none |
| H-39 | the dependency itself is the attack | the agent installs or updates dependencies | **no** | no package manager on the shell allowlist; `pip`/`npm` are `external` → gate | I-2 | `READ_ONLY_COMMANDS` | NOT_APPLICABLE | recheck if a build step is ever granted | none |
| H-42 | a synchronised volley: senders converge on one instant | several senders | **no** | one process per tick, `IgnoreNew` | scheduler | task settings | NOT_APPLICABLE | — | none |
| H-49 | a change removes the means of repairing it | authority over its own network/scheduler/credentials | **no** | same surface as H-17 | I-2 | allowlist | ALREADY_PROTECTED | — | none |
| H-12 | backups that exist and were never restored | durable state whose restore is untested | state yes | `:state-store-drill` proves JSONL quarantine/recovery on an isolated file | the drill is a shipped command, not a promise | command registry | ALREADY_PROTECTED | whether the drill covers the SHAPE of a real incident (see H-51) | none |
| H-02 / H-25 | two conventions with no end-to-end check (units, then time) | two components with differing conventions | **yes** | cost units are per-1k-tokens by tier; local time vs UTC in journals — the auditor tripped on the second this morning | none proven | measured today: local = UTC+3, and a tick log read as «last hours» was wrong | FUTURE_PLAUSIBLE_UNPROVEN | an executable case where the mismatch changes a DECISION, not a narration | record only |
| H-27 | an external effect happens, its accounting does not | effects plus a separate ledger | **yes** | `tools/base.py:139-145` writes the invoke receipt for success AND failure — correct — but wraps the write itself in `except Exception: pass`, with no log | none on the failure path | source read 2026-08-27 | **LOCALLY_REACHABLE** | show a receipt write failing (read-only dir) while the effect lands | tripwire, not a fix: the swallow is deliberate, the SILENCE is the defect |
| H-51 | recovery proven for one item, needed for many | a restore path exercised at unit scale only | **yes** | the state drill proves one isolated file | none — scale is untested | MIR-018 family | FUTURE_PLAUSIBLE_UNPROVEN | a drill over the simultaneous blast radius, not one store | record only |
| H-43 | a value becomes STRUCTURE for the next parser | a text format where a value can close its own frame | **yes** | 24 JSONL stores | `json.dumps` escapes newlines — but no writer was audited for manual concatenation | not yet checked | queued | audit every JSONL writer for hand-built lines | examine next |
| H-43 | a value becomes STRUCTURE for the next parser | a text format where a value can close its own frame | **yes**, 24 JSONL stores | every writer audited: `core/logger.py:36`, `core/checkpoint.py:135`, `core/state_integrity.append_state_jsonl` | `json.dumps` escapes the newline, so a value cannot end its own record; no hand-built line found | source read 2026-08-27 | ALREADY_PROTECTED | re-check if any writer ever formats a row by hand | none |
| H-29 | corruption detected, and silently LESS data returned | a store that drops damaged rows | **yes** | `read_state_jsonl_unlocked`: a damaged row is QUARANTINED to `*.bad.jsonl` and the file rewritten with the survivors | evidence is preserved — better than the historical case | source read 2026-08-27 | ALREADY_PROTECTED, **with a named residual** | the CALLER is not told: `load()` returns fewer records and no runtime event is emitted | tripwire, not a fix |
| H-41 | a weak or truncated hash used as a credential | a truncated digest standing for identity in an AUTHORITY path | **yes** | `_effects_dedup_key` = `sha256(goal)[:16]` — **64 bits** — and `_granted_effects_approval` finds a live approval BY that key | none: a collision would let an approval granted for goal A authorise goal B | source read 2026-08-27 | FUTURE_PLAUSIBLE_UNPROVEN | a demonstrated pair of goals colliding at 64 bits, plus a way to submit the second | the repair is one line (full digest) but it is a **migration contract**: existing pending items carry the old key and would stop matching |
| H-47 | automatic failover itself causes the harm | a recovery path that runs unattended | **yes** | the catalog autorefresh fires from an ordinary tier lookup when the cache expires | throttled: `_AUTOREFRESH_DONE`, one attempt per process | live 2026-08-27 | **LIVE_OBSERVED, fixed same day (MIR-170)** | — | done: the refresh no longer deletes a provider it could not ask |
| H-04 | a watchdog reset loop with no diagnosis | an unattended restarter that does not read outcomes | **yes** | the scheduled task re-fires every 4 h whatever the previous tick did | none on the restart path; MIR-135 already records that a daemon crashing every tick still reports `alive` | MIR-135, open | **FUTURE_PLAUSIBLE_UNPROVEN**, and it sharpens with autonomy | a run of consecutive failing ticks, to show nothing escalates | record; MIR-135 already owns the fix |
| H-50 | a write returned success and never reached disk | durable state without a barrier | **yes** | `core/logger.py` flushes but does not `fsync`; the state stores are append-then-read | the truncated tail is handled: a half-written row is quarantined on read, not crashed on | H-06 examined this family historically | ALREADY_PROTECTED for the READ side | whether a lost tail loses a DECISION, not just a line | none |
| H-40 | nodes disagree about time and each is right | several clocks | **no** | one host | — | — | NOT_APPLICABLE | — | none |
| H-03 | a computing unit silently wrong on rare inputs | a scorer whose wrongness is invisible | **yes** | the verifier and the relevance/quality scorers | J-statistic measurements exist (MIR-141/143/147) and are open | those entries | FUTURE_PLAUSIBLE_UNPROVEN | a scorer error that changes an ADMISSION, measured | record only |
| H-26 | two gates read one input and disagree | a check separated in TIME from the act it guards | **yes** | `file_write.risk_for` asks «does the target exist» and `run()` writes later — a classic check-then-use window on I-1 | parallelism is granted ONLY to read-only steps: `if any(not self._step_only_reads(step) …)` sends the whole batch sequential, so no write races another write | source read 2026-08-27 | ALREADY_PROTECTED | — | **fragile**: the day parallel writes are allowed, I-1 becomes race-able |
| H-34 | the boundary is checked for some shapes of input, not all | a scan applied by NAME rather than by property | **was yes** | all three exemptions measured 2026-08-27: a FILENAME carries an order through `list_dir`; a failing test prints file text through `run_tests`; the journal carries the guard's own `excerpt` through `read_logs` | two removed (MIR-172), the third kept deliberately | §5, MIR-171, MIR-172 | **CURRENT_DEFECT_REPRODUCED → fixed for two of three** | none for `list_dir`/`run_tests`; for `read_logs` the repair is on the WRITE side | done, with the remaining piece named |
| H-37 / H-13 | a harmless-looking field is EXPANDED downstream | a stored value reaching a template/parse layer | **no** | `source_library.search_template.format(topic=…)` — the template is a MODULE CONSTANT, not stored state | the template is code, so an attacker controls the argument, never the format string | source read 2026-08-27 | ALREADY_PROTECTED | — | **fragile**: if the source library ever becomes data the agent can write, this is the log4shell shape exactly |
| H-38 | a DATA update, not a code change, kills every consumer | config the agent rewrites at runtime | **yes** | `config/model_catalog.json`, rewritten by an autorefresh fired from an ordinary lookup | none before today | live 2026-08-27: the refresh emptied a provider and reddened seven guards | **LIVE_OBSERVED, fixed same day (MIR-170)** | — | done |
| H-32 | recovery itself becomes the load | an unthrottled repair path | **yes** | catalog autorefresh; checkpoint reactivation | throttled on both: `_AUTOREFRESH_DONE` is one attempt per process; reactivation is batched 3 with a 60-minute cooldown | source read | ALREADY_PROTECTED | — | none |
| H-44 | a repair pass normalises a value into someone else's | a read path that REWRITES what it read | **yes** | `read_state_jsonl_unlocked` rewrites the store after quarantining a damaged row, and upgrades encodings on read | the rewrite happens under `exclusive_file_lock` | source read | ALREADY_PROTECTED | whether an encoding «upgrade» can change a value's MEANING, not just its bytes | record only |
| H-48 | a stale path everyone believed unused | a premise that stopped being true without anyone re-asking | **yes** | the injection exemption is precisely this: written when those tools returned machine text, still standing after the guard began writing untrusted excerpts into one of them | none — the premise is re-checked only when someone looks | §6, MIR-171 | **LIVE_OBSERVED** (the `read_logs` arm), fixed for the document case | the other two exempt tools | as H-34 |
| H-05 | a fast path justified by a state that is not true | replay of a stored answer | **yes** | the episodic fast path replays a stored answer verbatim | it demands full `achieved`; MIR-169 deliberately did NOT relax that half | today's measurement | ALREADY_PROTECTED | — | **fragile**: any future widening of replay must keep the completion demand |
| H-06 | crash consistency: temp+rename without a durability barrier | durable state written by an unattended process | **yes** | 24 stores | historically a/b protected, c fixed; a truncated tail is quarantined on read | ledger H-06 | ALREADY_PROTECTED | — | none |
| H-07 / H-46 | wall-clock deadlines under a clock step; a calendar boundary | an unattended process spanning clock changes | **yes**, since the daemon runs | reproduced historically and found FAIL-SAFE in every measured direction | fails safe | ledger H-07 | ALREADY_PROTECTED | — | none |
| H-08 | a silently weakened entropy source that still looks random | anything minting identity | **yes** | `new_id` → `secrets` (H-30) | the pinning gap was closed historically | ledger H-08 | ALREADY_PROTECTED | — | none |
| H-10 | cost multiplied by attacker-supplied input | a regex meeting untrusted text | **yes** | the path and stat regexes | bounded, and pinned by TIMING tests | ledger H-10 | ALREADY_PROTECTED, **with a residual worth naming** | those guards flaked twice under full-battery load on 2026-08-26 | a timing guard that cries wolf teaches its readers to ignore it — consider a cost-based bound instead of a clock one |
| H-14 / H-38 | a configuration file that takes the whole system down | config the runtime rewrites | **yes** | `config/model_catalog.json` | fixed 2026-08-27 (MIR-170) | live | LIVE_OBSERVED, fixed | — | done |
| H-15 | adjacent data leaking into output | shared buffers across concurrent work | partial | parallel steps exist, but only for read-only work | the same invariant that protects H-26 | source read | ALREADY_PROTECTED | — | fragile if parallel writes are ever allowed |
| H-18 | a repair path that does not scale to the size of the incident | restore exercised at unit scale | **yes** | see H-51 | none | MIR-018 family | FUTURE_PLAUSIBLE_UNPROVEN | a drill over a simultaneous blast radius | record only |
| H-19 | information reaching a channel not built to carry it | any side channel | **yes** | **this is §5**: the guard's own journal carries attacker text into an unscanned reader | none on that path | §5 witness | LOCALLY_REACHABLE (same finding, different lens) | as §5 | as §5 |
| H-20 | commitment drift vs binding drift | a goal that stays in telemetry while losing causal control | **yes** | measured: the goal drove 14% of 267 cycles, and `goal_drove` now records it per cycle | MIR-163 makes the drift VISIBLE rather than preventing it | MIR-158…163 | ALREADY_MEASURED, not prevented | whether a visible drift is ever acted on | record only |
| H-21 | a state machine accepting a transition its diagram does not have | any lifecycle | **yes** | fixed historically — but the ledger records the fix as the ONE without a mutation probe | the fix is unproven by mutation | ledger H-21 | FUTURE_PLAUSIBLE_UNPROVEN | run the interrupted mutation probe | record; it is the only fix in the ledger lacking one |
| H-28 | the same expensive work paid for twice | dedup by a key that can collide or be missed | **yes** | dedup keys are truncated digests — see H-41 | 64-bit keys | H-41 | FUTURE_PLAUSIBLE_UNPROVEN | as H-41 | as H-41 |

**Coverage, counted from this table rather than estimated: all 47 classes the
ledger actually carries now hold a prospective verdict.** The numbering runs to
H-51 but has gaps — «51 classes» was my own miscount, corrected here by counting
the ledger's rows instead of its highest number.

---

## 6. LIVE_OBSERVED — the guard blinds the agent to its own institutional memory

Found while building the §5 witness, in the live journal of the agent's own
first unattended run.

**The observation.** `logs/trace_860bd9e6…jsonl`, **2026-08-27T00:31:55Z** —
during the unattended tick, `file_read` on
`docs/audit/MASTER_ISSUE_REGISTRY.md` produced `injection_blocked`. The agent
was refused its own defect registry by its own defence.

**Reproducible on demand**, measured 2026-08-27 against the current tree:

| document | verdict | findings |
|---|---|---|
| `docs/audit/MASTER_ISSUE_REGISTRY.md` — the defect registry | **blocked** | 263 |
| `docs/CODE_NOTES.md` — the reasoning book | **blocked** | 126 |
| `docs/audit/HISTORICAL_FAILURE_LEDGER.md` | suspicious | 19 |
| `README.md` | suspicious | 3 |

**The mechanism, and it is a loop.** The guard blocks text that reads like an
instruction to an agent. The registry and the notes are where injection findings
are *documented*, quoting the payloads so the finding can be checked. Quoting a
payload makes the document trip the scanner. So the more carefully this project
documents its own injection work, the less of its institutional memory the agent
can read. The loop closed on the auditor too: the witness written today for §5 is
itself `blocked`, and so is the 2026-08-14 witness that preceded it.

**Why this is the dangerous half.** §5 is a channel an attacker might use. This
one is already costing the agent something on every run: the two documents that
hold every measured defect, every rejected alternative and every named boundary
are exactly the two it cannot read. An agent that cannot read its own registry
re-derives what is already known, and cannot check whether a defect it is about
to report has been recorded — the failure mode this repository has spent months
trying to remove.

**What is NOT claimed.** That last night's `no_grounded_target` outcome was
CAUSED by this block. The block and the outcome are both in the same run; the
causal link is not established and is not assumed.

**Classification: LIVE_OBSERVED.** Occurred in a real unattended run, and
reproduces on demand.

**Why no fix is applied in this pass.** The obvious repair is the premise this
project already disproved: on 2026-08-14 `file_read` was removed from the
exemption precisely because «workspace content is inside the trusted boundary»
is false. Re-granting docs a pass would restore that error. The real options are
a design decision, not a patch:

* **A** — exempt the repo's own audit documents. Rejected on sight: it rebuilds
  the 2026-08-14 mistake, and `docs/` is writable by ingestion.
* **B** — stop quoting payloads in documentation. A process rule, no code; costs
  the checkability that makes those findings worth anything.
* **C** — neutralise quoted payloads where they are written (a documented
  escaping convention), so a quotation stops reading as an instruction. Keeps
  both the quotation and the guard, and costs a convention nobody enforces yet.
* **D** — keep the verdict but change its EFFECT for repo-internal reads:
  annotate rather than block, so the text arrives marked instead of missing.

The choice belongs to the operator: it trades the agent's access to its own
memory against the strength of a defence that is currently working correctly.

**Operator's decision, 2026-08-27: D.** Implemented as MIR-171. The verdict
stands; only the consequence changes, and only for a source committed to this
repository — `core/repo_provenance.is_committed_source`. Provenance, not
location, because location as a trust signal is the exact error corrected on
2026-08-14. The signal rests on I-1: the agent cannot alter a tracked file,
because an overwrite escalates. **If I-1 ever goes, this weakens with it** — the
dependency is recorded so it cannot be lost. The check fails closed: no git, no
repository, a hung call — nothing becomes «ours». Runtime confirmation is still
outstanding and named: `injection_blocked_downgraded` in the next tick's journal.

---

## 4. Summaries (as of the first batch)

**1. Current reproduced defects.** One, LIVE_OBSERVED (§6), now fixed as
MIR-171 under the operator's option D and awaiting runtime confirmation: the injection guard
blocks the agent from reading its own defect registry and reasoning book — 263
and 126 findings respectively — observed during the unattended run of
2026-08-27T00:31:55Z and reproducible on demand. No fix applied: the repair is a
design choice with four named options, and the obvious one restores a mistake
this project already corrected. The two defects fixed
today (MIR-169, MIR-170) came from the live run and the battery, not from this
audit.

**2. Proven future hazards.** Four reachable or observed, and they are not
independent — three of them are one subsystem seen from three angles:

* **§5 / H-19 / H-34 / H-48 — the guard's own record is an unscanned channel.**
  LOCALLY_REACHABLE. The injection guard writes excerpts of untrusted text into
  the run journal; the journal reader is exempt from the scan by NAME. Links 1–3
  are the current state of the tree; delivery into a prompt is the missing
  evidence and is not assumed. One arm (the document case) is fixed as MIR-171. The
  other two were then measured and fixed as MIR-172 — a filename alone carries an
  order through `list_dir`, and a failing test prints file text through
  `run_tests` — with false positives measured on real output BEFORE the change
  (`clean` in both cases), so the exemption was replaced by a narrowed scan view
  rather than removed blind. `read_logs` is kept exempt on purpose: its excerpts
  are the guard's own preserved evidence, and scanning them at the READ side
  would take away the agent's ability to investigate its own incidents. The
  remaining repair is on the WRITE side — neutralise the excerpt where it is
  stored, so it stays evidence and stops being an instruction.
* **H-27 — an effect can land while its accounting fails silently.**
  LOCALLY_REACHABLE. The receipt write is deliberately swallowed so it cannot
  break execution, but it is swallowed WITHOUT a log. The defect is the silence,
  not the swallow; the response is a tripwire.
* **H-41 / H-28 — a 64-bit truncated digest stands for identity in an authority
  path.** FUTURE_PLAUSIBLE_UNPROVEN. `_effects_dedup_key` is `sha256(goal)[:16]`
  and an approval is FOUND by it. The one-line repair is a migration contract,
  not a patch: pending items carry the old key.
* **H-11 — old state read by new code.** FUTURE_PLAUSIBLE_UNPROVEN, and it is the
  clearest capability trigger in the document: today the agent owns the state and
  not the code. Wire self-modification and it owns both.

Three separate rows are marked **fragile** rather than hazardous — protections
that hold today for a reason that could stop being true: parallel writes would
make I-1 race-able (H-26, H-15); a data-driven source library would turn
`str.format` into the log4shell shape (H-37); and widening replay would break the
completion demand the fast path rests on (H-05). One candidate is named with its
missing evidence: H-11 becomes autonomous the moment self-modification is wired,
because the agent would then change code and read its own older state — today the
second half exists and the first does not.

**3. Disproved / protected hypotheses.** The majority, and this is the useful
half of the result. Absent by construction: H-16 (no release path), H-31 and
H-42 and H-40 (no fleet, one clock), H-39 (no package manager on the allowlist),
H-45 (single instance plus human-launched subagents). Blocked by an invariant
shown to bite: H-17, H-33, H-36, H-49 (the shell allowlist and the fail-closed
gateway); H-26 and H-15 (parallelism only for read-only steps); H-43 (`json.dumps`
escapes the frame); H-30 and H-08 (128 bits from the CSPRNG); H-37 and H-13 (the
format template is code, not data); H-32 (both repair paths are throttled); H-44
(the rewriting read holds an exclusive lock); H-05, H-06, H-07, H-46, H-12, H-50.

Two carry a named residual rather than a clean pass: H-29 preserves the damaged
row but never tells the CALLER that records went missing, and H-10's cost bound
is pinned by TIMING tests that flaked twice under full-battery load on
2026-08-26 — a guard that cries wolf teaches its readers to ignore it.

---

## 5. LOCALLY_REACHABLE — the guard's own record is an unscanned channel

**Historical mechanism (H-35).** A value crosses a boundary where it stops being
data and starts being instruction.

**The chain, link by link, with the evidence for each.**

1. *Untrusted text enters.* `web_fetch` / `web_search` are live tools in the
   registry and their output reaches planning.
2. *It is persisted.* Every tool output is scanned
   (`core/loop_step_execution.py:662`), and a non-clean verdict is written to the
   run journal with `InjectionScanResult.to_log_payload()`, whose findings carry
   **`excerpt`** — a slice of the offending text itself
   (`core/injection_guard.py:322`).
3. *It is read back through a channel that does not re-check it.*
   `_INJECTION_SCAN_EXEMPT` (`core/loop_step_execution.py:67`) holds exactly
   `list_dir`, `run_tests`, `read_logs`. The exemption is by tool NAME, so
   anything `read_logs` returns is handed on without scanning.
4. *The reader feeds a decision-maker.* `ReadLogsTool` is registered into the
   live surface at `app/bootstrap.py:118`; its result flows to the planner like
   any other tool result.

**Reachability is not hypothetical.** The material already exists: **397**
`injection_suspicious` / `injection_blocked` events sit in the live `logs/`
today, each carrying an excerpt of the text that tripped the guard. Links 1–3
need no perturbation to demonstrate — they are the current state of the tree.

**The shape worth naming.** The injection GUARD is what writes the attacker's
words into the one channel exempt from the guard. The defence manufactures the
laundering material. That is the «recovery depends on the same subsystem that
failed» family, seen from an angle the historical ledger did not cover.

**What is NOT claimed.** Delivery is unproven: no run has been observed in which
the planner called `read_logs` and an excerpt reached the prompt. Downstream
consequence is therefore not inferred from reachability. Blast radius, if
delivered, is bounded by I-1: the widest autonomous effect available to a
misled planner is creating a NEW file inside the workspace.

**Next evidence needed.** A bounded witness that `read_logs` output reaches the
model unscanned in a real step — `existing state → one controlled call →
observable prompt content`. No production behaviour changed to obtain it.

**If that witness lands**, the response is an authority rule, not machinery: the
exemption exists because these three tools produce machine-generated local text,
and `read_logs` no longer satisfies that premise once the guard writes untrusted
excerpts into what it reads. Removing one name from a frozenset is the whole fix.
It is deliberately NOT done on the strength of this analysis alone.

---

**The single fact that would invalidate most of this document.** Granting the
agent autonomous overwrite — directly, or by wiring the self-apply lane into the
tick, or by any delete-then-create path. I-1 is what makes «create only» true,
and «create only» is what makes most of these classes unreachable.
