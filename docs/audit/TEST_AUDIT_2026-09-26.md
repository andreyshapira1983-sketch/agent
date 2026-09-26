# Ревизия тестов — 26.09.2026

Ветка `test-audit` от main `aa51a94`, в которой уже есть чистка `cleanup-prose-and-lint`. Это ревизия: ни один тест не изменён, не удалён и не пропущен. Решения по предложениям в конце — за владельцем.

Как считалось:
* один прогон с покрытием: `pytest -n 8 --cov=core,tools,cli --cov-branch --cov-context=test`, база покрытия с контекстом каждого теста сохранена;
* обычный прогон без покрытия — для времени и причин пропусков;
* статический разбор всех `tests/**/*.py` через `ast`;
* каждая находка из таблиц открыта и проверена вручную.

## Сводка

| показатель | значение |
|---|---|
| тестов (прогонов pytest) | **11 053**: 10 989 passed, 8 failed, 2 errors, 52 skipped, 2 xfailed |
| тестовых функций в коде (до параметризации) | 9 203 |
| падения в облаке | все 10 из-за окружения: OCR/песочница 5, office 1, https-сторож 2, нет `fastapi` 2; код, который исполняли только они, в покрытии выглядит непокрытым — дыры сверять на сервере |
| тестов без проверок | **28**: 1 пустой по смыслу, 27 проверяют только «не бросает исключение» |
| проверок, которые не могут упасть | **1** |
| тестов, не выполняющихся нигде | **1** (пустой набор параметров) |
| истёкших `xfail` (`until:` в прошлом) | **0**: два закреплены до 2026-09-30 |
| точных дублей | **4 пары** |
| почти дублей | **2 пары** |
| групп с одинаковым покрытием | 1 020 групп / 3 398 тестов; из них не дубли 919 (разные проверки), кандидатов в `parametrize` — 85 групп / 218 тестов |
| покрытие строк | всё 92,8% · `core` 93,2% · `cli` 92,7% · `tools` 88,4% |
| покрытие ветвей | всё 87,5% · `core` 87,7% · `cli` 89,0% · `tools` 84,4% |
| мёртвый код (ни кода, ни теста) | **2 функции** |
| функций, которые зовут только тесты | **47** |

## 1. Тесты, которые ничего не проверяют

### 1а. Пустой по смыслу

| тест | почему пустой |
|---|---|
| `tests/test_commands_approval.py:309` `test_producer_ledger_ignores_items_from_other_origins` | Докстринг обещает «не должно попасть в реестр», но реестр после трёх вызовов не читается; ни одного `assert`. Тест упадёт, только если вызов бросит исключение. |

### 1б. Проверка, которая не может упасть

| место | проверка | почему |
|---|---|---|
| `tests/test_a_past_write_is_not_a_present_claim.py:79` | `assert action_report_mismatch(...) is None or True` | `or True` делает её всегда истинной. Следующая строка (`!=`) проверяет по-настоящему, так что тест не пустой, но эта строка — балласт. |

Ещё 9 проверок вида `f(x) == f(x)` (например, `test_evidence.py:71`, `test_help_render.py:151`, `test_truth_hype_filter.py:87`) — **не пустые**: это проверки детерминизма, они падают, если функция недетерминирована.

### 1в. Тест, который не выполняется нигде

| тест | почему |
|---|---|
| `tests/test_loop_context_split.py:117` `test_logic_moved_symbol_for_symbol` | `PIECES = {8, 9}`, и оба куска внесены в `_RETIRED_BODY_EQUIVALENCE`. Набор параметров пуст, pytest пропускает тест с «got empty parameter set» на любой машине. |

### 1г. Проверяют только «вызов не бросает исключение» (27)

Явного `assert` нет, но тест упадёт, если функция бросит исключение, — для этих функций это и есть поведение (отказ = исключение). Слабые, но не пустые.

| файл:строка | тест |
|---|---|
| `test_a_named_but_missing_budget_config_is_not_no_limits.py:25` | `test_the_tick_accepts_a_cap_named_by_the_environment` |
| `test_a_stub_note_does_not_become_a_file.py:75, 83, 93` | 3 теста «не отказывает» |
| `test_a_write_carries_its_own_text.py:60, 64` | законный вывод или копия не отвергается |
| `test_cli.py:2648` | `test_streams_missing_reconfigure_dont_crash` |
| `test_ingestion_helpers.py:37` | `test_path_inside_workspace_is_allowed` |
| `test_models.py:76, 102, 116, 132, 146, 167, 171, 191, 212` | 9 тестов «модель принимает допустимое значение» (pydantic бросил бы исключение) |
| `test_mutation_probe.py:73` | `test_every_mutation_still_parses` |
| `test_network_deny.py:89` | `test_localhost_by_name_is_still_open` |
| `test_network_safety.py:208, 215` | сторож адреса пропускает публичный адрес и пустого собеседника |
| `test_the_daily_snapshot_covers_every_store.py:130` | `test_a_failing_snapshot_never_stops_the_tick` |
| `test_the_tick_selects_on_the_pinned_engine.py:39` | `test_a_missing_env_file_is_silent` |
| `test_the_verdict_reaches_the_author.py:82` | `test_an_in_memory_inbox_does_not_crash` |
| `test_windows_service.py:149` | `test_validate_contract_accepts_defaults` |
| `test_work_session.py:124, 156` | `to_dict()` сериализуется в JSON |

### 1д. Пропуски

52 пропуска в облаке. Кроме случая 1в, **все зависят от окружения** и на машине владельца выполняются: нет истории git (облако клонирует неглубоко), живых `data/` или `config/`, программ песочницы, `pypdf`, `pwsh`, или нужен Windows.

Всегда пропускаются на Linux:

| файл:строка | причина | где выполняется |
|---|---|---|
| `test_shell_exec_windows_pathext.py:33, 57` | PATHEXT is Windows-only | Windows |
| `test_run_tests_env.py:23` | PATHEXT is Windows-only | Windows |
| `test_shell_binary_resolution.py` | подмена разделителя только для `findstr` | Windows |
| `test_the_sandbox_is_an_explicit_authority.py` | файловая система различает регистр | Windows / macOS |

Истёкших `xfail` нет. Оба `xfail` (`test_recurrence_and_knowledge_gap_are_banked.py:96`, `test_minting_a_skill_has_no_judgement_of_its_own.py:84`) закреплены `until: 2026-09-30` — это через 4 дня.

## 2. Дубли

### 2а. Точные: одна функция, те же входы, те же проверки

| тесты | чем совпадают | что оставить |
|---|---|---|
| `test_a_document_goal_reaches_hands.py:70` `test_a_repair_goal_keeps_the_habitual_pick` = `test_study_turns_reading_into_hypotheses.py:35` `test_a_repair_goal_is_untouched` | Тело одно в одно, `_OPEN_ISSUE` в обоих файлах одинаковый. | Первый: у него докстринг с причиной. |
| `test_operator_intent.py:250` `test_routes_next_actions_phrases` = `:391` `test_best_next_action_does_not_steal_next_actions_list` | Та же фраза «Что делать дальше», те же три проверки. | Второй: имя называет границу. |
| `test_a_resolved_citation_must_be_about_the_claim.py:51` = `:140` `test_the_gate_still_fires_within_one_language` | Тот же вход «Курс акций компании вырос на 20 процентов.», тот же ожидаемый вердикт. | Первый; второй по докстрингу задуман про «один язык», но вход не отличается. |
| `test_fast_path_verification_provenance.py:89` `test_mir002_1_…` = `:108` `test_mir002_2_verifier_error_…` | Код один в один, включая `verified_chunks=0, unverified_chunks=0`. | **Не удалять, а дописать**: по докстрингу второй тест проверяет «верификатор бросил исключение», но этот случай отдельно не моделируется. Это дыра, а не лишний тест. |

### 2б. Почти дубли: разное оформление, те же вход и ожидание

| тесты | чем похожи | что оставить |
|---|---|---|
| `test_one_wall_must_not_evict_the_others.py:365` ≈ `test_the_stop_journal_reaches_goal_selection.py:133` | Нечитаемый `self_stops.jsonl` → `propose_charter_goal` → `proposed`, `stop_considered is False`; помощники `_workspace` и `_reply` совпадают. | Второй: файл целиком о журнале остановок. |
| `test_a_history_proof_outranks_the_quote_path.py:68` ≈ `test_referent_resolver.py:339` | Тот же предыдущий ход, та же фраза «покажи слабые стороны этого», те же две проверки. | `test_referent_resolver.py`: там живут тесты этой функции. |

### 2в. По покрытию: исполняют ровно тот же набор переходов кода

1 020 групп, 3 398 тестов (фаза `run` каждого теста; параметризации одного теста не считаются). Разобраны по исходнику:

| класс | групп | вывод |
|---|---|---|
| A: тело совпадает | 4 | это пары из 2а |
| B: проверки совпадают, входы разные | 97: 85 в одном файле (218 тестов), 12 межфайловых | межфайловые проверены вручную: дубли только 2б; остальные гонят разные входы через одну ветку. 85 внутрифайловых — **кандидаты на `parametrize`**, не дубли (например, `test_web_fetch.py::TestUrlValidation` — схемы `data`/`file`/`ftp`; `test_shell_exec.py` — 15 запрещённых команд) |
| C: тот же файл, проверки разные | 665 | не дубли: путь один, проверяется разное |
| D: разные файлы, проверки разные | 254 | не дубли |

Проверены вручную и **не являются** дублями, хотя похожи:
* `test_sensor_shadow_scenarios.py:128/147/155` и `test_subsystem_disagreement.py:59/96/128` — разные числа в отчёте;
* `test_read_logs_tool.py:224` и `test_the_hands_speak_the_agents_dialect.py:143` — разные пути обхода;
* `test_adaptive_routing.py:651` и `test_task_complexity.py:121` — 5 и 13 входов, пересечений 0;
* `test_secret_scanner.py:129` и `test_a_secret_next_to_a_public_id_is_not_ignored.py:52` — пересечений 0;
* `test_self_analysis_evidence.py:73` и `test_self_analysis_of_the_current_run.py:55` — разные фразы;
* `test_daemon_loop.py:241` и `test_file_watcher.py:355` — разные классы.

## 3. Покрытие

### 3а. Итог

| пакет | строки | ветви |
|---|---|---|
| `core/` | 33 277 / 35 705 = **93,2%** | 10 730 / 12 238 = **87,7%** |
| `cli/` | 3 648 / 3 935 = **92,7%** | 1 292 / 1 452 = **89,0%** |
| `tools/` | 3 019 / 3 416 = **88,4%** | 1 189 / 1 408 = **84,4%** |
| всё | 39 944 / 43 056 = **92,8%** | 13 211 / 15 098 = **87,5%** |

### 3б. Особый список: тормоза и ворота

| модуль | строки | что не исполняет ни один тест |
|---|---|---|
| `core/policy.py` | 100% | — |
| `core/budget_governor.py` | 100% | — |
| `core/approval.py` | 97,8% | заглушка `ApprovalProvider.request` (абстрактная) |
| `core/injection_guard.py` | 94,5% | `_to_text` для двух экзотических типов (стр. 481, 486); `annotate_suspicious_output` стр. 617 |
| `core/approval_inbox.py` | 93,2% | `expire_stale` — ветка истечения (405–410); `_within_hours` (117–120); ошибки чтения в `_load` (624–625) и `_emit_receipt` (574–575) |
| `core/egress_flow.py` | 92,9% | `EgressLedger.note` (67) и `EgressLedger.leak` (88) — по одной ветке |
| `core/self_apply_lane.py` | 91,5% | **откат при сбое записи файла (716–718) и при сбое коммита (771–773); ошибка создания временной ветки (705–706)**; в `judge_change_risks` — существующий тест, правленный вместе с политикой, который не читается или не разбирается (298, 311–312, 316, 321) |
| `core/patch_route.py` | 90,8% | в `settle_patch`: **правка трогает запретное (286–287)**, путь применения не дошёл до коммита (323–324), сбой слияния (327–328), файла правки нет (279) <br>Проверка на сервере 26.09: «правка ослабляет судей» (306–307) исполняется — `tests/test_the_route_without_a_human_cannot_weaken_its_judges.py` проходит; в облаке этот тест упал из-за окружения. |
| `core/budget_ledger.py` | 88,6% | хвостовое чтение большого журнала `_records_for_windows` (200–227); `_limits_from_config` на кривом конфиге (466–477); `_env_int` с плохим значением |
| `core/approval_triage.py` | 88,0% | `format_triage_report` (319–326), `_parse_iso` на плохой отметке |
| `core/budget_kill_switch.py` | 88,1% | **`BudgetKillSwitch.clear` — ручной сброс выключателя оператором (245–248)**; `load` на повреждённом файле (217–218) |
| `tools/journal_append.py` | 88,5% | `JournalAppendTool.validate_output` целиком (462–469); повтор обращения к человеку `_refuse_repeated_voice` (322–331); подсчёт вызовов за день (254–261) |

### 3в. 20 модулей `core/` с самым низким покрытием строк

| модуль | строки | не исполняется |
|---|---|---|
| `drive_goal.py` | 53,1% (102 из 227 не покрыто) | **`propose_drive_goal`** (41 стр.) — главная функция выбора цели по драйвам (`agent_tick --drives`); `_ask` (22), `_recent_tasks`, `_library_sample`, `_load_state` |
| `workflow_memory.py` | 63,1% | `induce_all` целиком (мёртвый, см. 3г); `experience_lines` (16 стр.) |
| `workspace_inventory.py` | 64,9% | `unattended_tools` (9) — список инструментов, открытых безнадзорному прогону |
| `self_stop_record.py` | 65,7% | `reason_kind` (12) — классификация причины остановки |
| `unit_score.py` | 71,4% | ветка «не число → 0.0» (2 стр.) |
| `code_state.py` | 72,8% | `_resolve_ref`, `describe_code_state`, `_files_newer_than_index` |
| `memory_embeddings.py` | 72,9% | `_encoder`, `_load_cache`, `_save_cache` (нужна модель эмбеддингов) |
| `bounded_subprocess.py` | 74,5% | **`kill_process_tree`** (11) — убийство дерева процессов по таймауту |
| `file_lock.py` | 75,0% | ветки `exclusive_file_lock` (22, 29–30) |
| `pending_clarification.py` | 76,5% | `pending_clarification` (8) |
| `wake_events.py` | 80,3% | `outside_commit_ts` (8), `_outside_head` |
| `work_kinds.py` | 81,8% | пустой текст (79) |
| `placeholder_text.py` | 82,1% | `_body_is_stub` (10), `_has_executable_statement` |
| `self_improvement_signals.py` | 82,4% | ветки `_recent_self_improvement_events` и `sync_self_improvement_issue_registry` |
| `autonomous_runtime_proposals.py` | 82,8% | ветки `_parse_proposals`, `_task_propose`, `_existing_proposal_fingerprints` |
| `record_fields.py` | 82,9% | `strict_bool` на недопустимом значении |
| `team_plan.py` | 83,0% | `TeamPlan.user_summary` (11) |
| `receipt_consumer.py` | 83,8% | по одной строке в трёх функциях |
| `campaign_io.py` | 84,4% | `_default_execute_action` (16), `_propose_repair_from_diagnosis` (9) |
| `subagent_quarantine.py` | 84,6% | `quarantine_finding`, `load_quarantine`, `quarantine_status_lines` |

### 3г. Мёртвый код: не вызывает ни код, ни тест

Имя нигде в репозитории, кроме своего определения: ни в `.py`, ни в `knowledge/`, `config/` и `*.qm`, ни строкой. Покрытие — 0.

| функция | где |
|---|---|
| `MarketClient.my_bids` | `core/market_client.py:192` |
| `induce_all` | `core/workflow_memory.py:158` |

### 3д. Функции, которые зовут только тесты (47)

Рабочий код их не вызывает, тесты вызывают. Это не мёртвый код, но и не рабочий: либо недоподключено, либо осталось от прошлого. Самые крупные:
* `ProceduralMemoryStore.recompute_legacy_confidence` (`core/smart_memory.py:851`, 88 стр.);
* `_code_line_count` (`core/backlog_signals.py:321`, 54 стр.);
* `run_intervention` / `try_generalization` / `refute` / `name_scope` (`core/causal_climb.py:58–129`);
* `make_default_proposal` (`core/subagent_memory_scope.py:257`);
* `extract_from_task_and_review` (`core/directive_extractor.py:345`);
* `reviewer_vs_contract` (`core/instruction_conflict_gate.py:350`);
* `SourceRegistry.register_claim` (`core/source_registry.py:275`);
* `format_duration_seconds` (`tools/current_time.py:14`).

Полный список (путь:строка):
- **core**:
  - `alert_ack.py:48`
  - `assumption_registry.py:366, 376, 594, 603`
  - `backlog_signals.py:321`
  - `causal_climb.py:58, 91, 118, 129`
  - `causal_lesson.py:252`
  - `clarification_gate.py:90`
  - `conflict_episode.py:312, 322`
  - `directive_extractor.py:230, 345`
  - `episodic_hygiene.py:196`
  - `evidence.py:178, 187, 833, 917`
  - `instruction_conflict_gate.py:350`
  - `lang_match.py:64`
  - `memory_echo_antibody.py:311`
  - `memory_embeddings.py:84, 187`
  - `model_outcomes.py:248`
  - `persistent_memory.py:216`
  - `prompt_registry.py:117, 122`
  - `rate_limiter.py:73`
  - `receipt_consumer.py:24`
  - `replan.py:73`
  - `smart_memory.py:851`
  - `source_registry.py:275, 312`
  - `strategy_router.py:87`
  - `subagent_memory_scope.py:257`
  - `synth_resilience.py:62`
  - `task_complexity.py:124, 180`
  - `tool_receipts.py:283`
  - `truth_hype_filter.py:157`
- **tools**: `current_time.py:14`
- **cli**:
  - `command_registry.py:89, 99`
  - `help.py:269`

## 4. Скорость: 20 самых медленных тестов

Прогон без покрытия, `-n 8`, весь набор за 75,9 с.

| с | тест |
|---|---|
| 19,84 | `test_a_forbidden_file_is_not_offered_as_work.py::test_the_live_backlog_offers_the_charter_nothing_fenced` |
| 9,39 | `test_backlog_architecture_audit.py::test_load_backlog_surfaces_real_audit_gaps` |
| 8,58 | `test_backlog_oversized_module.py::test_real_repo_surfaces_the_least_readable_function_first` |
| 8,08 | `test_the_memory_authority_probe_can_see.py::test_a_constant_counts_as_the_address_of_the_store` |
| 7,89 | `test_one_task_store.py::test_every_module_that_queues_tasks_names_the_same_file` |
| 7,40 | `test_cns_state_init.py::test_no_field_became_dead_unnoticed` |
| 6,85 | `test_cns_state_init.py::test_every_lifetime_class_matches_the_model` |
| 6,60 | `test_dependency_map.py::test_real_project_scan_finds_verifier_consumers` |
| 5,75 | `test_except_audit_ratchet.py::test_the_audit_looks_where_the_concern_is` |
| 5,70 | `test_every_paid_call_path_carries_the_cap.py::test_no_router_is_given_a_ledger_that_skips_the_budget` |
| 5,65 | `test_sensor_failure_journal.py::test_loop_py_has_no_unjustified_silent_handlers_left` |
| 5,48 | `test_except_audit_ratchet.py::test_the_scanner_sees_the_known_landscape` |
| 5,39 | `test_function_length_ratchet.py::test_every_watched_function_still_exists` |
| 5,06 | `test_except_audit_ratchet.py::test_the_zero_is_not_bought_with_fig_leaves` |
| 4,60 | `test_except_audit_ratchet.py::test_no_new_unjustified_silent_broad_excepts` |
| 4,44 | `test_function_length_ratchet.py::test_the_ratchet_holds` |
| 4,26 | `test_no_test_pins_a_production_path.py::test_the_file_that_blocked_part_b_no_longer_pins_a_path` |
| 4,11 | `test_prose_does_not_grow.py::test_prose_does_not_grow` |
| 4,10 | `test_the_memory_authority_probe_can_see.py::test_the_money_store_has_both_a_writer_and_a_reader` |
| 3,85 | `test_no_test_pins_a_production_path.py::test_no_new_test_reads_shipped_code_by_a_literal_path` |

Почти все медленные — сканеры всего репозитория (храповики, аудиты, карты). Четыре теста `test_except_audit_ratchet.py` (≈21 с суммарно) и два `test_function_length_ratchet.py` каждый заново сканируют все файлы.

## Предложения (в этой сессии ничего не сделано)

### Убрать или объединить

1. Удалить один из пары точных дублей:
   - `test_study_turns_reading_into_hypotheses.py:35` (дублирует `test_a_document_goal_reaches_hands.py:70`);
   - `test_operator_intent.py:250` (дублирует `:391`).
2. `test_a_resolved_citation_must_be_about_the_claim.py:140`: либо удалить, либо дать ему вход, который правда проверяет «один язык» (сейчас вход тот же, что у `:51`).
3. Почти дубли — оставить по одному:
   - удалить `test_one_wall_must_not_evict_the_others.py:365`;
   - удалить `test_a_history_proof_outranks_the_quote_path.py:68` (оставить `test_referent_resolver.py:339`).
4. Убрать балласт `or True` в `test_a_past_write_is_not_a_present_claim.py:79`: проверку этой строки заменить на `is None` (если это и есть ожидание) или удалить строку.
5. `test_loop_context_split.py:117`: параметров нет и не будет — удалить тест либо вернуть ему хотя бы один кусок.
6. По желанию — свести в `parametrize` 85 внутрифайловых групп (218 тестов), где одна проверка гоняется на разных входах. Число проверок не уменьшится, станет меньше кода. Крупнейшие:
   - `test_shell_exec.py` — 15 запрещённых команд;
   - `test_user_profile.py`;
   - `test_web_fetch.py::TestUrlValidation`;
   - `test_planner.py::*Sanitizer`;
   - `test_planner_self_repair_sanitizer.py`.
7. Мёртвый код: `MarketClient.my_bids`, `workflow_memory.induce_all` — удалить или подключить.
8. Скорость: храповикам `except_audit`, `function_length` и `cns_state_init` сканировать репозиторий один раз на модуль (фикстура уровня `module`) — экономия около 20 с на прогон.

### Дописать в первую очередь (важные непокрытые места)

1. **`patch_route.settle_patch`**: правка, трогающая запретное (`_FORBIDDEN`), обязана остановиться. Список запретного проверен отдельно, но ветку остановки в `settle_patch` не исполняет ни один тест.
2. **`self_apply_lane.run_self_apply_lane`**: откат при сбое записи файла и при сбое коммита; ошибка создания временной ветки. Самоправка без проверенного отката.
3. **`BudgetKillSwitch.clear`** — ручной сброс выключателя; и `load` на повреждённом файле состояния.
4. **`test_fast_path_verification_provenance.py:108`**: дать тесту настоящий эпизод «верификатор бросил исключение», а не копию первого случая.
5. **`test_commands_approval.py:309`**: проверить, что реестр после трёх вызовов пуст.
6. **`budget_ledger._records_for_windows`**: хвостовое чтение большого журнала (> `_TAIL_READ_BYTES`), где считаются деньги за окно.
7. **`drive_goal.propose_drive_goal`** — выбор цели по драйвам не исполняется ни одним тестом.
8. **`bounded_subprocess.kill_process_tree`** — единственный механизм, убивающий зависший процесс с детьми.
9. `approval_inbox.expire_stale` (истечение заявок), `tools/journal_append.validate_output` и `_refuse_repeated_voice` (повторное обращение к человеку).
