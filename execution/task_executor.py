"""
TaskExecutor — прямое исполнение задач через tool_layer без LLM.

Когда LLM пишет план-текст вместо кода, этот модуль берёт задачу,
определяет нужный инструмент и вызывает его напрямую.

Подключение в autonomous_loop: если ActionDispatcher нашёл 0 действий
→ передать задачу в TaskExecutor.
"""

import os
import re
import datetime
import importlib

from execution.task_router import TaskRouter, TaskIntent, TaskStatus


class TaskExecutor:
    """
    Прямой исполнитель задач через tool_layer.

    Правило: task_text → (tool_name, params) → tool_layer.use(...)
    """

    def __init__(self, tool_layer, working_dir: str = '.'):
        self.tool_layer = tool_layer
        self.working_dir = working_dir
        self._outputs_dir = os.path.join(working_dir, 'outputs')
        os.makedirs(self._outputs_dir, exist_ok=True)
        self._router = TaskRouter()

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def _out(self, filename: str) -> str:
        """Путь к файлу в outputs/."""
        return os.path.join(self._outputs_dir, filename)

    def _timestamp(self) -> str:
        return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    def _date(self) -> str:
        return str(datetime.date.today())

    def _use(self, tool: str, **kwargs) -> dict:
        try:
            return self.tool_layer.use(tool, **kwargs)
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError) as e:
            return {'success': False, 'error': str(e)}

    # ── Определение типа задачи ───────────────────────────────────────────────

    def _detect(self, task: str) -> str:
        """Определяет тип задачи через TaskRouter (legacy-обёртка для обратной совместимости)."""
        route = self._router.route(task)
        return route.intent.value

    def _extract_text(self, task: str) -> str:
        """Извлекает основной текст из задачи (убирает глаголы-команды)."""
        t = re.sub(
            r'^(создай|напиши|сгенерируй|сделай|подготовь|составь|generate|create|make|write)\s+',
            '', task.strip(), flags=re.IGNORECASE
        )
        return t[:500]

    # ── Исполнители по типу ───────────────────────────────────────────────────

    def _exec_pdf(self, task: str) -> dict:
        ts = self._timestamp()
        text = self._extract_text(task)
        path = self._out(f'report_{ts}.pdf')
        r = self._use('pdf_generator', action='from_text',
                      output=path,
                      title=text[:80] or f'Report {self._date()}',
                      text=f'{text}\n\nCreated: {datetime.datetime.now()}\nAgent: Andre AI Agent')
        return {'file': path, **r}

    def _exec_excel(self, task: str) -> dict:
        ts = self._timestamp()
        path = self._out(f'data_{ts}.xlsx')
        # Пробуем извлечь данные из задачи, иначе создаём шаблон
        r = self._use('spreadsheet', action='write',
                      path=path,
                      rows=[{
                          'Task': self._extract_text(task)[:100],
                          'Date': self._date(),
                          'Status': 'Created by agent',
                          'Note': 'Add your data here'
                      }])
        return {'file': path, **r}

    def _exec_pptx(self, task: str) -> dict:
        ts = self._timestamp()
        text = self._extract_text(task)
        path = self._out(f'presentation_{ts}.pptx')
        r = self._use('powerpoint', action='from_text',
                      output=path,
                      title=text[:80] or 'Presentation',
                      text=text)
        return {'file': path, **r}

    def _exec_archive(self, _task: str) -> dict:
        ts = self._timestamp()
        path = self._out(f'archive_{ts}.zip')
        # Архивируем всё из outputs/ кроме самого архива
        files = [
            os.path.join(self._outputs_dir, f)
            for f in os.listdir(self._outputs_dir)
            if not f.endswith('.zip') and os.path.isfile(os.path.join(self._outputs_dir, f))
        ]
        if not files:
            return {'success': False, 'error': 'Нет файлов для архивации'}
        r = self._use('archive', action='create', archive_path=path, files=files[:20])
        return {'file': path, **r}

    def _exec_screenshot(self, _task: str) -> dict:
        ts = self._timestamp()
        path = self._out(f'screenshot_{ts}.png')
        r = self._use('screenshot', save_path=path)
        return {'file': path, **r}

    def _exec_chart(self, task: str) -> dict:
        ts = self._timestamp()
        path = self._out(f'chart_{ts}.png')
        # Дефолтный демо-график
        r = self._use('data_viz', action='bar',
                      output=path,
                      data={'Cycles': [50, 80, 65, 90, 75]},
                      labels=['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
                      title=self._extract_text(task)[:60] or 'Chart')
        return {'file': path, **r}

    def _exec_translate(self, task: str) -> dict:
        # Извлекаем язык назначения
        target = 'en'
        if any(w in task.lower() for w in ['russian', 'русск', 'на русск']):
            target = 'ru'
        elif any(w in task.lower() for w in ['english', 'английск', 'на английск']):
            target = 'en'
        elif any(w in task.lower() for w in ['hebrew', 'иврит', 'на иврит']):
            target = 'he'
        text = self._extract_text(task)
        r = self._use('translate', text=text, target=target)
        return r

    def _exec_search(self, task: str) -> dict:
        query = self._extract_text(task)
        r = self._use('search', query=query, num_results=5)
        # Сохраняем результаты в файл
        if r.get('success') and r.get('results'):
            ts = self._timestamp()
            path = self._out(f'search_{ts}.txt')
            lines = [f"Query: {query}\n"]
            for i, item in enumerate(r['results'], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}\n   {item.get('snippet', '')}\n")
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            r['file'] = path
        return r

    def _exec_health(self, _task: str) -> dict:
        import json
        try:
            psutil = importlib.import_module('psutil')
            data = {
                'cpu': psutil.cpu_percent(1),
                'ram': psutil.virtual_memory().percent,
                'disk': psutil.disk_usage('C:\\').percent,
                'time': str(datetime.datetime.now()),
            }
        except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
            data = {'time': str(datetime.datetime.now()), 'status': 'ok'}
        ts = self._timestamp()
        path = self._out(f'health_{ts}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return {'success': True, 'file': path, 'data': data}

    def _exec_email(self, task: str) -> dict:
        """Выполняет email-задачу через EmailTool."""
        tl = task.lower()
        if any(kw in tl for kw in ('непрочитанн', 'unread')):
            action = 'unread'
        elif any(kw in tl for kw in ('черновик', 'draft')):
            action = 'unread'  # читаем черновики из папки Drafts
        elif any(kw in tl for kw in ('отправ', 'send')):
            action = 'send'
        elif any(kw in tl for kw in ('найди', 'поиск', 'search', 'find')):
            action = 'search'
        else:
            action = 'read'
        return self._use('email', action=action, query=task[:200])

    def _exec_calendar(self, task: str) -> dict:
        """Выполняет calendar-задачу через CalendarTool."""
        tl = task.lower()
        if any(kw in tl for kw in ('создай', 'create', 'schedule', 'добавь')):
            action = 'create'
        elif any(kw in tl for kw in ('свободн', 'free slot', 'занятост')):
            action = 'free_slots'
        elif any(kw in tl for kw in ('перенес', 'reschedule', 'move event')):
            action = 'update'
        elif any(kw in tl for kw in ('ответ', 'reply', 'accept', 'decline', 'приглашен')):
            action = 'respond'
        else:
            action = 'list'
        return self._use('calendar', action=action, query=task[:200])

    def _exec_contacts(self, _task: str) -> dict:
        """Выполняет contacts-задачу (заглушка — реального contacts-tool нет)."""
        return {
            'success': False,
            'status': TaskStatus.BLOCKED.value,
            'reason': 'Contacts tool не настроен. Нужен Google Contacts API.',
            'message': 'BLOCKED: для работы с контактами нужно настроить Google Contacts API в credentials.json',
        }

    def _exec_weather(self, task: str) -> dict:
        """Выполняет weather-задачу через WeatherTool или API."""
        # Пробуем weather tool
        result = self._use('weather', query=task)
        if result.get('success'):
            return result
        # Fallback: web search с явной пометкой о реальном источнике
        query = task
        r = self._use('search', query=f'weather forecast {query}', num_results=3)
        r['intent'] = 'weather'
        r['note'] = 'Результат из web-поиска — weather tool не настроен'
        return r

    def _exec_time(self, task: str) -> dict:
        """Определяет текущее время в указанном городе/часовом поясе."""
        import zoneinfo
        # Маппинг городов → IANA timezone
        city_tz = {
            'тель-авив': 'Asia/Jerusalem', 'tel aviv': 'Asia/Jerusalem',
            'тель авив': 'Asia/Jerusalem', 'israel': 'Asia/Jerusalem',
            'нью-йорк': 'America/New_York', 'new york': 'America/New_York',
            'new-york': 'America/New_York', 'nyc': 'America/New_York',
            'москва': 'Europe/Moscow', 'moscow': 'Europe/Moscow',
            'лондон': 'Europe/London', 'london': 'Europe/London',
            'берлин': 'Europe/Berlin', 'berlin': 'Europe/Berlin',
            'париж': 'Europe/Paris', 'paris': 'Europe/Paris',
            'токио': 'Asia/Tokyo', 'tokyo': 'Asia/Tokyo',
        }
        tl = task.lower()
        tz_name = None
        for city, tz in city_tz.items():
            if city in tl:
                tz_name = tz
                break
        if not tz_name:
            return {
                'success': False,
                'status': TaskStatus.BLOCKED.value,
                'message': 'BLOCKED: не указан город/часовой пояс в задаче.',
            }
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
            now = datetime.datetime.now(tz)
            return {
                'success': True,
                'status': TaskStatus.SUCCESS.value,
                'timezone': tz_name,
                'current_time': now.strftime('%Y-%m-%d %H:%M:%S %Z'),
                'time': now.strftime('%H:%M'),
                'message': f"Сейчас {now.strftime('%H:%M %Z')} ({tz_name})",
            }
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError) as e:
            return {'success': False, 'error': str(e)}

    def _exec_currency(self, task: str) -> dict:
        """Конвертация валют через открытый API или web-поиск."""
        # Пробуем currency tool
        result = self._use('currency', query=task)
        if result.get('success'):
            return result
        # Fallback: web search
        r = self._use('search', query=f'exchange rate {task}', num_results=3)
        r['intent'] = 'currency'
        r['note'] = 'Результат из web-поиска — currency tool не настроен'
        return r

    def _exec_sports(self, task: str) -> dict:
        """Расписание матчей через web-поиск."""
        r = self._use('search', query=task, num_results=5)
        r['intent'] = 'sports'
        return r

    def _exec_user_files(self, task: str) -> dict:
        """Поиск файлов пользователя через FileSystemTool."""
        r = self._use('filesystem', action='search', query=task)
        if not r.get('success'):
            return {
                'success': False,
                'status': TaskStatus.BLOCKED.value,
                'message': 'BLOCKED: не указан конкретный путь для поиска файлов пользователя.',
            }
        return r

    def _exec_general(self, task: str) -> dict:
        """Fallback: создаём текстовый файл с задачей и базовым ответом."""
        ts = self._timestamp()
        path = self._out(f'task_{ts}.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"Task: {task}\nDate: {datetime.datetime.now()}\n\nResult: Task recorded.\n")
        return {'success': True, 'file': path, 'note': 'Task saved to file'}

    # ── Главный метод ─────────────────────────────────────────────────────────

    def _verify_result_contract(self, result: dict) -> None:
        """Проверяет результат TaskExecutor по контракту (если применимо)."""
        try:
            from evaluation.action_contracts import contract_for_action_type, verify_contract
        except ImportError:
            return

        file_path = result.get('file', '')
        action_type = result.get('intent', 'general')
        success = result.get('success', False)

        contract = contract_for_action_type(
            action_type=action_type,
            action_input=result.get('task', ''),
            action_output=str(file_path),
            action_success=success,
        )

        # Для файловых результатов: если нет типового контракта — проверяем наличие файла
        if contract is None and file_path and success:
            from evaluation.action_contracts import contract_file_created
            contract = contract_file_created(file_path)

        if contract is None:
            return

        vr = verify_contract(contract)
        if not vr.passed:
            result['success'] = False
            result['status'] = 'failed'
            result.setdefault('contract_errors', []).extend(
                f'verify:contract:{d}' for d in vr.failed_checks
            )

    def execute(self, task: str) -> dict:
        """
        Исполняет задачу напрямую через tool_layer.

        Порядок:
          1. TaskRouter: определяем интент и проверяем аргументы
          2. NON_ACTIONABLE (заголовок) → skip без ошибки
          3. BLOCKED (нет аргументов) → честная ошибка с причиной
          4. Диспетчер: вызываем нужный executor по интенту

        Returns:
            {'success': bool, 'status': str, 'intent': str, ...}
        """
        route = self._router.route(task)

        # 1. Заголовок раздела — не задача
        if route.intent in (TaskIntent.HEADING, TaskIntent.EMPTY):
            return {**route.to_non_actionable_result(), 'task': task[:100]}

        # 2. Нет обязательных аргументов — BLOCKED
        if route.status == TaskStatus.BLOCKED:
            return {**route.to_blocked_result(), 'task': task[:100]}

        # 3. Диспетчер по интенту
        executors = {
            TaskIntent.EMAIL:        self._exec_email,
            TaskIntent.CALENDAR:     self._exec_calendar,
            TaskIntent.CONTACTS:     self._exec_contacts,
            TaskIntent.WEATHER:      self._exec_weather,
            TaskIntent.TIME:         self._exec_time,
            TaskIntent.CURRENCY:     self._exec_currency,
            TaskIntent.SPORTS:       self._exec_sports,
            TaskIntent.USER_FILES:   self._exec_user_files,
            TaskIntent.PDF_EXTRACT:  self._exec_pdf,
            TaskIntent.WEB_SEARCH:   self._exec_search,
            TaskIntent.CODE:         self._exec_general,
            # Файловые задачи
            # TaskIntent.GENERAL использует legacy _detect по строке
        }

        # Legacy-детекция для FILE-форматов (pdf/excel/pptx/archive/chart/translate)
        if route.intent == TaskIntent.GENERAL:
            legacy_type = self._legacy_detect(task)
            legacy_executors = {
                'pdf':        self._exec_pdf,
                'excel':      self._exec_excel,
                'pptx':       self._exec_pptx,
                'archive':    self._exec_archive,
                'screenshot': self._exec_screenshot,
                'chart':      self._exec_chart,
                'translate':  self._exec_translate,
                'health':     self._exec_health,
            }
            fn = legacy_executors.get(legacy_type, self._exec_general)
        else:
            fn = executors.get(route.intent, self._exec_general)

        result = fn(task)
        result.setdefault('intent', route.intent.value)
        result.setdefault('status', TaskStatus.SUCCESS.value if result.get('success') else TaskStatus.FAILED.value)
        result['task'] = task[:100]

        # Contract verification: проверяем результат по контрактам
        self._verify_result_contract(result)

        return result

    def _legacy_detect(self, task: str) -> str:
        """Legacy keyword-маппинг для файловых форматов (не перекрывает domain-routing)."""
        t = task.lower()
        rules = [
            (['excel', 'xlsx', 'таблиц', 'spreadsheet'],                        'excel'),
            (['powerpoint', 'pptx', 'слайд', 'pitch'],                          'pptx'),
            (['zip', 'архив', 'rar', 'запаков'],                                'archive'),
            (['скриншот', 'screenshot', 'снимок экрана'],                       'screenshot'),
            (['график', 'chart', 'диаграмм', 'визуализ', 'plot', 'bar'],       'chart'),
            (['перевод', 'переведи', 'translat', 'перевести'],                  'translate'),
            (['здоровье', 'health check', 'статус системы', 'system status'],   'health'),
            (['pdf', 'пдф', 'отчёт', 'report'],                                 'pdf'),
        ]
        for keywords, task_type in rules:
            if any(kw in t for kw in keywords):
                return task_type
        return 'general'
