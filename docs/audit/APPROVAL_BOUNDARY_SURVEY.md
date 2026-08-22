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

**What it is.** A helper drawn from already-permitted capabilities, running
under a contract whose `allowed_tools` / `forbidden_tools` scope it — and a
subagent's tool surface is bounded by the contract, not widened past the
parent. Under C0.P a helper for interior work is interior work.

**The one thing that is NOT interior:** concurrency itself. A helper does not
grant a new right, but it amplifies throughput, resource draw and blast radius
over time — a viability/safety property, hence constitutional. So the C0.P
boundary here is not "approve this subagent" but "the number of concurrent
workers is bounded", set once as an envelope, not asked per helper.

Candidate reading: the per-launch approval may be the wrong shape — a talon
where a constitutional concurrency cap belongs. Whether an individual launch
still deserves a checkpoint is the operator's call; the measurement only says
the launch itself does not cross a sovereignty line, while the aggregate count
does.

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

**Established, from code:** all three operations' effect paths are confined to
the workspace/local-repo lab, reversible, and unable to reach the network,
money, rights, or accounts. The real sovereignty boundary — push, spend,
external commitment, new credentials — lies outside all three and is already
absent from their surfaces by construction.

**Not established, and reserved to the operator:** whether each approval should
therefore be removed, kept as defence-in-depth, or reshaped (per-launch →
concurrency cap). The measurement says none of the three crosses a sovereignty
line as built; it does not say the checkpoints are worthless, because
defence-in-depth against a confinement bug is a legitimate reason to keep a
gate the happy path does not need. The asymmetry the operator named stands: a
superfluous boundary costs convenience, a missing one costs sovereignty — so
removal is his decision, made boundary by boundary, not a cleanup to infer.

**One caveat on the confinement claim.** "Confined to the lab" rests on the
resolvers holding: `file_write`'s `relative_to` check, `shell_exec`'s
whitelist, `SafeVCS` having no remote verb. Each is tested elsewhere, but the
survey did not re-run those tests; a confinement bug in any one would move an
operation from interior to boundary-crossing, which is exactly why the gates
may be worth keeping as a second wall regardless of the happy-path reading.
