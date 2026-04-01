"""
TaskRouter — определяет интент задачи, обязательные аргументы и статус выполнения.

Используется TaskExecutor и AutonomousLoop для:
  - детектирования заголовков разделов (NON_ACTIONABLE)
  - маппинга class задачи → tool family
  - валидации обязательных аргументов (BLOCKED когда параметр отсутствует)
  - запрета web-search для личных инструментов (gmail/calendar/contacts/файлы)

Исправляет дефект из аудита:
  «Агент берёт текст задачи, ищет по нему в web, возвращает ссылки,
   помечает ✅ завершён — хотя задача не выполнена».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


# ── Интенты (классы задач) ────────────────────────────────────────────────────

class TaskIntent(Enum):
    WEATHER      = 'weather'       # погода + рекомендации
    TIME         = 'time'          # время / часовые пояса
    CURRENCY     = 'currency'      # курс валют + калькулятор
    SPORTS       = 'sports'        # расписание матчей
    EMAIL        = 'email'         # Gmail: чтение, черновики, ярлыки
    CALENDAR     = 'calendar'      # Google Calendar: события, слоты
    CONTACTS     = 'contacts'      # Google Contacts: контакты, email по имени
    USER_FILES   = 'user_files'    # файлы пользователя (не outputs/)
    PDF_EXTRACT  = 'pdf_extract'   # извлечение из PDF
    WEB_SEARCH   = 'web_search'    # явный веб-поиск
    CODE         = 'code'          # генерация/выполнение кода
    GENERAL      = 'general'       # всё остальное
    HEADING      = 'heading'       # заголовок раздела (не задача)
    EMPTY        = 'empty'         # пустой текст


# ── Статусы результата ────────────────────────────────────────────────────────

class TaskStatus(Enum):
    SUCCESS        = 'success'         # задача реально выполнена
    PARTIAL        = 'partial'         # выполнена частично / с ограничением
    BLOCKED        = 'blocked'         # нет обязательного аргумента
    FAILED         = 'failed'          # задача не выполнена
    TIMEOUT        = 'timeout'         # таймаут
    SKIPPED        = 'skipped'         # явно пропущена
    NON_ACTIONABLE = 'non_actionable'  # заголовок / мета-фраза


# ── Вспомогательные классы ────────────────────────────────────────────────────

@dataclass
class RequiredArg:
    name: str
    description: str
    recoverable: bool = False    # можно взять из user-контекста?


@dataclass
class TaskRoute:
    intent: TaskIntent
    status: TaskStatus = TaskStatus.SUCCESS
    required_args: list[RequiredArg] = field(default_factory=list)
    missing_args: list[RequiredArg] = field(default_factory=list)
    tool_name: str = ''
    block_web_search: bool = False   # web-search запрещён для этого интента
    reason: str = ''                 # причина BLOCKED / SKIPPED / NON_ACTIONABLE

    @property
    def is_actionable(self) -> bool:
        return self.intent not in (TaskIntent.HEADING, TaskIntent.EMPTY)

    @property
    def can_execute(self) -> bool:
        return self.is_actionable and self.status not in (
            TaskStatus.BLOCKED,
            TaskStatus.NON_ACTIONABLE,
            TaskStatus.SKIPPED,
        )

    def to_blocked_result(self) -> dict:
        """Готовый dict-ответ для BLOCKED-задачи."""
        missing_desc = '; '.join(a.description for a in self.missing_args)
        return {
            'success': False,
            'status': self.status.value,
            'intent': self.intent.value,
            'reason': self.reason or f'Отсутствуют: {missing_desc}',
            'missing_args': [{'name': a.name, 'description': a.description} for a in self.missing_args],
            'message': (
                f"BLOCKED: {self.reason or f'нет аргументов: {missing_desc}'}. "
                f"Уточни задачу."
            ),
        }

    def to_non_actionable_result(self) -> dict:
        """Готовый dict-ответ для заголовков / мета-фраз."""
        return {
            'success': True,
            'status': TaskStatus.NON_ACTIONABLE.value,
            'intent': self.intent.value,
            'reason': self.reason,
            'message': f"SKIPPED (заголовок/не-задача): {self.reason}",
        }


# ── Основной маршрутизатор ────────────────────────────────────────────────────

class TaskRouter:
    """
    Классифицирует задачу и возвращает маршрут (intent + status + args).

    Не вызывает LLM — работает на основе keyword matching.
    Быстрый: O(n * keywords), выполняется за <1 мс.
    """

    # ── Шаблонные заголовки разделов (точные, lowercase) ─────────────────────
    _EXACT_HEADINGS: frozenset[str] = frozenset({
        'поиск и сбор информации',
        'работа с gmail',
        'работа с google calendar',
        'работа с файлами пользователя',
        'работа с файлами',
        'работа с pdf',
        'контакты и персональные данные',
        'работа с контактами',
        'google calendar',
        'gmail',
        'pdf и документы',
        'работа с web',
        'работа с web / поиск по источникам',
        'поиск по источникам',
    })

    # ── Keyword списки по интентам ────────────────────────────────────────────
    _EMAIL_KW = (
        'письм', 'email', 'почт', 'gmail',
        'непрочитанн', 'unread', 'inbox',
        'черновик', 'draft',
        'ярлык', 'label',
        'вложени', 'attachment',
        'архивир', 'archive',
        'отправи.*письм',
    )
    _CALENDAR_KW = (
        'календар', 'calendar',
        'событи', 'event',
        'свободн.*окн', 'free slot', 'free time slot',
        'создай встреч', 'создай событи',
        'перенес.*событ', 'перенеси встреч',
        'ответ.*приглашен', 'reply.*invite',
        'ближайш.*встреч', 'next meeting',
        'schedule.*meet', 'расписани.*встреч',
    )
    _CONTACTS_KW = (
        'контакт', 'contact',
        'адресн.*книг', 'address book',
        'найди.*имя', 'by name',
        'email.*контакт', 'contact.*email',
        'телефон.*контакт',
        'company.*contact', 'компани.*контакт',
        'найди контакт',
    )
    _USER_FILES_KW = (
        'мои файлы', 'my files',
        'среди.*файл',
        'в моих файлах',
        'загруженн.*файл', 'uploaded.*file',
        'последн.*загружен',
        'найди.*договор', 'найди.*инструкц',
        'найди.*документ',
        'файлы.*проект',
    )
    _PDF_KW = (
        'открой pdf', 'open pdf',
        'извлек.*из pdf', 'extract.*pdf',
        'найди.*в pdf', 'find.*in pdf',
        'найди.*pdf',
        'таблиц.*pdf',
        'диаграмм.*pdf', 'chart.*pdf',
        'оглавлен', 'table of contents',
        'сравни.*стран.*pdf',
        'раздел.*conclusion', 'conclusion.*pdf',
        'вывод.*pdf', 'вывод.*документ',
        'страниц.*pdf',
    )
    _WEATHER_KW = (
        'погода', 'weather',
        'зонт', 'umbrella',
        'forecast', 'прогноз погоды',
        'нужен ли зонт', 'брать ли зонт',
        'температура на улице',
        'осадки', 'precipitation',
    )
    _TIME_KW = (
        'время в ', 'time in ',
        'который час',
        'current time',
        'местное время',
        'часовой пояс', 'timezone', 'time zone',
        'сколько времени в ',
        'тель-авив время', 'тель авив время',
        'нью-йорк время', 'нью йорк время',
        'time difference',
    )
    _CURRENCY_KW = (
        'курс', 'exchange rate',
        'usd/ils', 'eur/usd', 'usd/rub',
        'конвертац', 'convert',
        'сколько стоит.*доллар', 'сколько стоит.*евро',
        'перевести.*валют',
        'currency conversion',
    )
    _SPORTS_KW = (
        'матч', 'расписание ближайших матчей',
        'match schedule', 'game schedule',
        'ближайш.*игр', 'следующ.*игр',
        'ближайш.*матч', 'следующ.*матч',
        'next.*match', 'next.*game',
        'football.*schedule', 'soccer.*schedule',
    )

    # ── Интенты, для которых web-search ЗАПРЕЩЁН как первичный инструмент ─────
    _NO_WEB_SEARCH_INTENTS: frozenset[TaskIntent] = frozenset({
        TaskIntent.EMAIL,
        TaskIntent.CALENDAR,
        TaskIntent.CONTACTS,
        TaskIntent.USER_FILES,
    })

    # ── Маппинг интент → инструмент ───────────────────────────────────────────
    _TOOL_MAP: dict[TaskIntent, str] = {
        TaskIntent.WEATHER:     'weather',
        TaskIntent.TIME:        'time',
        TaskIntent.CURRENCY:    'currency',
        TaskIntent.SPORTS:      'sports',
        TaskIntent.EMAIL:       'email',
        TaskIntent.CALENDAR:    'calendar',
        TaskIntent.CONTACTS:    'contacts',
        TaskIntent.USER_FILES:  'filesystem',
        TaskIntent.PDF_EXTRACT: 'pdf',
        TaskIntent.WEB_SEARCH:  'search',
        TaskIntent.CODE:        'python_runtime',
        TaskIntent.GENERAL:     'general',
        TaskIntent.HEADING:     '',
        TaskIntent.EMPTY:       '',
    }

    # ── Обязательные аргументы по интентам ───────────────────────────────────
    _SCHEMA: dict[str, list[RequiredArg]] = {
        TaskIntent.WEATHER.value: [
            RequiredArg('location', 'город или местоположение', recoverable=True),
        ],
        TaskIntent.TIME.value: [
            RequiredArg('timezone', 'город или часовой пояс'),
        ],
        TaskIntent.CURRENCY.value: [
            RequiredArg('currency_pair', 'пара валют (напр. USD/ILS)'),
        ],
        TaskIntent.SPORTS.value: [
            RequiredArg('team_name', 'название команды'),
        ],
        TaskIntent.PDF_EXTRACT.value: [
            RequiredArg('file_path', 'путь к PDF-файлу'),
        ],
        TaskIntent.EMAIL.value:    [],
        TaskIntent.CALENDAR.value: [],
        TaskIntent.CONTACTS.value: [],
        TaskIntent.USER_FILES.value: [],
    }

    # ── Маркеры «шаблонного» placeholder (команда без конкретного значения) ──
    _VAGUE_MARKERS = (
        'конкретного', 'определённого', 'заданного',
        'конкретной', 'определённой', 'заданной',
        'конкретное', 'определённое', 'заданное',
        'specific', 'given', 'particular', 'certain',
        'имя_контакта', 'sender_name', 'keyword_here',
        'название команды',
    )

    # ── Публичный API ─────────────────────────────────────────────────────────

    def route(self, task_text: str) -> TaskRoute:
        """
        Классифицирует задачу и возвращает TaskRoute.

        Args:
            task_text — текст задачи

        Returns:
            TaskRoute с intent, status, missing_args, block_web_search
        """
        if not task_text or not task_text.strip():
            return TaskRoute(
                intent=TaskIntent.EMPTY,
                status=TaskStatus.NON_ACTIONABLE,
                reason='Пустая задача',
            )

        t = task_text.strip()
        tl = t.lower()

        # 1. Проверка заголовков
        if self._is_heading(tl):
            return TaskRoute(
                intent=TaskIntent.HEADING,
                status=TaskStatus.NON_ACTIONABLE,
                reason=f'Заголовок раздела, не задача: "{t[:60]}"',
            )

        # 2. Определение интента
        intent = self._detect_intent(tl)

        # 3. Валидация аргументов
        missing = self._check_missing_args(intent, tl)

        # 4. Запрет web-search
        block_web = intent in self._NO_WEB_SEARCH_INTENTS

        # 5. Определение статуса
        if missing:
            all_recoverable = all(a.recoverable for a in missing)
            if all_recoverable:
                status = TaskStatus.PARTIAL
                reason = (
                    f"Аргументы {[a.name for a in missing]} могут быть получены "
                    f"из контекста пользователя"
                )
            else:
                status = TaskStatus.BLOCKED
                reason = (
                    "Отсутствуют обязательные аргументы: "
                    + ', '.join(f'"{a.description}"' for a in missing)
                )
        else:
            status = TaskStatus.SUCCESS
            reason = ''

        return TaskRoute(
            intent=intent,
            status=status,
            required_args=self._SCHEMA.get(intent.value, []),
            missing_args=missing,
            tool_name=self._TOOL_MAP.get(intent, 'general'),
            block_web_search=block_web,
            reason=reason,
        )

    # ── Heading detection ─────────────────────────────────────────────────────

    def _is_heading(self, text_lower: str) -> bool:
        """True если текст является заголовком раздела, а не исполняемой задачей."""
        # Точное совпадение с известными заголовками
        if text_lower in self._EXACT_HEADINGS:
            return True

        # Хаотичный заголовок вида "[7/40] Работа с web..." → strip number prefix
        if re.match(r'^\[?\d+/\d+\]?\s+', text_lower):
            body = re.sub(r'^\[?\d+/\d+\]?\s+', '', text_lower).strip()
            if body in self._EXACT_HEADINGS:
                return True

        # Класс заголовков: <=5 слов, нет глаголов-действий
        words = text_lower.split()
        if len(words) <= 5:
            action_kw = (
                'найди', 'создай', 'открой', 'получи', 'проверь', 'отправь',
                'перенеси', 'извлеки', 'сравни', 'ответь', 'примени', 'архивируй',
                'find', 'create', 'open', 'get', 'check', 'send', 'move',
                'extract', 'compare', 'reply', 'apply', 'schedule', 'search', 'show',
                'list', 'delete', 'update', 'compose', 'draft',
            )
            cat_kw = ('работа', 'раздел', 'блок', 'группа', 'секция')
            has_action = any(kw in text_lower for kw in action_kw)
            is_category = any(kw in text_lower for kw in cat_kw)
            if is_category and not has_action:
                return True

        return False

    # ── Intent detection ──────────────────────────────────────────────────────

    def _detect_intent(self, tl: str) -> TaskIntent:
        """Определяет интент задачи. Порядок: более специфичные — первее."""
        # Email/Gmail — ПЕРЕД общим поиском (содержит 'найди')
        if self._match(tl, self._EMAIL_KW):
            return TaskIntent.EMAIL

        if self._match(tl, self._CALENDAR_KW):
            return TaskIntent.CALENDAR

        if self._match(tl, self._CONTACTS_KW):
            return TaskIntent.CONTACTS

        if self._match(tl, self._PDF_KW):
            return TaskIntent.PDF_EXTRACT

        if self._match(tl, self._USER_FILES_KW):
            return TaskIntent.USER_FILES

        if self._match(tl, self._WEATHER_KW):
            return TaskIntent.WEATHER

        if self._match(tl, self._TIME_KW):
            return TaskIntent.TIME

        if self._match(tl, self._CURRENCY_KW):
            return TaskIntent.CURRENCY

        if self._match(tl, self._SPORTS_KW):
            return TaskIntent.SPORTS

        # Явный веб-поиск (официальный источник, документация)
        web_explicit = (
            'официальн.*источник', 'official source',
            'найди.*документаци', 'библиотека.*документаци',
            'надёжн.*источник', 'reliable source',
            'найди.*источник', 'find.*source',
            'новости', 'latest news', 'recent news',
        )
        if self._match(tl, web_explicit):
            return TaskIntent.WEB_SEARCH

        return TaskIntent.GENERAL

    # ── Required args validation ──────────────────────────────────────────────

    def _check_missing_args(self, intent: TaskIntent, tl: str) -> list[RequiredArg]:
        """Возвращает список отсутствующих обязательных аргументов."""
        missing: list[RequiredArg] = []

        if intent == TaskIntent.WEATHER:
            if not self._has_location(tl):
                # "в моём городе" — recoverable из user-context
                if any(kw in tl for kw in ('мой город', 'my city', 'my location', 'моём городе', 'где я')):
                    missing.append(RequiredArg('location', 'город/геолокация пользователя', recoverable=True))
                else:
                    missing.append(RequiredArg('location', 'город или местоположение', recoverable=False))

        elif intent == TaskIntent.TIME:
            if not self._has_city_for_time(tl):
                missing.append(RequiredArg('timezone', 'город или часовой пояс'))

        elif intent == TaskIntent.CURRENCY:
            if not self._has_currency_pair(tl):
                missing.append(RequiredArg('currency_pair', 'пара валют (напр. USD/ILS)'))

        elif intent == TaskIntent.SPORTS:
            if self._team_is_vague(tl):
                missing.append(RequiredArg('team_name', 'название команды'))

        elif intent == TaskIntent.EMAIL:
            # Поиск по конкретному отправителю требует имени
            if any(kw in tl for kw in ('от конкретного', 'from specific', 'specific sender',
                                        'конкретного отправителя', 'заданного отправителя')):
                if self._is_vague(tl):
                    missing.append(RequiredArg('sender', 'email или имя отправителя'))

        elif intent == TaskIntent.CONTACTS:
            if any(kw in tl for kw in ('по имени', 'by name', 'named', 'по имя')):
                if self._is_vague(tl):
                    missing.append(RequiredArg('contact_name', 'имя контакта'))
            if any(kw in tl for kw in ('компани', 'company', 'организаци')):
                if self._is_vague(tl):
                    missing.append(RequiredArg('company_name', 'название компании'))

        elif intent == TaskIntent.PDF_EXTRACT:
            # Только если явно нет пути к файлу
            if not self._has_file_reference(tl):
                missing.append(RequiredArg('file_path', 'путь к PDF-файлу'))

        elif intent == TaskIntent.CALENDAR:
            if any(kw in tl for kw in ('по ключевому', 'keyword', 'по теме встреч')):
                if self._is_vague(tl):
                    missing.append(RequiredArg('keyword', 'ключевое слово для поиска встречи'))

        return missing

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _match(text: str, keywords: tuple | list) -> bool:
        """True если любой keyword из списка есть в тексте (поддержка regex)."""
        for kw in keywords:
            if '.*' in kw or r'\b' in kw:
                if re.search(kw, text):
                    return True
            elif kw in text:
                return True
        return False

    def _has_location(self, tl: str) -> bool:
        """Есть ли конкретное местоположение в тексте."""
        # Явные города
        cities = (
            'москва', 'moscow', 'лондон', 'london', 'paris', 'париж',
            'тель-авив', 'tel aviv', 'tel-aviv',
            'нью-йорк', 'new york', 'berlin', 'берлин',
            'токио', 'tokyo',
        )
        if any(c in tl for c in cities):
            return True
        # Общий паттерн "в Город"
        m = re.search(r'\bв\s+([А-ЯЁ][а-яё]{2,})', tl)
        if m:
            return True
        m2 = re.search(r'\bin\s+([A-Z][a-z]{2,})', tl)
        if m2:
            return True
        return False

    def _has_city_for_time(self, tl: str) -> bool:
        """Есть ли конкретный город для запроса времени."""
        cities = (
            'тель-авив', 'tel-aviv', 'tel aviv',
            'нью-йорк', 'new york', 'new-york',
            'москва', 'moscow', 'лондон', 'london',
            'берлин', 'berlin', 'paris', 'париж',
            'токио', 'tokyo',
        )
        return any(c in tl for c in cities)

    def _has_currency_pair(self, tl: str) -> bool:
        """Есть ли пара валют в тексте."""
        pairs = ('usd', 'eur', 'gbp', 'ils', 'rub', 'jpy', 'cny', 'chf',
                 'доллар', 'евро', 'шекель', 'рубл')
        # Нужно хотя бы 2 валюты или явная пара
        found = sum(1 for p in pairs if p in tl)
        return found >= 1 and ('/' in tl or 'курс' in tl or 'convert' in tl or 'конвертир' in tl)

    def _team_is_vague(self, tl: str) -> bool:
        """True если команда не была конкретно указана."""
        vague = (
            'конкретной команды', 'specific team',
            'заданной команды', 'определённой команды',
            'конкретная команда',
        )
        return any(v in tl for v in vague)

    def _is_vague(self, tl: str) -> bool:
        """True если задача содержит placeholder вместо конкретного значения."""
        return any(m in tl for m in self._VAGUE_MARKERS)

    def _has_file_reference(self, tl: str) -> bool:
        """True если в задаче есть конкретная ссылка на файл."""
        indicators = ('.pdf', '.docx', '.doc', '/', '\\', 'c:', '/home/',
                      'рабочий стол', 'desktop')
        # Должно быть конкретное указание, а не просто слово "pdf"
        has_path = any(p in tl for p in indicators)
        has_specific = not self._is_vague(tl)
        return has_path and has_specific


# ── Быстрая функция для использования без инстанцирования ────────────────────

_default_router = TaskRouter()


def route(task_text: str) -> TaskRoute:
    """Convenience function: TaskRouter().route(task_text)."""
    return _default_router.route(task_text)
