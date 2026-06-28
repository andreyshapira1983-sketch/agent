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

Пропуск Planner/Synthesizer для команд:

:help
:model-usage
:budget-window-status
:models
:auto-status
:approval-list
:schedule-list
:source-registry
TD-003 — Planner JSON Parsing

Исправить parser:

принимать чистый JSON;
принимать JSON внутри ```json;
сохранять raw output;
улучшить диагностику ошибок.
TD-004 — Budget Enforcement

Сейчас агент может тратить огромный бюджет ещё до того, как поймёт, что задача простая.

Нужно проверять стоимость до первого LLM-вызова.

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
