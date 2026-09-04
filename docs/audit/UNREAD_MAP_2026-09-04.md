# Unread map — 2026-09-04 (snapshot before the evening window)

Method: static import reachability (absolute, relative, `import_module`, and module names quoted as strings) from the unattended entry `agent_tick.py` and the attended entries (`main.py`, `cli/app.py`, `cli/command_dispatch.py`, `cli/repl.py`, `app/bootstrap.py`). `changed` = touched by the audit blocks (commits since f0d3228); `named` = cited with line numbers in the audit or CODE_NOTES; `unread` = neither. `tests` = test files importing or naming the module. Reachability is POTENTIAL, not measured execution: a reachable module may still be dead on the live path, and a `none` module may be reached by a path this scan cannot see (subprocess, entry points). Scripts are listed apart: they run by hand or CI.

| reach | status | files | lines |
|---|---|---|---|
| unattended | changed | 24 | 15044 |
| unattended | named | 34 | 15426 |
| unattended | unread | 162 | 47990 |
| attended | named | 13 | 4205 |
| attended | unread | 47 | 12474 |
| none | named | 3 | 1014 |
| none | unread | 9 | 1055 |
| script | changed | 3 | 645 |
| script | named | 9 | 1142 |
| script | unread | 29 | 4598 |

## Unattended-reachable and never read — the audit queue, by size

| file | lines | tests |
|---|---|---|
| `core/loop_step_execution.py` | 1060 | 13 |
| `core/knowledge_pipeline.py` | 970 | 18 |
| `core/doc_routing.py` | 939 | 7 |
| `core/step_sanitizer.py` | 930 | 9 |
| `tools/shell_exec.py` | 887 | 19 |
| `core/self_apply_lane.py` | 776 | 16 |
| `core/evidence.py` | 706 | 64 |
| `core/subagent_registry.py` | 694 | 12 |
| `core/model_usage.py` | 685 | 18 |
| `core/repair_proposal.py` | 685 | 6 |
| `core/loop_attempt.py` | 667 | 13 |
| `core/subagent_runner.py` | 666 | 11 |
| `core/replan.py` | 634 | 23 |
| `core/referent_resolver.py` | 625 | 4 |
| `core/incremental_splitter.py` | 617 | 4 |
| `core/completion_contract.py` | 594 | 15 |
| `core/assumption_registry.py` | 577 | 4 |
| `core/ingestion.py` | 577 | 9 |
| `core/loop_verify_replan.py` | 571 | 10 |
| `core/file_request_intent.py` | 570 | 4 |
| `core/planner_prompt.py` | 570 | 6 |
| `core/injection_guard.py` | 567 | 10 |
| `core/scheduler.py` | 541 | 5 |
| `core/answer_format.py` | 540 | 9 |
| `core/memory_hygiene.py` | 538 | 6 |
| `core/tool_receipts.py` | 531 | 6 |
| `core/loop_response_deciders.py` | 527 | 6 |
| `core/source_ranker.py` | 527 | 8 |
| `core/subagent_memory_scope.py` | 519 | 5 |
| `core/loop_memory_read.py` | 518 | 7 |
| `core/source_registry.py` | 514 | 18 |
| `core/budget_ledger.py` | 504 | 12 |
| `core/loop_memory_write.py` | 503 | 11 |
| `core/autonomous_runtime_proposals.py` | 495 | 3 |
| `core/verifier_absence.py` | 491 | 9 |
| `core/verifier_utils.py` | 491 | 4 |
| `core/backlog_signals.py` | 463 | 6 |
| `core/unsupported_claims.py` | 444 | 6 |
| `core/subagent_contract.py` | 440 | 8 |
| `core/task_complexity.py` | 439 | 13 |
| `core/learning_planner.py` | 420 | 7 |
| `core/user_profile.py` | 389 | 5 |
| `core/backlog_target_mapper.py` | 384 | 3 |
| `core/instruction_conflict_gate.py` | 376 | 5 |
| `core/directive_extractor.py` | 372 | 2 |
| `core/memory_hygiene_commands.py` | 360 | 2 |
| `core/completion_obligation.py` | 355 | 8 |
| `core/confidence_vector.py` | 340 | 7 |
| `core/memory_echo_antibody.py` | 333 | 5 |
| `core/loop_context.py` | 332 | 5 |
| `core/approval_triage.py` | 331 | 2 |
| `core/conflict_episode.py` | 328 | 2 |
| `core/subagent_contract_audit.py` | 324 | 4 |
| `core/architecture_audit.py` | 323 | 4 |
| `core/role_router.py` | 317 | 4 |
| `core/loop_gates.py` | 314 | 3 |
| `core/team_plan.py` | 312 | 3 |
| `core/claim_arithmetic.py` | 307 | 3 |
| `core/placeholder_text.py` | 306 | 7 |
| `core/loop_evidence_chain.py` | 302 | 7 |
| `core/state_integrity.py` | 302 | 29 |
| `core/plan_parsing.py` | 301 | 3 |
| `core/backlog_selector.py` | 293 | 8 |
| `core/governance.py` | 290 | 5 |
| `core/model_outcomes.py` | 289 | 3 |
| `core/secret_scanner.py` | 288 | 5 |
| `core/compensation.py` | 285 | 5 |
| `core/truth_hype_filter.py` | 285 | 1 |
| `core/episodic_hygiene.py` | 283 | 4 |
| `tools/file_write.py` | 283 | 24 |
| `core/structured_facts.py` | 266 | 1 |
| `core/budget_kill_switch.py` | 265 | 5 |
| `core/source_library.py` | 264 | 2 |
| `core/clarification_gate.py` | 263 | 2 |
| `core/deep_escalation.py` | 261 | 9 |
| `core/reasoning_action_check.py` | 251 | 10 |
| `core/operational_domain.py` | 250 | 3 |
| `core/loop_verification.py` | 247 | 3 |
| `core/checkpoint.py` | 245 | 7 |
| `core/ingestion_utils.py` | 244 | 0 |
| `core/evidence_classes.py` | 243 | 3 |
| `core/memory.py` | 229 | 57 |
| `tools/diff_file.py` | 229 | 7 |
| `core/output_policy.py` | 228 | 3 |
| `core/alert_ack.py` | 221 | 1 |
| `core/clarification_policy.py` | 221 | 3 |
| `core/proposal_value_gate.py` | 217 | 1 |
| `core/synth_resilience.py` | 216 | 1 |
| `core/subsystem_disagreement.py` | 212 | 3 |
| `core/ingestion_reports.py` | 210 | 0 |
| `core/redaction.py` | 209 | 16 |
| `app/single_instance.py` | 204 | 4 |
| `tools/spawn_subagent.py` | 200 | 2 |
| `core/self_build_supervisor.py` | 198 | 1 |
| `core/actuation_gateway.py` | 197 | 5 |
| `core/dependency_map.py` | 197 | 2 |
| `core/loop_memory_commands.py` | 196 | 2 |
| `core/repair_commands.py` | 191 | 2 |
| `tools/semantic_scholar_search.py` | 189 | 1 |
| `core/backup_cleanup.py` | 188 | 4 |
| `core/self_repair_models.py` | 186 | 3 |
| `core/spend_report.py` | 184 | 1 |
| `tools/base.py` | 183 | 108 |
| `core/evidence_support.py` | 182 | 6 |
| `core/knowledge_use_policy.py` | 180 | 3 |
| `core/loop_observe.py` | 180 | 2 |
| `tools/file_read.py` | 179 | 53 |
| `core/source_registry_store.py` | 176 | 21 |
| `core/run_context.py` | 173 | 6 |
| `core/value_review.py` | 170 | 2 |
| `core/approval.py` | 160 | 57 |
| `core/response_draft.py` | 158 | 4 |
| `tools/python_probe.py` | 156 | 1 |
| `core/self_build_rules.py` | 153 | 2 |
| `core/safe_vcs.py` | 151 | 9 |
| `core/dlp.py` | 150 | 2 |
| `core/topic_tokens.py` | 147 | 4 |
| `core/termination_guard.py` | 145 | 4 |
| `core/completion_marker.py` | 144 | 1 |
| `core/attribute_sieve.py` | 141 | 2 |
| `core/verifier.py` | 141 | 39 |
| `core/verifier_patterns.py` | 138 | 3 |
| `core/budget_governor.py` | 137 | 4 |
| `core/code_state.py` | 137 | 1 |
| `core/policy.py` | 133 | 94 |
| `core/gateway_consult.py` | 130 | 2 |
| `core/prompt_registry.py` | 124 | 2 |
| `core/step_references.py` | 121 | 1 |
| `core/circuit_breaker.py` | 118 | 2 |
| `core/compactor.py` | 118 | 1 |
| `core/heartbeat_io.py` | 118 | 1 |
| `core/verifier_models.py` | 110 | 5 |
| `core/self_repair_utils.py` | 106 | 1 |
| `core/loop_synthesis_helpers.py` | 105 | 0 |
| `core/logger.py` | 103 | 93 |
| `core/data_classifier.py` | 101 | 3 |
| `core/host_tools_context.py` | 101 | 1 |
| `core/mentor_channel.py` | 101 | 1 |
| `core/subagent_quarantine.py` | 101 | 1 |
| `core/bilingual_terms.py` | 99 | 1 |
| `tools/current_time.py` | 99 | 2 |
| `scripts/snapshot_state.py` | 96 | 1 |
| `core/loop_repair.py` | 90 | 3 |
| `core/command_subjects.py` | 85 | 1 |
| `core/repo_provenance.py` | 82 | 2 |
| `core/runtime_self.py` | 81 | 3 |
| `tools/list_dir.py` | 77 | 4 |
| `core/lang_match.py` | 76 | 1 |
| `core/capability_events.py` | 74 | 2 |
| `tools/memory_bank.py` | 64 | 2 |
| `core/loop_knowledge.py` | 61 | 1 |
| `core/pending_clarification.py` | 58 | 1 |
| `core/receipt_consumer.py` | 51 | 1 |
| `core/step_repetition.py` | 51 | 1 |
| `core/loop_synthesis_state.py` | 44 | 0 |
| `core/veto_cause.py` | 36 | 0 |
| `core/builder_reply_diagnosis.py` | 35 | 0 |
| `core/file_lock.py` | 32 | 1 |
| `core/sensor_journal.py` | 31 | 0 |
| `core/loop_sensor.py` | 23 | 1 |
| `core/ids.py` | 20 | 13 |
| `core/writer_completion.py` | 17 | 1 |

## Reachable only from the attended path and never read

| file | lines | tests |
|---|---|---|
| `core/operator_intent_patterns.py` | 1134 | 3 |
| `cli/commands_plan.py` | 671 | 1 |
| `cli/commands_health.py` | 640 | 5 |
| `core/work_session.py` | 565 | 7 |
| `cli/commands_self_build.py` | 510 | 4 |
| `cli/commands_budget.py` | 448 | 1 |
| `app/runtime_cli.py` | 440 | 2 |
| `core/team_executor.py` | 437 | 1 |
| `core/self_task_builder.py` | 424 | 2 |
| `cli/command_specs_ops.py` | 419 | 1 |
| `core/source_connectors.py` | 413 | 1 |
| `cli/command_specs.py` | 412 | 1 |
| `core/model_discovery.py` | 403 | 6 |
| `app/task_scheduler_cli.py` | 383 | 2 |
| `cli/commands_memory.py` | 328 | 6 |
| `core/supply_chain.py` | 313 | 1 |
| `cli/commands_models.py` | 309 | 3 |
| `core/capability_request.py` | 300 | 2 |
| `cli/help.py` | 279 | 3 |
| `core/model_registry_audit.py` | 255 | 1 |
| `core/operator_intent.py` | 254 | 12 |
| `cli/commands_ingest.py` | 251 | 2 |
| `core/conflict_review.py` | 249 | 1 |
| `core/release_hygiene.py` | 200 | 4 |
| `core/conversation_contract.py` | 190 | 1 |
| `cli/commands_self_task.py` | 163 | 1 |
| `cli/commands_source_registry.py` | 161 | 2 |
| `cli/commands_team.py` | 152 | 2 |
| `core/state_store_drill.py` | 149 | 1 |
| `cli/commands_value_review.py` | 143 | 1 |
| `cli/commands_repair.py` | 138 | 1 |
| `core/intent_understanding.py` | 133 | 1 |
| `cli/commands_proposals.py` | 123 | 1 |
| `cli/commands_self_apply.py` | 122 | 2 |
| `cli/command_registry.py` | 101 | 3 |
| `cli/commands_knowledge_review.py` | 99 | 1 |
| `cli/commands_connectors.py` | 96 | 1 |
| `cli/commands_audit.py` | 94 | 1 |
| `cli/commands_learn.py` | 92 | 1 |
| `core/rate_limiter.py` | 90 | 2 |
| `cli/commands_self_split.py` | 89 | 1 |
| `core/strategy_router.py` | 89 | 1 |
| `cli/args.py` | 76 | 2 |
| `app/daemon_notice.py` | 52 | 0 |
| `app/io.py` | 42 | 10 |
| `main.py` | 27 | 41 |
| `cli/self_build_memory.py` | 16 | 1 |

## Reachable from neither entry — dead-or-hidden-path candidates (verify before deleting)

| file | lines | tests |
|---|---|---|
| `app/daemon.py` | 393 | 11 |
| `tools/agent_state_view.py` | 253 | 0 |
| `app/windows_service.py` | 217 | 1 |
| `core/anatomy_groups.py` | 120 | 1 |
| `tools/agent_mcp_server.py` | 68 | 1 |
| `app/__init__.py` | 1 | 5 |
| `cli/__init__.py` | 1 | 1 |
| `core/__init__.py` | 1 | 11 |
| `tools/__init__.py` | 1 | 0 |
| `app/worker_pool.py` | 380 | 2 |
| `app/file_watcher.py` | 356 | 1 |
| `app/priority_event_queue.py` | 278 | 2 |

## Scripts (by hand / CI)

| file | lines | tests |
|---|---|---|
| `scripts/docs_code_conformance.py` | 426 | 2 |
| `scripts/qm_link_check.py` | 418 | 0 |
| `scripts/migrate_memory_pollution.py` | 348 | 1 |
| `scripts/memory_authority_map.py` | 321 | 1 |
| `scripts/completion_legacy_report.py` | 281 | 1 |
| `scripts/decision_authority_census.py` | 222 | 0 |
| `scripts/qm_claim_check.py` | 218 | 0 |
| `scripts/qm_doc_coherence.py` | 194 | 0 |
| `scripts/gen_anatomy.py` | 168 | 3 |
| `scripts/decision_matrix.py` | 151 | 0 |
| `scripts/authority_provenance.py` | 150 | 1 |
| `scripts/capability_baseline.py` | 137 | 2 |
| `scripts/restore_drill.py` | 137 | 1 |
| `scripts/collapse_detector_issue_echoes.py` | 130 | 0 |
| `scripts/qm_mutation_journal.py` | 127 | 0 |
| `scripts/qm_py_anchor.py` | 117 | 0 |
| `scripts/commands_map_check.py` | 112 | 1 |
| `scripts/migrate_completion_backfill.py` | 111 | 0 |
| `scripts/qm_gate_claim.py` | 111 | 0 |
| `scripts/completion_backfill.py` | 102 | 2 |
| `scripts/registry_tally.py` | 100 | 1 |
| `scripts/retire_stale_checkpoints.py` | 99 | 0 |
| `scripts/demote_unverifiable_procedure_standing.py` | 98 | 2 |
| `scripts/agent_anatomy_check.py` | 85 | 0 |
| `scripts/dependency_drift.py` | 65 | 1 |
| `scripts/qm_subject_freeze.py` | 56 | 0 |
| `scripts/generate_sbom.py` | 52 | 2 |
| `scripts/audit_release.py` | 32 | 2 |
| `scripts/mentor_ask.py` | 30 | 0 |
| `scripts/except_audit.py` | 397 | 1 |
| `scripts/architecture_invariants.py` | 255 | 2 |
| `scripts/mutation_probe.py` | 238 | 0 |
| `scripts/check_function_length_baseline.py` | 176 | 2 |
| `scripts/measure_verifier_discrimination.py` | 135 | 0 |
| `scripts/measure_experience_retrieval_discrimination.py` | 130 | 0 |
| `scripts/demote_gate_wait_lessons.py` | 129 | 0 |
| `scripts/docs_link_check.py` | 102 | 1 |
| `scripts/check_ceo_file_baseline.py` | 72 | 3 |
| `scripts/memguard_axis_control.py` | 64 | 0 |
| `scripts/memguard_axis_probe.py` | 59 | 0 |
| `scripts/measure_lesson_provenance.py` | 30 | 0 |