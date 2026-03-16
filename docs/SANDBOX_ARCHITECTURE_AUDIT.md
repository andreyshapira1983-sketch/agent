# Обход кодовой базы: песочница, патчи, архитектура

## Что есть

### 1. Два потока применения изменений

| Поток | Инструменты | Где хранится | Валидация перед применением |
|-------|-------------|--------------|-----------------------------|
| **Evolution** | `propose_patch` → `validate_patch` → `accept_patch` | `config/candidate_patches/` + `_manifest.json` (поле `validated`) | Да: `accept_patch_to_stable` проверяет `entry["validated"]`, иначе возвращает "Patch must be validated...". |
| **File edit** | `propose_file_edit` → (подтверждение пользователя) → `write_file` | `config/pending_patches/` | При перезаписи: `write_file` сам запускает sandbox (pytest) при `WRITE_FILE_TEST_GATE=1` и пишет только при успехе. Отдельной «метки» validated в манифесте нет. |

**Вывод:** В evolution-потоке правило «применять только помеченное» уже выполняется. В file-потоке «метки» нет, но запись в файл идёт только после успешного pytest в sandbox при вызове `write_file`.

### 2. Песочница

- **`src/evolution/sandbox.py`:** `create_sandbox`, `apply_in_sandbox`, `run_pytest_in_sandbox`, `run_in_sandbox`, `cleanup_sandbox`. Копия проекта во временную папку, pytest с `PYTHONPATH=sandbox_root`.
- **`src/evolution/safety.py`:** `submit_candidate_patch` (пишет в `candidate_patches`, `validated: false`), `validate_candidate_with_tests` (ставит `validated: true`), `accept_patch_to_stable` (проверяет `validated`, при `EVOLUTION_ACCEPT_SANDBOX=1` повторно гоняет sandbox).
- **`src/tools/impl/file_tools.py`:** при перезаписи и `WRITE_FILE_TEST_GATE` вызывается `run_in_sandbox`; в основной проект пишется только при успехе тестов.

Дублирования логики sandbox нет: один модуль `evolution/sandbox.py`, его используют safety и file_tools.

### 3. Политики и защищённые пути

- **`src/governance/policy_engine.py`:** `DEFAULT_FORBIDDEN_PREFIXES` = `.cursor/`, `config/agent.json`, `src/main.py`, `src/hitl/`, `src/governance/`. Методы `is_path_allowed`, `check_apply_patch` — но **оркестратор при вызове `write_file`/`propose_file_edit` не передаёт path в policy**, только `check_run_tool(tool_name, arguments)` (квота и restricted_tools). То есть запрещённые префиксы политики **не проверяются** при записи файла.
- **`src/tools/impl/file_tools.py`:** `PROTECTED_PATHS` = `src/main.py`, `src/core/`, `file_tools.py`, `patch_request_tool.py`, `src/governance/`, `src/hitl/`. Проверка `_is_protected(path)` вызывается внутри `_write_file` и `_propose_file_edit`.

**Проблема:** Два разных списка. В `policy_engine` есть `.cursor/` и `config/agent.json`, в `file_tools` их нет — теоретически агент мог бы записать в `.cursor/` через `write_file`, т.к. проверка политики по path не выполняется. Нужно единообразно применять проверку пути (например, вызывать `policy.check_apply_patch(path)` в оркестраторе или в самом инструменте).

### 4. Patch guard и task guard

- **`patch_guard.can_patch`:** вызывается из `safety.submit_candidate_patch` и из `file_tools._write_file` / `_propose_file_edit`. Cooldown и лимит патчей на файл/директорию работают.
- **`task_guard.can_accept_evolution_patch`:** вызывается из `accept_patch_to_stable` — лимит accept_patch за цикл.

### 5. Где может «лечь» старый код на новый

- Эволюция (candidate_patches) и file edit (pending_patches) не смешиваются: разные директории и контракты. Конфликтов по данным нет.
- Риск: если в будущем появятся два способа «применить патч» (например, ещё один entry point в stable), нужно везде использовать один и тот же guard (validated + architecture check).

## Рекомендации (внедряем далее)

1. **Правило в коде:** Явно требовать в `accept_patch_to_stable`: применять только артефакты из песочницы с меткой «прошло тесты» (уже есть). Для `write_file` оставить текущее поведение (sandbox при перезаписи) и опционально ввести режим «только из validated» через команду/флаг.
2. **Единая проверка пути:** Перед `write_file`/`propose_file_edit` в оркестраторе вызывать `policy.check_apply_patch(path)`, чтобы применялись `forbidden_prefixes` из политики (в т.ч. `.cursor/`, `config/agent.json`).
3. **Архитектурный чек:** Перед применением в `accept_patch_to_stable` вызывать проверку «критичные модули / запрещённые изменения» (список или скрипт).
4. **Команды Telegram:** `/safe_expand`, `/apply_sandbox_only` — выставляют режим (state/env), который ограничивает применение только песочницей/validated.
