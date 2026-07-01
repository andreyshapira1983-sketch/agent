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

Статус: Done

Исправить parser:

принимать чистый JSON;
принимать JSON внутри ```json;
сохранять raw output;
улучшить диагностику ошибок.

Готово
- парсер не переписан: чистый JSON, JSON внутри ```json-блоков и raw output
  Planner принимаются как прежде; сохранение raw output (PlannerOutput.raw_response)
  не тронуто.
- при невалидном output _parse_json собирает structured diagnostics и кладёт их в
  PlannerOutput.diagnostics: stage (start/direct_parse/substring_extract/parsed/
  failed), reason (краткая причина, включая line/col JSONDecodeError), json_block_found
  (найден ли markdown-fence / JSON-подстрока), fallback (none/markdown_fence/substring/
  empty_plan) и raw_preview.
- raw_preview безопасен: DLP-редакция секретов через core.redaction.redact_dlp_text,
  экранирование переводов строк и жёсткий лимит длины (_RAW_PREVIEW_LIMIT=200) с
  суффиксом "… [+N chars truncated]"; полный секрет никогда не попадает в preview.
- loop.py логирует plan_parse_failed вместе с diagnostics и sanitized raw_preview.
- без self-repair: ошибка парсинга приводит к чистому безопасному fallback
  (пустой план + warning plan_parse_failed), без дополнительного LLM/provider-вызова.

Проверка
- tests/test_planner.py: malformed JSON, markdown-блок с битым JSON и обычный текст
  без JSON дают понятную diagnostics (stage=failed, fallback=empty_plan, корректный
  json_block_found, непустой reason); успешный парсинг — stage=parsed, fallback=none.
- raw output сохраняется (out.raw_response), preview редактирует секреты
  ([REDACTED:...]) и обрезается по длине.
- отсутствие LLM side effects: len(llm.calls) == 1 (ни retry, ни self-repair).
- full pytest: 3364 passed.

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


TD-016 — Cheap Path for Trivial Input

Статус: Done

Проблема
Тривиальные реплики (config-flag echoes вида effects=disabled, приветствия) всё
равно проходят полный конвейер planner (~7k input-токенов) + synthesizer, хотя
planner для них всегда возвращает пустой план (tools_chosen=[]). Шесть таких
реплик подряд выжигали весь часовой llm_calls-бюджет (12/12), и реальные тестовые
сценарии дальше падали с budget exceeded, ничего не проверив.

Что должно быть
Тривиальный no-tool ввод должен пропускать planner-вызов и синтезировать ответ
напрямую (один LLM-вызов вместо двух), не ломая tool-задачи.

Готово
- core/task_complexity.can_skip_planner(text, *, file_hint) — чистая эвристика без
  I/O и без LLM. Пропускает planner ТОЛЬКО при позитивном тривиальном сигнале
  (config-flag key=value ИЛИ чистое приветствие/благодарность) и при отсутствии
  любых дисквалификаторов: file_hint, tool-signal keyword, live-grounding, DEEP.
- Позитивный сигнал намеренно узкий: неоднозначные «what is X» / «define» / «list»
  НЕ пропускаются (могут требовать web_search/file_read), чтобы промах стоил лишь
  одного planner-вызова, а не потерянного tool-шага.
- Приветствия матчатся по словам (токенизация), а не по подстроке, чтобы «hi» не
  срабатывал внутри «this»/«which».
- core/loop.py: cheap-path-ветка в run() (attempt==1, без failure/replan context)
  строит пустой PlannerOutput с warning planner_skipped_cheap_path и логирует
  событие planner_cheap_path; далее идёт штатный empty-plan flow (synthesizer,
  verification, respond). Флаг конструктора cheap_path_enabled=True (можно
  выключить для отката/тестов).

Проверка
- tests/test_task_complexity.py: config-flag echoes и приветствия → skip;
  tool-signal / DEEP / live-grounding / file_hint / неоднозначные вопросы → planner
  выполняется; «hi» не матчится как подстрока; non-string/пустой ввод безопасны.
- tests/test_integration.py::test_cheap_path_skips_planner_llm_call — тривиальный
  ввод даёт ровно один LLM-вызов (synthesizer), событие planner_cheap_path,
  planner-событие с пустым планом; test_cheap_path_disabled_still_calls_planner —
  при выключенном флаге planner вызывается (2 вызова).
- full pytest: 3400 passed.
