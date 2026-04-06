#!/usr/bin/env python3
"""
Скрипт предзагрузки ошибок из .agent_memory/ в llm_errors.json.

Сканирует все исторические данные (failure_tracker, lessons, episodes,
failure_memory, capability_stats, commands.jsonl) и конвертирует найденные
ошибки в формат, понятный PersistentBrain.record_llm_error().

Запуск:
    python preload_llm_errors.py
"""

import json
import os
import time

MEMORY_DIR = os.path.join(os.path.dirname(__file__), ".agent_memory")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
OUTPUT_FILE = os.path.join(MEMORY_DIR, "llm_errors.json")

_seen: set[str] = set()


def _dedup_key(error_type: str, wrong: str) -> str:
    return f"{error_type}::{wrong[:100]}"


def _add(
    errors: list[dict],
    task: str,
    error_type: str,
    what_went_wrong: str,
    correct_approach: str = "",
    category: str = "",
    ts: float | None = None,
    repeat_count: int = 1,
):
    key = _dedup_key(error_type, what_went_wrong)
    if key in _seen:
        # Увеличиваем repeat_count у существующей записи
        for e in errors:
            if _dedup_key(e["error_type"], e["what_went_wrong"]) == key:
                e["repeat_count"] = e.get("repeat_count", 1) + repeat_count
                if correct_approach and not e.get("correct_approach"):
                    e["correct_approach"] = correct_approach[:500]
                return
        return
    _seen.add(key)

    now = ts or time.time()
    errors.append({
        "time": now,
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "task": str(task)[:300].strip(),
        "error_type": str(error_type)[:50],
        "what_went_wrong": str(what_went_wrong)[:500].strip(),
        "correct_approach": str(correct_approach)[:500] if correct_approach else "",
        "category": str(category)[:50] if category else "",
        "repeat_count": repeat_count,
        "last_seen": now,
        "resolved": False,
    })


def _load_json(name: str):
    path = os.path.join(MEMORY_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_from_failure_tracker(errors: list[dict]):
    """failure_tracker.json → raw_error записи."""
    data = _load_json("failure_tracker.json")
    if not data:
        return
    for entry in data.get("history", []):
        raw = entry.get("raw_error", "")
        cat = entry.get("category", "unknown")
        goal = entry.get("context_goal", "")[:300]
        ts = entry.get("timestamp")
        if not raw:
            continue

        # Классифицируем по содержимому
        if "exec(" in raw:
            _add(errors, goal, "sandbox_block",
                 "Использован exec() — заблокирован sandbox",
                 "Не использовать exec(). Писать код напрямую, без обёрток exec/eval.",
                 cat, ts)
        elif "NoneType" in raw and "__dict__" in raw:
            _add(errors, goal, "build_module_crash",
                 "Module smoke: import failed — AttributeError: 'NoneType' has no '__dict__'",
                 "Проверять spec/mod на None перед доступом к __dict__. Использовать module_builder harness с None-check.",
                 cat, ts)
        else:
            _add(errors, goal, "cycle_error", raw, "", cat, ts)


def extract_from_failure_memory(errors: list[dict]):
    """failure_memory.json → паттерны провальных целей."""
    data = _load_json("failure_memory.json")
    if not data or not isinstance(data, list):
        return
    for entry in data:
        goal = entry.get("goal_pattern", "")[:300]
        cat = entry.get("failure_category", "unknown")
        sig = entry.get("failure_signature", "")
        count = entry.get("occurrence_count", 1)
        ts = entry.get("last_seen")
        step = entry.get("failed_step", "")[:200]

        if cat == "verification_failed":
            _add(errors, goal, "verification_failed",
                 f"STEP_EVAL не прошёл: {sig}. Действие: {step}",
                 "Выполнять шаги буквально по инструкции. Не 'улучшать' задачу. Проверять результат инструментами.",
                 cat, ts, count)
        elif "Нельзя подменять" in sig or "Нужно строго" in sig:
            _add(errors, goal, "task_deviation",
                 f"Подмена задачи: агент 'усложнял' вместо точного выполнения. {sig[:200]}",
                 "Выполнять ровно то, что просят. Не добавлять лишний код/логику.",
                 cat, ts, count)
        elif sig:
            _add(errors, goal, "cycle_error",
                 f"Провальный паттерн: {sig[:200]}",
                 "", cat, ts, count)


def extract_from_lessons(errors: list[dict]):
    """lessons.json → ошибки сборки модулей и прочие уроки."""
    data = _load_json("lessons.json")
    if not data or not isinstance(data, list):
        return
    for entry in data:
        if entry.get("success"):
            continue
        lesson = entry.get("lesson", "")
        goal = entry.get("goal", "")[:300]
        ts = entry.get("time")

        # Sandbox блокировки
        if "Sandbox diff-review UNSAFE" in lesson:
            _add(errors, goal, "sandbox_block",
                 "Sandbox diff-review UNSAFE: нарушение Governance policy",
                 "Не генерировать код, нарушающий governance policy. Проверять через контракт до записи.",
                 "sandbox", ts)
        elif "Sandbox UNSAFE: Запрещённый вызов 'getattr()'" in lesson:
            _add(errors, goal, "sandbox_block",
                 "Sandbox UNSAFE: запрещённый вызов getattr() в sandbox",
                 "Не использовать getattr() в динамическом коде. Обращаться к атрибутам явно.",
                 "sandbox", ts)
        elif "Код заблокирован до выполнения: операции ОС" in lesson:
            _add(errors, goal, "sandbox_block",
                 "Sandbox: код заблокирован — операции ОС запрещены",
                 "Не использовать os.system/subprocess в sandbox-коде. Использовать инструменты (BASH/python tool).",
                 "sandbox", ts)
        elif "'__future__' не в whitelist" in lesson:
            _add(errors, goal, "import_blocked",
                 "Запрещённый импорт: '__future__' не в whitelist sandbox",
                 "Не импортировать __future__ в sandbox-коде. Писать код, совместимый без __future__.",
                 "sandbox", ts)
        elif "'traceback' не в whitelist" in lesson:
            _add(errors, goal, "import_blocked",
                 "Запрещённый импорт: 'traceback' не в whitelist sandbox",
                 "Не импортировать traceback в sandbox. Использовать try/except с str(e) вместо traceback.",
                 "sandbox", ts)
        elif "Запрещённый вызов: '__class__.__'" in lesson:
            _add(errors, goal, "sandbox_block",
                 "CodeValidator: запрещённый вызов __class__.__ — dunder-атрибуты заблокированы",
                 "Не использовать __class__, __dict__ и другие dunder-атрибуты в sandbox-коде.",
                 "sandbox", ts)
        elif "Сгенерированный код слишком большой" in lesson:
            _add(errors, goal, "code_too_large",
                 "Сгенерированный код слишком большой (>500 строк)",
                 "Ограничивать модули до 500 строк. Разбивать большую логику на несколько модулей.",
                 "build_module", ts)
        elif "Запись в '.' запрещена" in lesson:
            _add(errors, goal, "path_violation",
                 "Запись в корень '.' запрещена — build_module может писать только в разрешённые папки",
                 "build_module: писать только в dynamic_modules/, outputs/, project_template/, agents/, tools/.",
                 "build_module", ts)
        elif "WinError 206" in lesson:
            _add(errors, goal, "path_too_long",
                 "WinError 206: имя файла слишком длинное",
                 "Использовать короткие пути. Не генерировать файлы с длинными именами на Windows.",
                 "filesystem", ts)
        elif "NoneType" in lesson and "__dict__" in lesson:
            _add(errors, goal, "build_module_crash",
                 "Module smoke import failed: NoneType has no __dict__",
                 "Проверять spec/mod на None в module_builder harness.",
                 "build_module", ts)
        elif "ContentSanitizer" in lesson:
            _add(errors, goal, "sanitizer_block",
                 f"ContentSanitizer заблокировал: {lesson[:200]}",
                 "Не использовать запрещённые вызовы (getattr, exec, eval). Заменяй на явные обращения.",
                 "sanitizer", ts)


def extract_from_episodes(errors: list[dict]):
    """episodes.json → errors[] из каждого эпизода."""
    data = _load_json("episodes.json")
    if not data:
        return
    episodes = data.get("episodes", data) if isinstance(data, dict) else data
    if not isinstance(episodes, list):
        return

    for ep in episodes:
        if ep.get("success"):
            continue
        goal = ep.get("goal", "")[:300]
        ctx = ep.get("context", {})
        ep_errors = ctx.get("errors", []) if isinstance(ctx, dict) else []
        ts = ep.get("created_at")
        lessons = ep.get("lessons", [])

        # Извлекаем correct_approach из первого урока
        correct = ""
        if lessons and isinstance(lessons, list) and lessons[0]:
            correct = str(lessons[0])[:500]

        for err_text in ep_errors:
            err_str = str(err_text)
            if "ContentSanitizer: запрещённый вызов: getattr()" in err_str:
                _add(errors, goal, "sanitizer_block",
                     "ContentSanitizer: запрещённый вызов getattr()",
                     "Не использовать getattr(). Обращаться к атрибутам явно: obj.attr",
                     "sanitizer", ts)
            elif "namespace неполный" in err_str:
                _add(errors, goal, "python_namespace",
                     "Python-блок пропущен: предыдущий блок не выполнен (namespace неполный)",
                     "Каждый python-блок должен быть самодостаточным. Не зависеть от переменных предыдущих блоков.",
                     "python", ts)
            elif "STEP_EVAL: PARTIAL" in err_str:
                _add(errors, goal, "verification_failed",
                     "STEP_EVAL: PARTIAL — шаг выполнен только частично",
                     correct or "Доводить каждый шаг до полного завершения. Проверять результат.",
                     "evaluation", ts)
            elif "Код заблокирован" in err_str:
                _add(errors, goal, "sandbox_block",
                     err_str[:300],
                     "Избегать запрещённых вызовов (exec, eval, getattr) и операций ОС в sandbox.",
                     "sandbox", ts)
            elif err_str.strip():
                _add(errors, goal, "cycle_error", err_str[:300], correct, "", ts)


def extract_from_commands(errors: list[dict]):
    """commands.jsonl → повторяющиеся паттерны ошибок команд."""
    path = os.path.join(LOGS_DIR, "commands.jsonl")
    if not os.path.exists(path):
        return
    blacklist_hits = 0
    pytest_fails = 0
    returncode2 = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            rc = entry.get("returncode", 0)
            reject = entry.get("reject_reason", "")
            cmd = entry.get("command", "")

            if reject and "чёрном списке" in reject:
                blacklist_hits += 1
            elif rc == 4 and "pytest" in cmd:
                pytest_fails += 1
            elif rc == 2:
                returncode2 += 1

    if blacklist_hits:
        _add(errors, "выполнение команд", "blacklisted_command",
             f"Команды из чёрного списка (powershell/bash/curl) вызваны {blacklist_hits} раз",
             "На Windows: не использовать powershell/bash/curl. Использовать python/pip/git — разрешённые инструменты.",
             "command_gateway")
    if pytest_fails:
        _add(errors, "запуск тестов", "pytest_no_tests",
             f"pytest вернул код 4 (нет тестов) — {pytest_fails} раз. Тесты не найдены по указанным путям",
             "Проверять пути к тестам. Использовать правильные: python -m pytest outputs/log_analyzer/test_analyzer.py",
             "testing")
    if returncode2:
        _add(errors, "запуск скриптов", "script_error",
             f"python-скрипты завершились с кодом 2 (ошибка) — {returncode2} раз",
             "Проверять что скрипт существует по указанному пути. На Windows использовать обратные слэши.",
             "command_gateway")


def extract_from_capability_stats(errors: list[dict]):
    """capability_stats.json → подсказки по инструментам с высоким fail rate."""
    data = _load_json("capability_stats.json")
    if not data:
        return
    for tool, stats in data.items():
        s = stats.get("success", 0)
        f = stats.get("fail", 0)
        if f == 0:
            continue
        rate = f / (s + f)
        if rate >= 0.5 and f >= 5:
            _add(errors, f"инструмент {tool}", "high_fail_rate",
                 f"Инструмент '{tool}': {f} провалов из {s+f} вызовов ({rate:.0%} fail rate)",
                 _tool_advice(tool),
                 "capability")


def _tool_advice(tool: str) -> str:
    tips = {
        "python": "Python-блоки часто блокируются sandbox. Использовать только разрешённые импорты и вызовы. Каждый блок — самодостаточный.",
        "bash": "Bash в чёрном списке на Windows. Использовать python-инструмент или WRITE вместо bash.",
        "build_module": "build_module часто падает: sandbox, слишком длинный код, запрещённые imports. Ограничить до 500 строк, без __future__/traceback, без getattr/__class__.",
        "write": "Проверять путь записи: build_module → только в разрешённые папки. Не писать в корень.",
    }
    return tips.get(tool, f"Инструмент {tool} имеет высокий процент ошибок. Использовать с осторожностью.")


def extract_meta_lessons(errors: list[dict]):
    """Мета-уроки из шаблонов в уроках episodes: 'Нельзя подменять...' и т.д."""
    data = _load_json("episodes.json")
    if not data:
        return
    episodes = data.get("episodes", data) if isinstance(data, dict) else data
    if not isinstance(episodes, list):
        return

    for ep in episodes:
        lessons = ep.get("lessons", [])
        if not isinstance(lessons, list):
            continue
        for lesson in lessons:
            if not isinstance(lesson, str):
                continue
            if "Нельзя подменять" in lesson or "Нельзя считать" in lesson:
                _add(errors, ep.get("goal", "")[:300], "task_deviation",
                     "Нельзя подменять реальное выполнение текстовым описанием. WRITE:... CONTENT:... ≠ реальное создание файлов",
                     "Выполнять действия через реальные инструменты, а не 'описывать' результат в тексте.",
                     "behavior")
            if "Нужно строго" in lesson and "следовать" in lesson:
                _add(errors, ep.get("goal", "")[:300], "task_deviation",
                     "Переусложнение простых задач: агент добавляет лишний код/логику вместо точного следования инструкции",
                     "Выполнять ровно то, что написано в задаче. Не добавлять 'улучшения' без запроса.",
                     "behavior")
            if "LocalBrain:offline" in lesson:
                _add(errors, ep.get("goal", "")[:300], "llm_unavailable",
                     "LLM недоступен — fallback на шаблонный план. Бесполезные действия.",
                     "При недоступности LLM: сообщить пользователю, не выполнять шаблонные действия впустую.",
                     "availability")


def main():
    errors: list[dict] = []

    # Загружаем существующие (если файл уже есть)
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if isinstance(existing, list):
            for e in existing:
                key = _dedup_key(e.get("error_type", ""), e.get("what_went_wrong", ""))
                _seen.add(key)
            errors.extend(existing)

    before = len(errors)

    # Извлечение из всех источников
    extract_from_failure_tracker(errors)
    extract_from_failure_memory(errors)
    extract_from_lessons(errors)
    extract_from_episodes(errors)
    extract_from_commands(errors)
    extract_from_capability_stats(errors)
    extract_meta_lessons(errors)

    added = len(errors) - before

    # Сортируем по времени
    errors.sort(key=lambda e: e.get("time", 0))

    # Сохраняем
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(errors, f, ensure_ascii=False, indent=2, default=str)

    print(f"Всего ошибок в памяти: {len(errors)}")
    print(f"Добавлено новых: {added}")
    print(f"Файл: {OUTPUT_FILE}")

    # Статистика по типам
    types: dict[str, int] = {}
    for e in errors:
        t = e.get("error_type", "unknown")
        types[t] = types.get(t, 0) + 1
    print("\nПо типам:")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
