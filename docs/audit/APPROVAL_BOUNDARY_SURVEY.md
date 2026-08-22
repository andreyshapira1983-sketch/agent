# Which approvals sit on a real authority boundary — a survey, not a verdict

Measured 2026-08-22 on the operator's word. The question, in his framing: the
security of an autonomous agent should be built from a few hard boundaries
inside which the human is absent from the executive loop, not from a thousand
human "yes"es. So the prior question is not *which approval to keep* but
*should the operation cross an authority boundary at all*. Where it does not,
the approval is architecturally absent by right — scripted mistrust, the same
kind of thing as a scripted decision.

The membership test is C0.P, already ratified, in two questions:

    touches human sovereignty?   money past the envelope, rights, accounts,
                                 external irreversible commitments
    touches agent viability?     the ability to keep existing / be restored
    neither → interior territory → an approval does not belong there

The approval inbox drives exactly three operations (live: 137 rows, all
`requested_by=autonomous_runtime`). Each is walked to the most external,
most irreversible thing its effect path can reach.

## 1. `autonomous_runtime.allow_effects`

**What it switches.** `dry_run=False`. Nothing else. It does not add a tool,
widen a surface, or open a path — it lets the already-configured effect layer
actually run. The effect layer is exactly two tools
(`EFFECTFUL_TOOL_NAMES = {"file_write", "shell_exec"}`), and both are confined:

- `file_write` resolves every path and `relative_to(workspace_root)` — a write
  outside the workspace is refused, secrets are refused, and a workspace write
  is reversible (the module's own docstring: "delete it; no prior backup
  needed").
- `shell_exec` runs one whitelisted command with no shell metacharacters
  (`_FORBIDDEN_CHARS`), git limited to `add/commit/checkout`, **commit refused
  on main/master**, `--no-verify` refused, and `push/pull/fetch/clone`
  deliberately absent from the whitelist — "they touch the network or refs and
  are intentionally absent".

**Outer reach measured.** The furthest this path goes is a reversible write or
a local commit on a non-protected branch. It does not reach the network, does
not touch money, rights, or accounts, and cannot push. **Under C0.P this reads
as interior territory** — the lab, with its own gates and reversibility — not a
sovereignty crossing. The human boundary that C0.P would actually place is one
step further out, at push / network / spend, and those are already absent from
the surface by construction.

Candidate reading, for the operator: `allow_effects` may be a boundary
inherited from the "thousand yes"es era — a human checkpoint on writing inside
the lab, where the lab already contains the risk. Not asserted; this is the
row most worth his decision.

## 2. `launch_subagent`

**Correction, 2026-08-22, caught in review against the code.** The first
version of this survey said the operation "cannot reach the network or money".
That is false: the child's permitted tool set is a subset of `file_read,
list_dir, web_search, web_fetch, rss_fetch, semantic_scholar_search, run_tests,
read_logs, diff_file` (`tools/spawn_subagent.py:107`), so a subagent reads the
public internet, and its cognition runs through the model router, so it spends
the API budget. A helper is not offline and not free.

**The claim that IS true, and it is the survey's real lens:** the operation
grants **no new authority beyond the parent's already-delegated envelope**.
Spawn is never in a child registry (no recursion), `shell_exec` and
`file_write` are always blocked from children, and the contract's
`allowed_tools` / budget scope can only narrow. The child USES what the
operator already delegated — the API budget, the public-internet read access,
the read-only tool set — and can WIDEN none of it.

That is the distinction the whole survey turns on: **using a delegated
resource is interior executive territory; widening the resource or the right
is the human boundary.** "It spends money" and "it expands its own right to
spend money" are different sentences, and only the second crosses C0.P.

**What is still not interior:** concurrency itself. Helpers amplify
throughput, resource draw and blast radius over time — constitutional, an
envelope (a cap set once), not a per-launch ask.

Candidate reading, unchanged by the correction: the per-launch approval may be
the wrong shape — a talon where a constitutional concurrency cap belongs —
PROVIDED the worker cap, budget and tool surface are already bounded by
envelope. Whether an individual launch still deserves a checkpoint is the
operator's call.

## 3. `self_apply_lane.run`

**What it is.** The agent editing its own code, through
`core.safe_vcs.SafeVCS`, "which has no push / fetch / pull / remote method at
all" — the lane cannot reach the network by construction. Rollback is built in
(`reset_hard` + temp-branch delete). So the lab both contains the change and
records it if the code is not as it should be: the change does not leave the
lab unrepaired.

**Outer reach measured.** Local working tree and local refs only; no network,
no remote, fully reversible. **Under C0.P this is the clearest interior case of
the three** — self-editing inside a lab with gates and rollback is precisely
the "read, write, test, roll back, retry, finish" the operator named as the
agent's own territory. The human boundary lies past it, at push, which the lane
structurally cannot perform.

Note on the current burn: `self_apply_lane.run` is the one whose approval is
consumed by the ATTEMPT (MIR-117 / MIR-116-adjacent). Under this survey that is
a moot fix — if the operation is interior, the burn semantics of an approval
that should not exist is not the thing to repair.

## What the survey establishes, and what it does not

**Established, from code — stated per operation, because the first version
overclaimed it as one sentence:** none of the three operations grants reach
beyond the envelope the operator has already delegated. `allow_effects` adds
exactly workspace-confined writes and local git on top of what dry-run already
has (model spend and web reads exist in dry-run too — the switch does not add
them). `launch_subagent` adds concurrent use of already-delegated read/web/model
resources, and nothing else. `self_apply_lane.run` adds local commits with
rollback. What none of them can do is WIDEN the envelope: no push, no new
credential, no new access class, no raising of its own budget — those lie
outside all three surfaces by construction.

**Not established, and reserved to the operator:** whether each approval should
therefore be removed, kept as defence-in-depth, or reshaped (per-launch →
concurrency cap). The measurement says none of the three crosses a sovereignty
line as built; it does not say the checkpoints are worthless, because
defence-in-depth against a confinement bug is a legitimate reason to keep a
gate the happy path does not need. The asymmetry the operator named stands: a
superfluous boundary costs convenience, a missing one costs sovereignty — so
removal is his decision, made boundary by boundary, not a cleanup to infer.

**The axis this survey missed — added 2026-08-22 after checking the field.**
Everything above measures ONE axis: whose authority an operation widens. There
is a second, orthogonal axis this survey did not apply, and both reviewing
models missed it together: **who else can steer the agent.** Willison's lethal
trifecta — private data + untrusted content + an outbound channel — is safe in
any pair and dangerous as a triple. **Measured against the production paths
(falsification pass, same day): the default unattended path already breaks the
trifecta twice by construction** — `_AUTONOMOUS_GOAL_BLOCKED_TOOLS` blocks the
web tools AND `spawn_subagent`, and SECRET tool output is deep-redacted before
it reaches the planner or memory. The assembled triple exists only on paths
where web tools are live (interactive sessions), and — the reason this
paragraph stays — it is the standing cost of exactly the decision this survey
invites: unblocking web or subagents on the unattended path would re-assemble
the trifecta there.

So the correction to the survey's own lens: **the C0.P test measures
authority; the approvals it examined also served as hijack friction** —
protection against a third party, not against the agent. "Inside the lab" is
not "inside the trusted": the lab's interior contains the untrusted internet.
Removing a gate on the authority axis widens the injection blast radius on the
other, and the field's numbers are not small (ASB: 84.3% max average injection
success; WASP: 16–86% of injected instructions begin executing). The field's
defence: keep at least one leg of the trifecta broken on every path. Any
decision to remove or reshape a gate must therefore answer BOTH axes, not the
authority axis alone.

**One caveat on the confinement claim.** "Confined to the lab" rests on the
resolvers holding: `file_write`'s `relative_to` check, `shell_exec`'s
whitelist, `SafeVCS` having no remote verb. Each is tested elsewhere, but the
survey did not re-run those tests; a confinement bug in any one would move an
operation from interior to boundary-crossing, which is exactly why the gates
may be worth keeping as a second wall regardless of the happy-path reading.
