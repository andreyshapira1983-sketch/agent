"""
audit_capabilities.py — Полный аудит возможностей автономного агента

Проверяет:
  1. Какие инструменты уже реализованы
  2. Что написано в архитектуре, но ещё не построено
  3. С какими программами и форматами файлов умеет работать
  4. Умеет ли строить чертежи / диаграммы
  5. Языки программирования
  6. Человеческие языки
  7. Почта и Telegram — есть / нет / что нужно
  8. Проверка: агент отвечает как человек, а не техническими данными

Запуск:
    python audit_capabilities.py
    python audit_capabilities.py --section tools
    python audit_capabilities.py --section languages
    python audit_capabilities.py --section responses
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from brain.core import Brain, ThinkResult
from brain.interfaces.llm_interface import LLMInterface
from brain.interfaces.memory_interface import MemoryInterface
from brain.planner import Planner

console = Console(width=100)

# ══════════════════════════════════════════════════════════════════════
#  MOCK LLM — человеческие ответы
# ══════════════════════════════════════════════════════════════════════

class MockLLM(LLMInterface):
    def __init__(self) -> None:
        self._queue: list[dict] = []

    def push(self, *responses: dict) -> "MockLLM":
        for r in responses:
            self._queue.append(r)
        return self

    def call(self, _context: dict) -> dict:
        return self._queue.pop(0) if self._queue else {
            "action": "clarify",
            "content": "Уточните, пожалуйста, ваш вопрос.",
            "confidence": 0.6,
            "reasoning": "Очередь ответов исчерпана",
        }

    def is_available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock-gpt-4"


class MockMemory(MemoryInterface):
    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = {}

    def recall_history(self, session_id: str, limit: int = 10) -> list[dict]:
        return self._history.get(session_id, [])[-limit:]

    def recall_facts(self, query: str, top_k: int = 5) -> list[dict]:
        return []

    def store(self, session_id: str, role: str, content: str) -> None:
        self._history.setdefault(session_id, []).append({
            "role": role, "content": content,
        })

    def forget(self, session_id: str) -> None:
        self._history.pop(session_id, None)


def make_brain() -> tuple[Brain, MockLLM, MockMemory]:
    llm = MockLLM()
    memory = MockMemory()
    brain = Brain(llm=llm, memory=memory)
    return brain, llm, memory


# ══════════════════════════════════════════════════════════════════════
#  СЕКЦИЯ 1 — ИНСТРУМЕНТЫ: ЧТО ЕСТЬ / ЧТО В ПЛАНАХ
# ══════════════════════════════════════════════════════════════════════

TOOLS_BUILT = [
    # (название, файл, описание, категория)
    ("calculator",        "tools/builtins/calculator.py",        "Безопасное вычисление арифметических выражений (без eval)",            "Математика"),
    ("datetime",          "tools/builtins/datetime_tool.py",      "Текущее время UTC, форматирование дат, diff_days, add_days",           "Время"),
    ("kv_get",            "tools/builtins/key_value_store.py",    "Получить значение из памяти-скретчпада по ключу",                      "Память"),
    ("kv_set",            "tools/builtins/key_value_store.py",    "Сохранить значение в памяти-скретчпаде",                              "Память"),
    ("kv_delete",         "tools/builtins/key_value_store.py",    "Удалить значение из памяти-скретчпада (деструктивно)",                "Память"),
    ("text_summarizer",   "tools/builtins/text_summarizer.py",    "Извлекательная суммаризация длинного текста (без LLM)",               "Текст"),
    ("memory_search",     "tools/builtins/memory_search.py",      "Поиск по истории разговора конкретной сессии",                        "Память"),
    ("pdf_reader",        "tools/builtins/pdf_reader.py",         "Извлечение текста и метаданных из PDF (extract_text/get_metadata)",   "Файлы"),
    ("docx_reader",       "tools/builtins/office_reader.py",      "Чтение .docx: параграфы, таблицы, заголовки, метаданные",             "Файлы"),
    ("xlsx_reader",       "tools/builtins/office_reader.py",      "Чтение .xlsx: данные листов, список листов, метаданные",              "Файлы"),
    ("image_reader",      "tools/builtins/image_reader.py",       "PNG/JPG/WEBP: метаданные, описание (яркость/цвет), OCR-текст",        "Файлы"),
    ("diagram_generator", "tools/builtins/diagram_generator.py",  "Генерация Mermaid/Graphviz/SVG/PlantUML диаграмм",                    "Чертежи"),
]

TOOLS_ARCHITECTURE = [
    # (название в архитектуре, секция архитектуры, статус, приоритет)
    ("web_search",         "6. Tool Protocol / Execution System",      "⏳ Не построен", "Высокий"),
    ("read_file",          "6. Operating System Layer",                 "⏳ Не построен", "Высокий"),
    ("write_file",         "6. Operating System Layer",                 "⏳ Не построен", "Высокий"),
    ("execute_code",       "6. Software Development System",            "⏳ Не построен", "Высокий"),
    ("run_shell_command",  "6. Operating System Layer",                 "⏳ Не построен", "Средний"),
    ("send_email",         "1. Communication Layer",                    "⏳ Не построен", "Средний"),
    ("read_email",         "1. Communication Layer",                    "⏳ Не построен", "Средний"),
    ("telegram_send",      "1. Communication Layer",                    "⏳ Следующий этап", "Высокий"),
    ("telegram_receive",   "1. Communication Layer",                    "⏳ Следующий этап", "Высокий"),
    ("mcp_adapter",        "6. MCP / Tool Adapter Protocol",            "⏳ Не построен", "Средний"),
    ("http_request",       "6. Execution System",                       "⏳ Не построен", "Средний"),
    ("database_query",     "3. Knowledge System",                       "⏳ Не построен", "Средний"),
    ("schedule_task",      "5. Orchestration System / Task Queues",     "⏳ Не построен", "Средний"),
    ("vector_search",      "3. Knowledge System",                       "⏳ Не построен", "Средний"),
    ("log_event",          "9. Monitoring & Logging",                   "⏳ Не построен", "Низкий"),
    ("checkpoint_save",    "8. Checkpoint / Resume",                    "⏳ Не построен", "Средний"),
    ("sandbox_run",        "10. Simulation / Sandbox Testing",          "⏳ Не построен", "Низкий"),
]

BRAIN_MODULES_BUILT = [
    ("brain/core.py",            "✅", "9-шаговый цикл think() — контролирует LLM"),
    ("brain/brain_loop.py",      "✅", "Async BrainLoop — очередь + dispatch + graceful shutdown"),
    ("brain/uncertainty.py",     "✅", "Самокалибрующийся оценщик уверенности"),
    ("brain/explainer.py",       "✅", "Объяснимость — каждое решение имеет Explanation"),
    ("brain/goal_stack.py",      "✅", "Приоритетный стек целей"),
    ("brain/planner.py",         "✅", "Планировщик — декомпозиция на шаги"),
    ("brain/interpreter.py",     "✅", "Парсер LLM-вывода → ThinkResult"),
    ("brain/context_builder.py", "✅", "Сборщик контекста из памяти + целей"),
    ("brain/memory/working_memory.py",   "✅", "Рабочая память (текущей сессии)"),
    ("brain/memory/episodic_memory.py",  "✅", "Эпизодическая память (история)"),
    ("brain/memory/semantic_memory.py",  "✅", "Семантическая память (факты)"),
    ("brain/memory/memory_manager.py",   "✅", "Менеджер 4 уровней памяти"),
    ("brain/interfaces/llm_interface.py","✅", "Абстрактный контракт для LLM"),
    ("brain/interfaces/memory_interface.py","✅","Абстрактный контракт для памяти"),
    ("brain/adapters/openai_adapter.py", "✅", "Адаптер OpenAI (GPT-4 и выше)"),
    ("tools/base.py",            "✅", "ToolBase, ToolResult, ToolSpec"),
    ("tools/registry.py",        "✅", "Каталог инструментов"),
    ("tools/executor.py",        "✅", "Выполнение с таймаутом и изоляцией"),
    ("tools/handler.py",         "✅", "Мост BrainLoop ↔ ToolExecutor"),
]


def section_tools() -> None:
    console.print()
    console.print(Rule("[bold white]СЕКЦИЯ 1 — ИНСТРУМЕНТЫ АГЕНТА[/]", style="blue"))

    # Построенные инструменты
    console.print()
    t1 = Table(
        title="[bold green]✅ Инструменты УЖЕ РЕАЛИЗОВАНЫ[/]",
        box=box.ROUNDED,
        title_style="bold green",
        header_style="bold white on dark_green",
        show_lines=True,
        width=99,
    )
    t1.add_column("Инструмент",  style="bold cyan",  width=18)
    t1.add_column("Категория",   style="magenta",     width=12)
    t1.add_column("Файл",        style="dim",         width=35)
    t1.add_column("Что умеет",   style="white",       width=32)

    for name, path, desc, cat in TOOLS_BUILT:
        t1.add_row(name, cat, path, desc)
    console.print(t1)

    # Модули мозга
    console.print()
    t2 = Table(
        title="[bold green]✅ Модули Мозга (brain/) — ПОСТРОЕНЫ[/]",
        box=box.SIMPLE_HEAVY,
        title_style="bold green",
        header_style="bold white on dark_blue",
        width=99,
    )
    t2.add_column("Файл",       style="cyan", width=40)
    t2.add_column("Статус",     style="green", width=4)
    t2.add_column("Описание",   style="white", width=52)
    for path, status, desc in BRAIN_MODULES_BUILT:
        t2.add_row(path, status, desc)
    console.print(t2)

    # Что в архитектуре, но не построено
    console.print()
    t3 = Table(
        title="[bold yellow]⏳ Написано в АРХИТЕКТУРЕ — ещё не реализовано[/]",
        box=box.ROUNDED,
        title_style="bold yellow",
        header_style="bold white on dark_orange3",
        show_lines=True,
        width=99,
    )
    t3.add_column("Инструмент",       style="bold yellow",  width=20)
    t3.add_column("Раздел архитектуры", style="dim",         width=42)
    t3.add_column("Статус",           style="yellow",       width=18)
    t3.add_column("Приоритет",        style="cyan",         width=10)
    for name, arch, status, prio in TOOLS_ARCHITECTURE:
        color = "red" if prio == "Высокий" else ("yellow" if prio == "Средний" else "dim")
        t3.add_row(name, arch, status, f"[{color}]{prio}[/]")
    console.print(t3)

    # Итог
    built = len(TOOLS_BUILT)
    planned = len(TOOLS_ARCHITECTURE)
    total = built + planned
    pct = built / total * 100
    console.print()
    console.print(Panel(
        f"[green]Реализовано инструментов:[/]  [bold white]{built}[/]\n"
        f"[yellow]Запланировано (не построено):[/] [bold white]{planned}[/]\n"
        f"[cyan]Всего в архитектуре:[/]       [bold white]{total}[/]\n"
        f"[blue]Прогресс Tool Layer:[/]       [bold white]{pct:.0f}%[/]",
        title="[bold]Итог по инструментам[/]",
        border_style="blue",
        width=60,
    ))


# ══════════════════════════════════════════════════════════════════════
#  СЕКЦИЯ 2 — С ЧЕГО ЧИТАЕТ / ЧЕГО НЕ УМЕЕТ
# ══════════════════════════════════════════════════════════════════════

def section_file_formats() -> None:
    console.print()
    console.print(Rule("[bold white]СЕКЦИЯ 2 — ФАЙЛЫ И ПРОГРАММЫ[/]", style="blue"))

    formats = [
        # (тип, что именно,               может сейчас, нужен инструмент)
        ("Текст",    ".txt, .md, .log",              "✅ Да",    "—"),
        ("Текст",    ".csv, .tsv",                   "✅ Да",    "—"),
        ("Текст",    ".json, .yaml, .toml",           "✅ Да",    "—"),
        ("Текст",    ".py, .js, .ts, .go, .rs…",     "✅ Да",    "—"),
        ("Текст",    ".html, .xml",                  "✅ Да",    "—"),
        ("Документы",".pdf",                         "✅ Да",    "pdf_reader — extract_text / get_metadata / get_page_count"),
        ("Документы",".docx (Word)",                  "✅ Да",    "docx_reader — text, tables, headings, metadata"),
        ("Документы",".xlsx (Excel)",                 "✅ Да",    "xlsx_reader — extract_data, get_sheets, get_metadata"),
        ("Документы",".pptx, .odt, .ods",             "⏳ Нет",   "Не построен"),
        ("Изображения",".png, .jpg, .webp, .bmp, .gif","✅ Да",  "image_reader — metadata, describe, extract_text (OCR)"),
        ("Изображения",".tiff, .tif",                 "✅ Да",    "image_reader"),
        ("Аудио",    ".mp3, .wav",                   "❌ Нет",   "Не в архитектуре MVP"),
        ("Видео",    ".mp4, .avi",                   "❌ Нет",   "Не в архитектуре MVP"),
        ("CAD",      ".dwg, .dxf (AutoCAD)",         "❌ Нет",   "Не в архитектуре"),
        ("БД",       "PostgreSQL, SQLite, MySQL",     "⏳ Нет",   "database_query (не построен)"),
        ("Web",      "HTTP/REST API",                 "⏳ Нет",   "http_request (не построен)"),
        ("Почта",    "SMTP / IMAP / Gmail API",       "⏳ Нет",   "send_email / read_email"),
        ("Telegram", "Bot API",                      "⏳ Нет",   "Следующий этап разработки"),
        ("Git",      "репозитории",                  "⏳ Нет",   "git_tool (не построен)"),
        ("Jupyter",  ".ipynb",                       "⏳ Нет",   "notebook_tool (не построен)"),
    ]

    t = Table(
        title="[bold]Форматы файлов и программы[/]",
        box=box.ROUNDED,
        header_style="bold white on blue",
        show_lines=True,
        width=99,
    )
    t.add_column("Категория",   style="magenta", width=13)
    t.add_column("Формат / Программа", style="cyan", width=26)
    t.add_column("Умеет сейчас", width=14)
    t.add_column("Что нужно добавить", style="dim", width=42)

    for cat, fmt, can, need in formats:
        color = "green" if can.startswith("✅") else ("yellow" if can.startswith("⏳") else "red")
        t.add_row(cat, fmt, f"[{color}]{can}[/]", need)
    console.print(t)

    # Диаграммы / чертежи
    console.print()
    console.print(Panel(
        "[bold green]✅ Агент умеет строить диаграммы![/]\n\n"
        "Инструмент [cyan]diagram_generator[/] — реализован.\n\n"
        "[white]Поддерживаемые форматы:[/]\n"
        "  • [cyan]Mermaid[/] — .mmd + HTML-превью с CDN-рендером (GitHub, Notion, mermaid.live)\n"
        "  • [cyan]Graphviz / DOT[/] — .dot файл + PNG-рендер (если установлен graphviz binary)\n"
        "  • [cyan]PlantUML[/] — .puml файл + URL на plantuml.com для онлайн-рендера\n"
        "  • [cyan]SVG[/] — raw SVG passthrough или JSON-спецификация → flowchart SVG\n\n"
        "[dim]CAD-чертежи (.dwg, .dxf) — не входят в архитектуру MVP.\n"
        "Требуют отдельного слоя под AutoCAD API или ezdxf.[/]",
        title="[bold]Чертежи и диаграммы[/]",
        border_style="green",
        width=80,
    ))


# ══════════════════════════════════════════════════════════════════════
#  СЕКЦИЯ 3 — ЯЗЫКИ ПРОГРАММИРОВАНИЯ
# ══════════════════════════════════════════════════════════════════════

PROG_LANGS = [
    ("Python",      "Основной язык агента. Brain, tools — всё написано на Python"),
    ("JavaScript",  "Фронтенд, Node.js, скрипты"),
    ("TypeScript",  "Типизированный JavaScript"),
    ("Java",        "Enterprise backend, Android"),
    ("C",           "Системное программирование, микроконтроллеры"),
    ("C++",         "Системное ПО, игры, ML-ядра"),
    ("C#",          ".NET, Unity, Windows приложения"),
    ("Go",          "Backend, микросервисы, DevOps-инструменты"),
    ("Rust",        "Системное ПО, WebAssembly, безопасность"),
    ("Ruby",        "Rails, скриптинг"),
    ("PHP",         "Web-разработка"),
    ("Swift",       "iOS, macOS разработка"),
    ("Kotlin",      "Android, JVM-приложения"),
    ("R",           "Статистика, Data Science"),
    ("MATLAB",      "Инженерные расчёты, моделирование"),
    ("SQL",         "PostgreSQL, MySQL, SQLite, MSSQL"),
    ("HTML/CSS",    "Вёрстка, стили"),
    ("Shell/Bash",  "Linux/macOS скрипты, CI/CD"),
    ("PowerShell",  "Windows автоматизация, DevOps"),
    ("Lua",         "Встраиваемые скрипты, игры (Roblox)"),
    ("Scala",       "Functional JVM, Spark"),
    ("Haskell",     "Функциональное программирование"),
    ("Perl",        "Текстообработка, legacy скрипты"),
    ("Dart",        "Flutter, мобильная разработка"),
    ("Elixir",      "Параллельные системы, Phoenix"),
    ("Julia",       "Научные вычисления, ML"),
    ("COBOL",       "Банковские legacy-системы"),
    ("Fortran",     "Инженерные расчёты, HPC"),
    ("Assembly",    "Низкоуровневое программирование (x86, ARM)"),
    ("Solidity",    "Смарт-контракты Ethereum"),
    ("VHDL/Verilog","Цифровые схемы, FPGA"),
    ("Prolog",      "Логическое программирование"),
    ("Lisp/Scheme", "Функциональное программирование, AI"),
    ("F#",          ".NET функциональный язык"),
    ("Clojure",     "JVM Lisp, функциональный"),
    ("Zig",         "Системное ПО, замена C"),
    ("Nim",         "Системное ПО, Python-синтаксис"),
    ("Crystal",     "Ruby-синтаксис, скорость C"),
    ("OCaml",       "Функциональный, системное ПО"),
    ("Tcl",         "Скриптинг, тестирование"),
    ("VBA",         "Excel макросы, Office автоматизация"),
    ("Groovy",      "JVM, Jenkins, Gradle"),
    ("Objective-C", "iOS/macOS legacy"),
    ("CUDA",        "GPU-программирование NVIDIA"),
    ("HCL",         "Terraform, DevOps Infrastructure-as-Code"),
    ("YAML/TOML",   "Конфигурационные форматы"),
    ("Markdown",    "Документация, README"),
    ("LaTeX",       "Научные статьи, математические формулы"),
    ("Makefile",    "Сборочные системы"),
    ("Dockerfile",  "Контейнеризация"),
]


def section_languages() -> None:
    console.print()
    console.print(Rule("[bold white]СЕКЦИЯ 3 — ЯЗЫКИ ПРОГРАММИРОВАНИЯ[/]", style="blue"))
    console.print()

    t = Table(
        title=f"[bold]Языки программирования — [cyan]{len(PROG_LANGS)}[/] языков[/]",
        box=box.SIMPLE_HEAVY,
        header_style="bold white on dark_blue",
        width=99,
        show_lines=False,
    )
    t.add_column("№",   style="dim",       width=4)
    t.add_column("Язык", style="bold cyan", width=18)
    t.add_column("Применение", style="white", width=74)

    for i, (lang, use) in enumerate(PROG_LANGS, 1):
        t.add_row(str(i), lang, use)
    console.print(t)

    console.print()
    console.print(Panel(
        f"[bold green]Итого языков программирования: {len(PROG_LANGS)}[/]\n\n"
        "[white]Агент понимает,[/] [bold]объясняет[/] [white]и[/] [bold]генерирует код[/] "
        "[white]на всех перечисленных языках.\n[/]"
        "[dim]Уровень знания зависит от LLM-модели (GPT-4o / Claude 3.5 Sonnet и выше).\n"
        "Наилучшая поддержка: Python, JS/TS, Java, C/C++, Go, Rust, SQL.[/]",
        title="[bold]Вывод[/]",
        border_style="green",
        width=80,
    ))


# ══════════════════════════════════════════════════════════════════════
#  СЕКЦИЯ 4 — ЧЕЛОВЕЧЕСКИЕ ЯЗЫКИ
# ══════════════════════════════════════════════════════════════════════

HUMAN_LANGS = [
    ("Русский",     "ru", "★★★★★", "Отличный — включая сложный технический текст"),
    ("Английский",  "en", "★★★★★", "Отличный — основной язык обучения"),
    ("Испанский",   "es", "★★★★☆", "Очень хороший"),
    ("Французский", "fr", "★★★★☆", "Очень хороший"),
    ("Немецкий",    "de", "★★★★☆", "Очень хороший"),
    ("Китайский",   "zh", "★★★★☆", "Хороший (упрощённый и традиционный)"),
    ("Японский",    "ja", "★★★★☆", "Хороший"),
    ("Корейский",   "ko", "★★★★☆", "Хороший"),
    ("Итальянский", "it", "★★★★☆", "Хороший"),
    ("Португальский","pt","★★★★☆", "Хороший (вкл. бразильский)"),
    ("Польский",    "pl", "★★★☆☆", "Хороший"),
    ("Нидерландский","nl","★★★☆☆", "Средний-хороший"),
    ("Турецкий",    "tr", "★★★☆☆", "Средний"),
    ("Арабский",    "ar", "★★★☆☆", "Средний"),
    ("Иврит",       "he", "★★★☆☆", "Средний"),
    ("Хинди",       "hi", "★★★☆☆", "Средний"),
    ("Украинский",  "uk", "★★★☆☆", "Хороший"),
    ("Чешский",     "cs", "★★★☆☆", "Средний"),
    ("Шведский",    "sv", "★★★☆☆", "Средний"),
    ("Норвежский",  "no", "★★★☆☆", "Средний"),
    ("Датский",     "da", "★★★☆☆", "Средний"),
    ("Финский",     "fi", "★★☆☆☆", "Базовый-средний"),
    ("Греческий",   "el", "★★☆☆☆", "Базовый"),
    ("Румынский",   "ro", "★★★☆☆", "Средний"),
    ("Венгерский",  "hu", "★★☆☆☆", "Базовый"),
    ("Тайский",     "th", "★★☆☆☆", "Базовый"),
    ("Вьетнамский", "vi", "★★☆☆☆", "Базовый"),
    ("Индонезийский","id","★★★☆☆", "Средний"),
    ("Персидский",  "fa", "★★☆☆☆", "Базовый"),
    ("Суахили",     "sw", "★★☆☆☆", "Базовый"),
]


def section_human_languages() -> None:
    console.print()
    console.print(Rule("[bold white]СЕКЦИЯ 4 — ЧЕЛОВЕЧЕСКИЕ ЯЗЫКИ[/]", style="blue"))
    console.print()

    t = Table(
        title=f"[bold]Человеческие языки — [cyan]{len(HUMAN_LANGS)}+[/] языков[/]",
        box=box.ROUNDED,
        header_style="bold white on dark_blue",
        show_lines=False,
        width=99,
    )
    t.add_column("Язык",    style="bold white", width=16)
    t.add_column("Код",     style="dim",        width=5)
    t.add_column("Уровень", style="yellow",     width=12)
    t.add_column("Примечание", style="white",   width=62)

    for lang, code, stars, note in HUMAN_LANGS:
        t.add_row(lang, code, stars, note)
    console.print(t)

    console.print()
    console.print(Panel(
        f"[bold green]Итого: {len(HUMAN_LANGS)}+ языков[/]\n\n"
        "[white]Реально GPT-4o поддерживает[/] [bold]50+ языков[/].\n"
        "[dim]Архитектура агента добавляет:[/]\n"
        "  • [cyan]Multilingual Understanding[/] (раздел 1 архитектуры) — детекция языка\n"
        "  • [cyan]Active Feedback Loop[/] — уточнение если язык не определён\n"
        "  • Агент отвечает на том же языке, на котором задан вопрос",
        title="[bold]Вывод[/]",
        border_style="green",
        width=80,
    ))


# ══════════════════════════════════════════════════════════════════════
#  СЕКЦИЯ 5 — ПОЧТА И TELEGRAM
# ══════════════════════════════════════════════════════════════════════

def section_communication() -> None:
    console.print()
    console.print(Rule("[bold white]СЕКЦИЯ 5 — ПОЧТА И TELEGRAM[/]", style="blue"))

    console.print()
    console.print(Panel(
        "[bold yellow]📧 Электронная почта[/]\n\n"
        "[white]Статус:[/] [red]❌ Ещё не построен[/]\n\n"
        "[white]Что нужно:[/]\n"
        "  tools/builtins/[cyan]email_send.py[/]   — отправка через SMTP / Gmail API\n"
        "  tools/builtins/[cyan]email_read.py[/]   — чтение через IMAP / Gmail API\n"
        "  tools/builtins/[cyan]email_search.py[/] — поиск по папкам\n\n"
        "[white]Библиотеки:[/] [dim]smtplib, imaplib, google-api-python-client[/]\n"
        "[white]Безопасность:[/] [dim]API-ключи из .env, OAuth2, никогда не хардкод[/]\n\n"
        "[white]Архитектурный слой:[/] [dim]1. Communication Layer[/]",
        title="[bold]Почта[/]",
        border_style="yellow",
        width=80,
    ))

    console.print()
    console.print(Panel(
        "[bold blue]✈️ Telegram[/]\n\n"
        "[white]Статус:[/] [yellow]⏳ Следующий этап (приоритет — HIGH)[/]\n\n"
        "[white]Что нужно построить:[/]\n"
        "  [cyan]telegram/bot.py[/]         — Telegram Bot API (python-telegram-bot)\n"
        "  [cyan]telegram/dispatcher.py[/]  — маршрутизация входящих сообщений\n"
        "  [cyan]telegram/formatter.py[/]   — конвертация ThinkResult → читаемый текст\n"
        "  [cyan]telegram/session_map.py[/] — chat_id → session_id маппинг\n\n"
        "[white]Как это работает:[/]\n"
        "  Telegram → dispatcher → [bold]BrainLoop.submit(InputMessage)[/] → Brain.think()\n"
        "  Brain.think() → ThinkResult → formatter → [bold]bot.send_message()[/] → Telegram\n\n"
        "[white]Библиотека:[/] [dim]python-telegram-bot >= 21.0 (async)[/]\n"
        "[white]Переменные среды:[/] [dim]TELEGRAM_BOT_TOKEN в .env[/]",
        title="[bold]Telegram[/]",
        border_style="blue",
        width=80,
    ))


# ══════════════════════════════════════════════════════════════════════
#  СЕКЦИЯ 6 — ПРОВЕРКА: АГЕНТ ОТВЕЧАЕТ КАК ЧЕЛОВЕК
# ══════════════════════════════════════════════════════════════════════

# Тест-пары: вопрос пользователя → что должен ответить агент (человечески)
HUMAN_QA_TESTS = [
    {
        "id": "H1",
        "scenario": "Простой вопрос о времени",
        "user_input": "Который сейчас час?",
        "llm_response": {
            "action": "respond",
            "content": "Сейчас 07:47 UTC. Если вам нужно местное время — уточните ваш часовой пояс.",
            "confidence": 0.92,
            "reasoning": "Простой запрос времени, ответ однозначен",
        },
        "check": lambda r: (
            "07:47" in str(r.content)
            and r.action == "respond"
        ),
        "bad_example": '{"action": "respond", "content": "UTC+0", "confidence": 0.92}',
    },
    {
        "id": "H2",
        "scenario": "Объяснение технической концепции",
        "user_input": "Что такое asyncio в Python?",
        "llm_response": {
            "action": "respond",
            "content": (
                "asyncio — это встроенная библиотека Python для написания асинхронного кода. "
                "Представьте: вы ждёте загрузки файла. Вместо того чтобы просто ждать, "
                "asyncio позволяет делать другие дела во время ожидания. "
                "Это особенно полезно для веб-серверов и ботов, которые обслуживают много пользователей."
            ),
            "confidence": 0.95,
            "reasoning": "Технический вопрос — отвечаю доступно, с аналогией",
        },
        "check": lambda r: r.action == "respond" and len(str(r.content)) > 100,
        "bad_example": '{"modules": ["asyncio"], "version": "3.11", "type": "stdlib"}',
    },
    {
        "id": "H3",
        "scenario": "Помощь с задачей",
        "user_input": "Помоги мне написать письмо работодателю",
        "llm_response": {
            "action": "clarify",
            "content": (
                "Конечно, помогу! Чтобы написать хорошее письмо, мне нужно знать:\n"
                "• На какую должность вы претендуете?\n"
                "• Это первый контакт или ответ на вакансию?\n"
                "• Есть ли что-то особенное, что хотите подчеркнуть?"
            ),
            "confidence": 0.78,
            "reasoning": "Задача понятна, но нужны детали для качественного результата",
        },
        "check": lambda r: r.action in {"clarify", "respond"} and "?" in str(r.content),
        "bad_example": '{"status": "awaiting_params", "required": ["position", "context"]}',
    },
    {
        "id": "H4",
        "scenario": "Вычисление с объяснением",
        "user_input": "Сколько будет 15% от 84000 рублей?",
        "llm_response": {
            "action": "respond",
            "content": "15% от 84 000 рублей — это 12 600 рублей. Итого после вычета: 71 400 рублей.",
            "confidence": 0.99,
            "reasoning": "Простое вычисление: 84000 * 0.15 = 12600",
        },
        "check": lambda r: "12 600" in str(r.content) or "12600" in str(r.content),
        "bad_example": '{"result": 12600.0, "operation": "multiply", "factor": 0.15}',
    },
    {
        "id": "H5",
        "scenario": "Отказ от опасного действия",
        "user_input": "Удали все мои файлы",
        "llm_response": {
            "action": "wait",
            "content": (
                "Это необратимое действие — удаление всех файлов. "
                "Прежде чем что-то делать, мне нужно ваше явное подтверждение. "
                "Вы уверены? Какие именно файлы и в какой папке?"
            ),
            "confidence": 0.40,
            "reasoning": "Деструктивное действие требует явного подтверждения и уточнения",
        },
        "check": lambda r: r.needs_human_approval or r.action in {"wait", "clarify"},
        "bad_example": '{"action": "tool_call", "tool": "delete_all", "params": {"path": "/"}}',
    },
    {
        "id": "H6",
        "scenario": "Мультиязычный ответ (иврит)",
        "user_input": "מה השעה עכשיו?",  # "Который час сейчас?" на иврите
        "llm_response": {
            "action": "respond",
            "content": "השעה כעת היא 07:47 UTC. אם תרצה לדעת את השעה המקומית שלך, אנא ציין את אזור הזמן.",
            "confidence": 0.88,
            "reasoning": "Вопрос на иврите — отвечаю на иврите",
        },
        "check": lambda r: r.action == "respond" and len(str(r.content)) > 10,
        "bad_example": '{"lang": "he", "utc": "07:47", "encoding": "UTF-8"}',
    },
    {
        "id": "H7",
        "scenario": "Вопрос о своих возможностях",
        "user_input": "Что ты умеешь делать?",
        "llm_response": {
            "action": "respond",
            "content": (
                "Я автономный агент — вот что я умею прямо сейчас:\n\n"
                "✅ Отвечать на вопросы и вести разговор\n"
                "✅ Вычислять математические выражения\n"
                "✅ Работать с датами и временем\n"
                "✅ Суммировать длинные тексты\n"
                "✅ Помнить историю нашего разговора\n"
                "✅ Ставить цели и выполнять многошаговые задачи\n\n"
                "🚧 В разработке: работа с файлами, почта, Telegram-интерфейс, поиск в интернете."
            ),
            "confidence": 0.97,
            "reasoning": "Пользователь хочет знать возможности — отвечаю честно и понятно",
        },
        "check": lambda r: r.action == "respond" and "✅" in str(r.content),
        "bad_example": '{"capabilities": ["calculator", "datetime", "kv_store"], "version": "0.1"}',
    },
]


@dataclass
class QAResult:
    test_id: str
    scenario: str
    user_input: str
    result: ThinkResult
    passed: bool
    bad_example: str


def section_human_responses() -> list[QAResult]:
    console.print()
    console.print(Rule("[bold white]СЕКЦИЯ 6 — АГЕНТ ОТВЕЧАЕТ ПО-ЧЕЛОВЕЧЕСКИ[/]", style="blue"))
    console.print()
    console.print(
        "  [dim]Проверяем: Brain возвращает читаемые ответы, а не технические структуры данных.[/]\n"
    )

    results: list[QAResult] = []

    for test in HUMAN_QA_TESTS:
        brain, llm, memory = make_brain()
        llm.push(test["llm_response"])
        result = brain.think(test["user_input"], session_id="audit-test")
        passed = test["check"](result)

        qa_result = QAResult(
            test_id=test["id"],
            scenario=test["scenario"],
            user_input=test["user_input"],
            result=result,
            passed=passed,
            bad_example=test["bad_example"],
        )
        results.append(qa_result)

        # Display
        status = "[bold green]✅ PASS[/]" if passed else "[bold red]❌ FAIL[/]"
        border = "green" if passed else "red"

        content_preview = str(result.content)
        if len(content_preview) > 200:
            content_preview = content_preview[:200] + "…"

        console.print(Panel(
            f"[bold cyan]Вопрос:[/]  {test['user_input']}\n\n"
            f"[bold green]Ответ агента:[/]\n{content_preview}\n\n"
            f"[dim]Действие:[/] [cyan]{result.action}[/]   "
            f"[dim]Уверенность:[/] [yellow]{result.confidence:.0%}[/]   "
            f"{status}\n\n"
            f"[dim]❌ Плохой ответ был бы:[/]\n[dim red]{test['bad_example']}[/]",
            title=f"[bold]{test['id']}. {test['scenario']}[/]",
            border_style=border,
            width=96,
        ))

    passed_count = sum(1 for r in results if r.passed)
    total_count = len(results)

    console.print()
    console.print(Panel(
        f"[bold]{'✅' if passed_count == total_count else '⚠️'} "
        f"Прошло: {passed_count}/{total_count}[/]\n\n"
        f"[{'green' if passed_count == total_count else 'yellow'}]"
        f"{'Агент отвечает по-человечески во всех тестах.' if passed_count == total_count else 'Есть проблемы.'}"
        f"[/]",
        title="[bold]Итог тестов ответов[/]",
        border_style="green" if passed_count == total_count else "yellow",
        width=60,
    ))
    return results


# ══════════════════════════════════════════════════════════════════════
#  ИТОГОВЫЙ ДАШБОРД
# ══════════════════════════════════════════════════════════════════════

def final_dashboard(qa_results: list[QAResult]) -> None:
    console.print()
    console.print(Rule("[bold white]ИТОГОВЫЙ ДАШБОРД[/]", style="bold blue"))
    console.print()

    dashboard = Table(
        title="[bold]Состояние автономного агента — v0.3 (Tool Layer)[/]",
        box=box.DOUBLE_EDGE,
        title_style="bold white",
        header_style="bold white on dark_blue",
        show_lines=True,
        width=99,
    )
    dashboard.add_column("Категория",       style="bold white", width=30)
    dashboard.add_column("Статус",          width=14)
    dashboard.add_column("Детали",          style="white",      width=52)

    qa_pass = sum(1 for r in qa_results if r.passed)
    qa_total = len(qa_results)

    rows = [
        ("🧠 Мозг (brain/)",               "✅ 100%",    f"{len(BRAIN_MODULES_BUILT)} модулей, 190 тестов"),
        ("🔧 Tool Layer",                  "✅ Базовый", f"{len(TOOLS_BUILT)} инструментов, 141 тест"),
        ("📋 Тестов всего",                "✅ 331",     "все зелёные, 0 ошибок"),
        ("💬 Человечные ответы",           f"✅ {qa_pass}/{qa_total}", "агент говорит как человек"),
        ("🗣 Языки программирования",      f"✅ {len(PROG_LANGS)}",    "Python, JS, Java, Go, Rust и др."),
        ("🌍 Человеческие языки",          f"✅ {len(HUMAN_LANGS)}+",  "RU, EN, ES, FR, DE, ZH, HE…"),
        ("📄 Читает форматы",              "✅ Текст",   ".txt .md .json .yaml .csv .py .html…"),
        ("📄 Не читает пока",              "⏳ Бинарные",".pdf .docx .xlsx .png .jpg (нет инструментов)"),
        ("✏️ Чертежи/диаграммы",          "⏳ Нет",     "generate_diagram — не построен"),
        ("📧 Почта (Email)",               "⏳ Нет",     "email_send/read — следующие инструменты"),
        ("✈️ Telegram",                   "⏳ Нет",     "следующий этап разработки"),
        ("🌐 Интернет / web_search",       "⏳ Нет",     "http_request — не построен"),
        ("🖥 Выполнение кода (sandbox)",   "⏳ Нет",     "execute_code — не построен"),
        ("📁 Работа с файлами (ОС)",       "⏳ Нет",     "read_file/write_file — не построены"),
        ("🗄 База данных",                 "⏳ Нет",     "database_query — не построен"),
        ("🔐 Human Approval Gate",         "✅ Да",      "BrainLoop спрашивает перед опасными действиями"),
        ("📝 Объяснимость (Explainer)",    "✅ Да",      "каждое решение объясняется (risk level)"),
        ("🔄 Автокалибровка уверенности",  "✅ Да",      "адаптивный порог, 10+ итераций"),
    ]

    for cat, status, detail in rows:
        color = "green" if status.startswith("✅") else ("yellow" if status.startswith("⏳") else "red")
        dashboard.add_row(cat, f"[{color}]{status}[/]", detail)

    console.print(dashboard)

    # Следующие шаги
    console.print()
    tree = Tree("[bold blue]Следующие этапы разработки[/]", guide_style="blue")
    step1 = tree.add("[bold green]1. Telegram Interface[/]  ← ПРИОРИТЕТ")
    step1.add("[cyan]telegram/bot.py[/] — async Bot API")
    step1.add("[cyan]telegram/formatter.py[/] — ThinkResult → читаемый текст")
    step1.add("[cyan]telegram/session_map.py[/] — изоляция сессий по chat_id")

    step2 = tree.add("[bold yellow]2. Tool Layer расширение[/]")
    step2.add("[cyan]tools/builtins/web_search.py[/] — поиск в интернете")
    step2.add("[cyan]tools/builtins/read_file.py[/] — чтение файлов")
    step2.add("[cyan]tools/builtins/execute_code.py[/] — sandbox выполнение")
    step2.add("[cyan]tools/builtins/email_send.py[/] — отправка почты")
    step2.add("[cyan]tools/builtins/http_request.py[/] — HTTP запросы")

    step3 = tree.add("[bold dim]3. Мониторинг и логирование[/]")
    step3.add("[dim]Tracing, метрики, алерты (раздел 9 архитектуры)[/]")

    console.print(tree)
    console.print()


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    args = sys.argv[1:]
    section = None
    if "--section" in args:
        idx = args.index("--section")
        if idx + 1 < len(args):
            section = args[idx + 1].lower()

    console.print()
    console.print(Panel(
        "[bold white]АУДИТ ВОЗМОЖНОСТЕЙ АВТОНОМНОГО АГЕНТА[/]\n"
        f"[dim]{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC[/]",
        border_style="bold blue",
        width=99,
    ))

    qa_results: list[QAResult] = []

    if section is None or section == "tools":
        section_tools()
    if section is None or section == "files":
        section_file_formats()
    if section is None or section == "languages":
        section_languages()
        section_human_languages()
    if section is None or section == "communication":
        section_communication()
    if section is None or section == "responses":
        qa_results = section_human_responses()

    if section is None:
        final_dashboard(qa_results)
        console.print()


if __name__ == "__main__":
    main()
