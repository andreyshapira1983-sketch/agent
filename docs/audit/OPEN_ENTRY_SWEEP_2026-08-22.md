# Sweep of all 38 open registry entries — 2026-08-22

Assignment, in the operator's words: «я принципиальный человек… я хочу чтобы ты
вещь досконально начал разбирать. Я не люблю оставлять это на потом… если ты
понимаешь что ты не можешь эту разобрать ошибку и может быть ошибка непонятно
для тебя и ты начинаешь догадываться — проверь через интернет… и тебе очень
важно не допустить ошибку считать что это правильные советы».

Method, held to throughout: re-derive each claim against the current code or the
live stores rather than re-reading the entry; prefer running the code to reading
it; go to the field only where the next step would otherwise be a guess, and
record where the field's answer does NOT transfer.

## Headline

**Not one of the 38 was stale.** The sweep began on the hypothesis that a
month-old list would be inflated by entries already fixed. It was not: every
entry describes something still true. The registry does not over-report.

What it does do is under-report. Nine entries were found to understate their own
subject, and two contained claims that are now false.

## Result per entry

| verdict | entries |
|---|---|
| confirmed, unchanged, accurate | 011 · 020 · 021 · 023 · 026 · 044 · 100 · 103 · 114 · 120 |
| confirmed and found WORSE than recorded | 024 · 035 · 050 · 058 · 096 · 097 · 104 · 115 · 126 |
| contained a claim that is now FALSE | 105 (Russian immunity) · 126 (no census exists) |
| premise measured and REFUTED | 122 (context does not grow within a run) |
| materially REPAIRED since it was written | 098 |
| answered a question the operator had ruled must be answered first | 099 |
| awaiting the operator's decision, nothing to measure | 015 · 117 · 118 · 119 · 123 · 127 |
| written this same day, current by construction | 128 · 129 · 130 |
| harm hunt came back negative for the entry, positive elsewhere | 008 |
| confirmed and narrowed to an exact remaining half | 016 · 045 · 121 · 124 · 125 |

Two entries were opened by the sweep itself: **MIR-131** (maintenance organs the
autonomous path cannot move) and **MIR-132** (no provider-health notion).

## The four findings that outlived their entries

**1. Forward-only gates over records that cannot leave.** MIR-058 recorded it
first: "the gates are forward-only; they stopped new credit, they did not unwind
credit already granted." MIR-115 is the same shape one level up, and worse —
eligibility is frozen at write time (`decide_usage_eligibility` is called zero
times on the read path), and 126 of the 127 offending rows predate the narrowing
AND carry `lesson`, so eviction cannot reach them either. **A repair in this
family that does not say which existing rows it changes, and how they can leave
the store, changes a number only for records that do not exist yet.**

**2. Deferral is not free; it spends the choice.** MIR-058's three-way fork can
no longer be executed: 70 of 75 procedure→episode references (93%) now point at
evicted episodes, so the "reset the unverifiable portion" option cannot be
computed and "retire and re-earn" cannot identify its targets. MIR-050's return
condition — "revisit when density is enough" — cannot be satisfied by waiting,
because density did not move in a month (1.4 → 1.5) while the evidence was
destroyed. MIR-097's data cleanup, performed without the code fix, **lasted 24
hours**.

**3. Instruments that read clean because they broke.** The MIR-058 legacy report
defines "legacy" as a reference that resolves into the live store, so an evicted
episode cannot be legacy by construction and the headline converges to zero
exactly as the credit becomes less verifiable. MIR-126's census was a grep that
misses two-line handlers, undercounting 68 as 9 — while the repo's own
`scripts/except_audit.py` does the classification MIR-126 asks for, reports zero
unexplained, and walks `core/` only, which excludes eight of the nine sites the
entry worries about.

**And the same defect struck this sweep four times:** a wrong marker string, a
wrong nesting depth (`source_registry.jsonl` rows are nested twice, so every
pattern scored 0 of 5155), an AST census that excluded same-file callers and so
labelled the one correctly-wired mechanism dead, and `schtasks` through the Bash
tool returning 0 on a host where PowerShell sees 201 scheduled tasks. **Standing
rule from this sweep: every "nothing found" needs its probe proved before its
result is believed.**

**4. The field's advice cuts both ways, and both halves must be written down.**
On MIR-015 the literature measures detectors of that family at F1 0.65–0.77 at
best. The half that transfers: never put enforcement on a sensor of that class.
The half that does not: those papers compare prose against the model's *hidden*
computation, while ours compares two *observable* artefacts — the planner's prose
and the plan it emitted. Borrowing the pessimism whole would have retired a
solvable problem.

## What bears directly on an unattended run

- **Nothing currently schedules the agent.** Of 201 registered tasks, one invokes
  this project and it is Disabled; a second is a spent kill-switch that disabled
  a task no longer present on the host. See `SELF_BUILD_FAILURE_ANALYSIS.md`
  Addendum 2.
- **A budget-exhausted task never resumes.** `add_paused_checkpoint` parks it as
  `paused`; `pending()` returns only `pending`; `summary()` lists it under
  `resumable` and resumes nothing. Fourteen tasks have been parked since 2026-07-30.
- **Thirteen maintenance actions run only from a typed command** (MIR-131), so
  the autonomous path is the one path that cannot clean up after itself — which
  is why 43 identical episodes accumulated on 08-16.
- **An unanswered approval self-heals in 24 hours** via `expire_stale`, so the
  throughput ceiling is roughly one self-apply proposal per day, each expiring
  unapplied — not the deadlock this analysis first claimed.
- **Two of three configured providers are decorative** and the router cannot tell
  (MIR-132): Anthropic answers only with a credit-balance refusal, DeepSeek is
  not to be enabled until the operator says so.

## What this sweep did NOT do

- It repaired nothing. Every finding is recorded; the freeze holds and the
  repair choices remain the operator's.
- It did not spend money. Every measurement is from code, from the live stores,
  or from `logs/`. The two questions that need a funded probe are named where
  they arise: context degradation at the observed extremes (MIR-122) and the
  planner half of MIR-098's closure criterion.
- It did not settle the six decision-bound entries. 015, 117, 118, 119, 123 and
  127 wait on the operator, not on measurement.
