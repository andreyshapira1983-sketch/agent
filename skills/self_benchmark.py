# Self-Benchmark — Слой 31
# Самотестирование автономного агента по эталонным задачам.
#
# Загружает config/benchmark_tasks.json, прогоняет задачи через агента,
# логирует результаты, анализирует ошибки и предлагает исправления.
#
# Использование:
#   from skills.self_benchmark import SelfBenchmark
#   bench = SelfBenchmark(web_interface=wi)
#   bench.run(category_id=1)          # одна категория
#   bench.run()                       # все 45 задач
#   bench.report()                    # сводка по последнему запуску
#   bench.analyze_errors()            # анализ причин провалов

# pylint: disable=broad-exception-caught,protected-access

from __future__ import annotations

import json
import os
import time
import datetime
import uuid
import importlib.util
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum

# ── Путь к эталонным задачам ─────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_TASKS_FILE = os.path.join(_HERE, '..', 'config', 'benchmark_tasks.json')
_LOG_FILE   = os.path.join(_HERE, '..', 'outputs', 'benchmark_log.json')
_EVENTS_LOG_FILE = os.path.join(_HERE, '..', 'outputs', 'benchmark_events.jsonl')


# ── Статус результата ─────────────────────────────────────────────────────────

class BenchStatus(str, Enum):
    OK       = 'ok'        # задача выполнена
    FAIL     = 'fail'      # агент дал явно неверный/пустой ответ
    TIMEOUT  = 'timeout'   # превышен лимит времени
    SKIP     = 'skip'      # задача пропущена (нет нужных инструментов)
    PARTIAL  = 'partial'   # выполнено частично


# ── Структура одного результата ───────────────────────────────────────────────

@dataclass
class BenchResult:
    task_id:    int
    task_text:  str
    status:     BenchStatus
    reply:      str = ''
    error:      str = ''
    elapsed_s:  float = 0.0
    requires:   list  = field(default_factory=list)
    notes:      str   = ''
    category:   str   = ''
    pass_index: int   = 1

    def to_dict(self) -> dict:
        return {
            'task_id':   self.task_id,
            'task_text': self.task_text[:120],
            'status':    self.status.value,
            'reply':     self.reply[:300],
            'error':     self.error,
            'elapsed_s': round(self.elapsed_s, 2),
            'requires':  self.requires,
            'notes':     self.notes,
            'category':  self.category,
            'pass_index': self.pass_index,
        }


# ── Главный класс ─────────────────────────────────────────────────────────────

class SelfBenchmark:
    """
    Прогоняет эталонные задачи через агента и анализирует результаты.

    Параметры:
        web_interface  — экземпляр WebInterface (для вызова _handle_chat)
        timeout_sec    — лимит времени на одну задачу (сек, default 60)
    """

    def __init__(self, web_interface=None, timeout_sec: float = 60.0):
        self._wi        = web_interface
        self._timeout   = timeout_sec
        self._results:  list[BenchResult] = []
        self._tasks_raw: dict = {}
        self._last_run_id: str = ''
        self._last_profile: str = 'manual'
        self._load_tasks()

    # ── Загрузка задач ────────────────────────────────────────────────────────

    def _load_tasks(self):
        try:
            with open(_TASKS_FILE, encoding='utf-8') as f:
                self._tasks_raw = json.load(f)
        except FileNotFoundError:
            self._tasks_raw = {}

    def get_all_tasks(self) -> list[dict]:
        tasks = []
        for cat in self._tasks_raw.get('categories', []):
            for t in cat.get('tasks', []):
                t['category'] = cat['name']
                tasks.append(t)
        return tasks

    def get_tasks_by_category(self, category_id: int) -> list[dict]:
        for cat in self._tasks_raw.get('categories', []):
            if cat['id'] == category_id:
                return cat.get('tasks', [])
        return []

    def get_profile(self, profile_name: str) -> dict:
        profiles = self._tasks_raw.get('accelerated_mode', {}).get('profiles', {})
        return profiles.get(profile_name, {})

    def list_profiles(self) -> dict[str, dict]:
        return self._tasks_raw.get('accelerated_mode', {}).get('profiles', {})

    # ── Проверка доступности инструментов ────────────────────────────────────

    def _can_run(self, requires: list[str]) -> tuple[bool, str]:
        """Возвращает (можно_ли_запускать, причина_если_нет)."""
        for req in requires:
            if req in ('gmail_oauth', 'google_calendar_oauth', 'google_contacts_oauth'):
                creds = os.path.join(_HERE, '..', 'config', 'credentials.json')
                token = os.path.join(_HERE, '..', 'config', 'token.json')
                if not os.path.exists(creds) and not os.path.exists(token):
                    return False, f'Требует OAuth ({req}) — credentials.json не найден'
            elif req == 'zoneinfo_or_pytz':
                has_zoneinfo = importlib.util.find_spec('zoneinfo') is not None
                has_pytz = importlib.util.find_spec('pytz') is not None
                if not has_zoneinfo and not has_pytz:
                    return False, 'Требует zoneinfo (Python 3.9+) или pytz'
            elif req == 'pdf_reader':
                has_pdfplumber = importlib.util.find_spec('pdfplumber') is not None
                has_pypdf = importlib.util.find_spec('pypdf') is not None
                if not has_pdfplumber and not has_pypdf:
                    return False, 'Требует pdfplumber или pypdf'
            elif req == 'image_generator_or_editor':
                return False, 'Требует DALL-E API или PIL — не реализовано'
        return True, ''

    # ── Запуск одной задачи ───────────────────────────────────────────────────

    def _run_task(self, task: dict, pass_index: int = 1) -> BenchResult:
        task_id   = task['id']
        task_text = task['text']
        requires  = task.get('requires', [])
        notes     = task.get('notes', '')
        category  = task.get('category', '')

        # Проверяем доступность инструментов
        ok, reason = self._can_run(requires)
        if not ok:
            return BenchResult(
                task_id=task_id, task_text=task_text,
                status=BenchStatus.SKIP, error=reason,
                requires=requires, notes=notes,
                category=category, pass_index=pass_index,
            )

        if not self._wi:
            return BenchResult(
                task_id=task_id, task_text=task_text,
                status=BenchStatus.SKIP, error='web_interface не подключён',
                requires=requires, notes=notes,
                category=category, pass_index=pass_index,
            )

        # Запускаем через _handle_chat с таймаутом
        t0 = time.time()
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(self._wi._handle_chat, {'message': task_text})
                result = fut.result(timeout=self._timeout)

            elapsed = time.time() - t0
            reply   = result.get('reply', '')
            success = result.get('ok', False)

            # Оцениваем результат
            status = self._evaluate(task, reply, success)
            return BenchResult(
                task_id=task_id, task_text=task_text,
                status=status, reply=reply,
                elapsed_s=elapsed, requires=requires, notes=notes,
                category=category, pass_index=pass_index,
            )

        except FutureTimeoutError:
            return BenchResult(
                task_id=task_id, task_text=task_text,
                status=BenchStatus.TIMEOUT,
                error=f'Таймаут {self._timeout:.0f}с',
                elapsed_s=time.time() - t0,
                requires=requires, notes=notes,
                category=category, pass_index=pass_index,
            )
        except Exception as e:
            return BenchResult(
                task_id=task_id, task_text=task_text,
                status=BenchStatus.FAIL,
                error=str(e)[:200],
                elapsed_s=time.time() - t0,
                requires=requires, notes=notes,
                category=category, pass_index=pass_index,
            )

    # ── Оценка ответа ─────────────────────────────────────────────────────────

    def _evaluate(self, task: dict, reply: str, ok: bool) -> BenchStatus:
        """Простая эвристика качества ответа."""
        if not reply or len(reply.strip()) < 10:
            return BenchStatus.FAIL

        lower = reply.lower()

        # Отказы и ошибки
        _FAIL_PATTERNS = (
            'не могу выполнить', 'невозможно', 'не поддерживается',
            'не настроен', 'нет credentials', 'нет доступа',
            'я не могу', 'извините', 'к сожалению',
            'cannot', 'unable to', 'not configured',
        )
        if any(p in lower for p in _FAIL_PATTERNS):
            # Если это ожидаемый fail (OAuth не настроен) — SKIP
            requires = task.get('requires', [])
            if any(r in requires for r in ('gmail_oauth', 'google_calendar_oauth', 'google_contacts_oauth')):
                return BenchStatus.SKIP
            return BenchStatus.FAIL

        # Успешный ответ достаточной длины
        if ok and len(reply) > 30:
            # Частичный если ответ очень короткий или без данных
            if len(reply) < 80:
                return BenchStatus.PARTIAL
            return BenchStatus.OK

        if not ok:
            return BenchStatus.FAIL

        return BenchStatus.PARTIAL

    # ── Основной запуск ───────────────────────────────────────────────────────

    def run(
        self,
        category_id: int | None = None,
        task_ids: list[int] | None = None,
        profile: str | None = None,
        passes: int | None = None,
    ) -> list[BenchResult]:
        """
        Запускает бенчмарк.
        category_id — только задачи из категории
        task_ids    — конкретные ID задач
        """
        self._load_tasks()
        all_tasks = self.get_all_tasks()

        if profile:
            cfg = self.get_profile(profile)
            if cfg:
                task_ids = list(cfg.get('task_ids', []))
                if passes is None:
                    passes = int(cfg.get('passes', 1))
                timeout_override = cfg.get('timeout_sec')
                if isinstance(timeout_override, (int, float)) and timeout_override > 0:
                    self._timeout = float(timeout_override)
                self._last_profile = profile

        if category_id is not None:
            all_tasks = [t for t in all_tasks if
                         any(c['id'] == category_id for c in self._tasks_raw.get('categories', [])
                             if any(tt['id'] == t['id'] for tt in c.get('tasks', [])))]
        if task_ids is not None:
            all_tasks = [t for t in all_tasks if t['id'] in task_ids]

        self._results = []
        self._last_run_id = uuid.uuid4().hex[:12]
        run_passes = max(1, int(passes or 1))
        total = len(all_tasks)

        for p_idx in range(1, run_passes + 1):
            print(f'\n=== PASS {p_idx}/{run_passes} | profile={self._last_profile} ===')
            for i, task in enumerate(all_tasks, 1):
                print(f'[{i}/{total}] Задача #{task["id"]}: {task["text"][:60]}...')
                result = self._run_task(task, pass_index=p_idx)
                self._results.append(result)
                icon = {'ok': '✅', 'fail': '❌', 'timeout': '⏱', 'skip': '⏭', 'partial': '⚠️'}.get(result.status.value, '?')
                print(f'  {icon} {result.status.value.upper()} ({result.elapsed_s:.1f}с)')
                if result.error:
                    print(f'  └─ {result.error}')
                self._append_event_log(result)

        self._save_log()
        return self._results

    def run_profile(self, profile_name: str, passes: int | None = None) -> list[BenchResult]:
        """Запуск ускоренного профиля из config/benchmark_tasks.json."""
        cfg = self.get_profile(profile_name)
        if not cfg:
            return []
        return self.run(profile=profile_name, passes=passes)

    # ── Сводный отчёт ─────────────────────────────────────────────────────────

    def report(self) -> str:
        """Возвращает текстовый отчёт по последнему запуску."""
        if not self._results:
            return 'Нет результатов. Сначала запусти bench.run()'

        total   = len(self._results)
        ok      = sum(1 for r in self._results if r.status == BenchStatus.OK)
        partial = sum(1 for r in self._results if r.status == BenchStatus.PARTIAL)
        fail    = sum(1 for r in self._results if r.status == BenchStatus.FAIL)
        timeout = sum(1 for r in self._results if r.status == BenchStatus.TIMEOUT)
        skip    = sum(1 for r in self._results if r.status == BenchStatus.SKIP)
        avg_t   = sum(r.elapsed_s for r in self._results) / max(1, total)

        lines = [
            f'📊 BENCHMARK REPORT — {datetime.datetime.now().strftime("%d.%m.%Y %H:%M")}',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            f'Всего задач:  {total}',
            f'✅ OK:         {ok}  ({ok/total*100:.0f}%)',
            f'⚠️  Частично:  {partial}  ({partial/total*100:.0f}%)',
            f'❌ Провал:     {fail}  ({fail/total*100:.0f}%)',
            f'⏱  Таймаут:   {timeout}  ({timeout/total*100:.0f}%)',
            f'⏭  Пропущено: {skip}  (нет инструментов)',
            f'⏱  Среднее:   {avg_t:.1f}с/задача',
            '',
        ]

        # Провалы и таймауты — список с причинами
        problems = [r for r in self._results if r.status in (BenchStatus.FAIL, BenchStatus.TIMEOUT)]
        if problems:
            lines.append('❌ ПРОБЛЕМНЫЕ ЗАДАЧИ:')
            for r in problems:
                lines.append(f'  #{r.task_id}: {r.task_text[:60]}')
                lines.append(f'     → {r.status.value.upper()} | {r.error or r.reply[:60]}')
        return '\n'.join(lines)

    # ── Анализ ошибок ─────────────────────────────────────────────────────────

    def analyze_errors(self) -> str:
        """Группирует ошибки по причинам и предлагает исправления."""
        if not self._results:
            return 'Нет результатов.'

        problems = [r for r in self._results if r.status in (BenchStatus.FAIL, BenchStatus.TIMEOUT, BenchStatus.PARTIAL)]
        if not problems:
            return '✅ Всё работает — ошибок не найдено!'

        # Группировка по причинам
        groups: dict[str, list] = {
            'Таймаут (LLM/сеть)': [],
            'Нет входных данных (файл/URL не указан)': [],
            'Отказ LLM (проза вместо действия)': [],
            'Прочие ошибки': [],
        }

        for r in problems:
            txt = (r.error + r.reply).lower()
            if r.status == BenchStatus.TIMEOUT:
                groups['Таймаут (LLM/сеть)'].append(r)
            elif any(p in txt for p in ('не указан', 'нужно уточнить', 'прикрепи файл', 'путь не указан')):
                groups['Нет входных данных (файл/URL не указан)'].append(r)
            elif any(p in txt for p in ('я не могу', 'невозможно', 'не поддерживается', 'cannot')):
                groups['Отказ LLM (проза вместо действия)'].append(r)
            else:
                groups['Прочие ошибки'].append(r)

        lines = ['🔍 АНАЛИЗ ОШИБОК:', '']
        for group_name, items in groups.items():
            if not items:
                continue
            lines.append(f'▶ {group_name} ({len(items)} задач):')
            for r in items:
                lines.append(f'   #{r.task_id}: {r.task_text[:55]}')
            lines.append('')

        # Рекомендации
        lines.append('💡 РЕКОМЕНДАЦИИ:')
        if groups['Таймаут (LLM/сеть)']:
            lines.append('  • Добавить быстрые handlers для этих типов задач в _handle_quick_task')
            lines.append('  • Или снизить WEB_CHAT_TIMEOUT_SEC до 30с для быстрого fail-fast')
        if groups['Нет входных данных (файл/URL не указан)']:
            lines.append('  • Агент должен запрашивать уточнение вместо падения')
            lines.append('  • Добавить ранний выход с подсказкой что именно нужно')
        if groups['Отказ LLM (проза вместо действия)']:
            lines.append('  • Усилить system prompt: "Никогда не отвечай прозой — выполняй задачу"')
            lines.append('  • Добавить эти задачи в validate_response() как детектируемые паттерны')

        return '\n'.join(lines)

    # ── Сохранение лога ───────────────────────────────────────────────────────

    def _save_log(self):
        try:
            os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
            log_entry = {
                'run_id': self._last_run_id,
                'profile': self._last_profile,
                'timeout_sec': self._timeout,
                'timestamp': datetime.datetime.now().isoformat(),
                'results': [r.to_dict() for r in self._results],
            }
            # Добавляем к истории (последние 10 прогонов)
            history = []
            if os.path.exists(_LOG_FILE):
                try:
                    with open(_LOG_FILE, encoding='utf-8') as f:
                        history = json.load(f)
                except Exception:
                    history = []
            history.append(log_entry)
            history = history[-10:]  # хранить последние 10 прогонов
            with open(_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _append_event_log(self, result: BenchResult):
        """Пишет одно событие в JSONL для быстрой аналитики провалов/повторов."""
        try:
            os.makedirs(os.path.dirname(_EVENTS_LOG_FILE), exist_ok=True)
            event = {
                'run_id': self._last_run_id,
                'timestamp': datetime.datetime.now().isoformat(),
                'profile': self._last_profile,
                'pass_index': result.pass_index,
                'task_id': result.task_id,
                'category': result.category,
                'status': result.status.value,
                'elapsed_s': round(result.elapsed_s, 3),
                'requires': result.requires,
                'error': result.error,
                'reply_preview': (result.reply or '')[:240],
            }
            with open(_EVENTS_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception:
            pass

    def load_last_log(self) -> list[dict]:
        """Загружает последний сохранённый прогон."""
        try:
            with open(_LOG_FILE, encoding='utf-8') as f:
                history = json.load(f)
            return history[-1].get('results', []) if history else []
        except Exception:
            return []

    # ── Краткий статус одной задачи ───────────────────────────────────────────

    def test_one(self, task_id: int) -> str:
        """Быстро прогнать одну задачу по ID."""
        all_tasks = self.get_all_tasks()
        task = next((t for t in all_tasks if t['id'] == task_id), None)
        if not task:
            return f'Задача #{task_id} не найдена'
        result = self._run_task(task)
        icon = {'ok': '✅', 'fail': '❌', 'timeout': '⏱', 'skip': '⏭', 'partial': '⚠️'}.get(result.status.value, '?')
        lines = [
            f'{icon} Задача #{task_id}: {result.status.value.upper()} ({result.elapsed_s:.1f}с)',
            f'Текст: {result.task_text}',
        ]
        if result.reply:
            lines.append(f'Ответ: {result.reply[:200]}')
        if result.error:
            lines.append(f'Ошибка: {result.error}')
        return '\n'.join(lines)


def _main_cli():
    import argparse

    parser = argparse.ArgumentParser(description='SelfBenchmark ускоренный запуск')
    parser.add_argument('--profile', type=str, default='today_fast_start',
                        help='Имя профиля из config/benchmark_tasks.json -> accelerated_mode.profiles')
    parser.add_argument('--passes', type=int, default=None,
                        help='Сколько проходов сделать (если не задано — из профиля)')
    parser.add_argument('--task', type=int, default=None,
                        help='Запуск только одной задачи по ID')
    parser.add_argument('--list-profiles', action='store_true',
                        help='Показать доступные профили и выйти')
    args = parser.parse_args()

    bench = SelfBenchmark(web_interface=None)

    if args.list_profiles:
        profiles = bench.list_profiles()
        if not profiles:
            print('Профили не найдены')
            return
        for name, cfg in profiles.items():
            print(f'- {name}: {cfg.get("description", "")[:120]}')
        return

    if args.task is not None:
        print(bench.test_one(args.task))
        return

    if not bench._wi:
        print('web_interface не подключён. Для реального прогона создай SelfBenchmark(web_interface=wi).')
        print(f'Рекомендуемый профиль: {args.profile}')
        return

    results = bench.run_profile(args.profile, passes=args.passes)
    if not results:
        print(f'Профиль {args.profile} не найден или не содержит задач')
        return
    print(bench.report())


if __name__ == '__main__':
    _main_cli()
