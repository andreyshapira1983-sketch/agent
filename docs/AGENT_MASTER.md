# Единый документ ориентации агента

Этот документ — главная точка входа. В нём собрано: архитектура, структура проекта, из чего состоит система и где искать детали. Остальные файлы в `docs/` не удаляются; они перечислены в конце. Агент должен ориентироваться по этому документу и при необходимости открывать указанные файлы.

---

## 1. Архитектура: Telegram + Local Agent

- **Модель:** Telegram — только интерфейс (приём сообщений, показ ответов). Вся работа выполняется на компьютере, где запущен процесс бота (`python src/main.py`).
- **Цепочка:** Telegram Client → Telegram API → Local Agent (на ПК) → Tools (PowerShell, Python, файлы, pip, fetch…) → OS/программы.
- **Фон:** `run_python` и `run_powershell` выполняются в subprocess без видимого окна; вывод возвращается агенту текстом. Чтобы открыть программу/окно — например, `Start-Process` в PowerShell.
- Подробнее: `docs/CHANGELOG_AGENT_FEATURES.md` (раздел «Архитектура»).

---

## 2. Структура проекта

### Точки входа

- **Запуск бота:** `src/main.py` — загрузка .env, Telegram long polling, обработчики сообщений и команд.
- **Один автономный цикл:** `src/tools/orchestrator.py` — `Orchestrator().run_cycle()`.
- **Конфиг:** `config/agent.json`, `config/allowed_sites.json`; примеры — `.env.example`, `config/agent.json.template`.

### Основные каталоги и модули

| Каталог / файл | Назначение |
|----------------|------------|
| **src/core/** | Промпт (`prompt.py`), вызов LLM и инструментов (`intelligence.py`), интерпретация намерений (`intent.py`). |
| **src/tools/** | Реестр инструментов (`registry.py`), оркестратор цикла (`orchestrator.py`), реализация инструментов в `tools/impl/` (file_tools, evolution_tools, autonomy_tools, pip_tool, run_shell_tools, patch_request_tool, agent_tools, run_pytest_tool, self_model_tools, time_tool, tts_tool). |
| **src/planning/** | План по цели (`planner.py`), внутренние цели и выбор действия LLM (`planner_loop.py`), цели (`goals.py`). |
| **src/tasks/** | Очередь задач (`queue.py`), состояние задачи (`task_state.py`), генерация задач по эмоциям/метрикам (`task_creator.py`), менеджер (`manager.py`). |
| **src/evolution/** | Sandbox (`sandbox.py`), безопасное применение патчей (`safety.py`), авто-патч и авто-тесты, конфиг, саморемонт, версионирование. |
| **src/governance/** | Политики и квоты (`policy_engine.py`), patch_guard (cooldown, лимит патчей на файл), task_guard (очередь, TTL, evolution budget), evolution_lock (один accept в момент). |
| **src/communication/** | Telegram-клиент и обработчики (`telegram_client.py`), команды и статусы (`telegram_commands.py`), алерты и проактивная отправка (`telegram_alerts.py`), проактивный планировщик (`proactive_planner.py`), автономный режим (`autonomous_mode.py`), видео/фото (vision), голос (Whisper, TTS). |
| **src/memory/** | Краткосрочная память и контекст для LLM (`context_manager.py`, `short_term`), долгосрочная и векторная память. |
| **src/learning/** | Обратная связь, вывод правил (`feedback.py`, `rules_safety.py`, `rule_derivation`), самообучение. |
| **src/monitoring/** | Метрики вызовов и времени инструментов (`metrics.py`), системные метрики, алерты, верификатор ответов. |
| **src/personality/** | Эмоциональная матрица, триггеры, приоритеты задач по эмоциям, «фантазийные» фразы. |
| **src/agency/** | Супервизор, семейное дерево агентов (`family_store`), создание дочерних агентов. |
| **src/hitl/** | Audit log, одобрения (approvals). |
| **config/** | agent.json, allowed_sites.json, шаблоны. |
| **data/** | Память, лог прочитанного, состояние guard/lock, полученные файлы и т.д. |

---

## 3. Автономный цикл и планировщик

- **Цикл:** observe → reason → plan → act → reflect → improve. Реализован в `Orchestrator.run_cycle()`.
- **Observe:** метрики, self_assessment, sequence_trace, inbox (для дочерних агентов).
- **Reason:** эвристики по метрикам и success_rate; при пустой очереди — вызов **Planner Loop** (`planning/planner_loop.py`): LLM выбирает действие (read_books, analyze_system, share_with_user, run_maintenance, continue_queue), затем в очередь ставятся соответствующие задачи или отправляется проактивное сообщение.
- **Plan:** `planning.planner.make_plan(goal)` строит шаги; при пустой очереди также вызывается `task_creator.try_generate_and_enqueue(state)` (задачи по эмоциям и «хотелкам»).
- **Act:** из очереди извлекаются задачи, выполняется `run_tool(tool, arguments)` с учётом policy и квот.
- **Reflect / Improve:** обновление self_assessment, вывод правил из feedback.
- Подробнее: `docs/AUTONOMY.md`.

---

## 4. Эволюция и охраны

- **Патчи:** применение только через sandbox: предложение → валидация тестами в копии проекта → accept в основной проект. Инструменты: propose_patch, validate_patch, accept_patch; для правок из диалога — request_patch.
- **Охраны:** запрет удаления файлов и ограничение объёма правок; PROTECTED_PATHS; patch_guard (cooldown, MAX_PATCHES_PER_FILE); task_guard (MAX_TASK_QUEUE, MAX_TASKS_PER_CYCLE, TASK_TTL, MAX_EVOLUTION_PATCHES_PER_CYCLE); evolution_lock (один accept в момент).
- **Файлы:** `src/evolution/sandbox.py`, `safety.py`; `src/governance/patch_guard.py`, `task_guard.py`, `evolution_lock.py`; guardrails в `file_tools.py`, `patch_request_tool.py`.
- Подробнее: `docs/EVOLUTION.md`, `docs/AGENT_ARCHITECTURE.md`.

---

## 5. Семья агентов и проактивность

- **Семья:** дерево агентов (родитель → дети); создание через `create_agent_family`; данные в `agency/family_store`, inbox для сообщений между агентами.
- **Проактивность:** планировщик (`communication/proactive_planner.py`) решает, когда отправить сообщение пользователю (по интервалу и лимиту в день); сообщение формируется из лога прочитанного или короткого запроса к LLM. Вызов после каждого `run_cycle()` в `autonomous_mode.py`. Отправка через `telegram_alerts.send_alert`.
- Подробнее: `docs/FAMILY_AGENTS.md`, `docs/CHANGELOG_AGENT_FEATURES.md` (раздел «Проактивный автономный агент»).

---

## 6. Инструменты (категории)

- **Файлы и видимость:** read_file, write_file, propose_file_edit, describe_workspace, list_dir — `tools/impl/file_tools.py`. Ограничения и test gate — там же и в evolution.
- **Эволюция:** propose_patch, validate_patch, accept_patch, request_patch, get_metrics, list_pending_patches, run_self_repair и др. — `tools/impl/evolution_tools.py`.
- **Автономия и сеть:** fetch_url, parse_json, aggregate_simple, manage_queue, get_reading_log, log_reading, search_openlibrary, get_gutenberg_book_list, get_system_metrics, generate_question — `tools/impl/autonomy_tools.py`.
- **Выполнение на ПК:** run_python, run_powershell — `tools/impl/run_shell_tools.py`; pip_install — `tools/impl/pip_tool.py`.
- **Остальное:** get_current_time, run_pytest, agent_tools (ruff, pytest), self_model_tools, patch_request_tool, TTS и т.д. — в соответствующих модулях в `tools/impl/`. Регистрация всех инструментов — `src/tools/__init__.py`.

---

## 7. Telegram

- **Команды:** /status, /log, /tasks (или /queue), /mood (или /emotions), /guard, /autonomous, /stop. Обработчики передаются в `main.py`, ответы формируются в `communication/telegram_commands.py`.
- **Сообщения:** текст, голос (Whisper → текст), фото и видео (Vision → описание кадра/картинки). Обработка в `telegram_client.py`; описание видео/фото — `video_vision.py`.
- **Интерпретация намерений:** фраза пользователя → `core/intent.py` (interpret_intent) → при необходимости подставляется [Intent: read_book] и т.д., либо сразу выполняется run_cycle (run_cycle).
- **Проактивная отправка:** через `telegram_alerts.send_alert`; голос — `send_proactive_voice` (при необходимости).

---

## 8. Тестирование и использование

- **Тесты:** `pytest tests/ src/tests/`; скрипты в `scripts/` (run_smoke_tests.ps1, run_full_tests.ps1, run_nightly_tests.ps1). Артефакты — в `test-results/`.
- **Правило:** для нового или изменённого модуля добавлять тесты; модуль считается готовым после успешного прогона.
- **Задачи агенту:** формулировать чётко в чат; новый инструмент — реализовать в `src/tools/impl/`, зарегистрировать в `src/tools/__init__.py`, добавить тесты.
- Подробнее: `docs/TESTING.md`, `docs/USAGE.md`.

---

## 9. Обратная связь и дорожная карта

- Обратная связь: каналы и категории описаны в `docs/FEEDBACK.md`.
- Приоритеты и направления — в `docs/ROADMAP.md`; полная версия — `docs/archive/ROADMAP.full.md`.

---

## 10. Где что искать (все документы)

Полный индекс документов с кратким содержанием — в **docs/AGENT_DOCS_INDEX.md**. Ниже — сводная таблица.

| Документ | Содержание |
|----------|------------|
| **AGENT_MASTER.md** (этот файл) | Единая ориентация: архитектура, структура, цикл, эволюция, инструменты, Telegram, где что искать. |
| **AGENT_DOCS_INDEX.md** | Индекс всех документов: путь и одно предложение о содержании. |
| **CHANGELOG_AGENT_FEATURES.md** | Что сделано для агента, будет ли работать; архитектура Telegram+Local; pip, run_python/PowerShell, фото/видео, библиотеки, intent, проактивность, Planner Loop. |
| **AGENT_ARCHITECTURE.md** | Кто принимает решения (агент первый, LLM — генератор); видимость workspace; request_patch и защитный слой. |
| **README.md** | Запуск, требования, .env, команды Telegram, память, автономность, эмоции, алерты. |
| **AUTONOMY.md** | Цикл observe→reason→plan→act→reflect→improve; оркестратор, policy, очередь, task_creator, autonomy_tools, конфиг лимитов. |
| **EVOLUTION.md** | Guardrails, sandbox, patch_guard, task_guard, evolution_lock, применение патчей и откат. |
| **FAMILY_AGENTS.md** | Семейка агентов, фантазийные мысли, генеалогия, создание дочерних агентов. |
| **USAGE.md** | Практические сценарии: как ставить задачи, как добавлять инструмент. |
| **TESTING.md** | Линтер, mypy, bandit, pytest; скрипты PowerShell; артефакты и nightly. |
| **FEEDBACK.md** | Каналы обратной связи, категории, шаблон. |
| **ROADMAP.md** | Краткая дорожная карта; полная — в archive. |
| **ARCHITECTURE_PLAN.md** | Краткий план архитектуры; полная версия — в archive. |
| **GENETICS.md** | Краткая генетическая модель; полная — в archive. |
| **INDEX.md** | Карта документации: рабочий минимум и архив. |
| **AGENT_VISIBILITY_TEST.md** | Тесты видимости workspace для агента. |
| **archive/*.full.md** | Полные версии ARCHITECTURE_PLAN, GENETICS, ROADMAP. |

**Итог:** Агент ориентируется по **AGENT_MASTER.md** (этот файл) и при необходимости открывает конкретный документ по **AGENT_DOCS_INDEX.md** или по таблице выше. Исходные файлы не удаляются.
