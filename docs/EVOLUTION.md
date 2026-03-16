# EVOLUTION.md

Документация модуля эволюции: управление патчами, резервными копиями и откатом изменений.

---

## 0. Guardrails для self-modifying агента (file_tools)

В **`src/tools/impl/file_tools.py`** и **`patch_request_tool.py`**:

1. **Запрет удаления файлов** — пустой content при перезаписи отклоняется.
2. **Ограничение по объёму** — перезапись не может удалять больше **30%** объёма файла (`MAX_RELATIVE_REMOVAL`).
3. **Diff-based validation** — нельзя менять больше **30% строк** файла (сумма добавлений и удалений). Константа `MAX_LINES_CHANGED_RATIO = 0.30`. Так блокируется не только «удаление», но и массовая замена (LLM не может переписать 70 строк из 100).
4. **Backup перед записью** — создаётся `<filename>.bak`; откат вручную — восстановить из `.bak`.
5. **Test gate** — после `write_file` запускается `pytest tests/`; при падении файл восстанавливается из `.bak`. Выключить: `WRITE_FILE_TEST_GATE=0`.
6. **Syntax gate** — перед pytest для файлов `.py` выполняется `ast.parse(content)`; при синтаксической ошибке — откат из `.bak`. Быстрый фильтр до тяжёлого прогона тестов.
7. **Жёсткий лимит размера патча** — `MAX_PATCH_LINES = 200`: патч не может менять больше 200 строк (добавления + удаления). Отсекает огромные патчи от LLM.
8. **Self-protection (PROTECTED_PATHS)** — агент не может менять: `src/main.py`, `src/core/`, `src/tools/impl/file_tools.py`, `src/tools/impl/patch_request_tool.py`, `src/governance/`, `src/hitl/`. Иначе агент мог бы сломать сам механизм защиты.
9. **request_patch** — предпочтительно LLM возвращает **unified diff**, агент применяет и валидирует; при неудаче — полный контент.
10. **Sandbox** — при перезаписи файла (`write_file`) и при эволюции (`propose_patch` → `validate_patch` → `accept_patch`) патч применяется в копии проекта, тесты запускаются в sandbox; в основной проект пишется только при успехе. Самообучение (правила из feedback), самоэволюция (патчи через sandbox) и саморемонт (run_self_repair; исправление через propose→validate→accept) работают через один контур. Создание «семьи» (create_agent_family) можно выполнять после accept_patch — дочерние агенты запускаются с уже обновлённым кодом.
11. **Looping patch guard** (`src/governance/patch_guard.py`) — защита от бесконечного цикла правок одного файла: (a) **cooldown**: после изменения файла его нельзя патчить N циклов (`PATCH_COOLDOWN_CYCLES`, по умолчанию 10); (b) **budget**: максимум M патчей на файл (`MAX_PATCHES_PER_FILE`, по умолчанию 3), затем файл блокируется; (c) опционально **MAX_PATCHES_PER_DIRECTORY** (по умолчанию 0) — лимит суммарных патчей на директорию (обход через file_a_utils.py и т.п.). Сообщения guard явно предписывают «Switch file or task». Отключить: `PATCH_COOLDOWN_CYCLES=0`, `MAX_PATCHES_PER_FILE=0`, `MAX_PATCHES_PER_DIRECTORY=0`.
12. **Task explosion guard** (`src/governance/task_guard.py`) — защита от неограниченного роста очереди задач: (a) **MAX_TASK_QUEUE** (по умолчанию 20); (b) **MAX_TASKS_PER_CYCLE** (по умолчанию 2); (c) **TASK_TTL** (по умолчанию 50 циклов). Отключить: `MAX_TASK_QUEUE=0`, `MAX_TASKS_PER_CYCLE=0`, `TASK_TTL=0`.
13. **Evolution budget** (там же) — защита от *evolution drift*: бесконечной череды «улучшений» (improve logging, refactor), которые проходят тесты, но не дают реальной пользы. **MAX_EVOLUTION_PATCHES_PER_CYCLE** (по умолчанию 1) — не более N вызовов `accept_patch_to_stable` за один цикл. Счётчик сбрасывается в `advance_cycle()`. Отключить: `MAX_EVOLUTION_PATCHES_PER_CYCLE=0`.
14. **Regression guard** (идея на будущее): кроме «tests pass» можно добавить проверки *coverage not worse* и *performance not worse*. Пока не реализовано.
15. **Координация при нескольких агентах:** **evolution lock** — только один агент может применять патч (`accept_patch_to_stable`) в момент времени; остальные получают отказ с указанием держателя. Данные в `data/evolution_lock.json`, TTL `EVOLUTION_LOCK_TTL_SECONDS` (по умолчанию 120). **Telegram:** команда **/guard** выводит статус patch_guard, task_guard, evolution lock и семьи; в **/status** добавлена краткая строка про очередь и lock с подсказкой /guard.

---

## 1. Введение

Модуль **EvolutionManager** (`src/evolution/manager.py`) отвечает за управление патчами и резервными копиями в системе. Он обеспечивает:

- применение изменений к файлам через патчи;
- создание резервных копий перед изменением;
- откат к предыдущей версии при ошибке или по запросу.

В конструктор передаются три каталога: каталог патчей, каталог тестов (и целевых файлов), каталог бэкапов. Менеджер использует классы **AutoPatch**, **AutoTests** и **SelfReview** для применения патчей, запуска тестов и самооценки.

---

## 2. Применение патчей

### Метод `apply_patch(patch_name)`

Применяет патч по его имени (например, `example_patch.py`).

**Параметры:**

- `patch_name` — имя файла патча (берётся из `patch_directory`).

**Порядок работы:**

1. **Целевой файл** вычисляется через `get_target_file_path(patch_name)`: из имени патча берётся базовое имя без расширения, целевой путь — `test_directory / <base>.py`. Например, патч `foo.patch` → целевой файл `test_directory/foo.py`.

2. **Подготовка** вызывается `prepare_file_for_patch(original_file_path)`:
   - проверяется существование оригинального файла (иначе — `FileNotFoundError`);
   - создаётся резервная копия в `backup_directory` с именем вида `<filename>.<YYYYMMDD_HHMMSS>.bak`.

3. **Применение** выполняется через `self.auto_patch.apply_patch(patch_name)`:
   - **AutoPatch** читает файл патча из `patch_directory`, проверяет его существование;
   - передаёт содержимое во внутренний метод `_apply_patch_content` (текущая реализация — заглушка с логированием).

4. **При ошибке** в блоке `try/except`:
   - пишется сообщение в лог (`logging.error`) и в лог изменений (`log_change`);
   - вызывается `rollback_patch(original_file_path)` для восстановления файла из бэкапа.

---

## 3. Откат изменений

### Метод `rollback_patch(original_file_path)`

Восстанавливает файл из последней резервной копии.

**Параметры:**

- `original_file_path` — полный путь к файлу, который нужно восстановить.

**Порядок работы:**

1. **Поиск бэкапа** через `get_backup_path(original_file_path)`:
   - в `backup_directory` ищутся файлы с именем `<basename>.*.bak`;
   - возвращается путь к самому новому по времени изменения.

2. **Проверка существования** бэкапа: если файл не найден, выбрасывается `FileNotFoundError` с сообщением `"Backup file <path> does not exist."`.

3. **Восстановление**: `shutil.copy2(backup_path, original_file_path)` копирует содержимое бэкапа поверх целевого файла.

4. В лог изменений записывается сообщение об успешном откате.

---

## 4. Ошибки и исключения

| Ситуация | Исключение |
|----------|------------|
| Исходный файл не существует при вызове `prepare_file_for_patch` или `apply_patch` | `FileNotFoundError`: `"Original file <path> does not exist."` |
| Файл патча не найден (в **AutoPatch.apply_patch**) | `FileNotFoundError`: `"Patch file <path> does not exist."` |
| Каталог бэкапов отсутствует или в нём нет подходящего бэкапа (`get_backup_path`) | `FileNotFoundError`: `"Backup dir not found: ..."` или `"No backup for: ..."` |
| Файл бэкапа не найден при откате (`rollback_patch`) | `FileNotFoundError`: `"Backup file <path> does not exist."` |
| Ошибка при применении патча (в `auto_patch.apply_patch`) | Любое исключение перехватывается в `apply_patch`, логируется и выполняется откат через `rollback_patch`. |

---

## 5. Логирование

- **Модульный логгер** (`logging.getLogger(__name__)`): сообщения о создании бэкапов, ошибках бэкапа и отката.
- **Метод `log_change(message)`**: каждое важное событие (успешное применение патча, ошибка применения, откат) добавляется в список `evolution_log` и дублируется в логгер с префиксом `"evolution: "`.
- **AutoPatch** логирует факт применения патча через `logging.info("Applying patch: ...")`.

Просмотр лога изменений: `manager.evolution_log` после операций.

---

## 6. Безопасность самоизменения (sandbox)

Патчи к коду агента **не применяются напрямую** к работающему процессу (правило из `docs/ARCHITECTURE_PLAN.md`).

**Поток через sandbox:**

1. **propose_patch** (инструмент) → `src/evolution/safety.submit_candidate_patch()` — патч сохраняется в `config/candidate_patches/`.
2. **validate_patch** (инструмент) → `safety.validate_candidate_with_tests()` — запуск тестов (pytest); при успехе патч помечается как проверенный.
3. **accept_patch** (инструмент) → `safety.accept_patch_to_stable()` — применение к целевому файлу только если патч уже проверен.

Модуль **safety.py** — единственная точка применения кандидат-патчей к стабильному коду; все исключения и неудачные unlink логируются.

---

## 7. Примечания

- **Связанные компоненты**: **AutoPatch** (`src/evolution/auto_patch.py`) читает файлы патчей и вызывает `_apply_patch_content`; **AutoTests** и **SelfReview** подключаются в конструкторе для возможного расширения (тесты после патча, самооценка).
- **Расширение**: для новых типов патчей расширяйте **AutoPatch._apply_patch_content** или методы **EvolutionManager**, сохраняя использование `prepare_file_for_patch` и `rollback_patch`.
- **Качество кода**: в проекте используются ruff (линтер), mypy (типы), bandit (безопасность); логирование вместо глухих `except`, для намеренных вызовов subprocess/urlopen — комментарии `# nosec` с пояснением.
