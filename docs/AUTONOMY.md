# Автономный агент

Агент может выполнять полный цикл без вмешательства человека: **observe → reason → plan → act → reflect → improve**.

Связанные документы: **docs/ARCHITECTURE_PLAN.md** (схема систем), **EVOLUTION.md** (sandbox и патчи), **README.md** (запуск и тесты).

## Цикл

| Этап | Описание | Модули |
|------|----------|--------|
| **Observe** | Сбор метрик, self_assessment, sequence_trace | `monitoring.metrics`, `reflection.reflection` |
| **Reason** | Анализ состояния и формулировка цели | Эвристики по метрикам и success_rate |
| **Plan** | Построение плана и постановка задач в очередь | `planning.planner`, `tasks.queue` |
| **Act** | Выполнение задач (run_tool), с учётом политики и квот | `tools.orchestrator.run_tool`, `governance.policy_engine` |
| **Reflect** | Анализ результатов, self_assessment, трассировка | `reflection.reflection` |
| **Improve** | Обновление правил и приоритетов по обратной связи; вывод до 2 правил из feedback за цикл (`rule_derivation` → `rules_safety`) | `learning.learning_manager`, `learning.self_tuning`, `learning.rule_derivation` |

## Ключевые компоненты

- **Orchestrator** (`src.tools.orchestrator.Orchestrator`) — управляет циклом, связывает все модули. Один цикл: `run_cycle()`; бесконечный цикл с лимитом: `run(max_cycles=N)`.
- **Policy Engine** (`src.governance.policy_engine`) — запрещает изменение критических путей (`.cursor/`, `.git/`, `config/agent.json`, `src/main.py`, `src/hitl/`, `src/governance/`), ограничивает число действий за цикл и число циклов, опционально — лимит времени на цикл.
- **Planner и Task Queue** — `planning.planner.make_plan(goal)` и `tasks.queue` (enqueue/dequeue). Планировщик по ключевым словам цели выбирает шаги: время, fetch/API, приоритизация, агрегация чисел, разбор JSON. Задачи выполняются в `act()` через `run_tool`.
- **Генерация задач агентом (task_creator)** — если после `plan(goal)` очередь пустая, вызывается `tasks.task_creator.try_generate_and_enqueue(state)`: по метрикам и эмоциональному состоянию (скука, curiosity, anxiety, fatigue и т.д.) выбираются 1–2 задачи (например «улучшить метрики», «проверить алерты», «план улучшений», «список патчей») и кладутся в очередь через `enqueue()`. Цикл продолжается, `act()` выполняет эти задачи. Так агент сам придумывает себе занятия без явной цели от пользователя.
- **Инструменты расширения автономии** (`src.tools.impl.autonomy_tools`) — `fetch_url` (безопасный GET по allowlist: GitHub API, raw content), `parse_json`, `aggregate_simple` (min/max/sum/avg по массиву чисел), `suggest_priority` (порядок задач по длине описания). Регистрируются вместе с остальными инструментами; доступны через `run_tool` и цикл. Для устойчивости к большим данным: лимиты на длину ввода, размер массивов и вывод задаются в **config** (см. ниже) с запасными значениями по умолчанию; ответ fetch ограничен (параметр передаётся в `api_world`); агрегация — за один проход. **fetch_url** использует LRU-кэш (размер в конфиге); опционально **TTL** (`fetch_cache_ttl_sec`): при истечении запись считается устаревшей и выполняется повторный запрос. Метрики попаданий/промахов кэша: `get_fetch_cache_stats()` в `autonomy_tools` возвращает `fetch_cache_hits` и `fetch_cache_misses`. **manage_queue** — управление очередью задач: `action=status` (размер очереди), `enqueue` (task_id, tool, arguments), `dequeue` (следующая задача). **Метрики времени выполнения** инструментов пишутся в `monitoring.metrics` при каждом вызове через `run_tool` (поля `tool_times` в `get_metrics()`: last_sec, avg_sec, count по каждому инструменту). Инструмент **analyze_tool_performance** (доступен через реестр) возвращает текстовую сводку: самые медленные инструменты по среднему времени, с числом вызовов — для анализа узких мест. **export_performance_summary** выгружает сводку (tool_times + fetch_cache) в JSON-файл (по умолчанию `config/performance_logs/perf_YYYYMMDD_HHMMSS.json`) для исторического анализа. **check_performance_alerts** проверяет превышение порога по инструментам и при наличии пишет в audit запись `performance_alert`. **reset_fetch_cache** очищает кэш fetch_url по команде. Регулярный экспорт: при вызове `Orchestrator().run(..., export_performance_every_n_cycles=N)` сводка выгружается каждые N циклов; альтернатива — Cron: `python -c "from src.monitoring.metrics import export_performance_summary; export_performance_summary()"` по расписанию. Проверка алертов по циклу: параметр **check_alerts_every_n_cycles** в `run()` — при значении &gt; 0 каждые N циклов вызывается `check_performance_alerts()`, результат пишется в лог (при превышениях порога — также в audit). После каждого автономного цикла в лог пишется одна строка со сводкой по последнему времени выполнения инструментов (без детализации по каждому вызову).
- **Sandbox / эволюция** — применение патчей только через `evolution.safety`: submit_candidate_patch → validate_candidate_with_tests → accept_patch_to_stable.
- **Audit** — все действия автономного цикла пишутся в `hitl.audit_log` (autonomous_cycle_start, autonomous_act, autonomous_cycle_end).
- **Память и feedback на диск** — short_term (чат), feedback и long_term сохраняются в `data/*.json`; выведенные правила из `rules_safety` подставляются в контекст LLM при ответе. См. `config/SELF_MODEL.md`, раздел «Память и самообучение».

## Управление автономностью

- **Quotas / Limits**: в `PolicyEngine` задаются `max_actions_per_cycle`, `max_cycles`, `max_cycle_time_sec`.
- **Restricted tools**: в `PolicyEngine(restricted_tool_names=...)` можно передать множество имён инструментов, которые запрещены в автономном цикле (например `write_file`, `accept_patch`). По умолчанию `restricted_tool_names` пусто; константа `RESTRICTED_TOOLS_DEFAULT` задаёт рекомендуемый набор для строгого режима.
- **Approval Layer (опционально)**: при `Orchestrator(use_approval_layer=True)` перед выполнением инструмента вызывается `agency.autonomy_manager.needs_confirmation()`; при необходимости подтверждения действие пропускается (pending_approval).
- **Audit & Logging**: просмотр последних записей — инструмент `get_audit_log` или `src.hitl.audit_log.format_audit_tail(n)`.

## Запуск одного цикла

```python
from src.tools.orchestrator import Orchestrator

orch = Orchestrator()
summary = orch.run_cycle()
# summary: status, goal, outcomes_count, self_assessment, improvements
```

## Запуск цикла из CLI

```bash
python -m src.tools.orchestrator
```

По умолчанию выполняется один цикл (без бесконечного цикла).

## Остановка и квоты

- **Один цикл:** `run_cycle()` возвращает словарь с `status` (`"ok"` или `"quota_exceeded"`), `goal`, `outcomes_count`, `self_assessment`, `improvements`.
- **Несколько циклов:** `run(max_cycles=N)` выполняет до N циклов; при `status == "quota_exceeded"` цикл прерывается.
- Квоты задаются в `PolicyEngine`: при исчерпании `max_actions_per_cycle` или `max_cycles` новые действия/циклы блокируются.

## Конфигурация лимитов

В `config/agent.json` (и в `config/agent.json.template`) можно задать блок **autonomy_limits** для настройки лимитов без смены кода:

- **max_json_input_len** — макс. длина строки на входе parse_json / aggregate_simple / suggest_priority (по умолчанию 100000).
- **max_parse_json_output_len** — макс. длина вывода parse_json (50000).
- **max_aggregate_array_len** — макс. число элементов в массиве для aggregate_simple (10000).
- **max_suggest_priority_tasks** — макс. число задач в suggest_priority (1000).
- **fetch_max_response_bytes** — макс. размер тела ответа для fetch_url в байтах (1048576 = 1 MiB).
- **fetch_cache_max_entries** — размер LRU-кэша для fetch_url (0 = кэш выключен, 50 по умолчанию).
- **fetch_cache_ttl_sec** — время жизни записи в кэше в секундах (0 = без TTL, данные не устаревают).

В блоке **tool_performance**:
- **warn_threshold_sec** — порог времени выполнения инструмента в секундах; при превышении в лог пишется предупреждение и в audit — запись `tool_slow` (tool, duration_sec, threshold_sec). По умолчанию 2.0; 0 = отключить проверку.

При отсутствии блока или ключа используются перечисленные значения по умолчанию.

## Тесты

- `tests/test_governance_policy_engine.py` — Policy Engine (запрещённые пути, квоты, restricted tools, `check_action_allowed`, `check_quota`).
- `tests/test_orchestrator_autonomous.py` — Orchestrator (observe, reason, plan, act, reflect, improve, run_cycle).
- `tests/test_autonomy_tools.py` — инструменты расширения автономии (fetch_url, parse_json, aggregate_simple, suggest_priority).
- `tests/test_planning_planner_extended.py` — планировщик по целям (fetch, prioritize, aggregate, parse json).
