"""
generate_synthetic.py — Генерация ~5000 синтетических обучающих примеров на GPU-сервере.

Использует большую модель (Qwen2.5-14B или 72B-AWQ) для генерации
высококачественных примеров поведения автономного агента.

Запуск на сервере:
  python generate_synthetic.py                    # авто-выбор модели
  python generate_synthetic.py --model 14b        # Qwen 14B
  python generate_synthetic.py --model 72b        # Qwen 72B AWQ (нужно ~40GB VRAM)
  python generate_synthetic.py --num-per-prompt 15 # примеров на промпт
  python generate_synthetic.py --merge-existing    # объединить с существующим train.jsonl

Выход: training_data/synthetic_train.jsonl, training_data/synthetic_val.jsonl
       training_data/merged_train.jsonl (при --merge-existing)
"""

import argparse
import json
import os
import random
import hashlib
import time
import re
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# Системный промпт (тот же что для обучения)
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "Ты — автономный AI-агент с 46-слойной архитектурой. "
    "Ты выполняешь задачи через циклы: наблюдение → анализ → план → симуляция → действие → оценка. "
    "Ты работаешь на Windows. Для системных команд используй Python или PowerShell, НЕ bash. "
    "Запрещённые импорты в sandbox: importlib, subprocess, sys, ctypes. "
    "Используй psutil для системной информации. "
    "Всегда указывай полные пути к файлам. "
    "Не усложняй решение — делай минимально необходимое для цели."
)

# ═══════════════════════════════════════════════════════════════════════════════
# Шаблоны промптов по категориям
# ═══════════════════════════════════════════════════════════════════════════════

# Формат: (категория, user_template, контекст для генерации)
# {var} будет подставляться случайно из вариантов

PROMPT_TEMPLATES = []

# ──── Категория 1: Планирование и декомпозиция задач ─────────────────────────

_planning_tasks = [
    "Создать веб-скрапер для сбора цен товаров",
    "Автоматизировать еженедельный отчёт по метрикам",
    "Настроить CI/CD пайплайн для Python-проекта",
    "Разработать REST API для управления задачами",
    "Создать систему мониторинга доступности сайтов",
    "Автоматизировать обработку входящих email",
    "Разработать Telegram-бота для уведомлений",
    "Создать ETL пайплайн из CSV в базу данных",
    "Настроить автоматическое резервное копирование",
    "Разработать систему A/B тестирования",
    "Создать парсер PDF документов с извлечением таблиц",
    "Автоматизировать деплой Docker-контейнеров",
    "Разработать систему поиска по документации",
    "Создать дашборд для визуализации метрик",
    "Настроить автоматическую проверку безопасности кода",
]

for task in _planning_tasks:
    PROMPT_TEMPLATES.append((
        "planning",
        f"Декомпозируй задачу на подзадачи: {task}",
        "Разбей на 4-7 конкретных шагов с оценкой сложности и зависимостями. "
        "Укажи какие инструменты агента использовать на каждом шаге. "
        "Инструменты: filesystem, terminal, python_runtime, http_client, git, docker, "
        "search, database, package_manager, pdf_generator, spreadsheet, email."
    ))

# ──── Категория 2: Использование инструментов ────────────────────────────────

_tool_scenarios = [
    ("filesystem", "Прочитать все .py файлы в проекте и найти функции без docstring"),
    ("filesystem", "Создать структуру директорий для нового Python-пакета"),
    ("filesystem", "Найти все файлы больше 10MB и составить отчёт"),
    ("terminal", "Узнать текущую загрузку CPU и RAM на Windows"),
    ("terminal", "Проверить какие порты заняты на локальной машине"),
    ("python_runtime", "Подсчитать статистику по CSV-файлу (среднее, медиана, корреляции)"),
    ("python_runtime", "Сгенерировать случайный пароль заданной длины и сложности"),
    ("python_runtime", "Провести sentiment analysis текста без внешних API"),
    ("http_client", "Проверить статус и время отклика списка URL адресов"),
    ("http_client", "Скачать и распарсить JSON из публичного API"),
    ("http_client", "Получить курс валют через frankfurter.app API"),
    ("search", "Найти актуальную информацию о последней версии Python"),
    ("search", "Найти топ-5 библиотек для обработки изображений в Python"),
    ("git", "Создать новую ветку, сделать коммит и подготовить PR"),
    ("git", "Просмотреть историю изменений файла и найти когда появился баг"),
    ("pdf_generator", "Создать PDF отчёт с таблицей и графиком"),
    ("spreadsheet", "Создать Excel таблицу с формулами и форматированием"),
    ("data_viz", "Построить график зависимости двух переменных из данных"),
    ("database", "Написать SQL запрос для анализа продаж по месяцам"),
    ("docker", "Запустить контейнер с PostgreSQL и проверить подключение"),
    ("package_manager", "Проверить устаревшие пакеты и обновить безопасно"),
    ("archive", "Создать ZIP архив с отчётами за последний месяц"),
    ("email", "Подготовить email с вложением и отправить"),
    ("ssh", "Подключиться к удалённому серверу и проверить логи"),
    ("code_analyzer", "Проанализировать сложность кода и найти проблемные функции"),
    ("ocr", "Извлечь текст из скриншота и сохранить в файл"),
    ("translate", "Перевести документацию с английского на русский"),
    ("encryption", "Зашифровать конфиденциальный файл с паролем"),
    ("notification", "Настроить уведомление при завершении долгой задачи"),
    ("task_queue", "Добавить задачи в очередь приоритетов и обработать"),
]

for tool, scenario in _tool_scenarios:
    PROMPT_TEMPLATES.append((
        "tool_usage",
        scenario,
        f"Покажи как использовать инструмент '{tool}' агента для этой задачи. "
        f"Формат: tool.run(action='...', params={{...}}). "
        "Проверяй r['success'] после каждого вызова. Ошибки в r.get('error', ''). "
        "Файлы сохраняй в outputs/. Не используй запрещённые импорты."
    ))

# ──── Категория 3: Обработка ошибок и самовосстановление ─────────────────────

_error_scenarios = [
    "Bash-команда 'ls -la' не работает на Windows. Как исправить?",
    "ImportError: importlib заблокирован в sandbox. Альтернатива?",
    "FileNotFoundError: outputs/report.pdf — файл не создался. Что делать?",
    "Timeout при HTTP запросе к внешнему API. Стратегия retry?",
    "MemoryError при обработке большого CSV (2GB). Как обработать?",
    "SyntaxError в сгенерированном Python-коде. Как автоматически исправить?",
    "Git push отклонён — конфликт. Как разрешить автоматически?",
    "Docker контейнер не запускается — порт занят. Решение?",
    "API вернул 429 Too Many Requests. Как обработать rate limiting?",
    "JSON парсинг сломался — невалидный ответ от LLM. Как восстановить?",
    "Модуль не найден после pip install. Как проверить окружение?",
    "Диск заполнен на 95%. Как освободить место безопасно?",
    "SSL сертификат истёк при HTTPS запросе. Что делать?",
    "Encoding ошибка при чтении файла с кириллицей. Решение?",
    "Deadlock в очереди задач — две задачи ждут друг друга. Как обнаружить и устранить?",
    "Файл заблокирован другим процессом на Windows. Как обработать?",
    "Потеря соединения с Telegram API посреди отправки. Recovery стратегия?",
    "LLM вернул ответ не в ожидаемом формате JSON. Как извлечь данные?",
    "Цикл агента прервался из-за необработанного исключения. Как сделать robust?",
    "База знаний повреждена — JSON файл содержит мусор. Восстановление?",
]

for scenario in _error_scenarios:
    PROMPT_TEMPLATES.append((
        "error_recovery",
        scenario,
        "Объясни причину ошибки и дай пошаговое решение. "
        "Укажи как предотвратить повторение. Код должен работать на Windows. "
        "Не используй запрещённые импорты (importlib, subprocess, sys, ctypes). "
        "Покажи конкретный Python-код исправления."
    ))

# ──── Категория 4: Рефлексия и анализ результатов ────────────────────────────

_reflection_goals = [
    ("Создание документа", True, "PDF создан за 3 шага, содержание полное"),
    ("Создание документа", False, "PDF создан но пустой — шаблон не заполнился"),
    ("Анализ данных", True, "Статистика подсчитана, графики построены"),
    ("Анализ данных", False, "CSV не прочитался — неверная кодировка"),
    ("Web scraping", True, "Данные собраны с 50 страниц за 2 минуты"),
    ("Web scraping", False, "Сайт заблокировал по user-agent, собрано 0 страниц"),
    ("Автоматизация email", True, "Письмо отправлено с вложением"),
    ("Автоматизация email", False, "OAuth токен истёк, отправка не удалась"),
    ("Деплой сервиса", True, "Контейнер запущен, healthcheck проходит"),
    ("Деплой сервиса", False, "Порт 8080 занят, контейнер не стартовал"),
    ("Поиск информации", True, "Найдено 5 релевантных источников, сводка готова"),
    ("Поиск информации", False, "Все результаты нерелевантны — плохой запрос"),
    ("Рефакторинг кода", True, "Сложность снижена с O(n²) до O(n log n)"),
    ("Рефакторинг кода", False, "После рефакторинга 3 теста упали"),
    ("Настройка мониторинга", True, "Алерты настроены, тестовое уведомление получено"),
    ("Настройка мониторинга", False, "Webhook URL неверный, уведомления не приходят"),
]

for goal, success, outcome in _reflection_goals:
    status = "Да" if success else "Нет"
    PROMPT_TEMPLATES.append((
        "reflection",
        f"Рефлексия по задаче: {goal}\nЦель достигнута: {status}\nИтог: {outcome}",
        "Проведи глубокий анализ: что сработало, что нет, какие уроки извлечь. "
        "Дай 2-3 конкретные рекомендации для улучшения. "
        "Если задача провалена — укажи root cause и стратегию исправления."
    ))

# ──── Категория 5: Автономный цикл (observe → plan → act → evaluate) ────────

_cycle_situations = [
    "Получена новая задача от пользователя: создать отчёт по продажам из CSV",
    "Предыдущий цикл провалился с ошибкой таймаута при HTTP запросе",
    "Все задачи выполнены, нет активных целей — нужно сгенерировать новые",
    "Обнаружено обновление библиотеки с критическим багфиксом",
    "Мониторинг показал аномальное использование памяти (90%)",
    "Пользователь не отвечает 24 часа — агент работает автономно",
    "Telegram API вернул ошибку 502 — коммуникация нарушена",
    "Найдено 3 конфликтующие цели, нужно приоритизировать",
    "Knowledge base заполнена на 80% — нужна очистка или архивация",
    "Новый инструмент обнаружен через Capability Discovery — нужно оценить",
    "Self-test выявил деградацию точности на 15% за последнюю неделю",
    "Получен запрос на задачу, выходящую за компетенции агента",
    "Аудит выявил 5 не закрытых задач с истёкшим дедлайном",
    "Rate limit на OpenAI API — нужно переключить модель",
    "Обнаружен дубликат знаний в knowledge graph — нужна дедупликация",
]

for situation in _cycle_situations:
    PROMPT_TEMPLATES.append((
        "autonomous_cycle",
        f"Ситуация в автономном цикле:\n{situation}\n\nОпиши фазы цикла для этой ситуации.",
        "Опиши все фазы: 1) Наблюдение (что видим), 2) Анализ (что это значит), "
        "3) План (какие действия), 4) Действие (конкретные шаги), "
        "5) Оценка (как проверить результат). "
        "Укажи конкретные инструменты агента и метрики оценки."
    ))

# ──── Категория 6: Работа с 46 слоями архитектуры ────────────────────────────

_layer_scenarios = [
    (1, "Perception", "Как агент обрабатывает входящий PDF документ и извлекает структуру?"),
    (2, "Knowledge System", "Как выбрать между episodic и semantic memory для хранения?"),
    (3, "Cognitive Core", "Как принять решение при неполной информации?"),
    (4, "Agent System", "Когда делегировать подзадачу specialized агенту?"),
    (5, "Tool Layer", "Как выбрать правильный инструмент для задачи?"),
    (6, "OS Layer", "Как безопасно выполнить команду на Windows через агента?"),
    (7, "Software Dev", "Как сгенерировать и проверить Quality Gate для кода?"),
    (8, "Execution", "Как запустить долгий скрипт и отслеживать прогресс?"),
    (9, "Learning", "Как изучить новую технологию из документации?"),
    (10, "Reflection", "Как проанализировать провал и извлечь уроки?"),
    (11, "Self-Repair", "Как обнаружить и исправить баг в собственном коде?"),
    (12, "Self-Improvement", "Как оптимизировать часто используемый алгоритм?"),
    (16, "Security", "Как безопасно хранить API ключи и секреты?"),
    (17, "Monitoring", "Как настроить алерты на критические ошибки?"),
    (20, "Autonomous Loop", "Как определить когда остановить цикл и ждать пользователя?"),
    (21, "Governance", "Какие действия требуют подтверждения пользователя?"),
    (22, "HITL", "Как эскалировать решение оператору через Telegram?"),
    (23, "State Management", "Как восстановить сессию после аварийного перезапуска?"),
    (24, "Data Contracts", "Как валидировать ответ LLM перед использованием?"),
    (25, "Evaluation", "Как оценить качество выполнения задачи (KPI)?"),
    (26, "Budget Control", "Как распределить бюджет токенов на цикл?"),
    (27, "Environment Model", "Как предсказать последствия действия до выполнения?"),
    (29, "Skill Library", "Как сохранить и переиспользовать успешную стратегию?"),
    (32, "Model Management", "Как выбрать между light/heavy/local LLM для задачи?"),
    (35, "Capability Discovery", "Как найти и подключить новый инструмент из PyPI?"),
    (36, "Experience Replay", "Как использовать прошлый опыт для текущей задачи?"),
    (37, "Goal Management", "Как приоритизировать конфликтующие цели?"),
    (38, "Long-Horizon Planning", "Как спланировать проект на неделю вперёд?"),
    (39, "Attention Management", "Как фильтровать шум и сфокусироваться на важном?"),
    (45, "Identity", "Как агент оценивает свои текущие возможности и ограничения?"),
    (46, "Knowledge Verification", "Как проверить достоверность информации из интернета?"),
]

for layer_num, layer_name, question in _layer_scenarios:
    PROMPT_TEMPLATES.append((
        "architecture",
        question,
        f"Отвечай как автономный агент со слоем #{layer_num} ({layer_name}). "
        "Дай конкретный ответ с примером кода или псевдокода. "
        "Упомяни взаимодействие с другими слоями архитектуры."
    ))

# ──── Категория 7: Реальные рабочие сценарии (фриланс, задачи) ───────────────

_work_scenarios = [
    "Клиент просит создать лендинг с формой обратной связи",
    "Нужно проанализировать 10 000 строк лога и найти паттерн ошибок",
    "Написать скрипт миграции данных из MySQL в PostgreSQL",
    "Провести code review PR на 500 строк Python-кода",
    "Создать автоматические тесты для REST API (10 эндпоинтов)",
    "Настроить мониторинг сервера с алертами в Telegram",
    "Оптимизировать SQL запрос, выполняющийся 30 секунд",
    "Написать парсер XML фида товаров (~50 000 записей)",
    "Создать документацию к API в формате OpenAPI/Swagger",
    "Настроить GitHub Actions для автоматического деплоя",
    "Провести аудит безопасности веб-приложения",
    "Написать Telegram бота для управления задачами команды",
    "Создать ETL для агрегации данных из 5 источников",
    "Разработать систему кэширования для ускорения API",
    "Написать скрипт для автоматической генерации отчётов",
]

for scenario in _work_scenarios:
    PROMPT_TEMPLATES.append((
        "work_task",
        f"Задача: {scenario}\nСпланируй и выполни.",
        "Покажи полный цикл: анализ задачи → план → выполнение → проверка. "
        "Используй конкретные инструменты агента. Код должен быть рабочим. "
        "Указывай реальные команды и пути файлов."
    ))

# ──── Категория 8: Безопасность и governance ─────────────────────────────────

_security_scenarios = [
    "Пользователь просит удалить все файлы в C:\\. Как реагировать?",
    "Обнаружен API ключ в коде, который будет закоммичен. Что делать?",
    "Задача требует доступа к чужим данным. Этические границы?",
    "LLM предлагает использовать eval() для парсинга данных. Безопасно ли?",
    "Sandbox заблокировал subprocess. Как безопасно выполнить команду?",
    "Обнаружена SQL инъекция в сгенерированном коде. Как исправить?",
    "Нужно передать пароль в переменную окружения. Best practice?",
    "Пользователь просит отправить данные на неизвестный сервер. Проверки?",
    "Аудит показал логирование чувствительных данных. Как редактировать?",
    "Rate limiter сработал — нужно решить, продолжать ли задачу или ждать?",
]

for scenario in _security_scenarios:
    PROMPT_TEMPLATES.append((
        "security",
        scenario,
        "Ответь с точки зрения безопасности и governance. "
        "Укажи Layer 16 (Security) и Layer 21 (Governance) принципы. "
        "Если действие опасно — объясни почему и предложи безопасную альтернативу."
    ))

# ──── Категория 9: Мульти-шаговый диалог ────────────────────────────────────

_dialog_scenarios = [
    ("Что ты умеешь?",
     "Кратко опиши свои возможности по категориям: файлы, код, web, анализ, "
     "автоматизация, коммуникации. Упомяни 46 слоёв и ключевые инструменты."),
    ("Сколько стоит твоя работа?",
     "Объясни что ты автономный агент, бесплатный для владельца. "
     "Для фриланс-задач — стоимость зависит от сложности."),
    ("Ты можешь ошибаться?",
     "Честно ответь: да, но есть Layer 10 (Reflection), Layer 11 (Self-Repair), "
     "Layer 25 (Evaluation). Объясни как агент обнаруживает и исправляет ошибки."),
    ("Какие у тебя ограничения?",
     "Перечисли: sandbox ограничения, зависимость от API/LLM, нет физического доступа, "
     "нужно подтверждение для опасных действий. Будь честен."),
    ("Как ты учишься?",
     "Объясни: Layer 9 (Learning), Layer 36 (Experience Replay), "
     "persistent brain, lessons.json, reflections. Примеры обучения из опыта."),
    ("Расскажи о своей архитектуре",
     "Кратко: 46 слоёв от Perception до Knowledge Verification. "
     "Автономный цикл, инструменты, LLM роутер (light/heavy/local)."),
]

for user_q, context in _dialog_scenarios:
    PROMPT_TEMPLATES.append((
        "dialog",
        user_q,
        context
    ))

# ──── Категория 10: Оптимизация и performance ───────────────────────────────

_optimization_scenarios = [
    "Цикл агента занимает 45 секунд. Как ускорить до 15?",
    "LLM расходует 50K токенов на простую задачу. Как сократить?",
    "Knowledge base занимает 500MB RAM. Как оптимизировать?",
    "10 последовательных API вызовов — можно ли параллелизировать?",
    "Embedding поиск по 100K документов медленный. Как ускорить?",
    "Логи растут на 50MB/день. Стратегия ротации?",
    "Cold start агента занимает 30 секунд. Как сократить?",
    "Модель генерирует слишком длинные ответы. Как контролировать?",
]

for scenario in _optimization_scenarios:
    PROMPT_TEMPLATES.append((
        "optimization",
        scenario,
        "Дай конкретные технические решения с метриками. "
        "Покажи код или конфигурацию. Укажи ожидаемый эффект."
    ))

print(f"[INFO] Всего шаблонов промптов: {len(PROMPT_TEMPLATES)}")

# ═══════════════════════════════════════════════════════════════════════════════
# Генерация через LLM
# ═══════════════════════════════════════════════════════════════════════════════

GENERATION_META_PROMPT = """Ты генерируешь обучающие данные для fine-tuning автономного AI-агента.

Контекст: {context}

Сгенерируй ОТВЕТ агента на вопрос пользователя. Требования:
- Ответ должен быть конкретным, с кодом/командами где уместно
- Минимум 100 слов, максимум 800 слов
- Используй инструменты агента: tool.run(action='...', params={{...}})
- Файлы → outputs/, проверяй r['success']
- НЕ используй bash на Windows, только Python/PowerShell
- Запрещены: importlib, subprocess, sys, ctypes
- Стиль: уверенный, профессиональный, краткий

Вопрос пользователя: {user_message}

Твой ответ (начинай сразу, без преамбул):"""

VARIATION_META_PROMPT = """Перефразируй этот вопрос пользователя к AI-агенту, сохраняя смысл но меняя формулировку.
Вариант должен звучать естественно, как будто другой пользователь задаёт похожий вопрос.

Оригинал: {original}

Перефразированный вопрос (одна строка, без кавычек):"""


def load_model(model_size: str):
    """Загружает GGUF модель через llama-cpp-python (не зависит от torch)."""
    from llama_cpp import Llama
    from huggingface_hub import hf_hub_download

    model_map = {
        "7b": ("Qwen/Qwen2.5-7B-Instruct-GGUF", "qwen2.5-7b-instruct-q3_k_m.gguf"),
        "14b": ("Qwen/Qwen2.5-14B-Instruct-GGUF", "qwen2.5-14b-instruct-q3_k_m.gguf"),
    }

    if model_size not in model_map:
        model_size = "7b"

    repo_id, filename = model_map[model_size]
    print(f"[INFO] Скачивание GGUF: {repo_id}/{filename}")

    model_path = hf_hub_download(repo_id=repo_id, filename=filename)
    print(f"[INFO] Загрузка модели в GPU...")

    llm = Llama(
        model_path=model_path,
        n_ctx=4096,
        n_gpu_layers=-1,   # Все слои на GPU
        n_batch=512,
        verbose=False,
    )

    print("[INFO] Модель загружена через llama.cpp")
    return llm, None  # tokenizer не нужен, llama.cpp сам токенизирует


def generate_text(model, tokenizer, prompt: str, max_new_tokens=1024, temperature=0.8) -> str:
    """Генерирует текст через llama.cpp."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = model.create_chat_completion(
        messages=messages,
        max_tokens=max_new_tokens,
        temperature=temperature,
        top_p=0.9,
        top_k=50,
        repeat_penalty=1.1,
    )

    result = response["choices"][0]["message"]["content"].strip()
    return result


def generate_variation(model, tokenizer, original_question: str) -> str:
    """Генерирует вариацию вопроса."""
    prompt = VARIATION_META_PROMPT.format(original=original_question)
    messages = [{"role": "user", "content": prompt}]

    response = model.create_chat_completion(
        messages=messages,
        max_tokens=128,
        temperature=0.9,
        top_p=0.95,
    )

    result = response["choices"][0]["message"]["content"].strip()

    # Убираем кавычки и лишнее
    result = result.split("\n")[0].strip().strip('"').strip("'")
    return result if len(result) > 10 else original_question


def quality_filter(response: str) -> bool:
    """Проверяет качество сгенерированного ответа."""
    if len(response) < 80:
        return False
    if len(response) > 8000:
        return False
    # Слишком много повторов
    words = response.split()
    if len(words) > 20:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.25:
            return False
    # Содержит артефакты генерации
    bad_patterns = [
        "as an ai language model",
        "i cannot",
        "i'm sorry, but",
        "i don't have access",
        "i'm not able to",
    ]
    lower = response.lower()
    if any(p in lower for p in bad_patterns):
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Генерация синтетических данных")
    parser.add_argument("--model", default="14b", choices=["7b", "14b", "72b"],
                        help="Размер модели генератора")
    parser.add_argument("--num-per-prompt", type=int, default=15,
                        help="Вариаций на промпт (больше = больше данных)")
    parser.add_argument("--merge-existing", action="store_true",
                        help="Объединить с существующим train.jsonl")
    parser.add_argument("--output-dir", default="training_data",
                        help="Директория для выхода")
    parser.add_argument("--resume", action="store_true",
                        help="Продолжить с последнего checkpoint")
    args = parser.parse_args()

    # Загрузка модели
    model, tokenizer = load_model(args.model)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    checkpoint_path = output_dir / "synthetic_checkpoint.jsonl"
    all_samples = []

    # Resume
    if args.resume and checkpoint_path.exists():
        with open(checkpoint_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    all_samples.append(json.loads(line))
        print(f"[RESUME] Загружено {len(all_samples)} из checkpoint")
        done_indices = {s.get("_idx", -1) for s in all_samples}
    else:
        done_indices = set()

    total_templates = len(PROMPT_TEMPLATES)
    target_total = total_templates * args.num_per_prompt
    print(f"[INFO] Шаблонов: {total_templates}, вариаций/шаблон: {args.num_per_prompt}")
    print(f"[INFO] Целевое количество: ~{target_total} примеров")
    print(f"[INFO] Модель: {args.model}")
    print()

    t_start = time.time()
    generated = 0
    filtered = 0

    for idx, (category, user_template, gen_context) in enumerate(PROMPT_TEMPLATES):
        if idx in done_indices:
            continue

        print(f"[{idx+1}/{total_templates}] {category}: {user_template[:60]}...")

        for var_i in range(args.num_per_prompt):
            try:
                # Первый вариант — оригинальный промпт, остальные — вариации
                if var_i == 0:
                    user_msg = user_template
                else:
                    user_msg = generate_variation(model, tokenizer, user_template)

                # Генерируем ответ
                gen_prompt = GENERATION_META_PROMPT.format(
                    context=gen_context,
                    user_message=user_msg,
                )
                response = generate_text(model, tokenizer, gen_prompt)

                # Фильтр качества
                if not quality_filter(response):
                    filtered += 1
                    continue

                sample = {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": response},
                    ],
                    "_source": f"synthetic_{category}",
                    "_idx": idx,
                }

                all_samples.append(sample)
                generated += 1

                # Checkpoint каждые 50 примеров
                if generated % 50 == 0:
                    with open(checkpoint_path, "a", encoding="utf-8") as f:
                        for s in all_samples[-50:]:
                            f.write(json.dumps(s, ensure_ascii=False) + "\n")

                    elapsed = time.time() - t_start
                    rate = generated / elapsed * 3600
                    remaining = (target_total - generated) / (rate / 3600) if rate > 0 else 0
                    print(f"  [{generated}/{target_total}] "
                          f"+{50} ok, {filtered} filtered, "
                          f"{rate:.0f}/hr, ~{remaining/60:.0f} min left")

            except Exception as e:
                print(f"  [WARN] Ошибка var={var_i}: {e}")
                continue

    # ── Дедупликация ──────────────────────────────────────────────────────
    print("\n[INFO] Дедупликация...")
    seen_hashes = set()
    unique = []
    for s in all_samples:
        msgs = s["messages"]
        h = hashlib.md5(
            (msgs[1]["content"] + msgs[2]["content"]).encode()
        ).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique.append(s)

    print(f"[INFO] До дедупликации: {len(all_samples)}, после: {len(unique)}")

    # ── Сохранение ────────────────────────────────────────────────────────
    random.seed(42)
    random.shuffle(unique)

    split = max(1, int(len(unique) * 0.9))
    train = unique[:split]
    val = unique[split:]

    for name, data in [("synthetic_train.jsonl", train), ("synthetic_val.jsonl", val)]:
        path = output_dir / name
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                clean = {"messages": item["messages"]}
                f.write(json.dumps(clean, ensure_ascii=False) + "\n")
        print(f"[SAVE] {path}: {len(data)} примеров")

    # ── Merge с существующими данными ─────────────────────────────────────
    if args.merge_existing:
        existing_train = output_dir / "train.jsonl"
        if existing_train.exists():
            merged = []
            with open(existing_train, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        merged.append(json.loads(line))
            print(f"[MERGE] Существующий train: {len(merged)} примеров")

            # Добавляем синтетические (без дупликатов)
            existing_hashes = set()
            for m in merged:
                h = hashlib.md5(
                    (m["messages"][1]["content"] + m["messages"][2]["content"]).encode()
                ).hexdigest()
                existing_hashes.add(h)

            added = 0
            for s in train:
                h = hashlib.md5(
                    (s["messages"][1]["content"] + s["messages"][2]["content"]).encode()
                ).hexdigest()
                if h not in existing_hashes:
                    merged.append({"messages": s["messages"]})
                    added += 1

            random.shuffle(merged)
            merged_path = output_dir / "merged_train.jsonl"
            with open(merged_path, "w", encoding="utf-8") as f:
                for item in merged:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            print(f"[MERGE] merged_train.jsonl: {len(merged)} ({added} новых)")

    # ── Статистика ────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    from collections import Counter
    sources = Counter(s.get("_source", "?") for s in unique)

    print(f"\n{'='*60}")
    print(f"Генерация завершена за {elapsed/60:.1f} минут")
    print(f"Всего уникальных: {len(unique)} (отфильтровано: {filtered})")
    print(f"Train: {len(train)}, Val: {len(val)}")
    print("\nПо категориям:")
    for src, cnt in sources.most_common():
        print(f"  {src}: {cnt}")
    print(f"{'='*60}")

    # Удаляем checkpoint
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print("[CLEANUP] Checkpoint удалён")


if __name__ == "__main__":
    main()
