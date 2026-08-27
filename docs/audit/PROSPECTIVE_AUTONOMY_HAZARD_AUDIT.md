# Prospective autonomy hazard audit

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
| model-generated configuration | **partial** | the charter `knowledge/doctrine/future/CORPORATE_MODEL.md` steers goal choice and is an `.md` the agent may PROPOSE; it may not overwrite it (I-1) |

**The one-sentence denominator.** The agent's autonomous effect surface today is
*creating new files inside the workspace* — nothing else. Everything wider is
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
`gateway.evaluate` is called from exactly one place: `core/loop_step_execution.py:342`.
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
| H-01 | a diagnostic payload consumed as valid data | evidence pipeline that banks verified claims | **yes** | reproduced and fixed historically | fixed; MIR-140 interaction remains | ledger H-01 | queued for the prospective axis | whether the fix survives autonomous evidence gathering | re-examine |

Remaining classes (H-02…H-10, H-12…H-15, H-18…H-30, H-32, H-34, H-37…H-44,
H-46…H-51) are **not yet examined in this pass**. They are listed here rather
than silently omitted; a class with no row has no verdict.

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

---

## 4. Summaries (as of the first batch)

**1. Current reproduced defects.** One, LIVE_OBSERVED (§6): the injection guard
blocks the agent from reading its own defect registry and reasoning book — 263
and 126 findings respectively — observed during the unattended run of
2026-08-27T00:31:55Z and reproducible on demand. No fix applied: the repair is a
design choice with four named options, and the obvious one restores a mistake
this project already corrected. The two defects fixed
today (MIR-169, MIR-170) came from the live run and the battery, not from this
audit.

**2. Proven future hazards.** One reachable today, described in §5: the
injection guard persists excerpts of untrusted text into the run journal, and
the journal reader is exempt from the injection scan. Classified
LOCALLY_REACHABLE — links 1–3 are the current state, link 4 (delivery into a
prompt) is the missing evidence and is not assumed. One candidate is named with its
missing evidence: H-11 becomes autonomous the moment self-modification is wired,
because the agent would then change code and read its own older state — today the
second half exists and the first does not.

**3. Disproved / protected hypotheses.** Six so far: H-16 and H-31 are absent by
construction (no release path, no fleet); H-45 is blocked by two independent
facts; H-17, H-33 and H-36 are blocked by named invariants that were shown to
bite.

---

## 5. LOCALLY_REACHABLE — the guard's own record is an unscanned channel

**Historical mechanism (H-35).** A value crosses a boundary where it stops being
data and starts being instruction.

**The chain, link by link, with the evidence for each.**

1. *Untrusted text enters.* `web_fetch` / `web_search` are live tools in the
   registry and their output reaches planning.
2. *It is persisted.* Every tool output is scanned
   (`core/loop_step_execution.py:660`), and a non-clean verdict is written to the
   run journal with `InjectionScanResult.to_log_payload()`, whose findings carry
   **`excerpt`** — a slice of the offending text itself
   (`core/injection_guard.py:322`).
3. *It is read back through a channel that does not re-check it.*
   `_INJECTION_SCAN_EXEMPT` (`core/loop_step_execution.py:66`) holds exactly
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
