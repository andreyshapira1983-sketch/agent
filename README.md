# 🤖 Autonomous AI Agent — 46-Layer Architecture

Полностью автономный AI-агент с 46-слойной архитектурой, способный самостоятельно ставить цели, выполнять задачи, обучаться на опыте и взаимодействовать с пользователем через Telegram и Web-интерфейс.

**180 модулей** · **70 000+ строк кода** · **41 тест-файл** · **Python 3.13+**

---

## Ключевые возможности

- **Мультимодальное восприятие** — веб-страницы, PDF, DOCX, изображения, голос, видео
- **Когнитивное ядро** — LLM-маршрутизатор (GPT-5.1, Claude Opus 4, локальный Qwen)
- **Автономный цикл** — goal → plan → act → evaluate → learn → repeat
- **Мульти-агентная система** — менеджер, исследователь, кодер, дебаггер, аналитик
- **Самовосстановление** — автоматический патчинг упавших модулей
- **Самообучение** — experience replay, извлечение знаний из диалогов
- **46 слоёв** — от восприятия до этики, верификации знаний и социальной модели
- **Web UI** — чат с тёмной темой, загрузка файлов, панель активности, SSE
- **Telegram бот** — голос, фото, документы, inline-кнопки, кросс-канальный мост
- **Безопасность** — sandbox, этика, governance, human approval, content fence, secrets proxy

---

## Архитектура

```text
46 слоёв = 20 базовых + 6 управленческих + 20 расширяющих

 1  Perception          │ 17 Monitoring         │ 31 Knowledge Acquisition
 2  Knowledge System    │ 18 Orchestration      │ 32 Model Manager
 3  Cognitive Core      │ 19 Reliability        │ 33 Data Lifecycle
 4  Agent System        │ 20 Autonomous Loop    │ 34 Distributed Execution
 5  Tool Layer          │ 21 Governance         │ 35 Capability Discovery
 6  OS Layer            │ 22 Human Approval     │ 36 Experience Replay
 7  Software Dev        │ 23 State Management   │ 37 Goal Manager
 8  Execution System    │ 24 Data Validation    │ 38 Long-Horizon Planning
 9  Learning System     │ 25 Evaluation         │ 39 Attention & Focus
10  Reflection          │ 26 Budget Control     │ 40 Temporal Reasoning
11  Self-Repair         │ 27 Environment Model  │ 41 Causal Reasoning
12  Self-Improvement    │ 28 Sandbox            │ 42 Ethics
13  Package Manager     │ 29 Skill Library      │ 43 Social Model
14  Multilingual        │ 30 Task Decomposition │ 44 Hardware Layer
15  Communication       │                       │ 45 Identity & Self-Model
16  Security            │                       │ 46 Knowledge Verification
```

Подробное описание каждого слоя — в файле `архитектура автономного Агента.txt`.

---

## Быстрый старт

### 1. Клонирование

```bash
git clone https://github.com/YOUR_USERNAME/agent.git
cd agent
```

### 2. Настройка окружения

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

pip install -r config/requirements.txt
```

### 3. Конфигурация

Скопируйте шаблон и заполните свои ключи:

```bash
cp config/.env.example .env
```

**Обязательные переменные:**

| Переменная       | Описание                       |
| ---------------- | ------------------------------ |
| `OPENAI_API_KEY` | Ключ OpenAI API (основной LLM) |

**Опциональные (расширяют функциональность):**

| Переменная | Описание |
| - | - |
| `ANTHROPIC_API_KEY` | Claude Opus 4 для тяжёлых задач |
| `TELEGRAM` | Токен Telegram бота |
| `TELEGRAM_ALERTS_CHAT_ID` | Chat ID для алертов |
| `GITHUB` | GitHub PAT (для инструментов) |
| `HF_TOKEN` | HuggingFace (для локальных моделей) |
| `WEB_TOKEN` | Токен Web UI (генерируется автоматически) |
| `WEB_PORT` | Порт Web UI (по умолчанию 8000) |

Полный список переменных — в `config/.env.example`.

### 4. Запуск

```bash
# Интерактивный режим (REPL)
python agent.py

# Одноразовое выполнение цели
python agent.py "напиши функцию сортировки и тесты к ней"

# Автономный цикл (N итераций)
python agent.py --loop "изучить Python asyncio" --cycles 5

# Только Telegram бот
python agent.py --bot
```

Web-интерфейс автоматически запускается на `http://localhost:8000`.

---

## Режимы работы

### Интерактивный (REPL)

```bash
python agent.py
```

Диалог с агентом в терминале. Поддерживает многоходовую беседу с памятью.

### Одноразовая цель

```bash
python agent.py "создать REST API на FastAPI с авторизацией"
```

Агент выполняет цель и завершается.

### Автономный цикл

```bash
python agent.py --loop "освоить machine learning" --cycles 10 --delay 2.0
```

Агент работает в цикле: goal → plan → act → evaluate → learn.

### Telegram бот

```bash
python agent.py --bot
```

Полноценный интерфейс через Telegram:

- `/start` — приветствие · `/goal <текст>` — цель · `/run <задача>` — выполнить
- `/search <запрос>` — поиск · `/verify <факт>` — верификация · `/status` — состояние
- `/budget` — расход токенов · `/stop` — остановить цикл
- 🎤 Голосовые сообщения · 📄 Документы · 🖼 Фото — всё поддерживается

### Web UI

Автоматически доступен на `http://localhost:8000` при любом режиме запуска.

- Чат с агентом в браузере
- Загрузка файлов (PDF, DOCX, код, изображения)
- Панель активности в реальном времени (SSE)
- Кросс-канальная синхронизация с Telegram

---

## Docker

```bash
# Сборка hardened-контейнера
docker build -f Dockerfile.sandbox -t agent-sandbox .

# Запуск (read-only filesystem, unprivileged user)
docker run --read-only --tmpfs /tmp:rw,noexec,nosuid \
  --env-file .env \
  -p 8000:8000 \
  agent-sandbox
```

Песочница удаляет опасные утилиты (`curl`, `wget`, `ssh`, `apt`), запускает агента от непривилегированного пользователя с read-only файловой системой.

---

## Инструменты агента

| Инструмент | Описание |
| - | - |
| `terminal` | Выполнение shell-команд |
| `file_system` | Чтение/запись файлов |
| `python` | Исполнение Python-кода |
| `web_search` | Поиск в интернете (DuckDuckGo) |
| `browser` | Headless-браузер (Playwright) |
| `github` | Работа с GitHub (issues, PR, код) |
| `docker` | Управление контейнерами |
| `database` | SQL-запросы |
| `blender` | 3D-моделирование |
| `cad` | OpenSCAD-модели |
| `figma` | Дизайн через Figma API |
| `image_edit` | Редактирование изображений |
| `video_edit` | Редактирование видео |
| `voice_call` | Голосовые звонки |
| `mobile` | Управление Android (ADB/Appium) |
| `upwork` | Мониторинг Upwork-заказов |

---

## Безопасность

| Модуль | Функция |
| - | - |
| `safety/security.py` | Контроль доступа, ограничения действий |
| `safety/ethics.py` | Этические ограничения |
| `safety/governance.py` | Политики и правила |
| `safety/human_approval.py` | Запрос подтверждения у человека |
| `safety/content_fence.py` | Фильтрация вывода |
| `safety/secrets_proxy.py` | Изоляция секретов |
| `safety/hardening.py` | Защита рантайма |
| `environment/sandbox.py` | Песочница для исполнения кода |

---

## Тестирование

```bash
# Предполётная проверка (7 критических тестов)
python preflight.py

# Быстрая проверка
python preflight.py --quick

# Полный тест-сьют (41 файл)
python -m pytest tests/ -v

# Дымовые тесты
python tests/smoke_runner.py
```

---

## Структура проекта

```text
agent/
├── agent.py                 # Главный файл: сборка 46 слоёв + CLI
├── preflight.py             # Предполётная проверка
├── Dockerfile.sandbox       # Hardened Docker-контейнер
├── config/
│   ├── .env.example         # Шаблон переменных окружения
│   ├── requirements.txt     # Зависимости по слоям
│   └── requirements.lock    # Зафиксированные версии
├── core/                    # Когнитивное ядро, Goal Manager, Identity
├── llm/                     # LLM-бэкенды (OpenAI, Claude, HuggingFace, Local)
├── knowledge/               # Система знаний, Vector Store, Verification
├── perception/              # Восприятие (web, PDF, image, speech)
├── reasoning/               # Рассуждения (каузальные, временные, адаптивные)
├── execution/               # Исполнение задач, маршрутизация
├── tools/                   # 15+ инструментов (browser, github, docker, ...)
├── communication/           # Telegram бот, Web UI, ChannelBridge
├── safety/                  # 7 модулей безопасности
├── learning/                # Обучение, Experience Replay
├── reflection/              # Рефлексия и самоанализ
├── self_repair/             # Самовосстановление
├── self_improvement/        # Самоулучшение
├── monitoring/              # Логирование, алерты
├── loop/                    # Автономный цикл, оркестрация
├── agents/                  # Мульти-агентная система
├── skills/                  # Библиотека навыков
├── social/                  # Социальная модель, эмоции
├── attention/               # Фокус и приоритизация внимания
├── environment/             # Модель среды, sandbox
├── hardware/                # Аппаратный слой (CPU, RAM, GPU)
├── validation/              # Валидация данных
├── evaluation/              # Оценка и бенчмарки
├── multilingual/            # Мультиязычность
├── resources/               # Бюджет и ресурсы
├── state/                   # Состояние и сессии
└── tests/                   # 41 тест-файл
```

---

## Аргументы CLI

| Аргумент | Описание | По умолчанию |
| - | - | - |
| `"цель"` | Текст цели (позиционный) | — |
| `--model` | Модель LLM | `gpt-5.1` |
| `--loop GOAL` | Автономный цикл с целью | — |
| `--delay` | Пауза между циклами (секунды) | `1.0` |
| `--cycles` | Количество итераций | `0` (∞) |
| `--log FILE` | Файл лога | — |
| `--bot` | Только Telegram бот | `false` |

---

## Требования

- Python 3.13+
- Windows / Linux / macOS
- OpenAI API ключ (обязательно)
- ~4 ГБ RAM (без локальной модели)
- ~16 ГБ RAM + GPU (с локальной Qwen моделью)

---

## Лицензия

[MIT License](LICENSE)
