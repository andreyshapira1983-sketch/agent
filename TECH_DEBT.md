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

Статус: Done

Проблема
for_task выбирал tier (LIGHT/STANDARD/DEEP) по сложности, но PROVIDER всегда брался
из фиксированной role-route/default-строки. Не было маршрутизации провайдера по
сложности среди уже поддерживаемых провайдеров, и не было честного reason о том,
почему выбран тот или иной провайдер (и что было пропущено).

Что должно быть (согласованный scope)
Маршрутизация ТОЛЬКО провайдера по сложности среди уже поддерживаемых провайдеров.
Без Ollama/local backend, без fake-local alias, без хардкода имён моделей.
- LIGHT    → huggingface → openai → role default
- STANDARD → openai              → role default
- DEEP     → anthropic           → role default

Готово
- core/model_router.py:
  * _TIER_PROVIDER_PREF — упорядоченные списки провайдеров по tier (по value tier,
    без импорта task_complexity). local НЕ входит в дефолты (реального backend нет).
  * _TIER_PROVIDERS_ENV + _tier_provider_prefs — env-override
    AGENT_TIER_PROVIDERS_{LIGHT,STANDARD,DEEP} (список через запятую) перекрывает
    дефолтную преференцию.
  * _provider_has_credentials — провайдер доступен только если он в
    SUPPORTED_PROVIDERS и заданы все нужные env-ключи; неподдержанные (например
    local) и без ключей → пропускаются gracefully.
  * _resolve_tier_provider(tier, role_key) — идёт по преференции, первый
    поддержанный+с ключами провайдер, у которого ЕСТЬ catalog/env tier-модель
    (tier_model_for), выигрывает. Явный per-role provider (AGENT_<ROLE>_PROVIDER)
    полностью отключает преференцию — выбор оператора важнее.
  * for_task: сначала вызывает _resolve_tier_provider. Если провайдер выбран —
    модель всё равно берётся из model_catalog.tier_model_for(tier, provider) (имена
    не хардкодятся). Если нет — STANDARD уходит в for_role (как раньше), LIGHT/DEEP
    падают на role-default с прежней LIGHT-registry-fallback логикой. DEEP в обоих
    путях проходит существующий deep-escalation gate без изменений.
- Reason строки (честные): complexity:{tier}:{provider}, fallback:role_default,
  а пропуски добавляются как |skipped:provider_unavailable:<name>
  (например |skipped:provider_unavailable:local) или no_model:<name>.

Гарантии
- Имена моделей не хардкодятся (источник истины — tier_model_for).
- Static/mock LLM (ModelRouter.single) не затронут.
- DEEP по-прежнему требует operator escalation; без причины — downgrade в standard.
- Нет multi-provider config / ключей → поведение как раньше (role default).
- Явный role-provider env выигрывает у преференции.

Проверка
- tests/test_provider_routing_td010.py (10 тестов, без реальных API-вызовов):
  LIGHT→huggingface; LIGHT fallback→openai со skipped:hf; STANDARD→openai;
  DEEP→anthropic c operator escalation; DEEP без причины → downgrade; env-override
  провайдера; skipped:provider_unavailable:local; приоритет явного role-provider;
  no-config → role default; static LLM без изменений.
- Targeted: tests/test_model_router.py, test_adaptive_routing.py,
  test_task_complexity.py, test_model_usage.py — 187 passed.
- Full pytest: 3414 passed.

P2 — Будущее
TD-011 — Live Model Discovery
Статус: Done (read-only) — см. запись в конце файла.

Автоматически узнавать:

новые GPT;
новые Claude;
Gemini;
DeepSeek;
Llama.
TD-012 — Provider Catalog Refresh
Статус: Partial — dry-run/read-only foundation сделан; write/apply отложены.

Автоматически обновлять registry/каталог моделей — write/apply пока отложен.

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

TD-017 — Cheap Path Cost Reduction for General Questions

Статус: Done

Проблема
Даже когда cheap-path (TD-016) пропускает planner, тривиальная реплика всё равно
тратит больше, чем нужно: synthesizer идёт на STANDARD-модели (config-flag echo не
несёт LIGHT-сигнала для assess_complexity), в промпт синтезатора инжектится тяжёлый
<long_term_memory> блок (растёт от хода к ходу), а после ответа каждый ход гоняется
полная memory_consolidation (перечитывает ВСЕ эпизоды + процедуры) и knowledge
pipeline над пустой evidence-цепочкой. Для «привет» это чистые накладные расходы.

Что должно быть
На cheap-path-ходах (planner уже пропущен) удешевить оставшийся путь, не трогая
сам planner-skip и не ломая поведение обычных tool-задач:
1. дешёвая модель для synthesizer;
2. урезанный контекст в промпте synthesizer;
3. не гонять memory_consolidation и knowledge pipeline каждый такой ход.

Готово
- core/loop.py run(): флаг cheap_path_active выставляется только когда сработала
  cheap-path-ветка. Он прокидывается в три места:
  * synthesizer: model_router.for_task(SYNTHESIZER, q, force_tier=LIGHT) —
    гарантирует дешёвый tier даже если assess_complexity дал STANDARD; логируется
    событие cheap_path_synth_model.
  * knowledge pipeline: на cheap-path-ходе knowledge_pipeline.run и source-registry
    build пропускаются (цепочка пустая — каталогизировать нечего); логируется
    knowledge_pipeline_skipped, self.last_source_registry остаётся пустым.
  * consolidation: _record_experience_memory(skip_consolidation=True) — эпизод и
    процедура пишутся как обычно (обучение не теряется), но полная
    consolidate_memory пропускается; логируется memory_consolidation_skipped.
- core/loop.py _synthesize(..., lean_context): при lean_context=True из промпта
  убираются тяжёлые/нерелевантные блоки — <long_term_memory>, профиль пользователя,
  run-assumptions и role-блок. conversation_history сохраняется (континуитет).
- core/model_router.for_task(..., force_tier): опциональный явный tier. Более
  дорогой tier по-прежнему проходит через штатный escalation-гейт (forced DEEP не
  открывает Opus без оператора). Static/Fake LLM возвращается как есть (тесты не
  затронуты).

Проверка
- tests/test_integration.py:
  * test_cheap_path_skips_knowledge_pipeline_and_picks_light_synth — cheap-ход даёт
    knowledge_pipeline_skipped, cheap_path_synth_model и НЕ даёт knowledge_pipeline.
  * test_non_cheap_turn_still_runs_knowledge_pipeline — обычный tool-ход сохраняет
    knowledge_pipeline (регрессия).
  * test_synthesize_lean_context_drops_long_term_memory — lean_context=True убирает
    <long_term_memory> из промпта, дефолт его сохраняет.
- tests/test_smart_memory.py::test_cheap_path_skips_consolidation_but_still_records_episode
  — cheap-ход пишет эпизод, но НЕ гоняет consolidation (memory_consolidation_skipped,
  0 отчётов).
- full pytest: 3404 passed.


TD-019 — Duplicate Memory Write Waste (knowledge pipeline)

Статус: Done

Проблема
На повторяющемся read-only ходе (например, work-session, который каждый цикл
перезапускает одну и ту же цель) knowledge pipeline заново извлекает те же ~60
claim'ов из тех же файлов и пытается записать каждый в persistent memory. Раньше
КАЖДЫЙ AgentLoop.remember() перечитывал весь store (self.persistent_store.load(),
~456 записей) ради dedup-гейта — то есть O(claims × records) перезагрузок с диска
за цикл, причём почти все записи всё равно отклонялись как дубликаты. Чистые
накладные расходы, растущие с размером памяти.

Что должно быть
Грузить снапшот store ОДИН раз за проход pipeline, сохранив dedup/echo-поведение
ровно как было; не трогать одиночный remember() из ingestion.

Готово
- core/loop.py remember(): добавлен необязательный параметр existing:
  list[MemoryRecord] | None. При None — как раньше (self.persistent_store.load()),
  что сохраняет поведение всех текущих вызовов; при переданном списке (в т.ч.
  пустом []) reload не происходит. После save записанный (уже DLP-редакт.) record
  добавляется в existing — later-writes в том же проходе дедуплицируются ровно так
  же, как при свежем load().
- core/loop.py _knowledge_remember_batch() -> RememberFn: грузит снапшот один раз
  и возвращает closure, вызывающий remember(..., existing=snapshot). Оба вызова
  knowledge_pipeline.run(remember=...) теперь используют этот batch-callback вместо
  _remember_from_knowledge (тот оставлен без изменений — на него ссылаются
  core/ingestion.py и tests/test_ingestion_helpers.py).
- Echo-гейт (memory_write_registry.recent) и логирование каждого reject сохранены
  без изменений — фикс убирает лишний дисковый I/O, а не телеметрию.

Проверка
- tests/test_knowledge_remember_batch.py:
  * remember(existing=snapshot) не перезагружает store и добавляет сохранённый
    record в снапшот;
  * _knowledge_remember_batch() грузит store ровно один раз независимо от числа
    записей;
  * дубликат в том же проходе всё ещё reject (снапшот актуален без reload);
  * контент из прошлого цикла reject на следующем проходе без per-claim reload.
- targeted: test_knowledge_pipeline / test_persistent_integration / test_integration
  / test_ingestion_helpers / test_work_session / test_smart_memory — 102 passed.
- full pytest: 3418 passed.


TD-018 — Work-Session Convergence Stop

Статус: Done

Проблема
run_work_session крутил ровно max_cycles циклов, даже когда цель уже не двигалась:
каждый цикл заново запускал ту же goal с тем же результатом (agent переспрашивает
одно и то же, прогресса нет), выжигая весь бюджет впустую. Единственные early-stop
были time_budget, circuit_open, interrupted — сигнала «сошлось / нечего делать» не
было.

Что должно быть
Обнаружить установившееся состояние (несколько подряд идентичных по результату
циклов) и остановиться раньше, не ломая поведение коротких сессий и существующих
тестов, без большого рефактора.

Готово
- core/work_session.py WorkSessionConfig: добавлены stop_on_convergence: bool = True
  и convergence_window: int = 3 (валидация >= 2). Дефолт включён — CLI (:work-session)
  использует kwargs, поэтому фикс активен без изменений вызова.
- Новый WorkSessionStopReason "converged".
- _cycle_signature(run_report): контент-подпись прохода = (status, tuple((task.kind,
  status, summary))). Намеренно без времени/счётчиков — реальный прогресс меняет
  summary и рвёт серию; спиннинг даёт идентичную подпись. Best-effort: любая
  неожиданная форма схлопывается в стабильный tuple, детекция не падает.
- В цикле после cycle_reports.append(cr): считается серия одинаковых подписей;
  при repeat_count >= convergence_window И cycle < max_cycles — stop_reason=
  "converged", status="completed", лог work_session_converged, break.
- Guard cycle < max_cycles: серия, достигнутая ровно на последнем цикле, НЕ
  переопределяет естественное завершение (stop_reason=""), поэтому прогоны с
  max_cycles <= convergence_window (в т.ч. все текущие тесты) не затронуты.

Проверка
- tests/test_work_session_convergence.py:
  * _cycle_signature: идентичные отчёты равны; смена summary/status рвёт подпись;
    битый отчёт не бросает.
  * converged на 3-м цикле из 8 (stop_reason="converged", status="completed");
  * stop_on_convergence=False проходит все 5 циклов (stop_reason="");
  * max_cycles == convergence_window не режется раньше времени;
  * событие work_session_converged пишется в лог.
- tests/test_work_session.py: 31 существующий тест без изменений — passed.
- full pytest: 3428 passed.


TD-021 — Honest route_reason on TD-010 Fallback

Статус: Done

Проблема
Когда tier-модели нет (каталог не заполнен и нет AGENT_MODEL_TIER_*), for_task
падал в role-default через `return self.for_role(role_key)`. Модель выбиралась
правильная, но route_reason в ledger'е становился непрозрачным registry-id вроде
`policy:balanced:<route>` — по логам было не видно, что complexity-routing вообще
отработал и почему ушёл в fallback. Это и есть «adaptive_route как будто игнорит
TD-010»: маршрутизация работает, но её не видно.

ВАЖНО: полноценный routing по tier'ам всё равно требует источника tier-моделей
(каталог, TD-011/012). TD-021 — только честная наблюдаемость fallback'а, без
изменения выбора модели.

Что должно быть
На fallback-путях for_task сохранить ровно ту же модель, что и for_role, но
записать честный reason: assessed complexity tier + факт fallback + пропущенные
провайдеры. Никакого хардкода моделей, без нового поведения.

Готово
- core/model_router.py for_task: два reason-теряющих `return self.for_role(...)`
  заменены на `self._for_role_with_reason(role_key, <honest reason>)` (существующий
  механизм: та же кэшированная модель/cost_tier, меняется только stamped reason).
  * STANDARD без preferred provider:
    complexity:standard|fallback:role_default[|skipped:...]
  * нет tier-модели (LIGHT/STANDARD/DEEP):
    complexity:<tier>|fallback:role_default:no_tier_model[|skipped:...]
  Детали skipped добавляются через существующий _append_skipped (безопасно
  ограничены). DEEP-downgrade путь (_for_role_with_reason с decision.route_reason)
  не тронут.

Проверка
- tests/test_td021_honest_route_reason.py:
  * no-tier-model fallback: model == role-default-model, reason начинается с
    complexity:light, содержит fallback:role_default:no_tier_model и skipped;
    reason НЕ начинается с policy:.
  * STANDARD-no-pref fallback: reason complexity:standard|fallback:role_default с
    provider_unavailable:openai; model неизменна.
  * fallback-модель совпадает с for_role (provider+model) — фикс только про reason.
- tests/test_provider_routing_td010.py: 10 тестов TD-010 без изменений — passed.
- routing/adaptive/escalation suites: 137 passed.
- full pytest: 3431 passed.

----------------------------------------------------------------------

TD-020 — Reasoning ↔ Action Check: точность word-boundary matching

Статус: Done

Проблема
core/reasoning_action_check.py сверяет свободный текст reasoning планировщика с
выбранными tool'ами (MAST FM-2.6). Прямое направление `_reasoning_mentions`
искало ключевые слова простым substring-ом. Из-за этого короткие ASCII-стемы
ложно срабатывали внутри несвязанных слов:
- `read ` матчилось внутри `thread`;
- `ls ` матчилось внутри `calls` / `class`;
- `url` матчилось внутри `curl`.
Итог: действие помечалось как «обосновано», хотя reasoning его не упоминал —
реальный reasoning↔action mismatch тихо скрывался. Обратное направление
(mentioned_but_not_planned) уже имело strength-фильтр, прямое — нет
(асимметрия).

ВАЖНО: проверка остаётся сугубо наблюдательной (report only, вызывается под
`except: pass` в loop). Это не self-repair и не блокировка плана — только
точность диагностики. Никакого нового поведения, никаких LLM/provider-вызовов.

Что должно быть
Единый boundary-aware матчер ключевых слов, который:
- сохраняет substring-семантику для кириллических стемов (`прочита` →
  `прочитаю`) и многословных фраз (`read the file`) — это высокосигнальные
  и намеренно инфлективные записи;
- для одиночного ASCII-токена (`read`, `ls`, `url`) требует word-boundary,
  чтобы он не матчился внутри `thread` / `calls` / `curl`.

Готово
- core/reasoning_action_check.py:
  * добавлен helper `_keyword_in_text(text, kw)`: фразы и не-ASCII → substring;
    одиночный ASCII-токен → regex с lookaround-границами
    `(?<![0-9a-z])<kw>(?![0-9a-z])`.
  * `_reasoning_mentions` (прямое направление) использует helper вместо
    `kw.lower() in text`.
  * обратное направление (strong-keyword gate, `_` или len>=6) тоже проходит
    через helper — симметрия без изменения gate-логики.
  * добавлен `import re`.
- Стемы и фразы работают как раньше; ужесточены только короткие ASCII-токены —
  ровно источник ложных срабатываний.

Проверка
- tests/test_reasoning_action_check.py (7 → 13 тестов):
  * `read ` не матчит `thread` → file_read остаётся unjustified;
  * `ls ` не матчит `calls`/`class` → list_dir unjustified;
  * `url` не матчит `curl` → web_fetch unjustified;
  * настоящие whole-word `read`/`url` всё ещё матчат (нет false negatives);
  * кириллические стемы `содержим`/`каталог`/`прочита` не задеты фиксом.
- targeted: 13 passed.
- full pytest: 3463 passed.

----------------------------------------------------------------------

TD-011 / TD-012 — Live Model Discovery + Provider Catalog Refresh (read-only)

Статус:
- TD-011 Live Model Discovery: Done
- TD-012 Provider Catalog Refresh: Partial — dry-run/read-only foundation
  сделан; write/apply refresh каталога отложены (не сделаны).

Область (намеренно узкая, безопасная)
Только read-only discovery + dry-run diff. По явному одобрению scope:
- нет записи каталога;
- нет автопереключения моделей;
- нет self-update;
- нет реальных inference-вызовов;
- секреты не печатаются (только ИМЕНА env-переменных + booleans).
Запрос списка моделей у провайдера — metadata-only / non-inference, но всё
равно внешний сетевой вызов: делается только из явного dry-run пути, никогда
из audit-пути или обычного прогона.

Готово (TD-011)
- core/model_catalog.py: выделен discover_catalog() — read-only половина
  refresh_catalog() (запрос + классификация по тирам, без записи файла).
  refresh_catalog() теперь тонкая обёртка = discover_catalog() + _save_catalog();
  поведение и возвращаемое значение не изменились.
- core/model_discovery.py (новый): dataclasses ProviderDiscovery / CatalogDiff /
  DiscoveryReport; статусы провайдеров (queried / skipped_no_creds /
  unsupported_no_fetcher / unsupported_provider / skipped_by_arg);
  build_discovery_audit() — локально, без сети, ничего не пишет;
  _diff_catalog() — чистый diff added/removed/changed (tier_best) против
  config/model_catalog.json.
- cli/commands_models.py: _handle_model_discovery_audit -> команда
  :model-discovery-audit (алиас :discovery-audit), local-only, no-network.
- main.py: dispatch/help/import (тот же deterministic bypass-путь, что и у
  других :-команд — без Planner/Synthesizer/LLM). TD-010 routing не тронут.

Готово как foundation (TD-012, dry-run only)
- core/model_discovery.py: build_discovery_report() — dry-run discovery. Сеть
  только для провайдеров, у которых supported + fetcher + credentials; всё
  остальное репортится статусом и не контактируется. Каталог НЕ пишется.
- cli/commands_models.py: _handle_provider_catalog_refresh -> команда
  :provider-catalog-refresh --dry-run [--anthropic] [--openai] [--json].
  Без --dry-run — только usage (сеть недостижима). Флаг --write намеренно
  отклоняется (зарезервирован под будущий, отдельно одобряемый write/apply).

Отложено / НЕ сделано (осознанно, вне текущего scope)
- запись/обновление config/model_catalog.json новой командой (write/apply);
- rollback-механика для применённого refresh;
- huggingface / mock live-fetcher (сейчас unsupported_no_fetcher);
- любое автопереключение модели или правка model_registry.

Проверка
- tests/test_model_discovery.py (новый, 16 тестов): discover_catalog не пишет
  файл; refresh_catalog всё ещё пишет; diff added/removed/changed; audit —
  локальный, fetcher'ы не вызываются; dry-run контактирует только провайдеров
  с ключами (провайдер без ключа не контактируется — patched fetcher падает,
  если вызван); секрет-значение ключа не утекает в вывод; --write отклоняется;
  без --dry-run сети/записи/лог-события нет.
- tests/test_cli.py: :model-discovery-audit добавлен в TD-002 local-only списки
  (0 LLM-вызовов, нет model_call_start, Planner не вызывается).
- targeted: 16 passed (discovery) / 149 passed (discovery+catalog+cli).
- full pytest: 3482 passed.

----------------------------------------------------------------------

TD-022 — Budget-on-by-default + Persistent Daemon Kill-Switch

Статус: Done

Проблема
Persistent budget windows (core/budget_ledger.py) и hard ModelBudgetExceeded
pre-flight уже есть, но как дефолтная граница автономного демона бюджет-защита
недостаточно строгая: limit ≤ 0 (или отсутствующий config/budget_limits.json)
трактуется как «unlimited». Значит, unattended daemon (agent_tick.run_tick)
после исчерпания дневного бюджета мог продолжать тратить прогон за прогоном,
и не было персистентного kill-switch, переживающего рестарт процесса.

Область (только budget safety)
- без self-apply, без изменений file_write/shell_exec-политик;
- без изменений routing / provider catalog / registry schema;
- без git-автоматизации;
- лимиты НЕ поднимаются; существующий config/budget_limits.json уважается;
- не ломать интерактивные read-only status-команды.

Что должно быть
1. Enforcement ON by default для autonomous/daemon пути.
2. Missing/all-zero budget config в daemon-режиме НЕ значит «unlimited».
3. Персистентный kill-switch при исчерпании дневного (day) бюджета.
4. agent_tick / daemon проверяет kill-switch ДО любой LLM-heavy работы.
5. При активном kill-switch: пропустить работу, heartbeat/report
   reason=budget_kill_switch, никаких planner/synthesizer вызовов.
6. Kill-switch переживает рестарт процесса (latched state file).
7. Operator-readable статус: active/inactive, reason, counter, window,
   used/limit, timestamp.

Готово
- core/budget_kill_switch.py (новый):
  * CONSERVATIVE_DAY_LIMITS (llm_calls=100, model_tokens=300000,
    model_cost_units=500) — safety-net дневные лимиты, применяемые к дорогим
    счётчикам когда положительный лимит не сконфигурирован. Любой
    положительный config/env лимит имеет приоритеж (limit_source="config").
  * evaluate_day_budget(snapshot) — чистая функция: срабатывает как только
    used ≥ effective_limit по любому guarded day-счётчику. all-zero config →
    conservative default, никогда не unlimited.
  * BudgetKillSwitch(path): load/status(read-only)/engage_if_needed(latch)/
    clear. Latched-состояние (data/budget_kill_switch.json, атомарная запись)
    остаётся active между процессами, пока оператор не сбросит.
  * KillSwitchState dataclass: active/reason/counter/window/used/limit/
    limit_source/timestamp + to_dict/from_dict.
- agent_tick.py:
  * BUDGET_LEDGER_PATH / BUDGET_CONFIG_PATH константы;
  * _check_budget_kill_switch(workspace) — строит ledger напрямую (без agent/
    LLM) и latch'ит switch;
  * run_tick: gate шагом «0» ДО scheduler tick / build_agent. При active →
    tick-log + heartbeat event=budget_kill_switch (reason/counter/window/
    used/limit/timestamp), return 0, задача остаётся pending.
- cli/commands_budget.py + main.py:
  * :budget-kill-switch [--json] [--clear] — read-only operator status; --clear
    сбрасывает latched switch (не трогает лимиты/routing/catalog).
  * зарегистрирован в dispatch/help/command-list; добавлен в TD-002 local-only
    списки (0 LLM, нет model_call_start, Planner/Synthesizer не вызываются).

Проверка
- tests/test_budget_kill_switch.py (новый, 14 тестов):
  * missing/all-zero config → conservative default, не unlimited;
  * configured limit имеет приоритет над default;
  * status payload содержит reason/counter/window/used/limit/timestamp;
  * kill-switch переживает fresh helper load; clear() сбрасывает;
  * status() read-only (не latch'ит когда inactive); битый state → inactive;
  * daemon пропускает LLM-работу при исчерпанном day-бюджете (build_agent НЕ
    вызывается, задача pending, heartbeat=budget_kill_switch, нет scheduler_tick);
  * control: доступный бюджет → switch inactive, daemon идёт нормальным путём.
- tests/test_cli.py: :budget-kill-switch в local-only параметрах.
- targeted: 11 (kill-switch) / 197 (kill-switch+cli+daemon+ledger) passed.
- full pytest: 3522 passed.

----------------------------------------------------------------------

TD-023 — Trusted Low-Risk Self-Apply Lane

Статус: Done

Проблема
Self-build цикл был propose-only: :repair мог применять правки, но только за
per-action human approval; git commit/push заблокированы. Не было замкнутой
безопасной петли propose → apply → test → rollback-or-local-commit без
одобрения на каждый шаг для узко определённой low-risk полосы.

ЭТО НЕ общий autonomous write access. Полоса намеренно узкая.

Разрешённая полоса
- только low-risk self-build патчи (source/test/docs внутри репозитория);
- только на выделенной временной ветке;
- обязательный rollback-план;
- targeted tests, затем full pytest ДО локального коммита;
- fail → автоматический rollback; pass → локальный git commit;
- НЕТ push, НЕТ merge в main, НЕТ provider/model/catalog записей, НЕТ
  изменений budget-лимитов, НЕТ secrets/env/key файлов, НЕТ расширения
  shell_exec.

Hard safety-гейты (до любой записи файла, первый сработавший выигрывает)
1. budget kill-switch active → status=budget_kill_switch;
2. hour budget near-exhaustion → status=budget_wait;
3. pending approvals → status=approval_wait;
4. патч не low-risk / denylisted → status=rejected;
5. рабочее дерево грязное → status=rejected.

Готово
- core/safe_vcs.py (новый): узкий git-front-end на один workspace. Каждый метод
  — ровно один hard-coded git argv: current_branch/head_hash/status_porcelain/
  is_clean/create_temp_branch/checkout/delete_branch/stage_all/commit/
  reset_hard/clean_untracked. НЕТ методов push/fetch/pull/remote вообще (полоса
  физически не может выйти в сеть). Валидация имени ветки, guard protected
  веток (main/master/HEAD), commit-identity через -c (не трогает global git
  config), injectable runner.
- core/self_apply_lane.py (новый):
  * FileChange / SelfApplyProposal (files + reason + evidence + test_paths).
  * classify_patch_risk() — чистая: allowlist core|cli|tools|tests/*.py, docs/,
    *.md; denylist (config/budget_limits.json, config/model_registry.json,
    config/model_catalog.json, .env, secrets/, requirements/lock, .github/,
    *.pem/*.key, *secret*/*credential*). Denylist проверяется первым; каждый
    файл должен быть allowlisted И не denylisted; отклоняются абсолютные пути
    и `..`.
  * run_self_apply_lane() — оркестрация: гейты 1-5 → clean-tree → temp branch
    → apply (full-content write внутри workspace) → targeted tests → full
    pytest → rollback(reset_hard+clean+checkout original+delete temp) или
    local commit(stage_all+commit, checkout обратно на исходную ветку). НИКОГДА
    не push/merge, HEAD возвращается на исходную ветку, изменение живёт только
    на temp-ветке.
  * SelfApplyReport: status/reason/branch/files_changed/tests_run/
    rollback_status/commit_hash/rejected_files/risks/next_human_action.
  * Тяжёлые зависимости (git, test runner) инъектируются → полностью
    юнит-тестируемо без реального pytest/provider/network.

Область НЕ тронута
- shell_exec остаётся read-only (git log/diff/status); полоса использует
  отдельный SafeVCS, НЕ расширяет shell_exec.
- полоса НЕ подключена к autonomous daemon-у (никакого авто-триггера) — это
  осознанная граница: не general autonomous write access, вызывается
  осознанно человеком/следующей задачей.

Проверка
- tests/test_safe_vcs.py (новый, 11): реальный temp git-repo — branch/commit/
  checkout, reset_hard+clean восстанавливают дерево, delete protected refused,
  invalid branch names refused, failed git → VcsError, нет push/fetch/pull/
  remote методов.
- tests/test_self_apply_lane.py (новый, 36): classifier allow/deny (13),
  гейты kill-switch/budget_wait/approval_wait/denylist/dirty отказывают ДО
  apply (RaisingRunner доказывает: тесты не запускаются), реальный repo —
  low-risk патч применяется+коммитится локально, targeted-fail → rollback,
  full-fail → rollback, main HEAD не меняется, budget_limits.json не тронут,
  новый файл вычищается при rollback, нет push-метода.
- targeted: 47 passed (safe_vcs + self_apply_lane).
- full pytest: 3569 passed.

----------------------------------------------------------------------

TD-024 — Wire validated approval/proposal to trusted self-apply lane

Статус: Done

Проблема
TD-023 дал core/safe_vcs.py и core/self_apply_lane.py, но полоса не была
подключена к human-approval flow. Не хватало узкого моста: одобренный человеком
валидированный low-risk proposal -> run_self_apply_lane. Это мост, НЕ новая
широкая команда и НЕ daemon auto-write.

Готово
- core/self_apply_bridge.py (новый): чистая логика моста без CLI/IO.
  * SELF_APPLY_OPERATION="self_apply_lane.run" — принимается только эта операция.
  * build_self_apply_payload(): формирует well-formed payload; требует полный
    content на каждый файл (diff-only отвергается) — единая форма для
    producers (repair/supervisor/manual) и runtime.
  * rehydrate_proposal(): payload -> SelfApplyProposal; InvalidProposalError при
    нехватке полей или diff-only.
  * run_approved_self_apply(): гейты (первый выигрывает, до любой записи):
    1) item не approved -> approval_required;
    2) чужая operation / битый payload -> needs_validated_proposal;
    3) не low-risk (та же classify_patch_risk) -> risk_rejected;
    4) иначе -> run_self_apply_lane РОВНО один раз, статус пробрасывается как
       есть (budget_kill_switch/budget_wait/approval_wait/rejected/rolled_back/
       committed_local/error).
  * mark_executed ТОЛЬКО для terminal статусов committed_local/rolled_back;
    transient (budget_kill_switch/budget_wait/approval_wait) оставляют item
    approved -> ретрай возможен.
  * approvals_pending НЕ считает текущий approved item (только другие pending).
  * lane инъектируется -> полностью тестируемо без git/pytest/provider/network.
- cli/commands_self_apply.py (новый): :self-apply-run <approval-inbox-id>.
  * принимает РОВНО один id; free-text / лишние args / патч-текст -> usage-refuse;
  * строит SafeVCS/RunTestsTool/BudgetKillSwitch/budget snapshot и зовёт мост;
  * secret-free структурный лог self_apply_run (без содержимого файлов/diff).
- main.py: импорт + ветка :self-apply-run + строки в :help. Отдельная узкая
  команда — :approval-run НЕ расширяли.

Область НЕ тронута
- НЕ подключено к daemon/scheduler/agent_tick (только осознанный человеком trigger).
- shell_exec остаётся read-only; мост ходит через SafeVCS (нет push/fetch/pull/
  remote/merge). Нет provider/model/catalog записей, нет изменений budget-лимитов,
  config/budget_limits.json не тронут. self_apply_lane/safe_vcs не менялись.

Проверка
- tests/test_self_apply_approval_bridge.py (новый): approval_required,
  needs_validated_proposal (missing/wrong-op/invalid/diff-only), risk_rejected
  (denylist, файл не записан), lane вызван ровно один раз, committed_local с
  commit_hash + mark_executed, rolled_back + mark_executed, transient статусы
  без mark_executed (retryable), approvals_pending исключает текущий item,
  rehydrate roundtrip, нет network-методов у SafeVCS, мост не импортирует
  shell_exec/subprocess.
- tests/test_cli.py: :self-apply-run зарегистрирован, отвергает free-text и
  пустой ввод.
- targeted: 91 passed (bridge + CLI dispatcher).
- full pytest: 3591 passed.

===============================================================================

TD-025 — Subagent-backed full self-apply proposal producer

Статус: Done

Проблема
После TD-023 (self-apply lane) и TD-024 (approval bridge) полоса могла ПРИМЕНИТЬ
одобренный full-content proposal, но НИКТО не порождал валидный
operation="self_apply_lane.run" payload автономно. self-build supervisor был
advisory-only и выдавал unified diff / NO_PATCH. Producer-разрыв: агент "нашёл
идею", но не готовил валидный payload для применения.

Готово
- core/self_build_producer.py (новый): узкий producer с явным role-pipeline
  Manager -> Researcher -> Builder -> Critic -> Reporter. Каждая роль —
  отдельная функция, возвращает структурный RoleOutput; ProducerReport делает
  видимым, кто что решил. Всё инъектируется (llm/vcs/inbox/budget/kill-switch/
  file_reader) -> тестируемо без provider/network/git.
  * produce_self_apply_proposal(...): 4 safety-гейта ДО любой LLM-работы
    (первый выигрывает): 1) kill-switch active -> budget_kill_switch;
    2) hour budget near-exhaustion -> budget_wait; 3) уже есть неисполненный
    self_apply_lane.run item -> approval_wait; 4) грязное git-дерево ->
    dirty_tree_wait.
  * Manager выбирает РОВНО одну цель из жёсткого hardcoded allowlist
    (DEFAULT_CANDIDATE_TARGETS); свободного скана репозитория нет. Нет цели ->
    no_patch.
  * CRITICAL_DENY (main.py, core/loop.py, autonomous_runtime.py, model_usage.py,
    safe_vcs.py, self_apply_lane.py, self_apply_bridge.py, config/*): denylist
    выигрывает до allowlist — критические органы недоступны.
  * Builder генерирует ПОЛНЫЙ content файла (не diff). Critic валидирует
    (classify_patch_risk low-risk, не diff-like, отличие от текущего, ast.parse
    для .py, наличие targeted tests, размер, confidence >= threshold) и может
    наложить veto -> critic_veto, item НЕ создаётся.
  * Reporter (только без veto): build_self_apply_payload(origin=
    subagent_self_build_producer) + inbox.add(operation=self_apply_lane.run,
    dedup_key) -> РОВНО один approval item. Payload round-trip через TD-024
    rehydrate_proposal.
- cli/commands_self_build.py (новый): :self-build-produce — узкий operator
  trigger. Без аргументов и без патч-текста (лишнее -> usage-refuse); ТОЛЬКО
  создаёт один approval item; secret-free лог self_build_produce.
- main.py: импорт + ветка :self-build-produce + строки в :help.

Область НЕ тронута
- НЕТ apply / run_self_apply_lane / commit / push / fetch / pull / merge.
- НЕ подключено к daemon / scheduler / agent_tick (это будущий TD-026).
- Никакого расширения shell_exec; SafeVCS без сетевых методов.
- Никаких изменений budget/model/catalog config; config/budget_limits.json не
  тронут.
- За один запуск максимум один approval item.

Проверка
- tests/test_self_build_producer.py (16): гейты без LLM-работы, порядок ролей,
  diff-only veto, denylist/critical veto, low-confidence veto, ровно один item,
  round-trip через bridge, git не трогается, нет сетевых методов,
  config/budget_limits.json не пишется.
- tests/test_cli.py (+2): :self-build-produce зарегистрирован; отклоняет
  аргументы/free-text.
- Full pytest: 3609 passed.

===============================================================================

TD-026 — Daemon/supervisor wiring для self-build proposal producer

Статус: Done

Проблема
produce_self_apply_proposal (TD-025) существовал, но вызывался только вручную
через :self-build-produce. Daemon (agent_tick.run_tick) не порождал self-build
proposal автономно по расписанию — последнее недостающее звено петли
producer -> inbox -> bridge -> lane не было замкнуто в daemon.

Готово
- agent_tick.py: продюсер подключён к тику ОДИН раз за tick, после блока
  задач/репейра, перед tally inbox. Аддитивно, изолированно.
  * Новый persistent cooldown state: data/self_build_producer_state.json
    (last_proposed_at). Дефолт окна 12 часов; override через
    AGENT_SELF_BUILD_COOLDOWN_HOURS. Чистые helpers _read/_write_producer_state,
    _cooldown_remaining_seconds, _self_build_cooldown_hours.
  * _maybe_produce_self_build(...): cooldown-гейт — ЕДИНСТВЕННАЯ новая логика
    этого слоя — проверяется ДО build_agent (cooldown_wait НЕ строит агента и
    НЕ зовёт продюсер). Затем строит SafeVCS + ledger.snapshot() +
    KillSwitchState и зовёт produce_self_apply_proposal РОВНО один раз.
    Гейты kill-switch/budget/approval/dirty-tree остаются в самом продюсере —
    tick их НЕ дублирует.
  * last_proposed_at обновляется ТОЛЬКО при status="proposed". no_patch /
    critic_veto / budget_wait / budget_kill_switch / approval_wait /
    dirty_tree_wait / error cooldown НЕ сжигают -> следующий tick пробует снова.
  * Любое исключение -> status="error", tick НЕ падает (return 0), heartbeat
    записан.
  * summary/heartbeat #2 и stderr несут self_build_status, approval_id и
    next_human_action, когда доступны; событие self_build_produce в tick-логе.
- dry_run: продюсер кладёт ТОЛЬКО human-gated approval item (никаких effects),
  поэтому работает и в dry-run — как _maybe_propose_repair.

Статусы (1:1 с ТЗ)
budget_kill_switch | budget_wait | approval_wait | dirty_tree_wait |
cooldown_wait (новый) | proposed | no_patch | critic_veto | error.

Область НЕ тронута
- НЕТ run_self_apply_lane / apply / test / commit / push / fetch / pull / merge /
  remotes.
- НЕ подключён approval auto-run; human approval НЕ обходится.
- Максимум один producer-вызов и один approval item за tick.
- Никаких изменений budget limits; config/budget_limits.json не тронут.
- Продюсер остаётся source of truth для kill-switch/budget/approval/dirty-tree;
  agent_tick добавляет ТОЛЬКО cooldown.
- core/self_build_producer.py без изменений.

Проверка
- tests/test_agent_tick_self_build.py (20): cooldown-helpers, cooldown_wait не
  строит агента и не зовёт продюсер, proposed пишет last_proposed_at и
  прокидывает approval_id/next_human_action, non-proposed статусы cooldown не
  сжигают, второй tick после proposed -> cooldown_wait, exception -> error без
  падения, отсутствие run_self_apply_lane и git-вызовов в теле, реальный
  продюсер с fake LLM -> no_patch без item и без git, config/budget_limits.json
  не пишется.
- Full pytest: 3629 passed.

==============================================================================
TD-027 — Видимость self-build продюсера в operator --status (read-only)

Статус: Done

Проблема
После TD-026 self-build продюсер работает автономно в daemon, но у оператора
не было read-only окна в его состояние. agent_tick.py --status (_print_status)
показывал liveness демона, dry-run режим и pending inbox — но НИЧЕГО про
продюсер: был ли proposed, когда последний proposal, сколько осталось cooldown,
что записал последний tick, сколько self_apply_lane.run ждут одобрения. Всё это
приходилось читать руками из data/daemon_heartbeat.json и
data/self_build_producer_state.json.

Готово
- Расширен ТОЛЬКО _print_status (без нового командного слова, как просил ТЗ):
  read-only блок "Self-build:", источники — heartbeat + producer state file +
  живой пересчёт cooldown + счётчики inbox. Никакого запуска продюсера/линии.
- Показывает:
  * last self-build status — из heartbeat, ЯВНО помечен как "historical, from
    last tick — not a live gate". Только cooldown подаётся как live.
  * last_proposed_at + здоровье state-файла (ok / missing / no-timestamp /
    bad-timestamp).
  * cooldown remaining — единственное live-значение, пересчитывается через
    _cooldown_remaining_seconds + _self_build_cooldown_hours.
  * last approval_id и next_human_action — из heartbeat.
  * self_apply_lane.run: N pending, M approved ready for :self-apply-run —
    оба дёшево через inbox.pending()/inbox.list(status="approved").
- Полностью защищено: пустой/битый heartbeat, пустой/битый producer state,
  кривой timestamp, невалидный AGENT_SELF_BUILD_COOLDOWN_HOURS — деградирует в
  понятную строку, НИКОГДА не падает (весь gather обёрнут в try/except ->
  "status unavailable").
- Плейсхолдер отсутствующих значений — ASCII "none" (stderr демона попадает в
  Task Scheduler-логи; em-dash давал mojibake).

Область НЕ тронута
- НЕТ запуска продюсера.
- НЕТ запуска self_apply_lane.
- НЕТ apply / commit / push / fetch / pull / merge.
- НЕТ изменения поведения демона (запись self_build уже была в TD-026 — здесь
  только ЧТЕНИЕ/показ).
- Никаких изменений budget limits; config/budget_limits.json не тронут.
- Новой команды нет — расширен существующий --status.
- run_tick, гейты и core/self_build_producer.py без изменений.

Проверка
- tests/test_agent_tick_status.py (+10, всего 15): never-ran, proposed-поля
  помечены historical, live-cooldown при свежем proposal, ready при старом,
  block-reason показан как historical (не live), счёт pending+approved
  self_apply_lane.run, битый state-файл -> missing без падения, кривой timestamp
  -> bad-timestamp + cooldown ready, невалидный cooldown-env -> fallback 12h,
  чистый formatter не падает на None-входах.
- agent_tick targeted: 72 passed.
- Full pytest: 3639 passed.

==============================================================================
TD-028 — Subagent Registry + Performance Ledger (read/write, advisory-only)

Статус: Done

Проблема
Продюсер (TD-025) гоняет ролевой пайплайн Manager/Researcher/Builder/Critic/
Reporter, демон умеет его запускать (TD-026), --status показывает self-build
(TD-027). Но центральный агент не вёл персистентный реестр подагентов и их
эффективности: нельзя сказать, какая роль полезна, дорога, часто фейлит или
ветует. Без памяти о работе "сотрудников" управлять корпорацией агентов нельзя.

Готово
- core/subagent_registry.py (новый): RoleRecord (все метрики из ТЗ) +
  SubagentRegistry (load/save одного JSON data/subagent_registry.json, атомарно
  tmp+replace; битый/отсутствующий файл -> дефолтные 5 active-ролей).
- Метрики роли: status(active/paused/retired), invocations, successes, failures,
  vetoes, outputs_vetoed, proposals_created, proposals_approved, committed_local,
  rolled_back, cost_units, cost_source, last_used_at, trust_score,
  usefulness_score, recommendation(keep/watch/pause/retire).
- Decision mapping (клариф. 4/5/6): manager selected=success, no_target=neutral;
  researcher gathered=success; builder built=success, failed=failure; critic
  pass=success, veto=useful (поднимает trust/usefulness Critic, НЕ failure);
  reporter published=success. При status=proposed -> reporter.proposals_created++.
  При critic_veto -> builder.outputs_vetoed++ (повторные заветованные выходы
  Builder двигают ЕГО recommendation, но не Critic).
- Скоринг чистый/детерминированный: trust = positives/(positives+negatives), где
  positives=successes+vetoes, negatives=failures+outputs_vetoed, нейтральные
  (manager no_target) игнорируются; usefulness = value-события на invocation;
  recommendation по порогам trust с минимумом судимых событий.
- recommendation — ТОЛЬКО совет: НИКОГДА не меняет status. Нет auto-pause,
  auto-retire, изменений model routing.
- Стоимость: cost_units=0.0 + cost_source="unknown" по умолчанию (клариф. 3);
  общий счёт LLM НЕ делится по ролям.
- record_lane_outcome(...) определён для будущей привязки TD-023/TD-024, но в
  этом PR автоматически НЕ вызывается (клариф. 8).
- core/self_build_producer.py: аддитивный опциональный параметр registry=None у
  produce_self_apply_proposal; запись отчёта на каждом return через guarded
  _record. При registry=None поведение байт-в-байт прежнее; ошибка записи
  (клариф. 9) не ломает продюсер.
- agent_tick.py: _maybe_produce_self_build грузит и передаёт registry (guarded,
  сбой не ломает tick); в блок Self-build из TD-027 добавлена компактная строка
  "subagents: N roles - keepxK ..." (ASCII, без mojibake). Новой CLI-команды нет.
- status_report() — подробный read-only снапшот, оставлен в модуле для будущего.

Область НЕ тронута
- Записываем и рекомендуем — НЕ нанимаем/не увольняем; никаких авто-изменений
  status/routing.
- Нет запуска продюсера/линии из статуса; нет apply/commit/push/fetch/pull/
  merge/network.
- Нет изменений budget limits; config/budget_limits.json не тронут.

Проверка
- tests/test_subagent_registry.py (19): дефолты; счётчики по всем ролям;
  gate-wait без ролей = no-op; успех поднимает trust/usefulness; failures Builder
  -> pause/retire (status остаётся active); critic veto = useful, trust=1.0,
  keep; заветованные выходы Builder двигают его recommendation; retire только
  рекомендация; manager no_target нейтрален; round-trip персистентности; битый
  JSON -> defaults; from_dict терпит мусор; producer-хук пишет исходы; без
  registry — файл не создаётся; сбой записи не ломает продюсер; status_report
  read-only; summary_line ASCII; record_lane_outcome не авто-вызывается;
  cost_units=0/unknown.
- Targeted (registry+producer+self_build+status): 70 passed.
- Full pytest: 3658 passed.

==============================================================================
TD-029 — Agent Anatomy / Cognitive Architecture Map — Done

Статус
- Done. Read-only карта анатомии агента + read-only линтер синхронизации.
  Нового автономного поведения нет, новой CLI-команды нет.

Проблема
- Есть целевая архитектура (архитектура автономного Агента.txt) и доктрина
  (AGENT_DOCTRINE.md), но не было интегрированной карты: где мозг/память/нервы/
  иммунитет, что реально подключено в живую петлю, а что доступно только через
  оператора, что дублируется.

Готово
- docs/AGENT_ANATOMY.md — главный артефакт. Разделено "target architecture" и
  "actual wired-live architecture". 12 систем (мозг, память, мышление, диалог
  ролей, восприятие, голос/отчёты, руки/инструменты, иммунитет, метаболизм/
  бюджет, сон/консолидация, самопочинка, координация подагентов). Каждый из 99
  модулей core/ размечен: wired-live / manual-only / proposal-only / duplicated,
  с флагом ~ при слабой уверенности. Snapshot date вверху, mermaid-диаграмма
  живой петли, protection-path matrix (loop/runtime/daemon/manual), gap table,
  список кандидатов TD-030+ (advisory only).
- Ключевые находки: единого "мозга" нет (loop vs autonomous_runtime — два
  центра); четыре несвязанных механизма ролей/подагентов (team_plan/executor,
  subagent_runner, subagent_memory_scope, self_build_producer roles+registry);
  защиты распределены по разным путям (injection/redaction/clarification —
  только loop; circuit_breaker/budget_governor — runtime; budget_kill_switch —
  daemon/lane). Истинных orphan НЕ найдено: каждый core-модуль имеет
  production-импортёра (многие — только через пакет cli/, т.е. manual-only).
- scripts/agent_anatomy_check.py — строго read-only: только листинг core/ и
  чтение docs/AGENT_ANATOMY.md; не импортирует core, не исполняет агент, без
  LLM/сети/git, ничего не пишет. Обнаруживает расхождение карты и кода.
- README.md — одна additive-строка в индексе документов.

Область НЕ тронута
- Нет запуска producer/self_apply_lane, нет self-build proposal, нет model
  routing, нет budget/config изменений; config/budget_limits.json не тронут.
- Нет apply/commit/push/fetch/pull/merge/network; кода агента не менялось.

Проверка
- scripts/agent_anatomy_check.py: 99/99 in sync, exit 0.
- tests/test_agent_anatomy_check.py (6): файл существует; _core_modules без
  __init__; парсинг core/-токенов; doc и core синхронны (main()==0); drift
  детектируется через разность множеств; исходник не импортирует core/git/сеть.
- Full pytest: см. прогон в PR (регресс-гейт).
==============================================================================
TD-031 — Lane Outcomes to Subagent Ledger
Статус: Done

Проблема
- TD-028 писал в ledger только role-level producer-исходы (stage-сигналы).
  Ledger не знал, был ли произведённый self_apply_lane.run item одобрен,
  исполнен, committed_local или rolled_back — поэтому usefulness_score почти
  не отражал подтверждённую ценность.

Готово
- core/subagent_registry.py — добавлен LANE_OUTCOME_ROLE (approved->reporter,
  committed_local->builder, rolled_back->builder) и метод apply_lane_outcome(
  item_id, outcome): резолвит каноничную роль, дедуп по persistent-ключу
  f"{item_id}:{role_id}:{outcome}", делегирует в record_lane_outcome. Ledger
  теперь хранит applied_outcomes (tolerant-load старого JSON без ключа).
  Обновляет только счётчики/скоры/recommendation; status роли не мутируется.
- core/self_apply_bridge.py — run_approved_self_apply получил optional
  registry=None; на терминальном исходе (committed_local/rolled_back) вызывает
  guarded _record_lane_outcome со строгим фильтром operation==self_apply_lane.run
  и payload.origin==PRODUCER_ORIGIN (lazy-import, без дублирования строки).
- cli/commands_self_apply.py — :self-apply-run грузит registry (guarded) и
  прокидывает в bridge.
- cli/commands_approval.py — approve-хук _record_producer_approval пишет только
  outcome "approved" для producer-origin self_apply_lane.run item.
- Каноничная атрибуция: approved->reporter, committed_local/rolled_back->builder;
  остальные 4 роли не кредитуются в этом PR.

Область НЕ тронута
- Только запись исходов: нет auto-approve, auto self-apply, auto-retire/pause,
  изменений model routing/budget/config; config/budget_limits.json не тронут.
- Нет новой CLI-команды; нет запуска producer/lane из status; нет
  push/fetch/pull/merge/network. Ошибки записи в ledger никогда не ломают
  approval/self-apply flow (все хуки в try/except, best-effort). registry=None
  -> поведение bridge не меняется. record_lane_outcome/apply_lane_outcome не
  трогают role status.

Проверка
- tests/test_subagent_registry_lane.py (18): каноничный маппинг ролей; дедуп
  (no double-count + persist через reload); dedup-ключ включает role+outcome;
  unknown-outcome игнорируется; status роли не мутируется; tolerant-load старого
  JSON; bridge пишет committed/rolled_back для producer-item; игнор non-producer
  origin; registry=None -> без изменений/без файла; non-terminal статус ничего
  не пишет; сбой записи не ломает flow; hook идемпотентен; approve-хук пишет
  approved один раз, игнорит чужой origin/operation.
- Targeted: test_subagent_registry, test_self_apply_approval_bridge,
  test_self_apply_lane, test_approval* — зелёные.
- Full pytest: 3682 passed (+18).
Правка (scoring, TD-031): technical success != confirmed value
- Живой self-build эксперимент: proposal для core/redaction.py дошёл до
  committed_local и прошёл тесты, но по сути менял только регистр в комментарии
  (WIDEST -> widest) под вывеской "robustness improvement"; человек отклонил как
  low value.
- core/subagent_registry._usefulness_score: committed_local и approved теперь
  считаются techincal_success с малым весом _TECHNICAL_SUCCESS_WEIGHT=0.25, а не
  полноценным value-очком. Producer-stage сигналы (proposals_created, vetoes)
  остаются с полным весом; rolled_back по-прежнему вычитается полностью.
- committed_local по-прежнему пишется как технический lane-исход (счётчик
  committed_local += 1), но сам по себе не делает Builder high-value.
- confirmed_value (human-accepted / merged / value-reviewed) — будущий сигнал,
  в этом PR НЕ вводится (нет merge-tracking, нет новой CLI-команды, нет
  auto-merge/auto-retire/model routing/budget/config изменений).
- Тесты: committed_local инкрементит счётчик, но usefulness Builder остаётся
  низким без confirmed value; producer-stage veto перевешивает technical
  committed_local. Full pytest: 3684 passed.