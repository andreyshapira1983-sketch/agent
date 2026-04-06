"""
prepare_dataset.py — Генерация обучающего датасета из памяти агента.

Источники:
  - episodes.json    (44 эпизода: goal → actions → outcome)
  - reflections.json (33 рефлексии: goal → analysis → lessons)
  - llm_errors.json  (120 ошибок: task → what_went_wrong → correct_approach)
  - lessons.json     (21 урок)
  - failure_memory.json (75 записей о провалах)
  - strategies.json  (стратегии поведения)

Формат выхода: JSONL (ChatML) для SFT:
  {"messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}

Запуск: python prepare_dataset.py
Выход:  training_data/train.jsonl, training_data/val.jsonl
"""

import json
import os
import random
import hashlib

MEMORY_DIR = ".agent_memory"
OUTPUT_DIR = "training_data"

SYSTEM_PROMPT = (
    "Ты — автономный AI-агент с 46-слойной архитектурой. "
    "Ты выполняешь задачи через циклы: наблюдение → анализ → план → симуляция → действие → оценка. "
    "Ты работаешь на Windows. Для системных команд используй Python или PowerShell, НЕ bash. "
    "Запрещённые импорты в sandbox: importlib, subprocess, sys, ctypes. "
    "Используй psutil для системной информации. "
    "Всегда указывай полные пути к файлам. "
    "Не усложняй решение — делай минимально необходимое для цели."
)

samples = []


def load_json(name):
    path = os.path.join(MEMORY_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def add(user_msg, assistant_msg, source="unknown"):
    """Добавляет один обучающий пример."""
    if not user_msg or not assistant_msg:
        return
    if len(assistant_msg.strip()) < 20:
        return  # слишком короткий ответ — бесполезен
    h = hashlib.md5((user_msg + assistant_msg).encode()).hexdigest()
    samples.append({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg.strip()},
            {"role": "assistant", "content": assistant_msg.strip()},
        ],
        "_source": source,
        "_hash": h,
    })


# ═══ 1. Эпизоды: успешные → как надо, неуспешные → что пошло не так ═════════

episodes_data = load_json("episodes.json")
episodes = []
if isinstance(episodes_data, dict):
    episodes = episodes_data.get("episodes", [])
elif isinstance(episodes_data, list):
    episodes = episodes_data

for ep in episodes:
    goal = ep.get("goal", "")
    success = ep.get("success", False)
    actions = ep.get("actions", [])
    outcome = ep.get("outcome", "")
    lessons = ep.get("lessons", [])

    if not goal:
        continue

    if success and actions:
        # Успешный эпизод: покажи как выполнить задачу
        actions_text = ""
        if isinstance(actions, list):
            for i, a in enumerate(actions[:5], 1):
                if isinstance(a, dict):
                    actions_text += f"\nШаг {i}: {a.get('type', '?')} — {str(a.get('input', ''))[:300]}"
                else:
                    actions_text += f"\nШаг {i}: {str(a)[:300]}"
        elif isinstance(actions, str):
            actions_text = actions[:1000]

        response = f"План выполнения:{actions_text}"
        if outcome:
            response += f"\n\nРезультат: {str(outcome)[:500]}"
        add(f"Выполни задачу: {goal}", response, "episode_success")

    elif not success and lessons:
        # Неуспешный эпизод: что пошло не так и как исправить
        lessons_text = ""
        for i, lesson in enumerate(lessons[:3], 1):
            if isinstance(lesson, str):
                lessons_text += f"\n{i}. {lesson[:500]}"
            elif isinstance(lesson, dict):
                lessons_text += f"\n{i}. {lesson.get('text', str(lesson))[:500]}"

        if lessons_text:
            add(
                f"Задача: {goal}\nЗадача провалена. Проанализируй ошибки и дай рекомендации.",
                f"Анализ провала:{lessons_text}",
                "episode_failure",
            )


# ═══ 2. Рефлексии: goal → analysis + lessons ════════════════════════════════

reflections_data = load_json("reflections.json")
reflections = []
if isinstance(reflections_data, dict):
    reflections = reflections_data.get("reflections", [])
elif isinstance(reflections_data, list):
    reflections = reflections_data

for ref in reflections:
    if not isinstance(ref, dict):
        continue
    goal = ref.get("goal", "")
    analysis = ref.get("analysis", "")
    lessons = ref.get("lessons", [])
    achieved = ref.get("goal_achieved", None)

    if not goal or not analysis:
        continue

    user_msg = f"Рефлексия по задаче: {goal}\nЦель достигнута: {'Да' if achieved else 'Нет'}"

    response = f"Анализ:\n{analysis[:1500]}"
    if lessons:
        response += "\n\nУроки:"
        for i, l in enumerate(lessons[:3], 1):
            text = l if isinstance(l, str) else str(l)
            response += f"\n{i}. {text[:500]}"

    suggestions = ref.get("suggestions", "")
    if suggestions:
        response += f"\n\nРекомендации: {str(suggestions)[:500]}"

    add(user_msg, response, "reflection")


# ═══ 3. Ошибки LLM: task → what_went_wrong → correct_approach ═══════════════

errors = load_json("llm_errors.json") or []
if isinstance(errors, dict):
    # Если dict: {category: [items]}
    flat_errors = []
    for items in errors.values():
        if isinstance(items, list):
            flat_errors.extend(items)
    errors = flat_errors

for err in errors:
    if not isinstance(err, dict):
        continue

    task = err.get("task", "")
    wrong = err.get("what_went_wrong", "")
    correct = err.get("correct_approach", "")
    category = err.get("category", "unknown")

    if not wrong and not correct:
        continue

    user_msg = f"Ошибка при выполнении: {task[:300]}\nКатегория: {category}"
    response = ""
    if wrong:
        response += f"Что пошло не так: {wrong[:500]}"
    if correct:
        response += f"\n\nПравильный подход: {correct[:500]}"

    add(user_msg, response.strip(), "llm_error")


# ═══ 4. Уроки ════════════════════════════════════════════════════════════════

lessons_data = load_json("lessons.json")
lessons_list = []
if isinstance(lessons_data, dict):
    lessons_list = lessons_data.get("lessons", [])
elif isinstance(lessons_data, list):
    lessons_list = lessons_data

for lesson in lessons_list:
    if isinstance(lesson, str) and len(lesson) > 30:
        add(
            "Какие уроки ты извлёк из прошлого опыта?",
            lesson[:2000],
            "lesson",
        )
    elif isinstance(lesson, dict):
        text = lesson.get("text", lesson.get("content", lesson.get("lesson", "")))
        if text and len(str(text)) > 30:
            add(
                "Какие уроки ты извлёк из прошлого опыта?",
                str(text)[:2000],
                "lesson",
            )


# ═══ 5. Failure memory ═══════════════════════════════════════════════════════

failures = load_json("failure_memory.json")
if isinstance(failures, dict):
    for pattern, data in failures.items():
        if isinstance(data, dict):
            count = data.get("count", 0)
            desc = data.get("description", data.get("last_error", ""))
            fix = data.get("fix", data.get("mitigation", ""))
            if desc:
                user_msg = f"Паттерн ошибки: {pattern}\nПовторений: {count}"
                resp = f"Описание: {str(desc)[:500]}"
                if fix:
                    resp += f"\nИсправление: {str(fix)[:500]}"
                add(user_msg, resp, "failure_pattern")
        elif isinstance(data, list):
            for item in data[:3]:
                if isinstance(item, dict):
                    desc = item.get("error", item.get("description", ""))
                    if desc:
                        add(
                            f"Ошибка ({pattern}): что произошло?",
                            str(desc)[:1000],
                            "failure_pattern",
                        )


# ═══ 6. Hardcoded правила (из опыта агента) ═════════════════════════════════

RULES = [
    (
        "Ты на Windows. Как выполнить системную команду?",
        "На Windows нужно использовать Python-код или PowerShell. "
        "НЕЛЬЗЯ использовать bash-команды (cd, ls, cat). "
        "Для информации о системе: import psutil, platform, os. "
        "Для работы с файлами: import os, pathlib. "
        "Для запуска процессов: использовать Tool Layer, не subprocess напрямую.",
    ),
    (
        "Какие импорты запрещены в sandbox?",
        "Запрещены: importlib, subprocess, sys, ctypes, os.system(), exec(), eval() с произвольным кодом. "
        "Разрешены: os (для путей и файлов), json, datetime, math, re, collections, pathlib, psutil, platform.",
    ),
    (
        "Как правильно сохранять файлы?",
        "Всегда указывай полный путь с директорией: 'outputs/filename.ext', не просто 'filename.ext'. "
        "Перед записью создай директорию: os.makedirs('outputs', exist_ok=True). "
        "Контент файла должен быть содержательным (минимум 200 символов).",
    ),
    (
        "Как не усложнять решение?",
        "Делай минимально необходимое для достижения цели. "
        "Если задача — создать файл, не строй сложную систему мониторинга. "
        "Не используй getattr() в sandbox — заблокировано. "
        "Если первый подход не работает — упрости, не усложняй.",
    ),
    (
        "Что делать если bash-команда провалилась на Windows?",
        "Ошибка 'BASH НЕ ВЫПОЛНЕНА' означает, что ты послал Unix bash-команду на Windows. "
        "Решение: переписать на Python-код. Вместо 'ls' → os.listdir(), "
        "вместо 'cat file' → open('file').read(), вместо 'cd dir && cmd' → os.chdir() + subprocess.",
    ),
    (
        "Как проверять работоспособность системы?",
        "Используй psutil: psutil.cpu_percent(), psutil.virtual_memory(), psutil.disk_usage('C:\\\\'). "
        "Используй platform: platform.system(), platform.python_version(). "
        "Используй os: os.listdir(), os.path.exists(). "
        "НЕ используй importlib или subprocess для этих целей.",
    ),
]

for user_msg, assistant_msg in RULES:
    add(user_msg, assistant_msg, "rule")


# ═══ Дедупликация и сохранение ═══════════════════════════════════════════════

# Дедупликация по hash
seen = set()
unique = []
for s in samples:
    h = s.pop("_hash")
    if h not in seen:
        seen.add(h)
        unique.append(s)

random.seed(42)
random.shuffle(unique)

# 90/10 train/val split
split = max(1, int(len(unique) * 0.9))
train = unique[:split]
val = unique[split:]

os.makedirs(OUTPUT_DIR, exist_ok=True)

for name, data in [("train.jsonl", train), ("val.jsonl", val)]:
    path = os.path.join(OUTPUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            # Убираем _source для обучения
            clean = {"messages": item["messages"]}
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")

# Статистика
from collections import Counter
sources = Counter(s.get("_source", "?") for s in unique)
print(f"Датасет подготовлен:")
print(f"  Всего примеров: {len(unique)}")
print(f"  Train: {len(train)}")
print(f"  Val: {len(val)}")
print(f"  Источники: {dict(sources)}")
print(f"  Сохранено: {OUTPUT_DIR}/train.jsonl, {OUTPUT_DIR}/val.jsonl")
