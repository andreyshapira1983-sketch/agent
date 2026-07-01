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

