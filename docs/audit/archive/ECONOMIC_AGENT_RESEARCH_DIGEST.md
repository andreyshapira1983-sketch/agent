# Thirty years of resource-rational agents — a digest against our organs

Collected 2026-08-27 on the operator's instruction, before building the economic
self-accounting organ. The question the operator put: the field has decades of
experiments on agents that manage their own budgets — what do they actually
prove, and which of it lands here?

Method note: each entry carries its evidence strength and a LOCAL verdict —
`adopt` / `adapt` / `record only` / `do NOT import`. A mechanism is not adopted
because the paper is famous; it is adopted when its prerequisite exists here.

---

## The lineage

### 1. Value of computation (Horvitz 1987; Russell & Wefald 1991)
**Mechanism.** Deliberation is itself an action with expected value and cost;
compute only while the expected improvement of the decision exceeds the cost of
computing it. «Bounded-optimal» agents act optimally *given* their compute.
**Evidence.** Foundational theory plus classic experiments; the frame the whole
field stands on.
**Local verdict: adopt as the organ's decision rule.** We already record both
halves per cycle — `cost_units_spent` and the outcome (`verified_chunks`,
completion). The stop question «is another cycle worth its cost» is exactly
value-of-computation, and TODAY nothing asks it.

### 2. Anytime algorithms and deliberation scheduling (Dean & Boddy 1988; Zilberstein 1996; Hansen & Zilberstein 2001)
**Mechanism.** Algorithms whose answer improves with time, plus a monitor that
decides when further improvement stops paying. Monitoring/stopping is solved
with performance profiles.
**Evidence.** Strong; 800+ citations, deployed in real-time systems.
**Local verdict: already half-present, name it.** The paced campaign IS an
anytime system, and `max_unproductive_streak=3` is a crude performance-profile
monitor. The refinement the literature suggests: stop on *marginal utility per
unit cost*, not on a fixed streak count.

### 3. Contract Net (Smith 1980) and market-based control (1990s)
**Mechanism.** Tasks allocated by announcement–bid–award; prices coordinate
decentralised resource use.
**Evidence.** Solid for MULTI-agent allocation.
**Local verdict: do NOT import.** One agent, no market. The one piece that maps
— a delegated task carries an explicit budget scope — already exists as
`CanonicalBudgetScope` in the subagent contract.

### 4. Constrained / budget-constrained MDPs (Altman 1999; budget-CMDP line)
**Mechanism.** Maximise reward subject to hard cumulative cost bounds; the
budget is a *constraint*, not the objective.
**Evidence.** Mature theory, applied in scheduling/maintenance.
**Local verdict: adopt the SEMANTICS, not a solver.** This is the formal answer
to the operator's continuous-mode question: the daily ceiling is a constraint
(backstop), while the agent optimises verified outcomes inside it. Not «the
timer manages the agent».

### 5. FrugalGPT (Chen, Zaharia & Zou 2023)
**Mechanism.** LLM cascade: try cheap models first, escalate on low output
confidence; plus caching and approximation. Up to **98% cost reduction while
matching GPT-4** on their benchmarks.
**Evidence.** Medium-high; controlled benchmarks, widely replicated as a
pattern.
**Local verdict: adapt LATER, and the blocker is named.** A cascade is only as
good as the confidence signal that stops escalation. Our verifier's
discrimination is MEASURED imperfect (MIR-141: perfect on form, blind on
content; MIR-060: verdict follows citation resolution, not truth). Cascading on
an uncalibrated signal stops early on false confidence — the cheap model's
error would be *invisible by construction*. Order of work: fix the signal
before cascading on it.

### 6. RouteLLM (2024) and learned cost-aware routing
**Mechanism.** A learned router picks strong vs weak model per query; **85%
cost reduction at 95% of GPT-4 quality** on MT-Bench.
**Evidence.** Medium-high.
**Local verdict: adopt — this is the smallest real step.** `preferred_model`
already learns the best measured model per role from live outcomes. The word
«cost» does not appear in that organ (measured today: zero occurrences). Adding
the cost denominator — among models above a success bar, prefer the cheaper per
success — is RouteLLM's insight on machinery we already run.

### 7. Compute-equalized evaluation (Snell et al. 2024, via Agent Contracts 2026)
**Mechanism/finding.** «When compute is equalized, sophisticated reasoning
strategies often do not outperform simpler baselines; much apparent improvement
comes from using more resources rather than using them more intelligently.»
**Evidence.** Medium-high, and it is a WARNING, not a feature.
**Local verdict: adopt into the self-report.** Our economic report must state
outcomes PER UNIT SPENT (`achieved per 100 units`), or the organ will teach the
agent that spending more *is* improving — self-deception with a ledger.

### 8. Agent Contracts (Jan 2026, COINE/AAMAS)
**Mechanism.** A contract carries a multi-dimensional resource vector (tokens,
API calls, iterations, web searches, CPU seconds, USD); enforcement split into
*soft* (budget-aware prompts the agent may ignore) and *hard* (external monitor
halts on breach).
**Evidence.** Formal framework; fetched and read 2026-08-27.
**The decisive negative result for us:** the paper provides **no mechanism
where the agent tracks cost-per-outcome internally** — enforcement is external
only, and SelfBudgeter/BATS are cited without measured results. The gap we
found locally (2 MB of spend records, zero decision-readers) is the FIELD'S
open edge too, as of 2026.
**Local verdict:** our hard half already exists (governor, kill switch); the
multi-dimensional budget vector is worth adopting when the grant is next
reworked; the learning half we build ourselves, small and measured.

### 9. ZEBRA (2026) — budgeted allocation across phases
**Mechanism.** An allocation agent estimates per-phase utility curves and
splits a fixed budget across phases.
**Local verdict: record only.** Presupposes phase-utility estimates we cannot
yet produce honestly; revisit once the self-report has accumulated real
cost-per-outcome pairs.

---

## What this changes in the organ plan

1. The self-report's unit is **outcome per unit spent**, never raw spend
   (entry 7), computed from pairs the ledger already holds.
2. The first behavioural change is the **cost axis in `preferred_model`**
   (entry 6): smallest step, uses only success counts and tiers we already
   trust.
3. The continuous-mode ceiling is framed as a **CMDP constraint** (entry 4):
   a backstop the agent optimises within, not a manager.
4. The stop rule for a continuous campaign should trend toward **marginal
   value per cost** (entries 1–2), replacing the fixed unproductive-streak
   count once the report exists to feed it.
5. **Cascades are deferred with a named blocker** (entry 5): our confidence
   signal is measured miscalibrated, and a cascade would convert that defect
   into invisible early stops.

What we are about to build — an agent that reads its own spend-vs-achievement
record and lets it change tool and model choice — is not covered by a shipped
result even in the 2026 frameworks. The field supplies the decision rule, the
reporting discipline and the routing pattern; the closed loop is ours to build
and measure.
