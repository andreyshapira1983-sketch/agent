TECH_DEBT.md
P0 — Критично (делать в первую очередь)
TD-001 — Cheap Routing

Статус: Done

Проблема
Простые команды используют Claude, хотя могут выполняться локально или на дешёвой модели.

Что должно быть

local-only команды → 0 LLM
лёгкие задачи → GPT
тяжёлые задачи → Claude

Готово
- local-only bypass для :model-usage, :budget-window-status, :models, :help, :quit
- one-shot --ask meta-commands работают без Planner/Synthesizer
- REPL meta-commands работают без Planner/Synthesizer
- lightweight LLM-задачи направлять на openai-default-small / gpt-4o-mini
- тяжёлые задачи оставить на Anthropic
- добавить тесты на отсутствие model_call_start для local команд

Проверка
- REPL: session_calls=0 после local-only команд
- ModelRouter runtime check: LIGHT "привет" -> openai/gpt-4o-mini
- ModelRouter runtime check: STANDARD задача -> anthropic/claude-sonnet-4-5
- py_compile: main.py, core/model_router.py, tests/test_cli.py, tests/test_model_router.py
- full pytest сейчас заблокирован локальным Python окружением (.venv указывает на отсутствующий Python 3.11; bundled Python без pytest)

TD-002 — Deterministic Bypass

Статус: Done

Пропуск Planner/Synthesizer для команд:

:help
:model-usage
:budget-window-status
:models
:auto-status
:approval-list
:schedule-list
:source-registry

Готово
- :auto-status, :approval-list, :schedule-list, :source-registry разрешаются
  детерминированно через handle_meta_command (существующий local-only bypass),
  без Planner, без Synthesizer и без единого LLM/provider-вызова. Обработчики
  read-only: читают runtime status / approval inbox / scheduler / source registry
  и печатают результат.
- механизм не менялся (без рефакторинга); поведение уже готовых local-only команд
  (:help, :model-usage, :budget-window-status, :models) сохранено.

Проверка
- tests/test_cli.py::test_local_meta_commands_do_not_start_model_calls — список
  расширен четырьмя командами (+ варианты с аргументами): команда обработана,
  agent.llm.calls == 0, model_call_start отсутствует в логе.
- tests/test_cli.py::test_local_meta_commands_never_invoke_planner_or_synthesizer
  — spy на planner.plan доказывает 0 вызовов Planner; отсутствие model_call_start
  и agent.llm.calls == 0 доказывают, что Synthesizer/LLM не вызывались.
- full pytest: 3359 passed.

TD-003 — Planner JSON Parsing

Исправить parser:

принимать чистый JSON;
принимать JSON внутри ```json;
сохранять raw output;
улучшить диагностику ошибок.
TD-004 — Budget Enforcement

Статус: Done

Сейчас агент может тратить огромный бюджет ещё до того, как поймёт, что задача простая.

Нужно проверять стоимость до первого LLM-вызова.

Готово
- pre-flight оценка стоимости в ModelUsageLedger.assert_can_start: по размеру
  промпта (system+user) плюс output-cap оценивается число токенов и cost units
  предстоящего вызова.
- блокировка, если totals + оценка превышают session-лимит
  (AGENT_MODEL_MAX_TOKENS_PER_SESSION / AGENT_MODEL_MAX_COST_UNITS_PER_SESSION).
- BudgetLedger.check() — read-only проверка persistent-окон model_tokens /
  model_cost_units без записи; сначала выполняются неблокирующие проверки, и лишь
  затем резервируется llm_calls, чтобы заблокированная оценка не создавала
  фантомный вызов.
- параметры оценки прокидываются из UsageTrackedLLM.complete (system/user/
  max_tokens/cost_tier).
- нулевые лимиты по-прежнему означают «не enforced» — поведение по умолчанию не
  изменилось.

Проверка
- tests/test_model_usage.py: pre-flight блокирует один крупный вызов по session
  cost/token cap и по persistent-окну, не записывая фантомный llm_call.
- tests/test_budget_ledger.py: check() ничего не пишет и учитывает уже
  израсходованный бюджет.
- full pytest: 3348 passed.

P1 — Архитектура
TD-005 — Project Indexer

Индекс всего проекта.

TD-006 — Function Map

Карта:

функции;
классы;
зависимости;
импорты.
TD-007 — Chunk Retrieval

Не читать огромные файлы целиком.

Читать только нужные куски.

TD-008 — Context Reduction

Уменьшать контекст перед отправкой модели.

TD-009 — Source Registry

Улучшить регистрацию:

файлов;
функций;
зависимостей;
изменений.
TD-010 — Model Routing

Добавить реальные правила выбора моделей:

Local
GPT
Claude

в зависимости от сложности.

P2 — Будущее
TD-011 — Live Model Discovery

Автоматически узнавать:

новые GPT;
новые Claude;
Gemini;
DeepSeek;
Llama.
TD-012 — Provider Catalog Refresh

Автоматически обновлять registry моделей.

TD-013 — Self Improvement

Агент сам предлагает архитектурные улучшения.

TD-014 — Long-term Memory Optimization

Оптимизация памяти.

TD-015 — Automatic Refactoring Suggestions

Автоматические предложения по рефакторингу.

Roadmap
Sprint 1
Cheap Routing
Deterministic Bypass
Planner JSON
Budget Enforcement
Sprint 2
Project Indexer
Function Map
Chunk Retrieval
Context Reduction
Sprint 3
Live Model Discovery
Provider Catalog
Self Improvement
Memory Optimization
