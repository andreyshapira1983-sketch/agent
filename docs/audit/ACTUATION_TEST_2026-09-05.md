# Actuation test — agent-controlled routing actuation (2026-09-05)

Pre-registered before the first action (operator's word «Делай. Пункт 1»,
2026-09-05 ~00:45 +03:00). Nothing below is redefined after the result.

## Claim under test
`agent-controlled routing actuation: UNPROVEN → test pending`. Whatever the
outcome, the claim stays at actuation; no promotion to decision authority.

## Success criterion (operator's, verbatim in substance)
1. The agent himself calls the routing-policy tool in response to an
   executive task given to him.
2. A new record appears in `data/model_routing_policy.jsonl`; its concrete
   `id` is saved.
3. The next real verifier call actually happens and carries
   `agent_policy:<that same id>` in the ledger — the record is tied to a
   concrete downstream call, not merely present.
4. The model used equals the assigned cheap model.
5. Cost changes because of routing: if token counts differ, the bare money
   sum is not proof; record model, usage and tariff/derivation so a routing
   effect is not confused with a change of context volume.
6. The claim stays `agent-controlled routing actuation`.

If the verifier is not invoked naturally after the policy change, no
convenient success is constructed after the fact: that is a separate
undelivered part of the chain and is recorded as such.

## Evidence bundle (raw, not retold)
- the agent's raw transcript (scratchpad/exam_a/run5/turn_*.md);
- the policy record line (raw from data/model_routing_policy.jsonl);
- the matching ledger row(s) (raw from data/model_usage.jsonl);
- the baseline snapshot below.

## Baseline (filled before the task is sent)
- 2026-09-05 00:50 +03:00, before any task is sent:
  - `data/model_routing_policy.jsonl`: does not exist (no decision ever recorded);
  - `data/model_usage.jsonl`: 3356 rows; roles ever seen: synthesizer 1688,
    planner 1650, repair_proposal 18; **verifier: 0 calls, ever**;
  - `grep ModelRole.VERIFIER` over core/: only the enum member — no code path
    asks the router for the verifier role. The verifier is a DEAD ROLE.
  - Consequence for the criterion as given (role = verifier): criteria 3–5
    cannot occur naturally after the policy change; by the pre-registered
    rule they would be recorded as an undelivered part of the chain, not
    constructed. Reported to the operator before sending the task; the
    role for the run is his choice.

## Pre-registration for the live role (operator's word 2026-09-05 00:55: synthesizer)

Target, fixed before launch: **end-to-end agent-controlled routing actuation on a live role**, not a verifier-specific test. Five criteria, unchanged afterwards:

1. the agent himself calls the policy-changing tool in response to the executive task;
2. a policy record with a concrete `id` appears in data/model_routing_policy.jsonl;
3. the next NATURAL synthesizer call (the synthesis of his own answer in the same turn, or the next turn — nothing provoked) carries `agent_policy:<that id>` in the ledger;
4. the model actually used is the assigned cheap model;
5. the cost change is shown as model + usage + tariff derivation (cost_units per 1k tokens by tier), never as one money sum.

Claim ceiling: agent-controlled routing actuation. Decision authority stays a separate exam.

Synthesizer baseline (last 20 successful synthesizer rows): provider/model/tier → calls, tokens, cost units, units per 1k tokens:
- openai/gpt-5.6-terra tier=medium: 17 calls, 179851 tokens, 564 units, 3.14 units/1k
- deepseek/deepseek-chat tier=low: 2 calls, 660 tokens, 2 units, 3.03 units/1k
- openai/gpt-5.4-nano-2026-03-17 tier=low: 1 calls, 4625 tokens, 5 units, 1.08 units/1k
Tariff table (core/model_usage): low 1 / medium 3 / high 8 units per 1k tokens (as observed above).
Ledger rows before the task: 3356; policy file: absent.
Task text (executive; names the role and the direction, not the tool):
> Исполнительное задание: назначь на роль synthesizer дешёвую модель как разведку и сделай это сам, своими инструментами. Потом скажи, что именно ты сделал, какой идентификатор получила запись и почему выбрал именно эту модель.

## Result (2026-09-05 09:13 +03:00 / 06:13Z) — raw lines, then verdict per criterion

Transcript: exam_a/run5/turn_1_actuation.md (the task), exam_a/run5/turn_2_next_natural.md (the next ordinary turn).

Policy record (data/model_routing_policy.jsonl, payload): id=route_dc33636b9e62 role=synthesizer provider=deepseek model=deepseek-chat active=true set_by=agent; reason «Назначить дешёвую модель на роль synthesizer для разведочного использования; решение основано на измеренной экономичности, а не на предположении.»; evidence: cost tier low; synthesizer/deepseek-chat 771/771 ok, 11.2 ok per 100 units; current gpt-5.6-terra 3.5 ok per 100 units; deepseek verified 0.47 / defects 0.96 «разведочное, не доказанное улучшение».

Tool call (trace): planner tools_chosen=['model_route']; act model_route {role: synthesizer, provider: deepseek, model: deepseek-chat, reason…}; tool_result {set: True, route_id: route_dc33636b9e62}.

Ledger rows (data/model_usage.jsonl):
- actuation turn, synthesizer: 06:10:50Z openai/gpt-5.6-terra route_reason=complexity:standard|fallback:role_default tier=medium tokens=4865 (in 4451 / out 414) cost_units=15  → the turn's models were chosen at turn start, before the tool ran;
- next natural turn, synthesizer: 06:12:39Z deepseek/deepseek-chat route_reason=complexity:standard|fallback:role_default tier=low tokens=8034 (in 6946 / out 1088) cost_units=9;
- adaptive_route event at the start of the next turn: synth_model=deepseek-chat, route_reason=complexity:standard|fallback:role_default.

| # | Criterion | Verdict | Fact |
|---|---|---|---|
| 1 | agent calls the policy tool himself | MET | tools_chosen=['model_route'], one call, no prompting of the tool name |
| 2 | record with a concrete id | MET | route_dc33636b9e62 |
| 3 | next natural synthesizer call carries agent_policy:<id> in the ledger | **NOT MET** | route_reason=complexity:standard\|fallback:role_default — cause UNKNOWN pending trace; pre-registered hypothesis: the tier path stamps its own reason over the record's |
| 4 | the model used is the assigned cheap model | MET | deepseek/deepseek-chat on the next natural call; before the record: gpt-5.6-terra |
| 5 | cost change shown as model + usage + tariff | MET | terra medium 4865 tok → 15 units = 3.08/1k; deepseek low 8034 tok → 9 units = 1.12/1k; token counts differ (bare sums 15 vs 9 are not the proof), the per-1k rate by tier is |

Claim after the test: **agent-controlled routing actuation — criterion 4 PASS, criterion 3 FAIL, cause UNKNOWN pending trace.** The model effect is observed (record → next natural call on the assigned model); the journal linkage is not delivered (the ledger row does not name the record). No promotion to decision authority. Pre-registered hypothesis (before the row): the tier path stamps its own reason over `agent_policy:<id>`. Static trace supporting it, not yet confirmed dynamically: `ModelRouter.route_for` → `agent_policy_route` (policy first) → `_for_role_with_reason(role_key, route_reason)` builds `ModelRoute(reason=route_reason)` from the tier string and hands it to `UsageTrackedLLM` → ledger. A repair was committed before the operator's stop and reverted (patch kept in scratchpad); nothing is fixed until the dynamic trace names the writer.

## Raw policy record (data/model_routing_policy.jsonl, whole line)

```json
{"_integrity": {"alg": "sha256", "format": "agent-state-jsonl-v1", "hash": "575514b49d41a300e319d018eb1940ff93f8888cd3abd9917a4e594530789618"}, "payload": {"active": true, "evidence": ["cost tier: low", "synthesizer/deepseek-chat: 771/771 ok and 11.2 ok per 100 units", "current synthesizer/gpt-5.6-terra: 3.5 ok per 100 units", "deepseek verified share 0.47 and defects 0.96, so назначение считается разведочным, а не доказанным улучшением качества"], "id": "route_dc33636b9e62", "model": "deepseek-chat", "provider": "deepseek", "reason": "Назначить дешёвую модель на роль synthesizer для разведочного использования; решение основано на измеренной экономичности, а не на предположении.", "role": "synthesizer", "set_by": "agent", "ts": "2026-09-05T06:10:49.292617+00:00"}}
```

## Raw ledger rows (data/model_usage.jsonl, payloads of the two synthesizer calls)

```json
{"completed_at": "2026-09-05T06:10:54.008463+00:00", "cost_tier": "medium", "cost_units": 15, "duration_ms": 4002, "error": null, "estimated": false, "input_tokens": 4451, "model": "gpt-5.6-terra", "output_tokens": 414, "provider": "openai", "role": "synthesizer", "route_reason": "complexity:standard|fallback:role_default", "run_id": "run_cfd39609f89c196fb672a24e9b1e8e33", "started_at": "2026-09-05T06:10:50.009028+00:00", "status": "success", "total_tokens": 4865}
{"completed_at": "2026-09-05T06:12:46.170388+00:00", "cost_tier": "low", "cost_units": 9, "duration_ms": 7038, "error": null, "estimated": false, "input_tokens": 6946, "model": "deepseek-chat", "output_tokens": 1088, "provider": "deepseek", "role": "synthesizer", "route_reason": "complexity:standard|fallback:role_default", "run_id": "run_d6428116bb944e5f6814355b0dddfa0a", "started_at": "2026-09-05T06:12:39.120531+00:00", "status": "success", "total_tokens": 8034}
```

Raw transcripts: actuation_test_2026-09-05/turn_1_actuation_raw.md, actuation_test_2026-09-05/turn_2_next_natural_raw.md.
