# When the agent asks the human — the escalation contract

WHO NEEDS THIS. The operator said it plainly on 2026-08-22: half of the
"operator decisions" this project records are decisions he — not a programmer
— honestly cannot make. His design intent: the agent depends on him ONLY when
a genuinely его-class problem arises (money, irreversibility, "I don't know
how"), and then it ASKS, in his language. Everything else must not queue on
him. This document is the field's answer to that, checked at sources, with
the parts that do NOT transfer stated — and it re-triages the standing
decision backlog accordingly.

## What the field does (verified 2026-08-22)

**1. Escalation triggers are STRUCTURAL, never the model's self-confidence.**
The production guidance ([Galileo](https://galileo.ai/blog/human-in-the-loop-agent-oversight),
[escalation design 2026](https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026),
[HITL patterns](https://understandingdata.com/posts/human-in-the-loop-patterns/))
converges on three triggers: the action is **irreversible**, the action
**spends beyond a budget line**, the task is **outside declared scope**. Five
patterns cover ~90% of deployments: approval gate, escalation ladder,
confidence routing, collaborative drafting, and **audit-trail-with-lazy-review**
— act, record, let the human inspect later.

**2. Why self-judged "I'm not sure, I'll ask" cannot be the mechanism.** The
measured half, and it is decisive: [AgentAbstain](https://agentabstain.github.io/)
finds the best frontier models get **fewer than 60%** of should-I-act pairs
right; [clarification studies](https://arxiv.org/html/2605.25284v1) show models
recognise ambiguity but rarely ask; and when selective help-seeking is
required, pass rates collapse from 75–89% to **4–24%**
([Learning to Ask](https://aclanthology.org/2025.emnlp-main.1104.pdf)). So an
agent that "will know when to ask" does not exist today. **The ask-triggers
must be rules over action classes, not judgement** — which is what this
repository already builds (risk classes, budget gates, CRITICAL_DENY), and
this external result says that instinct was right.

**3. Autonomy is granted by reversibility, not by trust.**
([Autonomy ladder](https://dev.to/jackm-singularity/ai-agent-autonomy-ladder-let-agents-act-without-losing-control-3if0),
[design principles](https://arxiv.org/pdf/2606.20630)): autopilot is safe for
narrow, reversible, well-tested actions; risky exactly where an action spends
money, touches permissions, or communicates externally. Explanation depth is
calibrated to the USER's expertise — over-explaining wastes the expert,
under-explaining strands the non-expert.

**What does not transfer:** the confidence-routing pattern (route by the
model's own score) — MIR-060/119 measured here that the verifier cannot tell a
resolvable citation from a true claim and the critic shares the builder's
brain, and the field's own abstention numbers above say the same thing from
the other side. Structural triggers only.

## The contract

**The agent asks the operator when and only when a structural trigger fires:**

1. **Money** — an action would spend past a budget line, a provider refuses
   for balance, or a paid resource is needed that does not exist.
2. **Irreversibility** — the §9 classes: merge/push, deletion of durable data,
   external communication, changes to its own walls, kill-switch territory.
3. **Genuine dead end** — every road tried and failed, and the failure is
   recorded with what was tried (not "I am unsure": that is what retries,
   fallbacks and defaults are for).

**Every question to the operator is worded for him** — in Russian, no jargon:
what happened (one sentence), 2–3 options, **exactly one recommended default
first**, and for each option its consequence and whether it can be undone.
A question without a recommended default is not allowed to reach him: bringing
an open-ended expert question to a non-expert is the failure this contract
exists to end.

**Everything below the triggers is decided by default, recorded, and
reversible** — the field's audit-trail-with-lazy-review: the decision is
written down with its grounds and a veto line («отменяется одним словом
оператора»), the operator reviews when he chooses, and a veto rolls it back.
Expert-design questions (thresholds, identities, sensor scopes) are exactly
this class: the expert in the loop — this assistant, or the agent where its
walls allow — picks the published-practice default and carries the burden of
recording why.

**Sovereignty is not delegated by this contract.** The §9 rights and the
ratified rulings (C0.P, norms A/B, «аудит не подставляет своё решение») stand.
A vacancy the operator ruled must stay vacant (MIR-119's judge) is not filled
by a "default" — the default there is the conservative behaviour already in
place (full battery, rollback, no unattended self-apply).

## The backlog, re-triaged under this contract

Entries this project had parked as «operator decision», re-sorted:

| bucket | entries | what happens now |
|---|---|---|
| **default chosen, recorded, veto-able** | 015 (no enforcement on the mismatch sensor — the field's F1 numbers ARE the default), 016 (evidence gate stays observational; the enforcing layer already exists separately), 023 (observational stays), 024 (threshold untouched until the tokenizer family is fixed — MIR-105/008 upstream), 050 (deferral stands, unblocking is upstream in 128/131), 122 (funded probe deferred until a week's real traffic exists), 124 (register = the catalog file gains retirement fields when first needed), 127 (the ranking stays a utility table, relabelled as such) | recorded as defaults with grounds; no question to the operator |
| **genuinely his, framed with defaults** | the three Saturday decisions (launch mode, spend cap, grant shape — `SATURDAY_LAUNCH_DECISIONS.md` already frames each with a recommended default), DeepSeek funding, any future merge/push | asked once, in Russian, options + default |
| **vacancies that stay vacant** | 119 (whose «better»), 114/120 (the freeze and the wall class), 117's retry-semantics question he explicitly reserved | conservative behaviour is the standing default; no one fills the seat quietly |
| **needs building, not deciding** | 128/129/130 (memory designs), 121's write-side provenance, 096's learning channel | engineering work under the freeze rules; scheduled like any other repair |

## What this changes in practice, starting now

- The assistant stops sending expert questions upward: it recommends, records,
  and acts where reversible; the operator's word overrides at any time.
- Approval-inbox summaries addressed to the operator are written in Russian
  with options and one recommended default (the standing grant of 2026-08-16
  already had this shape — it is the house template).
- «Не знаю, как решить» from the operator is a VALID answer that routes the
  question back into the default bucket — it is the contract working, not
  failing.
