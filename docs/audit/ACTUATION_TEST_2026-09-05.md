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

## Dynamic trace (2026-09-05 09:25 +03:00), offline reproduction on the reverted code

Same inputs as the live run (policy record for synthesizer → deepseek/deepseek-chat, env pin openai/gpt-5.6-terra, a usage ledger in a temp dir, no agent, no network):

```
1 route_for            -> deepseek deepseek-chat  reason: agent_policy:route_f6633191dfda      (core/model_router.py:1313-1315)
2 _for_role_with_reason -> deepseek deepseek-chat  reason: complexity:standard|fallback:role_default (core/model_router.py:1427, 1438, 1455)
3 ledger row           -> deepseek deepseek-chat  route_reason: complexity:standard|fallback:role_default (core/model_usage.py:435-457)
```

Reading: the selected route (step 1) carries the record id; the tier path (step 2) rebuilds a ModelRoute with `reason=route_reason` — the tier string it was handed — at model_router.py:1455, and hands that to the usage-tracked client; the ledger writer (step 3) records what it is given. The id is lost between steps 1 and 2. The live row of 06:12:39Z matches step 3 exactly. Verdict stays: criterion 4 PASS, criterion 3 FAIL; cause now TRACED to the tier path's route rebuild (hypothesis confirmed). Repair: patch 0001 in scratchpad, reverted from the tree, applied only on the operator's word as attempt 2.


## Provenance repair — verification (2026-09-05 09:20–09:25 +03:00), separate from the pre-registered run

The pre-registered run stays 4/5; criterion 3 FAIL is not rewritten. This section verifies the repair (commit 090c36e) on its own terms.

- **Targeted tests:** 64 passed (tests/test_the_agent_routes_his_own_models.py incl. the new one, test_model_router.py, the size ratchet).
- **Differential replay** — the same scenario and an equivalent state, except a freshly generated policy id (the replay writes a new record into a temporary store; the live store still holds route_dc33636b9e62 untouched):
  ```
  before (reverted code): route_for -> agent_policy:route_f6633191dfda | tier path -> complexity:standard|fallback:role_default | ledger -> complexity:standard|fallback:role_default
  after  (090c36e):       route_for -> agent_policy:route_461e84afa1df | tier path -> agent_policy:route_461e84afa1df|complexity:standard|fallback:role_default | ledger -> the same
  ```
- **Post-repair live provenance: NOT OBSERVED — the single permitted natural live turn was cut by the exam driver, my tool, not by the agent or the router.** Raw: the turn's planner row landed (06:21:08Z planner openai/gpt-5.6-sol, 13912 tokens, 42 units), the adaptive route at turn start chose synth_model=deepseek-chat, and the driver — which ends a turn after 40 s of silent output — declared the turn over during the long planner call; the stop I then sent quit the process before synthesis, so no synthesizer row exists for that turn (ledger rows 3361; the last synthesizer row is still 06:12:39Z with the old reason). Whether the live ledger keeps `agent_policy:route_dc33636b9e62|…` remains pending; any further live turn is the operator's call, after the driver learns to wait for the end-of-turn marker instead of silence.

Two wording corrections accepted (operator, 09:30): (1) «the same inputs before and after» → «the same scenario and an equivalent state except the regenerated policy id»; (2) criterion 5 = PASS for the observed normalized internal metric `cost_units / 1k tokens` under the router's own tariff derivation (medium 3 / low 1 per 1k); it says nothing about the providers' dollars until `cost_units` is verified against invoices.

Status: agent tool actuation — observed live; policy write — observed live; downstream model switch to DeepSeek — observed live; original provenance criterion — FAIL, forever 4/5; cause — reproduced offline and localized (model_router.py:1455 rebuilt the route's reason); repair — targeted tests + differential replay PASS; post-repair live provenance — pending.

## Raw timeline of the cut live turn (2026-09-05, local = UTC+3) — the driver as cause, proven, not assumed

| local | UTC | source | event |
|---|---|---|---|
| 09:21:05 | 06:21:05 | exam_a/.marker_live1 mtime | question written to exam_q/next.txt |
| 09:21:06–08 | 06:21:06–08 | trace run_identity … model_call_start planner | the driver saw output (turn «produced»), then the planner call went silent |
| 09:21:08 → 09:21:52 | 06:21:08.030 → 06:21:52.022 | ledger planner row, duration_ms=43977 | planner call, 44 s, no output during it |
| 09:21:48 | 06:21:48 | exam_a/turn_1.md mtime; its journals line «seconds: 42, calls: 0» | **driver ended the turn after 40 s of silence** (QUIET_SECONDS=40), 4 s BEFORE the planner returned; usage snapshot empty because no row existed yet |
| 09:21:52.7 | 06:21:52.749 | trace planner / plan / act | the real turn continued: plan with one tool step |
| 09:21:57.9 | 06:21:57.903 | trace tool_result | tool step finished |
| 09:21:58.3 | 06:21:58.285 | trace model_call_start planner (second call, replan) — LAST EVENT of the trace | |
| ~09:21:59 | | my wait loop fired on turn_1.md, my next command wrote exam_q/stop | |
| 09:22:03 | 06:22:03 | exam_a/driver.log mtime («stopped») | driver wrote `:quit`, slept 3 s, `proc.terminate()` — process killed during the second planner call |
| — | — | trace: no `model_call_start role=synthesizer`, ledger: no synthesizer row for run_a6e403ec | **synthesis never happened** |

Sequence proven: 40 s silence → driver declared end of turn (09:21:48, before the planner's 09:21:52 return) → my stop → `:quit` + terminate (09:22:03) → process exit during the second planner call → no synthesizer call, no ledger row. Cause: the exam driver's end-of-turn rule (silence), mine. Repair follows: end of turn by the turn's own closing marker, never by silence alone.

## Addendum — criterion 3 closed separately, post-repair (2026-09-05 09:38 +03:00)

The pre-registered run stays **4/5**. This addendum records a separate verification on the repaired version (090c36e and later, tree a069022, authoritative battery `pytest exit=0`, 9567 passed), one ordinary live turn through the marker-based driver (`scripts/exam_driver.py`, turn ended_by=marker after 36 s, 2 calls captured):

```
06:37:46Z role=planner     provider=openai   model=gpt-5.6-sol    route_reason=complexity:standard|fallback:role_default                 tier=medium tokens=12142 units=39
06:38:11Z role=synthesizer provider=deepseek model=deepseek-chat  route_reason=agent_policy:route_dc33636b9e62|complexity:standard|fallback:role_default tier=low tokens=15815 units=16
```

Field by field: the synthesizer row names the LIVE record the agent wrote (`route_dc33636b9e62`, not a replay id), the model is the assigned cheap one, the tier note follows the id, cost 16 units / 15815 tokens = 1.01 units per 1k (internal metric, tariff low). Raw transcript: actuation_test_2026-09-05/turn_3_post_repair_live_raw.md.

Status after this addendum: agent tool actuation — observed live; policy write — observed live; downstream model switch — observed live; original provenance criterion — FAIL (run stays 4/5); cause — reproduced, localized, then proven by timeline for the cut turn (the driver); repair — targeted tests + differential replay PASS; **post-repair live provenance — CONFIRMED on the agent's own record**. Claim ceiling unchanged: agent-controlled routing actuation. Decision authority: a separate exam, not sat.
