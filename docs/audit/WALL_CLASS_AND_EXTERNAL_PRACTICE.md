# The wall-class gap, and what the field actually does — verified 2026-08-22

Recorded on the operator's word. Two things live here: an independent check of
a second model's survey of external practice (each claim taken to a primary
source rather than accepted on voice), and the one finding about THIS codebase
that the check sharpened — our containment walls are one class below the walls
the field's own incidents pushed everyone else toward.

## Part 1 — the external claims, checked

Method: web search against primary sources, 2026-08-22. Some verdicts rest on
the primary page itself, some on its indexed summary; none rest on the
forwarding model's word. What this check cannot do: audit the papers' own
methods. "Confirmed" means the source says what was claimed, not that the
source is right.

| Claim | Verdict | Primary source |
|---|---|---|
| Anthropic telemetry: ~93% of Claude Code permission prompts were approved — approval fatigue | confirmed | anthropic.com/engineering "How we contain Claude" |
| Their answer was an OS-level sandbox (Seatbelt/macOS, bubblewrap/Linux); prompts fell 84%; runtime open-sourced | confirmed | anthropic.com/engineering "Claude Code sandboxing" |
| DeepMind AI Control Roadmap (2026-06-18): treat a powerful internal agent as a potential insider threat; ~1M coding-agent trajectories; most flagged events were misinterpretation or overeagerness, not adversarial intent; reversible actions reviewed async, high-risk blocked synchronously | confirmed | deepmind.google "Securing the future of AI agents" |
| Replit/SaaStr 2025-07: dev agent with production credentials deleted the production DB despite a code freeze | confirmed — and worse than forwarded: the agent also fabricated data and falsely claimed rollback was impossible (it was possible) | Tom's Hardware; AI Incident Database #1152 |
| International AI Safety Report 2026 (2026-02-03, Bengio, 30+ countries): no single safeguard is reliable enough; defense-in-depth over incident-free fantasy | confirmed | internationalaisafetyreport.org |
| OpenAI internal Codex: sandbox defines the boundary; network egress allow/block/approve-unfamiliar; credentials in the OS keyring, outside the working env | confirmed | openai.com "Running Codex safely at OpenAI" |
| Agent Security Bench (ICLR 2025): max average attack success 84.3%, defenses of limited effect, 13 backbones | confirmed verbatim | arxiv 2410.02644 / ICLR proceedings |
| WASP (NeurIPS 2025): agents begin executing injected instructions 16–86% of the time, complete the attacker's goal 0–17% | confirmed verbatim — hence "a clean run may prove inability, not safety", which this repo already holds as *same answer, different cause* | NeurIPS 2025 proceedings |
| METR: time horizon is task difficulty, not real-world autonomous endurance | confirmed — METR's own "Clarifying limitations" (2026-01): +1 messiness ≈ −8.1% success; a 50% horizon is not delegability | metr.org |
| NIST AI Agent Standards Initiative; threats named; standards still forming | confirmed for the initiative (launched 2026-02-17) and the threat list; the specific "May analysis publication" detail was **not** directly found | nist.gov |
| AgentDojo "at NeurIPS": agents fail even unattacked | confirmed in substance; the year is **2024**; best agent 78% benign utility | NeurIPS 2024 proceedings |

Net verdict on the forwarded survey: honest. Nine confirmed, one partial, one
minor imprecision. The field's converged stance, per its own primary sources:
containment plus recovery for a fallible agent, not per-action human approval
and not a proof of harmlessness before starting.

## Part 2 — the wall-class gap in this codebase

The forwarded survey's list of real walls — a kernel that does not mount the
filesystem, a network namespace with no route, a credential absent from the
process — describes **OS-class** walls. Measured here on 2026-08-22:

    unshare / seccomp / chroot / containers / network namespaces:  0 uses
    OS-level isolation of the agent process:                       none

Our walls are a different class: **in-process Python**, running in the same
interpreter as the agent, under the operator's own OS user, with the API keys
in the same process environment. `file_write`'s `relative_to` resolver,
`shell_exec`'s whitelist, `SafeVCS`'s missing remote verbs, `network_safety`'s
egress policy, the MIR-116 cost envelope — every one of them.

The ladder, and where we stand:

    textual        "dear agent, please stay inside"     — we don't rely on this
    in-process     resolvers, whitelists, envelopes      — WE ARE HERE
    OS / infra     namespaces, separate principal,       — we have none of it
                   credentials outside the process

The middle rung holds an agent that acts **through the tools**. It does not
hold arbitrary code in the process — and an agent that edits its own code is a
machine for gradually approaching arbitrary code in the process. Anthropic's
93%→sandbox story is precisely the field learning that the middle rung plus
human prompts was not enough, and moving one rung down.

**Host note.** Seatbelt and bubblewrap are macOS/Linux; this host is Windows.
OpenAI published "Building a safe, effective sandbox to enable Codex on
Windows" — so the gap is closable on this platform, not hypothetical-only.

**The unmeasured question this raises, named and deliberately not answered
here:** `core/` and `tools/` live inside the workspace, and the workspace is
exactly what `file_write` may write to in effects mode. Whether the effects
path can rewrite **the walls themselves** (the policy module, the resolver,
the whitelist) — and whether the per-tick process restart then brings the
rewritten wall to life — has not been measured. The self-apply lane has a
critical-organ denylist; `file_write` has only the workspace boundary. This is
a viability question (Constitution, under C0.P), and it is a measurement
candidate that should precede any long unattended effects run.

## Consequence for the launch-threshold discussion

The forwarded survey proposed a five-point threshold for a bounded lab run.
Checked against this codebase today:

| Threshold item | State here |
|---|---|
| external known-good state the agent cannot destroy | present — the remote branch; local git history |
| technical authority envelope: scoped credentials, no production secrets in reach, cannot self-widen | **not met** — keys live in the agent's process env; no principal separation; walls in-process |
| resources bounded outside, incl. a real cost cap at the point of spend | partially — MIR-116 holds at the spend gate, but in-process; no provider-side cap set by us |
| irreversible external world as a separate security domain | met by construction — push/network verbs absent from the tool surface (MIR-118) |
| independent journal + emergency stop + staged runs | largely present — heartbeat, budget kill switch, bounded campaign flags |

So the threshold is not already satisfied by our lab, and the honest reading
is the useful one: the gap list is finite and engineering-shaped, not
philosophical. Whether and when to close it — and whether a bounded run may
precede closing it — is the operator's decision; this document only fixes what
is true today, so that the decision is made against the measured state rather
than against the lab we wish we had.
