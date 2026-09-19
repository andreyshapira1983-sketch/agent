# Silent-failure review — verdict ledger (2026-09-04)

Rule: reviewers generate candidates; a line becomes CONFIRMED only after my own
execution/measurement, REJECTED with the reason, UNPROVEN until then.
Owner: `infra` = observation/authority/assurance (Fable's hands);
`agent` = functional organ (evidence into his registry, his load).

## Batch 4 (27 files) — reviewer top-10 plus my verdicts

| # | file:line | claim | verdict (my run) | sev | owner |
|---|---|---|---|---|---|
| 4.1 | core/claim_arithmetic.py:289-305 | version-only excerpt → `values=[]`: ZeroDivisionError on average; `refutes expected='0' computed_from=''` on sum/count | CONFIRMED: `evaluate("the average is 5","version = 1.2.3")` raises; `evaluate("it sums to 99", …)` → refutes/expected 0 | high | agent (verifier truth) |
| 4.2 | core/evidence_classes.py:91-113 | look-ahead binds to one branch; `"your behavior with retries"` first turn → self-analysis, world-fact verification skipped | CONFIRMED: `is_self_analysis_turn(..., has_prior_turn=False)` → True/current_run_introspection | high | infra (completion truth) |
| 4.3 | core/self_repair_utils.py:10-11, 34-37 | `timed_out is False` = "diagnosis verified"; non-dict diff = "not empty" | CONFIRMED: both calls as claimed | high | infra (authority gate input) |
| 4.4 | core/actuation_gateway.py:18 | `journal_append`, `memory_bank`, `spawn_subagent` not effectful → no gateway/kill-switch/receipt | CONFIRMED: set is {file_write, shell_exec} | high | infra (authority) |
| 4.5 | core/governance.py:121-122 | `fetch_web` allowed in diagnostic mode as read-only | CONFIRMED fact (`allow`); severity: read-only egress is doctrine since 09-01 → low | low | doctrine |
| 4.6 | core/scheduler.py:214-217+265-266 | invalid schedule row skipped on read, erased by next rewrite; total_count post-deletion | UNPROVEN (needs tmp store run) | high | infra (persistence) |
| 4.7 | core/approval.py:66-69 | hidden-key notice misses a key whose name appears in another value (`mode: overwrite` hidden, unlisted) | UNPROVEN (needs run) | high | infra (authority UI) |
| 4.8 | core/completion_obligation.py:216-220 | filename mention satisfies `_discloses` → silent-missing sensor quiet when any failure code exists | UNPROVEN | high | infra (completion truth) |
| 4.9 | core/incremental_splitter.py:139-165 | conditional imports judged movable but never copied → NameError at runtime | UNPROVEN — RELEVANT TONIGHT: three splits to apply; check plans for conditional imports before the lane runs | high | agent (his splitter) / infra tonight |
| 4.10 | core/tool_receipts.py:162-166 | file_write receipt fingerprints path+length only | CONFIRMED by reading; low-effort fix (salted content hash) | medium | infra (receipts) |
| 4.11 | core/tool_receipts.py:526 | `recent=0` → all rows | CONFIRMED (`[-0:]`) | low | infra |
| 4.12 | core/scheduler.py:402-403 | `limit=0` hot-spin; `SchedulerService` has no production caller | UNPROVEN; dead-class candidate | medium | infra |
| 4.13 | core/subagent_quarantine.py:64-68 | failed append returns success row; nothing logged | UNPROVEN | high | infra (observability) |
| 4.14 | core/model_usage.py:392-409 | llm_calls reserved before dispatch, never released on failure | UNPROVEN | medium | infra (accounting) |
| 4.15 | core/model_usage.py:557-575 | health path reads ledger without integrity check | UNPROVEN | medium | infra (persistence) |
| 4.16 | tests/test_the_mismatch_sensor_was_measured.py:78-84 | docstring-substring test cannot fail | UNPROVEN (reads plausible) | medium | infra (guards) |
| 4.17 | core/step_sanitizer.py:171-207, 28-36 | file_read has no `..` guard; SSRF gate misses decimal/octal IP literals | UNPROVEN | medium | infra (safety) |
| 4.18 | core/compensation.py:221-230 | `path="."` escapes `_protected_root` → rmtree workspace | UNPROVEN — check before any rollback use | high | infra (safety) |

Remaining batch-4 items (veto_cause, builder_reply_diagnosis, lang_match, approval_triage, loop_memory_commands, autonomous_runtime_proposals, subsystem_disagreement, termination_guard, verifier_models, unsupported_claims, reasoning_action_check, source_registry_store): recorded as candidates, low/medium, unverified.

## Batch 5 (27 files) — reviewer top-10 plus my verdicts

| # | file:line | claim | verdict (my run) | sev | owner |
|---|---|---|---|---|---|
| 5.1 | core/loop_response_deciders.py:508-511 | `completion_contract` accepted by `_build_response_draft`, never forwarded to `_enforce_answer_safety` → prohibition annotation dead on the live path; guarding test is an AST keyword check that cannot fail | CONFIRMED: call kwargs = user_question, local_critique_active, verifier_failure; signature has completion_contract | high | infra (completion truth) |
| 5.2 | tools/shell_exec.py:433-444 | `git add '*.py'` / `git add :/` pass the name-each-file guard (pathspec globs) | UNPROVEN (needs temp repo run) | high | infra (authority/effects) |
| 5.3 | core/subagent_registry.py:450-453 | corrupt registry json → all counters reset, `applied_outcomes` emptied, next save overwrites history | CONFIRMED: after writing `{`, record → file rewritten with `applied_outcomes: []` | high | infra (persistence) |
| 5.4 | core/budget_ledger.py:495-497 | usage rows with unparseable `created_at` are uncounted in every window → reserve keeps allowing | CONFIRMED: 5 rows over limit 2 → `reserve().allowed is True` | high | infra (accounting/money) |
| 5.5 | core/repair_commands.py:146/162 | `compensation_log.pop()` before `apply_compensation_plan` → a failed apply loses the plan; retry says «no plans registered» | CONFIRMED by source order | high | infra (safety) |
| 5.6 | core/referent_resolver.py:576 | containment skipped when `workspace_root is None` → absolute out-of-workspace path becomes `trusted_path`/`needs_tool` | CONFIRMED: `('needs_tool','trusted_path')` for `C:\Windows\win.ini.txt` | high | infra (safety) |
| 5.7 | core/budget_ledger.py:250-271 | reserve check-then-append not atomic, no lock → concurrent daemon+CLI exceed ceiling | UNPROVEN (plausible by reading) | high | infra (money) |
| 5.8 | scripts/snapshot_state.py:63-64, 49-51 | uncopyable store skipped silently; empty dated dir = «already snapshotted» | UNPROVEN (plausible by reading) | high | infra (backup H-51) |
| 5.9 | tools/shell_exec.py:834-847 | byte-truncation mid-codepoint → OEM re-decode → mojibake as success | UNPROVEN | medium | infra |
| 5.10 | core/instruction_conflict_gate.py:325-327, 91-102 | empty-subject directive dropped silently → «no conflicts»; forbidden names don't match real tools | UNPROVEN | medium | infra (authority) |
| 5.11 | core/deep_escalation.py:192-194 | `"²".isdigit()` → ValueError instead of fail-closed | CONFIRMED | low | infra |
| 5.12 | core/learning_planner.py:387 | `.replace(tzinfo=utc)` discards a real offset → freshness penalty escaped | CONFIRMED: score 100 with -05:00 1h-old read | medium | agent (learning organ) |
| 5.13 | core/completion_marker.py:26 | marker on the same line as prose → `missing` and the nonce stays in the text | CONFIRMED: `('missing', True)` | medium | infra (completion truth) |
| 5.14 | core/loop_observe.py:51-52 | comment claims secrets are caught before the LLM; code only logs and forwards verbatim | UNPROVEN (reads plausible) | high | infra (safety) |

## Batch 3 (27 files) — top-10 plus my verdicts

| # | file:line | claim | verdict (my run) | sev | owner |
|---|---|---|---|---|---|
| 3.1 | core/state_integrity.py:135-137 | all-corrupt store → read rewrites it to 0 bytes (rows quarantined, live store reads empty) | CONFIRMED: size 0 after read, `.quarantine/store.jsonl.*.bad.jsonl` created | high | infra (persistence) |
| 3.2 | core/completion_contract.py:532-534 | cache-served `file_write` closes a file obligation | CONFIRMED: `unmet_obligations` → `()` with issue «served from working-memory cache» | high | infra (completion truth) |
| 3.3 | tools/diff_file.py:215-216 | `---`/`+++` body lines skipped → undercount; repair rejected as empty diff | CONFIRMED: front-matter removal counted 0/1 | high | infra (effects) |
| 3.4 | core/clarification_policy.py:150-151, 89-90 | informational word bypasses destructive gate; «всё/all» counts as target | CONFIRMED: «check then delete everything» → proceed; «удали всё» → proceed; «удали» → ask | high | infra (safety) |
| 3.5 | core/structured_facts.py:131-133 | scalars inside lists dropped → no facts | CONFIRMED: empty StructuredFacts | high | agent (verifier truth) |
| 3.6 | core/state_integrity.py:57-58 | checksum opt-in per row (no `_integrity` = unverified) | CONFIRMED | high | infra (persistence) |
| 3.7 | core/subagent_memory_scope.py:439-442 | zero budgets widened to defaults; unknown risk → low | UNPROVEN (my probe used wrong attribute names) | high | infra (authority) |
| 3.8 | core/injection_guard.py:402-406 | apostrophe in label breaks the suspicious wrapper regex | CONFIRMED: False vs True | high | infra (safety) |
| 3.9 | core/heartbeat_io.py:66 | future heartbeat → age 0.0, never stale | CONFIRMED | high | infra (liveness) |
| 3.10 | core/run_context.py:135,163 | restrictions outside a run mint `run_id="unscoped"` | CONFIRMED | medium | infra (accounting) |

## Batch 1 (27 files) — top-10 plus my verdicts

| # | file:line | claim | verdict (my run) | sev | owner |
|---|---|---|---|---|---|
| 1.1 | core/loop_step_execution.py:396-412 + core/memory.py | working-memory cache answers ANY repeated tool call before gateway/policy/approval; cache_store has no risk filter — a repeated `file_write`/`shell_exec` «succeeds» without writing or asking | CONFIRMED by reading: cache_lookup precedes gateway and policy.check; no risk/cacheable guard | high | infra (authority/effects) — same root as 3.2 |
| 1.2 | tools/base.py:143-148 | receipt recording swallowed by bare except: total silent receipt loss | UNPROVEN (reads plausible) | high | infra (receipts) |
| 1.3 | core/answer_format.py:258 | `safety:` in `_SKIP_PREFIXES` → mandated Safety section dropped from every human answer | CONFIRMED: only «x» rendered | high | infra (completion truth) |
| 1.4 | core/loop_step_execution.py:319-319 | unresolved `{{step:…}}` marks step failed then runs it with the placeholder | UNPROVEN | high | infra |
| 1.5 | core/subagent_runner.py:549-567 | execution receipt fields are constants (memory_read_tags=(), file_writes=0, verifier_status literal) | UNPROVEN (reads plausible) | high | infra (receipts) |
| 1.6 | core/architecture_audit.py:306-313 | «present» = file exists → always ready, zero gaps | CONFIRMED: (True, {'present': 18}, 0) | medium | infra (guards) |
| 1.7 | tools/spawn_subagent.py:181-182 | only-unsafe allowed_tools → None → child gets ALL safe tools | CONFIRMED: returns None | high | infra (authority) |
| 1.8 | core/loop_memory_read.py:443-445 | bare except around re-ask detection | UNPROVEN | medium | infra |
| 1.9 | core/operational_domain.py:186 | one coding keyword disables money/physical/authority escalations for the whole message | UNPROVEN (reads plausible) | high | infra (safety) |
| 1.10 | core/safe_vcs.py:150-152 | rollback runs `git clean -fd` over the whole workspace | CONFIRMED by reading | high | infra (safety) |
| 1.11 | core/truth_hype_filter.py:214-217 | one substance marker launders hype | CONFIRMED: hype → substantive with «Save 50%» | medium | agent |
| 1.12 | core/budget_governor.py:101 | limit 0 = unlimited | CONFIRMED: allowed True | medium | infra (money) |
| 1.13 | core/mentor_channel.py:98-100 | one long question → empty mentor block, counter says shown | CONFIRMED: 0 chars of the question | medium | infra |

## Batch 2 (27 files) — top-10 plus my verdicts

| # | file:line | claim | verdict (my run) | sev | owner |
|---|---|---|---|---|---|
| 2.1 | core/dlp.py:117-119 | phone regex swallows a following date → date-suppression discards the phone | CONFIRMED: `[]` vs one finding without the date | high | infra (safety/DLP) |
| 2.2 | core/loop_verification.py:130,134 | verifier crash → soft report `chain_was_empty=True`, logged as real verification, disables evidence gate | CONFIRMED by reading | high | infra (completion truth) |
| 2.3 | tools/file_write.py:190-192 | same-second backups collide; original content lost; tool still returns backup_path | CONFIRMED: one backup holding «v2», «orig» gone | high | infra (effects/safety) |
| 2.4 | core/checkpoint.py:193-194 | one truncated trailing line → `load()` None → resume/pause lost | CONFIRMED | high | infra (persistence) |
| 2.5 | core/memory_hygiene_commands.py:86-94,148-153 | one try around five steps; `_explain` prints zeros as measurements with «high confidence» | UNPROVEN (reads plausible) | high | infra (observability) |
| 2.6 | core/backlog_selector.py:179-190 | unreadable TECH_DEBT → silent loss of top source; `_path_exists` returns True on OSError | CONFIRMED (second half by reading) | high | infra |
| 2.7 | core/verifier_absence.py:390 | `"20" in "2026"` → changed number passes | UNPROVEN (needs Evidence fixture) | high | infra (completion truth) |
| 2.8 | core/loop_attempt.py:339-340,363-364 | two sensors die silently (bare except) | UNPROVEN (an in-repo test documents it) | high | infra (observability) |
| 2.9 | core/source_ranker.py:509 | `docs.*`/`support.*` any domain = authoritative, ceiling removed | CONFIRMED rule present | high | infra (safety) |
| 2.10 | core/knowledge_pipeline.py:739-782 | one control byte = broken encoding; `* ` bullets = code → silently rejected | CONFIRMED: (True, True) | high | agent (knowledge organ) |

## Batch 6 (27 files) — top-10 plus my verdicts

| # | file:line | claim | verdict (my run) | sev | owner |
|---|---|---|---|---|---|
| 6.1 | core/source_registry.py:159-161 | `suspect` status not in from_dict's valid set → quarantine survives only in memory; after reload injection-flagged claims become `extracted` | CONFIRMED: roundtrip → `extracted` | high | infra (safety) |
| 6.2 | core/redaction.py:126-175 | non-container objects returned untouched → dataclass with a token logged verbatim | CONFIRMED: `P(token='ghp_…')` | high | infra (safety) |
| 6.3 | core/self_apply_lane.py:469-477 | authority-document check on raw path; `./knowledge/doctrine/ROADMAP.md` passes as «documents only» | UNPROVEN (needs a proposal fixture; plausible by reading) — TONIGHT: verify before any documents-only auto-apply | high | infra (authority) |
| 6.4 | core/self_apply_lane.py:699-702, 719 | test_runner.run unguarded → an exception leaves the patched tree on the temp branch, no report | CONFIRMED by reading (no try around either call) — TONIGHT: check branch/status after each lane run | high | infra (lane) |
| 6.5 | core/backlog_signals.py:443-444 | `item_target_map` never supplied by production → human vetoes never penalise targets | UNPROVEN (reads plausible) | high | infra (commitments) |
| 6.6 | core/receipt_consumer.py:41-45 | no ledger / no trace id → «receipt present» | UNPROVEN (reads plausible) | high | infra (completion truth) |
| 6.7 | core/loop_verify_replan.py:142 | `refuted` = has-a-reason, not verdict refuted → phantom claim_refuted triggers | UNPROVEN | high | infra |
| 6.8 | core/budget_kill_switch.py:113-118 | `None` snapshot = healthy; campaign passes None unconditionally → kill switch inert on the campaign path | CONFIRMED: `evaluate_day_budget(None).active is False` | high | infra (money) |
| 6.9 | core/self_apply_lane.py:738-751 | post-commit checkout failure → `_rollback()` deletes the branch holding the commit; reports «commit failed» | CONFIRMED by reading | high | infra (lane) |
| 6.10 | core/clarification_gate.py:93-98 | `is_forbidden` never called; the «chaos set» is prose | UNPROVEN (grep claim) | medium | infra (authority) |

Runners-up (unverified): topic_tokens polarity weight (113-116), response_draft dedup-then-rewrite (103-104), memory_hygiene archived_count (534-536), role_router substring double count (255-257).

## Totals (all six batches)

Reviewer candidates: ~180 findings across 162 files. My verdicts so far: **41 CONFIRMED** by execution or by reading the exact lines, **~30 UNPROVEN** (plausible, not yet run), 0 REJECTED **on the verified high-priority subset** — a sample enriched with the strongest candidates; it says nothing about the precision of the remaining ~110 (Кодекс's correction, 2026-09-04).

Priority after tonight's window (blast radius, ratified order): 🔴 cache before the gates (1.1+3.2: authority bypass + false effect proof) → 🔴 budget switch on `None` (6.8: missing evidence → permission) → 🔴 corrupt state → empty valid state (3.1, 3.6, 5.3, 5.4, 2.4, 2.6: unreadable must not widen freedom) → 🟠 the judges (2.1, 3.4, 3.8, 6.1, 2.9, 6.2) → 🟠 recovery lane (6.4, 6.9, 2.3, 5.5, 4.9), operationally red tonight.

Invariants to derive, one per family, then search for violations en masse: effectful operation — a cache MUST NOT satisfy a fresh-effect obligation; corrupt persisted authority/accounting state MUST NOT increase permission; missing/unreadable evidence MUST NOT become success evidence; recovery machinery — failure MUST preserve a recoverable original state.

Tonight's lane containment (Кодекс, ratified): (1) record clean HEAD and a saved ref before each split; (2) one split at a time; (3) after each — independently check HEAD, branch/commit existence, `git status`, the test result; (4) any exception or discrepancy → stop, restore the known state, no next split; (5) the lane's own success/failure message is not proof until the external check. Owner split of the confirmed: ~34 infra (authority, persistence, completion truth, safety, money, lane), ~7 agent organs (verifier facts, knowledge pipeline, hype filter, learning planner).

Root families among the confirmed (one cause, many sites):
- **Cache/short-circuit before the gates**: 1.1 + 3.2 (working-memory cache answers file_write before gateway/policy and closes obligations).
- **Fail-open on unreadable input**: 3.1, 3.6, 5.4, 6.8, 2.4, 2.6, 5.3, 1.12 (empty/None/unparseable reads as healthy, unlimited, or absent).
- **Verdict machinery that cannot see**: 2.1, 3.4, 3.8, 2.9, 6.1, 6.2, 5.6 (redaction/DLP/injection/clarification/authority checks bypassed by punctuation, wording, a subdomain, a reload, an object type).
- **The lane's own recovery paths**: 6.4, 6.9, 2.3, 5.5, 4.9 (rollback deletes the commit; test-runner exception strands the tree; backup collision; plan popped before apply; conditional imports).

Runners-up (unverified): secret_scanner annotated default (152/235), file_request_intent ASCII-only paths (242), subagent_registry constant cost fields (146), subagent_contract_audit optional budgets pass (322), referent_resolver artifact TTL unreachable (401), file_lock one-byte msvcrt lock (22), value_review malformed latest row (131), team_plan verifier not in plan (42/215), output_policy ceiling logged not applied (70-72), step_references stringifies None (101), host_tools_context stale path asserted available (63-64), list_dir truncation unqualified claim (56-58), self_build_supervisor only two counters (81-88), episodic_hygiene prune deletes malformed rows (170-191), tool validate_output cannot fail (list_dir 74-77).
