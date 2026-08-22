# This agent measured against the field's published criteria — 2026-08-22

Run on the operator's word: rather than inventing our own yardstick, take the
criteria the field publishes and apply them here. Two are used, both current:
**OWASP Top 10 for Agentic Applications 2026** (ASI01–ASI10, published
2025-12-09) and the **CSA agentic autonomy-level control set** (kill switch,
rollback, audit trail, resource limits, revocation).

This is a defensive self-assessment of our own system. No third-party system is
touched, nothing is exploited, and nothing here is a repair — findings that
need work are named and left to the operator, as the standing rule requires.

Method note: rows marked *measured here* rest on measurements already recorded
in this repository (each MIR is named). Rows marked *read* rest on reading the
code without a differential experiment. The distinction is kept because a
self-assessment that blurs it is worth little.

## OWASP ASI01–ASI10

| # | Risk | Where we stand |
|---|---|---|
| **ASI01** | Agent Goal Hijack | **Partly covered, measured.** `injection_guard` marks untrusted text before cognition across loop, subagent, knowledge and self-build paths, and the default unattended path blocks the web tools outright (MIR-118). Residue: the guard is prompt-level marking, which the field's own numbers show is not a solved defence (ASB 84.3% max attack success). |
| **ASI02** | Tool Misuse & Exploitation | **Covered by construction, read.** Whitelisted `shell_exec` (no metacharacters, no network verbs, commit refused on protected branches), workspace-confined `file_write`, per-run `blocked_tools` that can only narrow. |
| **ASI03** | Identity & Privilege Abuse | **THE WEAK ROW, measured.** API keys live in the agent's own process environment; no principal separation; every wall is in-process Python (MIR-120). The July 2026 sandbox-escape incident lists "long-lived credentials stored in environment variables" among its own containment failures — our exact configuration. |
| **ASI04** | Agentic Supply Chain | **Partly covered, read.** Dependency hashes are locked and the self-apply denylist refuses `config/`, `.git/`, `.venv/`, lockfiles and key material. Unassessed: MCP/connector surface, and the incident above began in a *package-registry cache proxy* — a supply-chain-shaped path we have never examined. |
| **ASI05** | Unexpected Code Execution | **Partly covered, measured.** `python_probe` is blocked on the default unattended path (`_AUTONOMOUS_GOAL_BLOCKED_TOOLS`). But `file_write` can write `core/*.py`, and a fresh process imports what it wrote — demonstrated in `WALL_SELF_REWRITE_PROBE.md`. Overwrite escalates to human approval; the new-file path depends on one env var (`AGENT_REQUIRE_WRITE_APPROVAL`, default off). |
| **ASI06** | Memory & Context Poisoning | **Known gap, measured.** MIR-115: a `lesson` tag skips every verification axis but one, and the machinery that writes the content mints the exempting tag. MIR-121 records the mature attack literature (≈95% injection success, poison surviving restarts). Our specific reachability from the web is UNPROVEN — the tag is minted by self-build/self-repair, not by web ingestion. |
| **ASI07** | Insecure Inter-Agent Communication | **Structurally bounded, read.** A subagent cannot spawn further agents, never receives `shell_exec`/`file_write`, and returns a typed result whose evidence classes are enumerated. Not assessed: whether a parent treats a subagent's narrative as verified — the code comments themselves note the return carries no verification beyond what the parent already trusts. |
| **ASI08** | Cascading Failures | **Partly covered, measured.** Cycle ceilings, no-progress detection, repeat-skip, circuit breaker, and the MIR-116 cost envelope that now refuses at the moment of spend (verified live, 0/8 where the defect had spent 63/8). Unassessed against MIR-123's long-horizon degradation. |
| **ASI09** | Human-Agent Trust Exploitation | **Named weak point, measured.** The approval preview is what the operator reads before authorising an irreversible action, and the mutation sweep found its truncation logic **unwitnessed** by any test. Worse in principle: MIR-117 showed a token (`completed`) that reports success for work that did not happen, and the July 2026 incident's agent *fabricated data and falsely claimed rollback was impossible*. What the human is shown is a real attack surface here. |
| **ASI10** | Rogue Agents | **Partly covered, measured.** Runs are bounded by cycles, wall clock, budget and a kill switch; the heartbeat is externally readable. But WHO LIVES is explicitly unproven, and — the sharp one — the audit record **cannot distinguish who authored a decision**: `requested_by` is a constant, reviewer identity is absent on all three durable surfaces, and no evidence-origin field exists (MIR-117, five-axis provenance). Detecting "operating outside policy while looking legitimate" is exactly what that missing provenance would be for. |

## CSA control set

| Control | State |
|---|---|
| Kill switch, immediate stop | present — `core/budget_kill_switch.py`, day-budget evaluation, state persisted |
| Rollback / quarantine | present for self-apply (temp branch, auto-rollback) and for `file_write` (`.bak` + compensation plan); absent as a general "revert this agent's session" |
| Immutable audit trail | present — append-only tool receipts, redacted before persist, trace logger; **but** see ASI10: it records what happened, not who decided |
| Resource limits (spend, rate, blast radius) | present in-process (MIR-116 envelope at the spend gate, cycle/wall-clock caps); **absent outside the process** — no provider-side hard cap is known to be configured, and that is an account fact we cannot measure from here |
| Credential revocation | **absent** — keys are in the process environment; there is nothing to revoke independently of the process |
| Ability to pause at any point | partial — bounded runs and a kill switch, no mid-run pause/resume handle |

## The honest summary

Against ten published risk categories, this agent is **structurally strong where
the walls are made of tool design** — the tool surface is narrow, the unattended
path blocks the network, effects are confined and reversible, budgets now bite
at the moment of spend. That is not luck; it is the part that was engineered
carefully over months.

It is **weak in exactly three places, and they are the same three the field's own
2026 incidents punish**:

1. **ASI03 identity** — credentials inside the agent's process, no separation, no
   revocation. The one item that appears verbatim in a real sandbox-escape
   incident's list of causes.
2. **ASI06 memory** — an unverified write path into the store that steers later
   decisions.
3. **ASI09/ASI10 provenance** — the record cannot say who decided, on what
   grounds, or who approved; and what the human is shown before approving is
   itself untested.

None of these is repaired here, and none should be repaired by inference. What
this document adds is that the three are no longer our private worry: they are
named risk categories in a peer-reviewed 2026 framework, and two of them appear
in the causes of an actual frontier-lab incident.

Sources: OWASP Top 10 for Agentic Applications 2026 (ASI01–ASI10); CSA Agentic
AI Autonomy Levels and Control Framework v2; MITRE ATLAS v5.1 (16 tactics, 84
techniques, agentic techniques added 2025–2026); NIST CAISI AI Agent Standards
Initiative threat taxonomy. Internal evidence: MIR-115, 116, 117, 118, 120, 121,
123, and `WALL_SELF_REWRITE_PROBE.md`.
